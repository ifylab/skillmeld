# SPDX-License-Identifier: Apache-2.0
"""The driver skill is spec-clean and its wrapper finds the engine from any agent."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from skillmeld.emit.portability import PORTABLE, lint_skill
from skillmeld.merge.pipeline import _split_frontmatter
from skillmeld.models import API_DESCRIPTION_LIMIT, SkillDoc, SkillSource

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "skillmeld"
RUN_SH = SKILL_DIR / "scripts" / "run.sh"


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(RUN_SH), *args], capture_output=True, text=True, env=env, check=False
    )


def _fake_engine(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "skillmeld"
    fake.write_text('#!/usr/bin/env bash\necho "fake-engine $*"\n', encoding="utf-8")
    fake.chmod(0o755)
    return bin_dir


def test_driver_skill_md_is_spec_clean_and_portable() -> None:
    frontmatter, body = _split_frontmatter((SKILL_DIR / "SKILL.md").read_text(encoding="utf-8"))
    assert set(frontmatter) <= {"name", "description", "license", "compatibility"}
    assert frontmatter["name"] == "skillmeld" and frontmatter["license"] == "Apache-2.0"
    assert "${CLAUDE_" not in body and "CLAUDE_SKILL_DIR" not in body
    doc = SkillDoc(source=SkillSource(name="skillmeld"), frontmatter=dict(frontmatter), body=body)
    report = lint_skill(doc)
    assert report.verdict == PORTABLE, [f.message for f in report.findings]
    assert report.findings == []


def test_driver_description_within_spec_cap() -> None:
    frontmatter, _ = _split_frontmatter((SKILL_DIR / "SKILL.md").read_text(encoding="utf-8"))
    assert 0 < len(str(frontmatter["description"])) <= API_DESCRIPTION_LIMIT
    assert len(str(frontmatter.get("compatibility", ""))) <= 500


def test_run_sh_prefers_skillmeld_on_path(tmp_path: Path) -> None:
    env = {"PATH": f"{_fake_engine(tmp_path)}:/usr/bin:/bin", "HOME": str(tmp_path)}
    proc = _run(env, "--version")
    assert proc.returncode == 0 and proc.stdout.strip() == "fake-engine --version"


def test_run_sh_honours_engine_override(tmp_path: Path) -> None:
    env = {
        "PATH": f"{_fake_engine(tmp_path)}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "SKILLMELD_ENGINE": "path",
    }
    assert _run(env, "intake", "x").stdout.strip() == "fake-engine intake x"
    env["SKILLMELD_ENGINE"] = "nonsense"
    proc = _run(env, "intake", "x")
    assert proc.returncode == 2 and "SKILLMELD_ENGINE" in proc.stderr


def test_run_sh_fails_plainly_with_no_engine(tmp_path: Path) -> None:
    # /usr/bin and /bin hold neither skillmeld nor uv, so every route is closed.
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    proc = _run(env, "--version")
    assert proc.returncode == 127
    assert "uv tool install skillmeld" in proc.stderr


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_run_sh_uses_the_checkout_when_root_is_skillmeld(tmp_path: Path) -> None:
    uv_dir = str(Path(shutil.which("uv") or "uv").parent)
    env = {
        "PATH": f"{uv_dir}:/usr/bin:/bin",
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(tmp_path / "uv-cache")),
    }
    proc = _run(env, "--version")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("skillmeld ")
