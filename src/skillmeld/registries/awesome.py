# SPDX-License-Identifier: Apache-2.0
"""Build-time scout over a curated awesome-list: candidate repos for the hosted catalog.

A maintained list such as VoltAgent/awesome-agent-skills is a breadth feed with no API key and
no daily quota. ``scout`` pulls every ``github.com/owner/name`` link out of the list's README,
drops repos already in ``hosted/sources.py`` and the list itself, looks up stars for the first
``lookups`` slugs through the GitHub API, and ranks the rest. It prints candidates for a human
to curate — catalog membership stays a deliberate decision.
"""

from __future__ import annotations

import re
from typing import cast

import httpx

from skillmeld.hosted.sources import PRODUCTION_REPOS
from skillmeld.registries.github_crawl import _API, _headers

DEFAULT_LIST = "https://raw.githubusercontent.com/VoltAgent/awesome-agent-skills/main/README.md"
_TIMEOUT = 30.0
_REPO_LINK = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
_RAW_LIST = re.compile(r"raw\.githubusercontent\.com/([^/]+)/([^/]+)/")
# First path segments on github.com that are site sections, not repository owners.
_NOT_OWNERS = frozenset(
    {
        "about",
        "apps",
        "blog",
        "codespaces",
        "collections",
        "contact",
        "copilot",
        "enterprise",
        "events",
        "explore",
        "features",
        "issues",
        "join",
        "login",
        "marketplace",
        "new",
        "notifications",
        "orgs",
        "organizations",
        "pricing",
        "pulls",
        "readme",
        "search",
        "security",
        "settings",
        "site",
        "sponsors",
        "team",
        "topics",
        "trending",
    }
)


class AwesomeScoutError(RuntimeError):
    """The list could not be fetched."""


def scout(
    url: str = DEFAULT_LIST,
    *,
    limit: int = 50,
    lookups: int = 60,
    client: httpx.Client | None = None,
) -> dict[str, object]:
    """Scout a list: unique repo slugs, ranked by stars, minus what the catalog already has."""
    own = client is None
    http = client or _client()
    try:
        slugs = _slugs(_fetch_text(http, url), url)
        ranked: list[dict[str, object]] = []
        looked_up = 0
        for slug in slugs[: max(lookups, 0)]:
            looked_up += 1
            meta = _repo_meta(http, slug)
            if meta is not None:
                ranked.append(meta)
    finally:
        if own:
            http.close()
    ranked.sort(key=lambda item: (-cast(int, item["stars"]), cast(str, item["repo"]).lower()))
    return {
        "source": url,
        "listed": len(slugs),
        "looked_up": looked_up,
        "repos": ranked[: max(limit, 0)],
        "note": "candidates only — curate into hosted/sources.py by hand",
    }


def _slugs(text: str, url: str) -> list[str]:
    excluded = {repo.lower() for repo in PRODUCTION_REPOS}
    own_list = _RAW_LIST.search(url)
    if own_list:
        excluded.add(f"{own_list.group(1)}/{own_list.group(2)}".lower())
    ordered: list[str] = []
    seen: set[str] = set()
    for owner, name in _REPO_LINK.findall(text):
        slug = f"{owner}/{name.removesuffix('.git')}"
        key = slug.lower()
        if owner.lower() in _NOT_OWNERS or key in excluded or key in seen:
            continue
        seen.add(key)
        ordered.append(slug)
    return ordered


def _repo_meta(http: httpx.Client, slug: str) -> dict[str, object] | None:
    try:
        response = http.get(f"{_API}/repos/{slug}")
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    payload = cast(dict[str, object], data)
    stars = payload.get("stargazers_count")
    return {
        "repo": slug,
        "stars": stars if isinstance(stars, int) else 0,
        "description": str(payload.get("description") or ""),
    }


def _fetch_text(http: httpx.Client, url: str) -> str:
    try:
        response = http.get(url)
    except httpx.HTTPError as exc:
        raise AwesomeScoutError(f"list unreachable: {exc}") from exc
    if response.status_code != 200:
        raise AwesomeScoutError(f"list returned HTTP {response.status_code}")
    return response.text


def _client() -> httpx.Client:
    return httpx.Client(timeout=_TIMEOUT, headers=_headers())
