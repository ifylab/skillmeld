# SPDX-License-Identifier: Apache-2.0
"""Awesome-list scout tests: link extraction, exclusions, star ranking, and the CLI surface."""

from __future__ import annotations

import json
from typing import cast

import httpx
import pytest

from skillmeld.cli import main
from skillmeld.registries import awesome
from skillmeld.registries.awesome import AwesomeScoutError, scout

README = """# Awesome Agent Skills
- [pdf](https://github.com/anthropics/skills/tree/main/skills/pdf) - already in the catalog
- [weekly review](https://github.com/langfuse/langfuse) - big
- [retro](https://github.com/TraderAlice/OpenAlice.git) - git suffix
- [dup](https://github.com/langfuse/langfuse/blob/main/README.md)
- [topic](https://github.com/topics/agent-skills)
- [gone](https://github.com/nobody/vanished)
- [self](https://github.com/VoltAgent/awesome-agent-skills)
"""
STARS = {"langfuse/langfuse": 33170, "TraderAlice/OpenAlice": 6528}


def _transport(seen: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(200, text=README)
        assert request.url.host == "api.github.com"
        slug = request.url.path.removeprefix("/repos/")
        if slug not in STARS:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(
            200, json={"stargazers_count": STARS[slug], "description": f"{slug} does things"}
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_scout_ranks_by_stars_and_skips_known_and_non_repo_links() -> None:
    seen: list[str] = []
    report = scout(client=_transport(seen))
    repos = cast(list[dict[str, object]], report["repos"])
    assert [r["repo"] for r in repos] == ["langfuse/langfuse", "TraderAlice/OpenAlice"]
    assert repos[0]["description"] == "langfuse/langfuse does things"
    assert report["listed"] == 3
    assert report["looked_up"] == 3
    assert "hand" in str(report["note"])
    assert sum(1 for url in seen if "api.github.com" in url) == 3


def test_scout_lookups_cap_bounds_api_calls() -> None:
    seen: list[str] = []
    report = scout(client=_transport(seen), lookups=1)
    assert report["looked_up"] == 1
    assert sum(1 for url in seen if "api.github.com" in url) == 1


def test_scout_unreachable_list_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(AwesomeScoutError):
        scout(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_cli_awesome_scout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(awesome, "_client", lambda: _transport([]))
    assert main(["awesome-scout", "--limit", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [r["repo"] for r in out["repos"]] == ["langfuse/langfuse"]
    assert out["listed"] == 3
