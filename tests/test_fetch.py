# SPDX-License-Identifier: Apache-2.0
"""Tests for the bundle fetcher: pinned hashes, traversal defense, idempotence, cleanup."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from skillmeld.models import CatalogEntry, SkillFile, SkillSource
from skillmeld.registries import fetch as fetch_module
from skillmeld.registries.catalog import bundle_hash
from skillmeld.registries.fetch import FetchError, fetch_bundle, get_with_retry

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


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(fetch_module, "sleep", recorded.append)
    return recorded


def _scripted(script: list[httpx.Response | Exception], calls: list[str]) -> httpx.Client:
    """Answer each request with the next scripted response, or raise it if it is an error."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_retry_recovers_from_transient_5xx(sleeps: list[float]) -> None:
    calls: list[str] = []
    script: list[httpx.Response | Exception] = [
        httpx.Response(501),
        httpx.Response(502),
        httpx.Response(200, content=b"ok"),
    ]
    response = get_with_retry(_scripted(script, calls), "https://host/raw/f")
    assert response.status_code == 200 and response.content == b"ok"
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]


def test_retry_recovers_from_a_transport_error(sleeps: list[float]) -> None:
    calls: list[str] = []
    script: list[httpx.Response | Exception] = [
        httpx.ConnectError("reset"),
        httpx.Response(200, content=b"ok"),
    ]
    assert get_with_retry(_scripted(script, calls), "https://host/raw/f").status_code == 200
    assert sleeps == [1.0]


def test_retry_gives_up_after_three_retries(sleeps: list[float]) -> None:
    calls: list[str] = []
    script: list[httpx.Response | Exception] = [httpx.Response(503) for _ in range(4)]
    assert get_with_retry(_scripted(script, calls), "https://host/raw/f").status_code == 503
    assert len(calls) == 4
    assert sleeps == [1.0, 2.0, 4.0]


def test_retry_reraises_a_persistent_transport_error(sleeps: list[float]) -> None:
    calls: list[str] = []
    script: list[httpx.Response | Exception] = [httpx.ReadTimeout("slow") for _ in range(4)]
    with pytest.raises(httpx.ReadTimeout):
        get_with_retry(_scripted(script, calls), "https://host/raw/f")
    assert len(calls) == 4


@pytest.mark.parametrize("status", [200, 400, 403, 404])
def test_retry_leaves_non_transient_statuses_alone(sleeps: list[float], status: int) -> None:
    calls: list[str] = []
    script: list[httpx.Response | Exception] = [httpx.Response(status)]
    assert get_with_retry(_scripted(script, calls), "https://host/raw/f").status_code == status
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [
        ("7", 7.0),
        ("9999", 60.0),
        ("0", 0.0),
        ("-5", 0.0),
        ("nan", 1.0),
        ("Wed, 21 Oct 2026 07:28:00 GMT", 1.0),
    ],
)
def test_retry_honors_a_capped_retry_after(
    sleeps: list[float], retry_after: str, expected: float
) -> None:
    calls: list[str] = []
    script: list[httpx.Response | Exception] = [
        httpx.Response(429, headers={"Retry-After": retry_after}),
        httpx.Response(200),
    ]
    assert get_with_retry(_scripted(script, calls), "https://host/raw/f").status_code == 200
    assert sleeps == [expected]


def test_fetch_survives_a_transient_5xx(tmp_path: Path, sleeps: list[float]) -> None:
    failed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/raw/")
        if path == "SKILL.md" and not failed:
            failed.append(path)
            return httpx.Response(501)
        return httpx.Response(200, content=CONTENT[path])

    entry = _entry()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    dest = fetch_bundle(entry, cache_dir=tmp_path, client=client)
    assert (dest / "SKILL.md").read_bytes() == CONTENT["SKILL.md"]
    assert sleeps == [1.0]


def test_persistent_5xx_is_a_fetch_error_with_no_leftovers(
    tmp_path: Path, sleeps: list[float]
) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    with pytest.raises(FetchError, match="fetch of"):
        fetch_bundle(_entry(), cache_dir=tmp_path, client=client)
    assert list((tmp_path / "bundles").iterdir()) == []
