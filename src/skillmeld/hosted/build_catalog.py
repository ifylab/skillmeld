# SPDX-License-Identifier: Apache-2.0
"""Build a signed catalog cache the client verifies identically to the hosted one.

Crawls repos into a CatalogDocument, writes content-addressed blobs, and signs a manifest with
an Ed25519 key. Locally this uses a generated dev key plus the ``SKILLMELD_DEV_PUBKEY`` trust
hook, so the whole discovery path runs offline with no Cloudflare, no production key, and no
ops. The publish wrapper (upload to R2, embed the production key) is layered on top later.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from skillmeld.models import Artifact, CatalogDocument, CatalogManifest
from skillmeld.registries.catalog import ARTIFACT_CATALOG
from skillmeld.registries.catalog_client import sha256_hex
from skillmeld.registries.github_crawl import crawl


def generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate a dev signing keypair; return the private key and its raw public hex."""
    private = Ed25519PrivateKey.generate()
    public_hex = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    return private, public_hex


def build_catalog(
    document: CatalogDocument,
    out_dir: Path,
    *,
    private_key: Ed25519PrivateKey,
    key_id: str,
    generated_at: str,
    base_url: str,
) -> CatalogManifest:
    """Write blobs + a signed manifest into ``out_dir`` (cache layout). Returns the manifest."""
    blobs = out_dir / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)

    payload = document.model_dump_json().encode("utf-8")
    digest = sha256_hex(payload)
    (blobs / digest).write_bytes(payload)
    artifact = Artifact(
        name=ARTIFACT_CATALOG,
        version=generated_at,
        url=f"{base_url.rstrip('/')}/blobs/{digest}",
        sha256=digest,
        size=len(payload),
    )
    manifest = CatalogManifest(generated_at=generated_at, key_id=key_id, artifacts=[artifact])
    raw = _canonical_manifest(manifest)
    signature = private_key.sign(raw)
    (out_dir / "manifest.json").write_bytes(raw)
    (out_dir / "manifest.sig").write_bytes(signature)
    return manifest


def build_dev_catalog(
    repos: list[str],
    out_dir: Path,
    *,
    generated_at: str,
    ref: str | None = None,
    client: httpx.Client | None = None,
) -> tuple[CatalogManifest, str]:
    """Crawl ``repos`` and build a dev-signed catalog cache. Returns (manifest, public-key hex)."""
    entries = crawl(repos, ref=ref, client=client)
    document = CatalogDocument(generated_at=generated_at, entries=entries)
    private, public_hex = generate_keypair()
    manifest = build_catalog(
        document,
        out_dir,
        private_key=private,
        key_id="dev",
        generated_at=generated_at,
        base_url=out_dir.resolve().as_uri(),
    )
    return manifest, public_hex


def _canonical_manifest(manifest: CatalogManifest) -> bytes:
    """Serialize the manifest deterministically (sorted keys) so the signature is reproducible."""
    return json.dumps(manifest.model_dump(), sort_keys=True, separators=(",", ":")).encode("utf-8")
