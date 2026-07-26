# SPDX-License-Identifier: Apache-2.0
"""Build a signed catalog cache the client verifies identically to the hosted one.

Crawls repos into a CatalogDocument, writes content-addressed blobs, and signs a manifest with
an Ed25519 key. Two entry points share the machinery: ``build_dev_catalog`` generates a dev key
plus the ``SKILLMELD_DEV_PUBKEY`` trust hook so the whole discovery path runs offline with no
Cloudflare and no production key; ``build_production_catalog`` signs with the production key
(seed from ``SKILLMELD_SIGNING_KEY``), refuses a key the shipped client would not trust, and
adds the verdict-index artifact — every crawled bundle fetched, hash-verified, and scanned.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from skillmeld.models import (
    Artifact,
    CatalogDocument,
    CatalogEntry,
    CatalogManifest,
    VerdictIndex,
    VerdictRecord,
)
from skillmeld.registries.catalog import ARTIFACT_CATALOG, ARTIFACT_VERDICTS
from skillmeld.registries.catalog_client import (
    PRODUCTION_KEY_ID,
    TRUSTED_KEYS,
    sha256_hex,
)
from skillmeld.registries.fetch import fetch_bundle
from skillmeld.registries.github_crawl import crawl
from skillmeld.security.scan import scan_bundle

_SIGNING_KEY_ENV = "SKILLMELD_SIGNING_KEY"


def generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate a dev signing keypair; return the private key and its raw public hex."""
    private = Ed25519PrivateKey.generate()
    public_hex = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    return private, public_hex


def signing_key_from_env() -> Ed25519PrivateKey:
    """Load the production signing key (32-byte seed, hex) from ``SKILLMELD_SIGNING_KEY``.

    Refuses a key whose public half is not the one embedded in the client under
    ``PRODUCTION_KEY_ID`` — a catalog signed with anything else would be built successfully
    and then rejected by every client, so fail at build time instead.
    """
    seed_hex = os.environ.get(_SIGNING_KEY_ENV, "").strip()
    if not seed_hex:
        raise ValueError(f"{_SIGNING_KEY_ENV} is not set; refusing to build an unsigned catalog")
    try:
        private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex))
    except ValueError as exc:
        raise ValueError(f"{_SIGNING_KEY_ENV} is not a valid 32-byte hex seed") from exc
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    if TRUSTED_KEYS.get(PRODUCTION_KEY_ID) != public:
        raise ValueError(
            f"{_SIGNING_KEY_ENV} does not match the embedded public key "
            f"{PRODUCTION_KEY_ID!r}; clients would refuse everything it signs"
        )
    return private


def build_catalog(
    document: CatalogDocument,
    out_dir: Path,
    *,
    private_key: Ed25519PrivateKey,
    key_id: str,
    generated_at: str,
    base_url: str,
    verdicts: VerdictIndex | None = None,
) -> CatalogManifest:
    """Write blobs + a signed manifest into ``out_dir`` (cache layout). Returns the manifest."""
    payloads: dict[str, bytes] = {ARTIFACT_CATALOG: document.model_dump_json().encode("utf-8")}
    if verdicts is not None:
        payloads[ARTIFACT_VERDICTS] = verdicts.model_dump_json().encode("utf-8")

    blobs = out_dir / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    artifacts: list[Artifact] = []
    for name, payload in payloads.items():
        digest = sha256_hex(payload)
        (blobs / digest).write_bytes(payload)
        artifacts.append(
            Artifact(
                name=name,
                version=generated_at,
                url=f"{base_url.rstrip('/')}/blobs/{digest}",
                sha256=digest,
                size=len(payload),
            )
        )
    manifest = CatalogManifest(generated_at=generated_at, key_id=key_id, artifacts=artifacts)
    raw = _canonical_manifest(manifest)
    signature = private_key.sign(raw)
    (out_dir / "manifest.json").write_bytes(raw)
    (out_dir / "manifest.sig").write_bytes(signature)
    return manifest


def build_verdict_index(
    entries: list[CatalogEntry],
    *,
    generated_at: str,
    fetch_cache_dir: Path | None = None,
    client: httpx.Client | None = None,
) -> VerdictIndex:
    """Fetch and scan every catalog entry into advisory verdict records.

    Each bundle is downloaded through the same hash-verifying fetcher the runtime uses, then
    run through the local security gate. The records are advisory by construction: a client
    treats a hosted PASS as a fast-path cache and re-scans on any scanner or ruleset mismatch,
    and a hosted BLOCK only ever pre-drops — the local gate remains authoritative.
    """
    records: list[VerdictRecord] = []
    for entry in entries:
        bundle_dir = fetch_bundle(entry, cache_dir=fetch_cache_dir, client=client)
        report = scan_bundle(bundle_dir)
        records.append(
            VerdictRecord(
                bundle_hash=entry.bundle_hash,
                scanner_version=report.scanner_version,
                ruleset_versions=report.rulesets,
                verdict=report.verdict,
                findings=report.findings,
                scanned_at=generated_at,
                license=entry.source.license,
            )
        )
    return VerdictIndex(generated_at=generated_at, records=records)


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


def build_production_catalog(
    repos: list[str],
    out_dir: Path,
    *,
    private_key: Ed25519PrivateKey,
    generated_at: str,
    base_url: str,
    ref: str | None = None,
    fetch_cache_dir: Path | None = None,
    client: httpx.Client | None = None,
) -> tuple[CatalogManifest, CatalogDocument, VerdictIndex]:
    """Crawl, scan, and sign the production artifacts (catalog + verdict index)."""
    entries = crawl(repos, ref=ref, client=client)
    document = CatalogDocument(generated_at=generated_at, entries=entries)
    verdicts = build_verdict_index(
        entries, generated_at=generated_at, fetch_cache_dir=fetch_cache_dir, client=client
    )
    manifest = build_catalog(
        document,
        out_dir,
        private_key=private_key,
        key_id=PRODUCTION_KEY_ID,
        generated_at=generated_at,
        base_url=base_url,
        verdicts=verdicts,
    )
    return manifest, document, verdicts


def _canonical_manifest(manifest: CatalogManifest) -> bytes:
    """Serialize the manifest deterministically (sorted keys) so the signature is reproducible."""
    return json.dumps(manifest.model_dump(), sort_keys=True, separators=(",", ":")).encode("utf-8")
