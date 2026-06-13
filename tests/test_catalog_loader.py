# SPDX-License-Identifier: Apache-2.0
"""Tests for cache-side artifact loading and the pinned bundle-hash canonicalization."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from skillmeld.models import (
    CatalogDocument,
    CatalogEntry,
    SkillFile,
    SkillSource,
    Verdict,
    VerdictIndex,
    VerdictRecord,
)
from skillmeld.registries import catalog
from skillmeld.registries.catalog_client import CatalogError


def _build_cache(root: Path, artifacts: dict[str, bytes]) -> Path:
    cache = root / "cache"
    blobs = cache / "blobs"
    blobs.mkdir(parents=True)
    listed: list[dict[str, object]] = []
    for name, data in artifacts.items():
        digest = hashlib.sha256(data).hexdigest()
        (blobs / digest).write_bytes(data)
        listed.append(
            {
                "name": name,
                "version": "1",
                "url": f"https://host/{name}.json",
                "sha256": digest,
                "size": len(data),
            }
        )
    manifest = {
        "schema_version": 1,
        "generated_at": "2026-06-09T00:00:00Z",
        "key_id": "test-key-1",
        "artifacts": listed,
    }
    (cache / "manifest.json").write_text(json.dumps(manifest))
    return cache


def _catalog_bytes() -> bytes:
    entry = CatalogEntry(id="a/b:skill", source=SkillSource(name="skill"))
    document = CatalogDocument(generated_at="2026-06-09T00:00:00Z", entries=[entry])
    return document.model_dump_json().encode()


def _verdicts_bytes() -> bytes:
    records = [
        VerdictRecord(
            bundle_hash="bad-hash",
            scanner_version="0.1",
            verdict=Verdict.BLOCK,
            scanned_at="2026-06-09T00:00:00Z",
        ),
        VerdictRecord(
            bundle_hash="good-hash",
            scanner_version="0.1",
            verdict=Verdict.PASS,
            scanned_at="2026-06-09T00:00:00Z",
        ),
    ]
    index = VerdictIndex(generated_at="2026-06-09T00:00:00Z", records=records)
    return index.model_dump_json().encode()


def test_bundle_hash_canonicalization_is_pinned() -> None:
    files = [
        SkillFile(path="a/b.txt", sha256="bb"),
        SkillFile(path="SKILL.md", sha256="aa"),
    ]
    expected = hashlib.sha256(b"SKILL.md\x00aa\na/b.txt\x00bb").hexdigest()
    assert catalog.bundle_hash(files) == expected
    assert catalog.bundle_hash(list(reversed(files))) == expected


def test_load_catalog_from_cache(tmp_path: Path) -> None:
    cache = _build_cache(tmp_path, {"catalog": _catalog_bytes()})
    document = catalog.load_catalog(cache)
    assert [entry.id for entry in document.entries] == ["a/b:skill"]


def test_load_catalog_requires_a_synced_cache(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="catalog sync"):
        catalog.load_catalog(tmp_path / "empty")


def test_load_catalog_rejects_corrupted_blob(tmp_path: Path) -> None:
    cache = _build_cache(tmp_path, {"catalog": _catalog_bytes()})
    blob = next((cache / "blobs").iterdir())
    blob.write_bytes(b"tampered")
    with pytest.raises(CatalogError, match="hash check"):
        catalog.load_catalog(cache)


def test_load_catalog_rejects_invalid_document(tmp_path: Path) -> None:
    cache = _build_cache(tmp_path, {"catalog": b'{"entries": "not-a-list"}'})
    with pytest.raises(CatalogError, match="invalid"):
        catalog.load_catalog(cache)


def test_load_catalog_file_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    path.write_bytes(_catalog_bytes())
    document = catalog.load_catalog_file(path)
    assert len(document.entries) == 1


def test_load_catalog_file_missing(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="not readable"):
        catalog.load_catalog_file(tmp_path / "absent.json")


def test_fixture_catalog_parses(fixtures_dir: Path) -> None:
    document = catalog.load_catalog_file(fixtures_dir / "catalog.json")
    assert len(document.entries) == 10


def test_blocked_hashes_from_verdict_index(tmp_path: Path) -> None:
    cache = _build_cache(tmp_path, {"catalog": _catalog_bytes(), "verdicts": _verdicts_bytes()})
    assert catalog.load_blocked_hashes(cache) == {"bad-hash"}


def test_blocked_hashes_empty_without_index(tmp_path: Path) -> None:
    cache = _build_cache(tmp_path, {"catalog": _catalog_bytes()})
    assert catalog.load_blocked_hashes(cache) == set()
    assert catalog.load_blocked_hashes(tmp_path / "unsynced") == set()
