# SPDX-License-Identifier: Apache-2.0
"""SkillsMP adapter tests. The fixture pins the undocumented response schema as observed
live on 2026-08-19; if SkillsMP changes shape, these fail before the scout misreads it."""

from __future__ import annotations

import json
from typing import cast

import httpx
import pytest

from skillmeld.cli import main
from skillmeld.registries.skillsmp import (
    SkillsMPError,
    _client,
    discover_repos,
    search,
)


def _skill(name: str, repo_path: str, stars: int, language: str = "en") -> dict[str, object]:
    owner_repo = repo_path.split("/tree/")[0]
    return {
        "id": f"{name}-skill-md",
        "name": name,
        "author": owner_repo.split("/")[0],
        "description": f"{name} does things.",
        "contentLanguage": language,
        "githubUrl": f"https://github.com/{repo_path}",
        "skillUrl": f"https://skillsmp.com/creators/{repo_path}",
        "stars": stars,
        "updatedAt": 1779012244,
    }


def _page(skills: list[dict[str, object]], page: int, has_next: bool) -> dict[str, object]:
    # Envelope pinned from the live response: success/data/meta, data.skills + data.pagination.
    return {
        "success": True,
        "data": {
            "skills": skills,
            "pagination": {
                "page": page,
                "limit": len(skills),
                "total": 3,
                "totalPages": 2,
                "hasNext": has_next,
                "hasPrev": page > 1,
                "totalIsExact": False,
            },
            "filters": {"search": "excel", "sortBy": "stars"},
        },
        "meta": {"requestId": "fixture", "responseTimeMs": 1},
    }


def _transport(pages: dict[int, dict[str, object]], seen: list[dict[str, str]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/skills/search"
        params = dict(request.url.params)
        seen.append(params)
        page = int(params.get("page", "1"))
        body = pages.get(page)
        assert body is not None, f"unexpected page {page}"
        return httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_search_parses_the_pinned_schema() -> None:
    seen: list[dict[str, str]] = []
    pages = {
        1: _page(
            [_skill("diagram-maker", "openclaw/openclaw/tree/main/skills/diagram-maker", 386424)],
            1,
            has_next=False,
        )
    }
    hits = search("excel", limit=10, client=_transport(pages, seen))
    assert len(hits) == 1
    hit = hits[0]
    assert hit.name == "diagram-maker"
    assert hit.author == "openclaw"
    assert hit.stars == 386424
    assert hit.repo == "openclaw/openclaw"  # slug extracted from the tree URL
    assert seen[0]["q"] == "excel"
    assert seen[0]["page"] == "1"


def test_search_pages_until_has_next_is_false() -> None:
    seen: list[dict[str, str]] = []
    pages = {
        1: _page([_skill("a", "o/r1/tree/main/a", 10)], 1, has_next=True),
        2: _page([_skill("b", "o/r2/tree/main/b", 20)], 2, has_next=False),
    }
    hits = search("x", limit=10, client=_transport(pages, seen))
    assert [hit.name for hit in hits] == ["a", "b"]
    assert [params["page"] for params in seen] == ["1", "2"]


def test_search_respects_the_request_budget() -> None:
    seen: list[dict[str, str]] = []
    pages = {
        page: _page([_skill(f"s{page}", f"o/r{page}/tree/main/s", page)], page, has_next=True)
        for page in range(1, 10)
    }
    search("x", limit=100, client=_transport(pages, seen), budget=2)
    assert len(seen) == 2  # the 500/day quota is real; a call never runs away


def test_search_rejects_schema_drift() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": {"unexpected": []}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SkillsMPError, match="schema drift"):
        search("x", client=client)


def test_search_surfaces_http_errors() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SkillsMPError, match="429"):
        search("x", client=client)


def test_discover_repos_aggregates_and_ranks_by_stars() -> None:
    seen: list[dict[str, str]] = []
    pages = {
        1: _page(
            [
                _skill("low", "small/repo/tree/main/low", 5),
                _skill("high", "big/repo/tree/main/high", 500),
                _skill("second", "big/repo/tree/main/second", 400),
                {"id": "no-url", "name": "orphan", "stars": 9},
            ],
            1,
            has_next=False,
        )
    }
    report = discover_repos(["x"], client=_transport(pages, seen))
    repos = cast(list[dict[str, object]], report["repos"])
    assert [row["repo"] for row in repos] == ["big/repo", "small/repo"]  # orphan dropped
    assert repos[0]["skills"] == 2
    assert repos[0]["stars"] == 500


def test_client_requires_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SKILLSMP_API_KEY", raising=False)
    with pytest.raises(SkillsMPError, match="SKILLSMP_API_KEY"):
        _client()
    monkeypatch.setenv("SKILLSMP_API_KEY", "sekret")
    client = _client()
    try:
        assert client.headers["Authorization"] == "Bearer sekret"
    finally:
        client.close()


def test_cli_scout_emits_the_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from skillmeld.registries import skillsmp

    def _fake(queries: list[str], *, per_query: int = 100) -> dict[str, object]:
        return {"queries": queries, "repos": [], "note": f"limit {per_query}"}

    monkeypatch.setattr(skillsmp, "discover_repos", _fake)
    code = main(["skillsmp-scout", "--queries", "excel", "pdf", "--limit", "5"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["queries"] == ["excel", "pdf"]
    assert "limit 5" in out["note"]
