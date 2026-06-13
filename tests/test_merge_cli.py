# SPDX-License-Identifier: Apache-2.0
"""CLI-level merge test: two bundle dirs in, a verified MergeResult out."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from skillmeld.cli import main
from skillmeld.merge.pipeline import load_bundle
from skillmeld.models import UseCaseProfile

BUNDLE_A = """---
name: retriever
description: Retrieve documents.
---
# Retriever

Retrieve documents for a query.

- Always validate the query first.
- Use embeddings for ranking.
"""

BUNDLE_B = """---
name: reviewer
description: Review documents.
---
# Reviewer

Review retrieved documents.

- Always validate the query first.
- Flag low-quality matches.
"""


def _bundle(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.mkdir()
    (path / "SKILL.md").write_text(text, encoding="utf-8")
    return path


def test_merge_cli_emits_verified_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = _bundle(tmp_path, "retriever", BUNDLE_A)
    b = _bundle(tmp_path, "reviewer", BUNDLE_B)
    profile = tmp_path / "profile.json"
    profile.write_text(
        UseCaseProfile(
            summary="Search and review documents for a query.",
            tasks=["retrieve documents for a query", "review document quality"],
        ).model_dump_json()
    )

    code = main(["merge", "--bundles", str(a), str(b), "--profile", str(profile)])
    out: Any = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["problems"] == []
    assert out["result"]["skills"]
    # the shared "Always validate the query first." directive deduped to one survivor
    assert any("validate the query" in s["doc"]["body"] for s in out["result"]["skills"])


def test_emit_cli_refuses_a_skill_without_a_description(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = _bundle(tmp_path, "retriever", BUNDLE_A)
    b = _bundle(tmp_path, "reviewer", BUNDLE_B)
    profile = tmp_path / "profile.json"
    profile.write_text(
        UseCaseProfile(
            summary="Search and review documents for a query.",
            tasks=["retrieve documents", "review documents"],
        ).model_dump_json()
    )
    assert main(["merge", "--bundles", str(a), str(b), "--profile", str(profile)]) == 0
    merge_out = tmp_path / "merge.json"
    merge_out.write_text(capsys.readouterr().out)

    code = main(
        [
            "emit",
            "claude-code",
            "--result",
            str(merge_out),
            "--bundles",
            str(a),
            str(b),
            "--out",
            str(tmp_path / "out"),
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 1
    assert "no description" in out["error"]


def test_merge_sources_carry_catalog_license_into_the_plan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from skillmeld.models import Candidate, CatalogEntry, LicenseInfo, SkillFile, SkillSource
    from skillmeld.security.verdict import dir_bundle_hash

    a = _bundle(tmp_path, "retriever", BUNDLE_A)
    profile = tmp_path / "profile.json"
    profile.write_text(
        UseCaseProfile(
            summary="Retrieve documents.", tasks=["retrieve documents"]
        ).model_dump_json()
    )
    entry = CatalogEntry(
        id="x/skills:retriever",
        source=SkillSource(
            name="retriever",
            repo="x/skills",
            license=LicenseInfo(spdx_id="MIT", source="license-file"),
        ),
        files=[SkillFile(path="SKILL.md", sha256="0" * 64)],
        bundle_hash=dir_bundle_hash(a),
        fetch_base="https://example/x",
    )
    discover_json = tmp_path / "discover.json"
    discover_json.write_text(
        json.dumps({"candidates": [Candidate(entry=entry, score=1.0, matched=[]).model_dump()]})
    )

    code = main(
        ["merge", "--bundles", str(a), "--profile", str(profile), "--sources", str(discover_json)]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["result"]["plan"]["license_resolution"]["spdx_id"] == "MIT"


def test_merge_cli_rejects_a_bundle_without_skill_md(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    profile = tmp_path / "profile.json"
    profile.write_text(UseCaseProfile().model_dump_json())
    code = main(["merge", "--bundles", str(empty), "--profile", str(profile)])
    out = json.loads(capsys.readouterr().out)
    assert code == 1
    assert "no SKILL.md" in out["error"]


def test_load_bundle_splits_frontmatter(tmp_path: Path) -> None:
    path = _bundle(tmp_path, "retriever", BUNDLE_A)
    doc = load_bundle(path)
    assert doc.source.name == "retriever"
    assert doc.frontmatter["description"] == "Retrieve documents."
    assert doc.body.startswith("# Retriever")
