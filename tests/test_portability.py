# SPDX-License-Identifier: Apache-2.0
"""Portability lint: Claude-only syntax and fields are named, never edited, and never gate."""

from __future__ import annotations

from pathlib import Path

from skillmeld.emit.portability import (
    CLAUDE_ONLY,
    DEGRADES,
    PORTABLE,
    lint_set,
    lint_skill,
    portability_section,
    portability_warnings,
)
from skillmeld.emit.provenance import build_provenance
from skillmeld.eval.evaluate import evaluate
from skillmeld.merge.dedupe import collapse
from skillmeld.merge.group import default_grouping
from skillmeld.merge.parse import parse_skill
from skillmeld.merge.partition import partition
from skillmeld.merge.prune import prune_and_close
from skillmeld.merge.synthesize import assemble
from skillmeld.models import MergeResult, SkillDoc, SkillSource, UseCaseProfile
from skillmeld.security.rules import Severity

SKILL_A = SkillDoc(
    source=SkillSource(name="retriever"),
    body="# Retriever\n\nRetrieve documents.\n\n- Always validate the query first.\n",
)
SKILL_B = SkillDoc(
    source=SkillSource(name="reviewer"),
    body="# Reviewer\n\nReview documents.\n\n- Never skip the quality check.\n",
)
PROFILE = UseCaseProfile(summary="Retrieve and review documents.", tasks=["retrieve", "review"])


def _merge() -> tuple[MergeResult, list[SkillDoc]]:
    sources = [SKILL_A, SKILL_B]
    survivors = collapse([a for s in sources for a in parse_skill(s)]).survivors
    grouping = default_grouping(survivors)
    pruned = prune_and_close(survivors, PROFILE)
    part = partition(pruned.kept, grouping.groups)
    result = assemble(part, {a.id: a for a in survivors}, kinds=grouping.kinds)
    for skill in result.skills:
        skill.doc.frontmatter["description"] = "Does the thing."
    return result, sources


def _doc(body: str, **frontmatter: object) -> SkillDoc:
    front: dict[str, object] = {"name": "ifc-qto", "description": "Quantity takeoff."}
    front.update(frontmatter)
    return SkillDoc(source=SkillSource(name="ifc-qto"), frontmatter=front, body=body)


def test_claude_variables_and_command_blocks_make_a_skill_claude_only() -> None:
    body = (
        "# Run\n\nRun `${CLAUDE_SKILL_DIR}/scripts/go.sh` first.\n\n"
        "## Status\n!`git status --short`\n"
    )
    report = lint_skill(_doc(body))
    assert report.verdict == CLAUDE_ONLY
    rules = {f.rule_id for f in report.findings}
    assert rules == {"portability:claude-body-variable", "portability:claude-command-block"}
    assert all(f.severity == Severity.LOW for f in report.findings)
    assert any(f.locus == "body:3" for f in report.findings)
    assert any(f.locus == "body:6" for f in report.findings)


def test_arguments_alone_degrades_not_claude_only() -> None:
    report = lint_skill(_doc("# Go\n\nHandle $ARGUMENTS and $ARGUMENTS[1] carefully.\n"))
    assert report.verdict == DEGRADES
    assert len(report.findings) == 2
    assert all("Kiro" in f.message for f in report.findings)


def test_positional_slots_are_ignored_inside_fenced_code() -> None:
    body = "# Go\n\n```bash\necho $1\n```\n\nThen use $2 as the target.\n"
    report = lint_skill(_doc(body))
    assert [f.locus for f in report.findings] == ["body:7"]
    assert report.verdict == DEGRADES


def test_claude_only_frontmatter_and_tool_names_degrade() -> None:
    report = lint_skill(
        _doc(
            "# Go\n\nPlain.\n",
            **{
                "allowed-tools": "Bash(git:*) Read mcp__github__list",
                "disable-model-invocation": True,
            },
        )
    )
    assert report.verdict == DEGRADES
    rules = sorted(f.rule_id for f in report.findings)
    assert rules == ["portability:claude-only-frontmatter", "portability:tool-names"]
    assert all(f.locus == "frontmatter" for f in report.findings)


def test_spec_limit_findings_are_info_and_keep_portable() -> None:
    report = lint_skill(
        _doc(
            "# Go\n\nPlain.\n",
            name="Not_Spec",
            description="d" * 1100,
            compatibility="c" * 600,
        )
    )
    assert report.verdict == PORTABLE
    rules = sorted(f.rule_id for f in report.findings)
    assert rules == [
        "portability:compatibility-over-500",
        "portability:description-over-spec",
        "portability:name-format",
    ]
    assert all(f.severity == Severity.INFO for f in report.findings)


def test_carried_support_file_is_linted_with_its_own_locus(tmp_path: Path) -> None:
    helper = tmp_path / "references" / "help.md"
    helper.parent.mkdir()
    helper.write_text("# Help\n\nSee ${CLAUDE_PROJECT_DIR}/docs.\n", encoding="utf-8")
    binary = tmp_path / "assets" / "logo.png"
    binary.parent.mkdir()
    binary.write_bytes(b"\x89PNG\x00\x00$ARGUMENTS")
    report = lint_skill(
        _doc("# Go\n\nSee references/help.md.\n"),
        support_files=[("references/help.md", helper), ("assets/logo.png", binary)],
    )
    assert [f.locus for f in report.findings] == ["references/help.md:3"]
    assert report.verdict == CLAUDE_ONLY


def test_lint_never_mutates_the_result() -> None:
    result, _ = _merge()
    result.skills[0].doc.body += "\n!`date`\n"
    before = result.model_dump()
    lint_set(result)
    assert result.model_dump() == before


def test_lint_set_orders_orchestrator_first_and_evaluate_carries_it() -> None:
    result, sources = _merge()
    reports = lint_set(result)
    assert reports[0].skill == "orchestrator"
    assert reports[0].verdict == PORTABLE
    assert len(reports) == 1 + len(result.skills)
    evaluated = evaluate(result, sources)
    assert [r.skill for r in evaluated.portability] == [r.skill for r in reports]
    # Advisory: a claude-only body changes the verdict but never enters the gate formula. (The
    # edit also breaks the byte trace, which is what fails ``passed`` here, not the lint.)
    result.skills[0].doc.body += "\n!`date`\n"
    after = evaluate(result, sources)
    assert after.portability[1].verdict == CLAUDE_ONLY
    gate = all(q.passed for q in after.quality) and not after.verifier_problems
    assert after.passed == (gate and not after.leakage)
    assert after.verifier_problems


def test_portability_section_and_warnings_are_deterministic() -> None:
    result, sources = _merge()
    result.skills[0].doc.body += "\nUse $ARGUMENTS here.\n"
    reports = lint_set(result)
    assert portability_section(reports) == portability_section(lint_set(result))
    warnings = portability_warnings(reports)
    assert len(warnings) == 1
    assert warnings[0].startswith(f"{reports[1].skill}: degrades on other agents: ")
    text = build_provenance(result, sources, generated_at="2026-09-30T00:00:00+00:00")
    assert "## Portability" not in text
    text = build_provenance(
        result,
        sources,
        generated_at="2026-09-30T00:00:00+00:00",
        portability=reports,
        sidecars=["agents/openai.yaml beside each skill"],
    )
    assert "## Portability" in text and "- **orchestrator**: portable" in text
    assert "## Sidecars" in text and "agents/openai.yaml" in text
