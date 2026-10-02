# SPDX-License-Identifier: Apache-2.0
"""AGENTS.md block: a marker-delimited pointer to installed skills, idempotent, never a body."""

from __future__ import annotations

from pathlib import Path

from skillmeld.emit.agents_md import END, START_TEMPLATE, render_block, upsert_block

SKILLS = [("orchestrator", "Routes requests."), ("ifc-qto", "Quantity takeoff from IFC.")]
DIRS = [(".agents/skills", ["codex", "cursor"]), (".claude/skills", ["claude-code"])]


def test_block_text_names_skills_descriptions_and_directories() -> None:
    block = render_block("ifc-qto", SKILLS, DIRS)
    assert block.startswith(START_TEMPLATE.format(set="ifc-qto") + "\n")
    assert block.endswith(END + "\n")
    assert "## Skills composed by skillmeld (ifc-qto)" in block
    assert "`.agents/skills/` (codex, cursor), `.claude/skills/` (claude-code)" in block
    assert "- `ifc-qto`: Quantity takeoff from IFC." in block
    assert "PROVENANCE-ifc-qto.md" in block


def test_upsert_creates_then_replaces_idempotently(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    block = render_block("ifc-qto", SKILLS, DIRS)
    assert upsert_block(path, "ifc-qto", block) == "created"
    first = path.read_bytes()
    assert upsert_block(path, "ifc-qto", block) == "replaced"
    assert path.read_bytes() == first
    newer = render_block("ifc-qto", SKILLS, [(".agents/skills", ["codex"])])
    assert upsert_block(path, "ifc-qto", newer) == "replaced"
    text = path.read_text(encoding="utf-8")
    assert text.count("skillmeld:start") == 1 and "(codex)" in text and "cursor" not in text


def test_upsert_appends_without_touching_another_sets_block(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text("# Project\n\nRun the tests.\n", encoding="utf-8")
    other = render_block("review", [("review", "Reviews PRs.")], [])
    assert upsert_block(path, "review", other) == "appended"
    mine = render_block("ifc-qto", SKILLS, DIRS)
    assert upsert_block(path, "ifc-qto", mine) == "appended"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Project\n\nRun the tests.\n\n")
    assert text.index("skillmeld:start review") < text.index("skillmeld:start ifc-qto")
    assert upsert_block(path, "review", other) == "replaced"
    assert path.read_text(encoding="utf-8") == text


def test_block_contains_no_body_line() -> None:
    body_lines = ["# IFC QTO", "Take off quantities from IFC.", "- Always validate first."]
    block = render_block("ifc-qto", SKILLS, DIRS)
    assert not any(line in block for line in body_lines)


def test_block_without_directories_points_at_the_emit_output() -> None:
    assert "Skill directories: see the emit output" in render_block("x", SKILLS, [])
