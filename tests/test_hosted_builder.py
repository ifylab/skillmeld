# SPDX-License-Identifier: Apache-2.0
"""W8a: crawl GitHub (mocked) -> build a dev-signed catalog -> verify -> discover, all offline."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from skillmeld.hosted.build_catalog import build_dev_catalog
from skillmeld.registries import catalog as catalog_data
from skillmeld.registries import catalog_client as cat
from skillmeld.registries.github_crawl import crawl

REPO = "acme/skills"
TREE = {
    "tree": [
        {"path": "LICENSE", "type": "blob"},
        {"path": "ifc-qto/SKILL.md", "type": "blob"},
        {"path": "ifc-qto/reference.md", "type": "blob"},
        {"path": "review/SKILL.md", "type": "blob"},
        {"path": "review", "type": "tree"},
    ]
}
FILES = {
    "LICENSE": b"MIT License\n\nPermission is hereby granted, free of charge, to any person...\n",
    "ifc-qto/SKILL.md": (
        b"---\nname: ifc-qto\ndescription: Quantity takeoff.\ntags: ifc, qto\n---\n"
        b"# QTO\n\nDo the takeoff.\n"
    ),
    "ifc-qto/reference.md": b"# Reference\n\nDetails.\n",
    "review/SKILL.md": (
        b"---\nname: review\ndescription: Review models.\n---\n# Review\n\nReview them.\n"
    ),
}


def _mock_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.github.com" in url and "/git/trees/" in url:
            return httpx.Response(200, json=TREE)
        if "api.github.com" in url and url.rstrip("/").endswith(f"/repos/{REPO}"):
            return httpx.Response(200, json={"default_branch": "main"})
        if "raw.githubusercontent.com" in url:
            rel = url.split(f"/{REPO}/main/", 1)[1]
            if rel in FILES:
                return httpx.Response(200, content=FILES[rel])
            return httpx.Response(404)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_crawl_builds_entries_with_hashes_and_license() -> None:
    entries = crawl([REPO], client=_mock_client())
    assert [e.id for e in entries] == ["acme/skills:ifc-qto", "acme/skills:review"]
    qto = entries[0]
    assert qto.source.license.spdx_id == "MIT"
    assert qto.description == "Quantity takeoff."
    assert "ifc" in qto.tags and "qto" in qto.tags
    assert {f.path for f in qto.files} == {"SKILL.md", "reference.md"}
    assert qto.bundle_hash and all(len(f.sha256) == 64 for f in qto.files)
    assert qto.fetch_base == "https://raw.githubusercontent.com/acme/skills/main/ifc-qto"


def test_crawl_resolves_a_non_main_default_branch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.github.com" in url and "/git/trees/master" in url:
            return httpx.Response(200, json={"tree": [{"path": "s/SKILL.md", "type": "blob"}]})
        if "api.github.com" in url and url.rstrip("/").endswith("/repos/acme/legacy"):
            return httpx.Response(200, json={"default_branch": "master"})
        if "raw.githubusercontent.com/acme/legacy/master/" in url:
            return httpx.Response(200, content=b"---\nname: s\ndescription: d\n---\n# S\n\nGo.\n")
        return httpx.Response(404)

    entries = crawl(["acme/legacy"], client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert len(entries) == 1
    assert entries[0].fetch_base == "https://raw.githubusercontent.com/acme/legacy/master/s"


def test_crawl_includes_nested_skill_files() -> None:
    tree = {
        "tree": [
            {"path": "skills/x/SKILL.md", "type": "blob"},
            {"path": "skills/x/references/guide.md", "type": "blob"},
            {"path": "skills/x/resources/data.jsonl", "type": "blob"},
            {"path": "README.md", "type": "blob"},  # outside any skill dir -> excluded
        ]
    }
    skill_md = b"---\nname: x\ndescription: d\n---\n# X\n\nSee references/guide.md.\n"
    files = {
        "skills/x/SKILL.md": skill_md,
        "skills/x/references/guide.md": b"# Guide\n",
        "skills/x/resources/data.jsonl": b'{"k": 1}\n',
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.github.com" in url and "/git/trees/" in url:
            return httpx.Response(200, json=tree)
        if "api.github.com" in url and url.rstrip("/").endswith("/repos/acme/nested"):
            return httpx.Response(200, json={"default_branch": "main"})
        if "raw.githubusercontent.com" in url:
            rel = url.split("/acme/nested/main/", 1)[1]
            return httpx.Response(200, content=files[rel]) if rel in files else httpx.Response(404)
        return httpx.Response(404)

    entries = crawl(["acme/nested"], client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert len(entries) == 1
    assert {f.path for f in entries[0].files} == {
        "SKILL.md",
        "references/guide.md",
        "resources/data.jsonl",
    }


def test_dev_catalog_round_trips_through_the_signed_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    manifest, pubkey = build_dev_catalog(
        [REPO], cache, generated_at="2026-06-10T00:00:00Z", client=_mock_client()
    )
    assert manifest.key_id == "dev"

    # The signed manifest verifies against the exported dev key.
    monkeypatch.setenv("SKILLMELD_DEV_PUBKEY", pubkey)
    verified = cat.load_snapshot(cache)
    assert verified.generated_at == "2026-06-10T00:00:00Z"

    # And the discovery loader reads the cached catalog by its pinned hash.
    document = catalog_data.load_catalog(cache)
    assert {e.id for e in document.entries} == {"acme/skills:ifc-qto", "acme/skills:review"}


def test_dev_catalog_manifest_signature_is_rejected_when_untrusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    build_dev_catalog([REPO], cache, generated_at="2026-06-10T00:00:00Z", client=_mock_client())
    monkeypatch.delenv("SKILLMELD_DEV_PUBKEY", raising=False)
    with pytest.raises(cat.CatalogError, match="untrusted key"):
        cat.load_snapshot(cache)


def test_dev_catalog_cache_shape(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _, pubkey = build_dev_catalog(
        [REPO], cache, generated_at="2026-06-10T00:00:00Z", client=_mock_client()
    )
    assert (cache / "manifest.json").is_file()
    assert (cache / "manifest.sig").is_file()
    assert list((cache / "blobs").iterdir())
    assert len(bytes.fromhex(pubkey)) == 32
    payload = json.loads((cache / "manifest.json").read_bytes())
    assert payload["key_id"] == "dev"
