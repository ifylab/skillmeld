# SPDX-License-Identifier: Apache-2.0
"""Build-time adapter for SkillsMP search. Used by the hosted build pipeline, not at runtime.

Authed breadth feed over the SkillsMP registry: ``search`` pages ``GET /api/v1/skills/search``
and ``discover_repos`` folds the hits into ``owner/name`` GitHub slugs for a human to curate
into ``hosted/sources.py`` — the scout surfaces candidates; catalog membership stays a
deliberate decision. The response schema is undocumented upstream, so the shape observed live
(2026-08-19) is pinned in ``tests/test_skillsmp.py``; the daily quota is 500 requests and
every call runs under an explicit request budget.
"""

from __future__ import annotations

import os
import re
from typing import cast

import httpx
from pydantic import BaseModel

BASE_URL = "https://skillsmp.com/api/v1"
DAILY_QUOTA = 500
_TIMEOUT = 30.0
_PAGE_LIMIT = 100  # the API's per-page maximum

_GITHUB_REPO = re.compile(r"^https?://github\.com/([\w.-]+)/([\w.-]+)")


class SkillsMPError(RuntimeError):
    """The API refused, or the response did not match the pinned schema."""


class SkillsMPHit(BaseModel):
    name: str
    author: str = ""
    description: str = ""
    github_url: str = ""
    stars: int = 0
    language: str = ""

    @property
    def repo(self) -> str | None:
        """The ``owner/name`` GitHub slug behind the hit, when the URL carries one."""
        match = _GITHUB_REPO.match(self.github_url)
        return f"{match.group(1)}/{match.group(2)}" if match else None


def search(
    query: str,
    limit: int = 100,
    *,
    client: httpx.Client | None = None,
    budget: int = 10,
) -> list[SkillsMPHit]:
    """Page SkillsMP search results. Build-time only; ``budget`` caps requests for this call."""
    own = client is None
    http = client or _client()
    try:
        hits: list[SkillsMPHit] = []
        page = 1
        for _ in range(max(budget, 1)):
            page_size = min(_PAGE_LIMIT, max(limit - len(hits), 1))
            data = _get(http, {"q": query, "limit": page_size, "page": page})
            skills = data.get("skills")
            if not isinstance(skills, list):
                raise SkillsMPError("schema drift: data.skills is not a list")
            for item in skills:
                if isinstance(item, dict):
                    hits.append(_hit(cast(dict[str, object], item)))
            pagination = data.get("pagination")
            has_next = isinstance(pagination, dict) and bool(
                cast(dict[str, object], pagination).get("hasNext")
            )
            if len(hits) >= limit or not has_next:
                break
            page += 1
        return hits[:limit]
    finally:
        if own:
            http.close()


def discover_repos(
    queries: list[str],
    *,
    per_query: int = 100,
    client: httpx.Client | None = None,
    budget: int = 20,
) -> dict[str, object]:
    """Scout candidate repos across queries: unique slugs ranked by their strongest star signal.

    Returns a JSON-ready report; nothing here mutates the curated source list.
    """
    own = client is None
    http = client or _client()
    per_budget = max(budget // max(len(queries), 1), 1)
    try:
        by_repo: dict[str, list[SkillsMPHit]] = {}
        for query in queries:
            for hit in search(query, per_query, client=http, budget=per_budget):
                slug = hit.repo
                if slug is not None:
                    by_repo.setdefault(slug, []).append(hit)
    finally:
        if own:
            http.close()
    ordered = sorted(by_repo.items(), key=lambda pair: (-max(h.stars for h in pair[1]), pair[0]))
    return {
        "queries": queries,
        "repos": [
            {
                "repo": slug,
                "skills": len(repo_hits),
                "stars": max(hit.stars for hit in repo_hits),
                "sample": repo_hits[0].name,
            }
            for slug, repo_hits in ordered
        ],
        "note": (
            f"candidates only — curate into hosted/sources.py by hand; daily quota {DAILY_QUOTA}"
        ),
    }


def _client() -> httpx.Client:
    key = os.environ.get("SKILLSMP_API_KEY", "").strip()
    if not key:
        raise SkillsMPError("SKILLSMP_API_KEY is not set")
    return httpx.Client(
        timeout=_TIMEOUT,
        headers={"Authorization": f"Bearer {key}", "User-Agent": "skillmeld-scout"},
    )


def _get(http: httpx.Client, params: dict[str, str | int]) -> dict[str, object]:
    try:
        response = http.get(f"{BASE_URL}/skills/search", params=params)
    except httpx.HTTPError as exc:
        raise SkillsMPError(f"SkillsMP unreachable: {exc}") from exc
    if response.status_code != 200:
        raise SkillsMPError(f"SkillsMP returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise SkillsMPError("SkillsMP returned non-JSON") from exc
    if not isinstance(payload, dict) or not payload.get("success"):
        raise SkillsMPError("schema drift: no success envelope")
    data = cast(dict[str, object], payload).get("data")
    if not isinstance(data, dict):
        raise SkillsMPError("schema drift: no data object")
    return cast(dict[str, object], data)


def _hit(item: dict[str, object]) -> SkillsMPHit:
    stars = item.get("stars")
    return SkillsMPHit(
        name=str(item.get("name") or item.get("id") or "unnamed"),
        author=str(item.get("author") or ""),
        description=str(item.get("description") or ""),
        github_url=str(item.get("githubUrl") or ""),
        stars=stars if isinstance(stars, int) else 0,
        language=str(item.get("contentLanguage") or ""),
    )
