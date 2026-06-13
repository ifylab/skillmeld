# SPDX-License-Identifier: Apache-2.0
"""Build-time adapter: crawl GitHub repos for SKILL.md bundles into catalog entries.

Uses the Trees API to enumerate a repo, fetches each skill's files via raw content, computes
our own per-file and bundle hashes, and reads the repo LICENSE. Build-time only (never a
runtime dependency); the HTTP client is injectable so tests run without network. An optional
``GITHUB_TOKEN`` raises the rate limit but is not required for public repos.
"""

from __future__ import annotations

import os
from posixpath import dirname
from typing import cast

import httpx

from skillmeld.merge.pipeline import _split_frontmatter
from skillmeld.models import CatalogEntry, LicenseInfo, SkillFile, SkillSource
from skillmeld.registries.catalog import bundle_hash
from skillmeld.registries.catalog_client import sha256_hex
from skillmeld.security.license import detect_text

_API = "https://api.github.com"
_RAW = "https://raw.githubusercontent.com"
_TIMEOUT = 30.0
_LICENSE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING")


def crawl(
    repos: list[str], *, ref: str | None = None, client: httpx.Client | None = None
) -> list[CatalogEntry]:
    """Crawl ``owner/name`` repos for SKILL.md bundles. Returns catalog entries, sorted by id.

    ``ref=None`` resolves each repo's own default branch (community repos are not all on ``main``).
    Pass an explicit ref to pin every repo to the same branch or SHA.
    """
    owns_client = client is None
    http = client if client is not None else httpx.Client(timeout=_TIMEOUT, headers=_headers())
    entries: list[CatalogEntry] = []
    try:
        for repo in repos:
            entries.extend(_crawl_repo(http, repo, ref))
    finally:
        if owns_client:
            http.close()
    entries.sort(key=lambda entry: entry.id)
    return entries


def _crawl_repo(http: httpx.Client, repo: str, ref: str | None) -> list[CatalogEntry]:
    resolved = ref if ref is not None else _default_branch(http, repo)
    tree = _get_json(http, f"{_API}/repos/{repo}/git/trees/{resolved}?recursive=1")
    raw_nodes = tree.get("tree", [])
    blobs: set[str] = set()
    if isinstance(raw_nodes, list):
        for raw_node in raw_nodes:
            if not isinstance(raw_node, dict):
                continue
            node = cast("dict[str, object]", raw_node)
            path = node.get("path")
            if isinstance(path, str) and node.get("type") == "blob":
                blobs.add(path)
    license_info = _repo_license(http, repo, resolved, blobs)

    entries: list[CatalogEntry] = []
    skill_dirs = sorted({dirname(path) for path in blobs if path.rsplit("/", 1)[-1] == "SKILL.md"})
    for skill_dir in skill_dirs:
        entry = _build_entry(http, repo, resolved, skill_dir, blobs, skill_dirs, license_info)
        if entry is not None:
            entries.append(entry)
    return entries


def _owner(path: str, skill_dirs: list[str]) -> str | None:
    """The deepest skill dir that contains ``path``, so a nested skill keeps its own files."""
    best: str | None = None
    for skill_dir in skill_dirs:
        prefix = f"{skill_dir}/" if skill_dir else ""
        if path.startswith(prefix) and (best is None or len(skill_dir) > len(best)):
            best = skill_dir
    return best


def _default_branch(http: httpx.Client, repo: str) -> str:
    """Resolve a repo's default branch (``main``, ``master``, or anything else)."""
    data = _get_json(http, f"{_API}/repos/{repo}")
    branch = data.get("default_branch")
    return branch if isinstance(branch, str) and branch else "main"


def _build_entry(
    http: httpx.Client,
    repo: str,
    ref: str,
    skill_dir: str,
    blobs: set[str],
    skill_dirs: list[str],
    license_info: LicenseInfo,
) -> CatalogEntry | None:
    prefix = f"{skill_dir}/" if skill_dir else ""
    member_paths = sorted(
        p for p in blobs if p.startswith(prefix) and _owner(p, skill_dirs) == skill_dir
    )
    fetch_base = f"{_RAW}/{repo}/{ref}/{skill_dir}".rstrip("/")

    files: list[SkillFile] = []
    skill_md_text = ""
    for path in member_paths:
        rel = path[len(prefix) :]
        content = _get_bytes(http, f"{fetch_base}/{rel}")
        if content is None:
            return None
        files.append(SkillFile(path=rel, sha256=sha256_hex(content)))
        if rel == "SKILL.md":
            skill_md_text = content.decode("utf-8", errors="replace")
    if not skill_md_text:
        return None

    frontmatter, _ = _split_frontmatter(skill_md_text)
    name = frontmatter.get("name") or (skill_dir.rsplit("/", 1)[-1] or repo.split("/")[-1])
    return CatalogEntry(
        id=f"{repo}:{skill_dir}" if skill_dir else repo,
        source=SkillSource(
            name=name, repo=repo, url=f"https://github.com/{repo}", license=license_info
        ),
        description=frontmatter.get("description", ""),
        tags=_split_list(frontmatter.get("tags", "")),
        languages=_split_list(frontmatter.get("languages", "")),
        files=files,
        fetch_base=fetch_base,
        bundle_hash=bundle_hash(files),
    )


def _repo_license(http: httpx.Client, repo: str, ref: str, blobs: set[str]) -> LicenseInfo:
    for name in _LICENSE_NAMES:
        if name in blobs:
            content = _get_bytes(http, f"{_RAW}/{repo}/{ref}/{name}")
            if content is not None:
                spdx = detect_text(content.decode("utf-8", errors="replace"))
                return LicenseInfo(spdx_id=spdx, source="license-file")
    return LicenseInfo()


def _headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "skillmeld-crawl"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_json(http: httpx.Client, url: str) -> dict[str, object]:
    response = http.get(url)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {}


def _get_bytes(http: httpx.Client, url: str) -> bytes | None:
    response = http.get(url)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


def _split_list(value: str) -> list[str]:
    return [part.strip() for part in value.strip("[]").split(",") if part.strip()]
