# SPDX-License-Identifier: Apache-2.0
"""Tests for the bundle fetcher: pinned hashes, traversal defense, idempotence, cleanup."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from skillmeld.models import CatalogEntry, SkillFile, SkillSource
from skillmeld.registries.catalog import bundle_hash
from skillmeld.registries.fetch import FetchError, fetch_bundle

CONTENT = {
    "SKILL.md": b"# fixture skill\n",
    "scripts/run.sh": b"echo hi\n",
}


def _entry(content: dict[str, bytes] | None = None, **overrides: object) -> CatalogEntry:
    payload = CONTENT if content is None else content
    files = [
        SkillFile(path=path, sha256=hashlib.sha256(data).hexdigest())
        for path, data in sorted(payload.items())
    ]
    fields: dict[str, object] = {
        "id": "acme/skills:fixture",
        "source": SkillSource(name="fixture", repo="acme/skills"),
        "files": files,
        "fetch_base": "https://host/raw",
        "bundle_hash": bundle_hash(files),
    }
    fields.update(overrides)
    return CatalogEntry.model_validate(fields)


def _client(
    content: dict[str, bytes] | None = None, calls: list[str] | None = None
) -> httpx.Client:
    payload = CONTENT if content is None else content

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request.url.path)
        path = request.url.path.removeprefix("/raw/")
        if path in payload:
            return httpx.Response(200, content=payload[path])
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_writes_verified_bundle(tmp_path: Path) -> None:
    entry = _entry()
    dest = fetch_bundle(entry, cache_dir=tmp_path, client=_client())
    assert dest == tmp_path / "bundles" / entry.bundle_hash
    assert (dest / "SKILL.md").read_bytes() == CONTENT["SKILL.md"]
    assert (dest / "scripts" / "run.sh").read_bytes() == CONTENT["scripts/run.sh"]


def test_fetch_is_idempotent_offline(tmp_path: Path) -> None:
    entry = _entry()
    fetch_bundle(entry, cache_dir=tmp_path, client=_client())
    calls: list[str] = []
    dest = fetch_bundle(entry, cache_dir=tmp_path, client=_client(calls=calls))
    assert calls == []
    assert dest.is_dir()


def test_fetch_refetches_a_corrupted_cache(tmp_path: Path) -> None:
    entry = _entry()
    dest = fetch_bundle(entry, cache_dir=tmp_path, client=_client())
    (dest / "SKILL.md").write_bytes(b"tampered")
    calls: list[str] = []
    fetch_bundle(entry, cache_dir=tmp_path, client=_client(calls=calls))
    assert calls != []
    assert (dest / "SKILL.md").read_bytes() == CONTENT["SKILL.md"]


def test_hash_mismatch_refuses_and_cleans_up(tmp_path: Path) -> None:
    entry = _entry()
    wrong = {"SKILL.md": b"not what was pinned", "scripts/run.sh": CONTENT["scripts/run.sh"]}
    with pytest.raises(FetchError, match="hash check"):
        fetch_bundle(entry, cache_dir=tmp_path, client=_client(content=wrong))
    bundles = tmp_path / "bundles"
    assert not (bundles / entry.bundle_hash).exists()
    assert not list(bundles.glob(".tmp-*"))


def test_traversal_paths_are_refused_before_any_fetch(tmp_path: Path) -> None:
    calls: list[str] = []
    client = _client(calls=calls)
    for bad in ("../evil", "/etc/passwd", "a/../../b", "C:evil", "a\\b"):
        files = [SkillFile(path=bad, sha256="00")]
        entry = _entry(files=files, bundle_hash=bundle_hash(files))
        with pytest.raises(FetchError, match="unsafe file path"):
            fetch_bundle(entry, cache_dir=tmp_path, client=client)
    assert calls == []


def test_inconsistent_catalog_entry_is_refused(tmp_path: Path) -> None:
    entry = _entry(bundle_hash="not-the-real-hash")
    with pytest.raises(FetchError, match="inconsistent"):
        fetch_bundle(entry, cache_dir=tmp_path, client=_client())


def test_entry_without_files_or_base_is_refused(tmp_path: Path) -> None:
    with pytest.raises(FetchError, match="no files"):
        fetch_bundle(_entry(files=[], bundle_hash=""), cache_dir=tmp_path, client=_client())
    with pytest.raises(FetchError, match="fetch_base"):
        fetch_bundle(_entry(fetch_base=None), cache_dir=tmp_path, client=_client())


def test_extra_files_in_cache_force_a_refetch(tmp_path: Path) -> None:
    entry = _entry()
    dest = fetch_bundle(entry, cache_dir=tmp_path, client=_client())
    (dest / "extra.txt").write_bytes(b"smuggled")
    calls: list[str] = []
    fetch_bundle(entry, cache_dir=tmp_path, client=_client(calls=calls))
    assert calls != []
    assert not (dest / "extra.txt").exists()
