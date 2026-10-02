# SPDX-License-Identifier: Apache-2.0
"""Tests for the grounding scan and profile derivation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skillmeld.cli import main
from skillmeld.grounding import ground, profile_from, scan

PYPROJECT = """\
[project]
name = "sample-app"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["flask>=3", "pydantic>=2"]

[dependency-groups]
dev = ["pytest>=8"]
"""

APP_PY = "VALUE = 1\n"
TEST_PY = "def test_ok():\n    assert True\n"
README = "# Sample\n\nA tiny app.\n"


def _make_sample(root: Path) -> None:
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "node_modules" / "dep").mkdir(parents=True)
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (root / "ruff.toml").write_text("line-length = 100\n", encoding="utf-8")
    (root / "README.md").write_text(README, encoding="utf-8")
    (root / "src" / "app.py").write_text(APP_PY, encoding="utf-8")
    (root / "tests" / "test_app.py").write_text(TEST_PY, encoding="utf-8")
    (root / "node_modules" / "dep" / "junk.js").write_text("// x\n", encoding="utf-8")


def test_scan_collects_evidence(tmp_path: Path) -> None:
    _make_sample(tmp_path)
    evidence = scan(tmp_path)
    assert evidence.file_counts.get(".py", 0) >= 2
    assert ".js" not in evidence.file_counts
    assert "flask" in evidence.dependencies
    assert "pydantic" in evidence.dependencies
    assert "pytest" in evidence.dependencies
    assert "ruff" in evidence.config_files
    assert evidence.has_tests
    assert evidence.readme_excerpt.startswith("# Sample")
    assert "src" in evidence.top_dirs
    assert "node_modules" not in evidence.top_dirs


def test_profile_derivation(tmp_path: Path) -> None:
    _make_sample(tmp_path)
    profile = profile_from(scan(tmp_path))
    assert "Python" in profile.languages
    assert "Flask" in profile.frameworks
    assert "Pydantic" in profile.frameworks
    assert "tests" in profile.conventions
    assert profile.summary == ""
    assert profile.tasks == []
    assert ground(tmp_path) == profile


def test_cli_ground_emits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _make_sample(tmp_path)
    code = main(["ground", str(tmp_path)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert "Python" in out["profile"]["languages"]
    assert "flask" in out["evidence"]["dependencies"]


def test_cli_ground_missing(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["ground", "/no/such/path/zzz"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1
    assert "error" in out


def test_frameworks_match_exact_package_names_only() -> None:
    from skillmeld.grounding import _frameworks

    assert _frameworks(["license-expression", "nextcloud-client", "preact-compat"]) == []
    assert _frameworks(["express", "pydantic", "scikit_learn"]) == ["Express", "Pydantic"]


# --- 0.5.0: agent instructions and markers ---------------------------------------------


def test_scan_reads_agents_md_excerpt_first(tmp_path: Path) -> None:
    _make_sample(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# Agents\n\nUse uv.\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Claude\n\nOther.\n", encoding="utf-8")
    evidence = scan(tmp_path)
    assert evidence.instructions_file == "AGENTS.md"
    assert evidence.instructions_excerpt == "# Agents\n\nUse uv."


def test_scan_falls_back_to_claude_md_then_copilot_instructions(tmp_path: Path) -> None:
    _make_sample(tmp_path)
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "copilot-instructions.md").write_text("# Copilot\n", encoding="utf-8")
    assert scan(tmp_path).instructions_file == ".github/copilot-instructions.md"
    (tmp_path / "CLAUDE.md").write_text("# Claude\n", encoding="utf-8")
    assert scan(tmp_path).instructions_file == "CLAUDE.md"


def test_scan_excerpt_is_capped_at_40_lines(tmp_path: Path) -> None:
    _make_sample(tmp_path)
    (tmp_path / "AGENTS.md").write_text("\n".join(f"line {n}" for n in range(100)), "utf-8")
    excerpt = scan(tmp_path).instructions_excerpt
    assert excerpt.splitlines()[-1] == "line 39" and len(excerpt.splitlines()) == 40


def test_scan_detects_agent_dirs_and_maps_to_targets(tmp_path: Path) -> None:
    _make_sample(tmp_path)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".cursor").mkdir()
    (tmp_path / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    evidence = scan(tmp_path)
    assert evidence.agent_dirs == [".claude", ".cursor", "AGENTS.md"]
    assert evidence.agents == ["claude-code", "cursor", "agents"]
    bare = scan(tmp_path / "src")
    assert bare.agent_dirs == [] and bare.agents == [] and bare.instructions_file == ""


def test_cli_ground_emits_agents(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _make_sample(tmp_path)
    (tmp_path / ".gemini").mkdir()
    code = main(["ground", str(tmp_path)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["evidence"]["agents"] == ["gemini-cli"]
    assert out["evidence"]["agent_dirs"] == [".gemini"]
