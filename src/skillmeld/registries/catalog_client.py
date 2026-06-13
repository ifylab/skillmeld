# SPDX-License-Identifier: Apache-2.0
"""Runtime client for the hosted data layer: fetch, verify (Ed25519), cache, offline snapshot.

The hosted layer is pull-only signed static data. We verify a signed manifest against an
embedded public key, hash-pin each artifact, refuse rollbacks, and support an offline snapshot.
The server only ever sees a public file being fetched.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import ValidationError

from skillmeld.models import Artifact, CatalogManifest

# Custom-domain route on ifylab.dev (Cloudflare R2). Wired to the live bucket in W8.
DEFAULT_BASE_URL = "https://ifylab.dev/skillmeld"

# Embedded Ed25519 public keys, by key id. The production key is added in W8.
TRUSTED_KEYS: dict[str, bytes] = {}

# Dev-only trust hook: a local builder exports its public key here so a locally-signed catalog
# verifies offline, with no production key. Never set this in a trusted/production environment.
_DEV_PUBKEY_ENV = "SKILLMELD_DEV_PUBKEY"

_TIMEOUT = 30.0


class CatalogError(Exception):
    """A hosted artifact could not be fetched, verified, or trusted."""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def effective_trusted_keys() -> dict[str, bytes]:
    """Embedded production keys plus an optional dev key from the environment."""
    keys = dict(TRUSTED_KEYS)
    dev_hex = os.environ.get(_DEV_PUBKEY_ENV)
    if dev_hex:
        with contextlib.suppress(ValueError):
            keys["dev"] = bytes.fromhex(dev_hex)
    return keys


def cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "skillmeld"


def verify_manifest(
    raw: bytes, signature: bytes, trusted_keys: Mapping[str, bytes] | None = None
) -> CatalogManifest:
    """Verify the manifest signature against a trusted key, then parse it."""
    keys = effective_trusted_keys() if trusted_keys is None else trusted_keys
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise CatalogError("manifest is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise CatalogError("manifest must be a JSON object")
    key_id = parsed.get("key_id")
    if not isinstance(key_id, str) or key_id not in keys:
        raise CatalogError(f"manifest signed by an untrusted key: {key_id!r}")
    try:
        Ed25519PublicKey.from_public_bytes(keys[key_id]).verify(signature, raw)
    except InvalidSignature as exc:
        raise CatalogError("manifest signature did not verify") from exc
    try:
        return CatalogManifest.model_validate(parsed)
    except ValidationError as exc:
        raise CatalogError("manifest structure is invalid") from exc


def verify_artifact(artifact: Artifact, data: bytes) -> None:
    """Check artifact bytes against the hash pinned in the signed manifest."""
    actual = sha256_hex(data)
    if actual != artifact.sha256:
        raise CatalogError(
            f"artifact {artifact.name!r} failed its hash check "
            f"(expected {artifact.sha256[:12]}, got {actual[:12]})"
        )


def ensure_fresh(incoming: CatalogManifest, cached: CatalogManifest | None) -> None:
    """Refuse a manifest older than the cached one. generated_at is ISO-8601 UTC."""
    if cached is not None and incoming.generated_at < cached.generated_at:
        raise CatalogError("refusing a manifest older than the cached one (possible rollback)")


def cached_manifest(cache_dir: Path) -> CatalogManifest | None:
    """Read the previously verified manifest from the cache, if present."""
    path = cache_dir / "manifest.json"
    if not path.is_file():
        return None
    try:
        return CatalogManifest.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return None


def load_snapshot(
    snapshot_dir: Path, trusted_keys: Mapping[str, bytes] | None = None
) -> CatalogManifest:
    """Verify and load an offline snapshot dir (manifest.json + manifest.sig); no network."""
    raw = (snapshot_dir / "manifest.json").read_bytes()
    signature = (snapshot_dir / "manifest.sig").read_bytes()
    return verify_manifest(raw, signature, trusted_keys)


def sync(
    base_url: str = DEFAULT_BASE_URL,
    *,
    cache_dir: Path | None = None,
    trusted_keys: Mapping[str, bytes] | None = None,
    client: httpx.Client | None = None,
) -> CatalogManifest:
    """Fetch the signed manifest and artifacts, verify everything, and cache locally."""
    target = cache_dir if cache_dir is not None else cache_root()
    owns_client = client is None
    http = client if client is not None else httpx.Client(timeout=_TIMEOUT)
    try:
        raw = _fetch(http, f"{base_url}/manifest.json")
        signature = _fetch(http, f"{base_url}/manifest.sig")
        manifest = verify_manifest(raw, signature, trusted_keys)
        ensure_fresh(manifest, cached_manifest(target))
        blobs = target / "blobs"
        blobs.mkdir(parents=True, exist_ok=True)
        for artifact in manifest.artifacts:
            dest = blobs / artifact.sha256
            if dest.is_file() and sha256_hex(dest.read_bytes()) == artifact.sha256:
                continue
            data = _fetch(http, artifact.url)
            verify_artifact(artifact, data)
            dest.write_bytes(data)
        (target / "manifest.json").write_bytes(raw)
        (target / "manifest.sig").write_bytes(signature)
        return manifest
    except httpx.HTTPError as exc:
        raise CatalogError(f"fetch failed: {exc}") from exc
    finally:
        if owns_client:
            http.close()


def _fetch(http: httpx.Client, url: str) -> bytes:
    response = http.get(url)
    response.raise_for_status()
    return response.content
