# SPDX-License-Identifier: Apache-2.0
"""The install path table behind ``emit --install-for``: every agent maps, readers share."""

from __future__ import annotations

import pytest

from skillmeld.emit.targets import (
    AGENT_NAMES,
    DETECTION_MARKERS,
    INSTALL_TARGETS,
    UNIVERSAL,
    UNIVERSAL_PROJECT,
    UNIVERSAL_USER,
    agents_for_markers,
    parse_agents,
    resolve_targets,
)


def test_every_agent_maps_to_project_and_user_dirs() -> None:
    for name in AGENT_NAMES:
        target = INSTALL_TARGETS[name]
        assert target.project_dir and not target.project_dir.startswith("~")
        assert target.user_dir.startswith("~/")
        assert target.doc_url.startswith("https://")
    assert UNIVERSAL not in AGENT_NAMES


def test_universal_readers_share_one_agents_dir() -> None:
    resolved = resolve_targets(["codex", "cursor", "gemini-cli", "github-copilot"], scope="project")
    assert len(resolved) == 1
    only = resolved[0]
    assert only.path_rel == UNIVERSAL_PROJECT
    assert only.agents == ("codex", "cursor", "gemini-cli", "github-copilot")
    assert only.spec_only is True
    assert only.scope == "project"


def test_claude_factory_kiro_resolve_to_native_dirs() -> None:
    agents = ["claude-code", "factory", "kiro"]
    project = {t.path_rel: t for t in resolve_targets(agents, scope="project")}
    assert set(project) == {".claude/skills", ".factory/skills", ".kiro/skills"}
    user = {t.path_rel: t for t in resolve_targets(agents, scope="user")}
    # Factory reads the convention at user scope only; Kiro's docs name no convention path.
    assert set(user) == {"~/.claude/skills", UNIVERSAL_USER, "~/.kiro/skills"}
    assert user[UNIVERSAL_USER].agents == ("factory",)


def test_native_flag_writes_each_agents_own_dir() -> None:
    resolved = resolve_targets(["cursor", "gemini-cli", "codex"], scope="project", native=True)
    assert [t.path_rel for t in resolved] == [".cursor/skills", ".gemini/skills", UNIVERSAL_PROJECT]


def test_parse_agents_all_and_unknown_with_suggestion() -> None:
    assert parse_agents("all") == list(AGENT_NAMES)
    assert parse_agents("codex, Codex ,claude-code") == ["codex", "claude-code"]
    assert parse_agents("agents") == [UNIVERSAL]
    with pytest.raises(ValueError, match="did you mean cursor"):
        parse_agents("cursur")
    with pytest.raises(ValueError, match="names no agent"):
        parse_agents(" , ")


def test_claude_code_is_the_only_non_spec_only_target() -> None:
    assert [name for name, t in INSTALL_TARGETS.items() if not t.spec_only] == ["claude-code"]
    mixed = resolve_targets(["claude-code", "codex"], scope="project")
    by_path = {t.path_rel: t.spec_only for t in mixed}
    assert by_path == {".claude/skills": False, UNIVERSAL_PROJECT: True}


def test_resolve_rejects_an_unknown_scope() -> None:
    with pytest.raises(ValueError, match="scope must be"):
        resolve_targets(["codex"], scope="global")


def test_agents_for_markers_follow_table_order_with_universal_last() -> None:
    found = ["AGENTS.md", ".cursor", ".claude", ".github/skills"]
    assert agents_for_markers(found) == ["claude-code", "cursor", "github-copilot", UNIVERSAL]
    assert agents_for_markers([]) == []
    assert {agent for _, agent in DETECTION_MARKERS} <= set(INSTALL_TARGETS)
