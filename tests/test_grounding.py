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
