# SPDX-License-Identifier: Apache-2.0
"""Tests for the catalog client: signature, hash pinning, anti-rollback, snapshot, sync."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from skillmeld.models import Artifact
from skillmeld.registries import catalog_client as cat

KEY_ID = "test-key-1"


def _keypair() -> tuple[Ed25519PrivateKey, dict[str, bytes]]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private, {KEY_ID: public}


def _signed_manifest(
    private: Ed25519PrivateKey,
    artifacts: list[dict[str, object]],
    generated_at: str = "2026-06-09T00:00:00Z",
) -> tuple[bytes, bytes]:
    manifest = {
        "schema_version": 1,
        "generated_at": generated_at,
        "key_id": KEY_ID,
        "artifacts": artifacts,
    }
    raw = json.dumps(manifest).encode()
    return raw, private.sign(raw)


def test_verify_manifest_accepts_good_signature() -> None:
    private, trusted = _keypair()
    raw, sig = _signed_manifest(private, [])
    manifest = cat.verify_manifest(raw, sig, trusted)
    assert manifest.key_id == KEY_ID


def test_verify_manifest_rejects_tampered() -> None:
    private, trusted = _keypair()
    raw, sig = _signed_manifest(private, [])
    tampered = raw.replace(b"2026", b"2025")
    with pytest.raises(cat.CatalogError):
        cat.verify_manifest(tampered, sig, trusted)


def test_verify_manifest_rejects_unknown_key() -> None:
    private, _ = _keypair()
    raw, sig = _signed_manifest(private, [])
    with pytest.raises(cat.CatalogError):
        cat.verify_manifest(raw, sig, {})


def test_verify_artifact_hash_mismatch() -> None:
    artifact = Artifact(name="x", version="1", url="https://e/x", sha256="deadbeef")
    with pytest.raises(cat.CatalogError):
        cat.verify_artifact(artifact, b"not the right bytes")


def test_ensure_fresh_blocks_rollback() -> None:
    private, trusted = _keypair()
    new_raw, new_sig = _signed_manifest(private, [], "2026-06-09T00:00:00Z")
    old_raw, old_sig = _signed_manifest(private, [], "2026-06-01T00:00:00Z")
    newer = cat.verify_manifest(new_raw, new_sig, trusted)
    older = cat.verify_manifest(old_raw, old_sig, trusted)
    cat.ensure_fresh(newer, older)
    with pytest.raises(cat.CatalogError):
        cat.ensure_fresh(older, newer)


def test_load_snapshot_roundtrip(tmp_path: Path) -> None:
    private, trusted = _keypair()
    raw, sig = _signed_manifest(private, [])
    (tmp_path / "manifest.json").write_bytes(raw)
    (tmp_path / "manifest.sig").write_bytes(sig)
    manifest = cat.load_snapshot(tmp_path, trusted)
    assert manifest.schema_version == 1


def test_sync_fetches_verifies_and_caches(tmp_path: Path) -> None:
    private, trusted = _keypair()
    blob = b"catalog-bytes"
    digest = cat.sha256_hex(blob)
    artifacts: list[dict[str, object]] = [
        {
            "name": "catalog",
            "version": "1",
            "url": "https://host/catalog.json",
            "sha256": digest,
            "size": len(blob),
        }
    ]
    raw, sig = _signed_manifest(private, artifacts)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("manifest.json"):
            return httpx.Response(200, content=raw)
        if path.endswith("manifest.sig"):
            return httpx.Response(200, content=sig)
        if path.endswith("catalog.json"):
            return httpx.Response(200, content=blob)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://host")
    manifest = cat.sync("https://host", cache_dir=tmp_path, trusted_keys=trusted, client=client)
    assert [a.name for a in manifest.artifacts] == ["catalog"]
    assert (tmp_path / "blobs" / digest).read_bytes() == blob
    assert (tmp_path / "manifest.json").read_bytes() == raw


def test_sync_rejects_bad_artifact_hash(tmp_path: Path) -> None:
    private, trusted = _keypair()
    artifacts: list[dict[str, object]] = [
        {
            "name": "catalog",
            "version": "1",
            "url": "https://host/catalog.json",
            "sha256": "deadbeef",
            "size": 1,
        }
    ]
    raw, sig = _signed_manifest(private, artifacts)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("manifest.json"):
            return httpx.Response(200, content=raw)
        if request.url.path.endswith("manifest.sig"):
            return httpx.Response(200, content=sig)
        return httpx.Response(200, content=b"wrong bytes")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://host")
    with pytest.raises(cat.CatalogError):
        cat.sync("https://host", cache_dir=tmp_path, trusted_keys=trusted, client=client)


def _served_catalog() -> tuple[dict[str, bytes], dict[str, bytes], bytes, str]:
    """A signed one-artifact catalog as served paths: (files, trusted keys, blob, digest)."""
    private, trusted = _keypair()
    blob = b"catalog-bytes"
    digest = cat.sha256_hex(blob)
    artifacts: list[dict[str, object]] = [
        {
            "name": "catalog",
            "version": "1",
            "url": "https://host/catalog.json",
            "sha256": digest,
            "size": len(blob),
        }
    ]
    raw, sig = _signed_manifest(private, artifacts)
    served = {"/manifest.json": raw, "/manifest.sig": sig, "/catalog.json": blob}
    return served, trusted, blob, digest


def test_sync_survives_a_transient_5xx(tmp_path: Path, sleeps: list[float]) -> None:
    served, trusted, blob, digest = _served_catalog()
    failed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/manifest.json" and not failed:
            failed.append(request.url.path)
            return httpx.Response(502)
        return httpx.Response(200, content=served[request.url.path])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cat.sync("https://host", cache_dir=tmp_path, trusted_keys=trusted, client=client)
    assert failed == ["/manifest.json"]
    assert sleeps == [1.0]
    assert (tmp_path / "blobs" / digest).read_bytes() == blob


def test_sync_without_a_client_identifies_as_skillmeld(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from skillmeld.registries.net import USER_AGENT, new_client

    served, trusted, _, _ = _served_catalog()
    agents: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        agents.append(request.headers["User-Agent"])
        return httpx.Response(200, content=served[request.url.path])

    def identified() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), headers=new_client().headers)

    monkeypatch.setattr(cat, "new_client", identified)
    cat.sync("https://host", cache_dir=tmp_path, trusted_keys=trusted)
    assert len(agents) == 3 and set(agents) == {USER_AGENT}
