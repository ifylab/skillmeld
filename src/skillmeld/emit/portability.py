# SPDX-License-Identifier: Apache-2.0
"""Portability lint: what in a composed set only Claude Code understands, and what strains the spec.

Read-only and advisory. Findings use the scanner's ``ScanFinding`` shape under the ``portability``
category at INFO or LOW severity, so they never move a security verdict; the per-skill verdict is
its own three-word vocabulary. Nothing here edits a body: a body that leans on Claude Code
substitution is left exactly as the sources wrote it, and the lint says so, because rewriting it
would break the byte trace. Facts behind each rule (agent docs read 2026-09-30): no agent other
than Claude Code interprets ``!`cmd``` blocks or ``${CLAUDE_*}``; only Kiro's CLI also reads
``$ARGUMENTS``; every other loader ignores Claude-only frontmatter and most validators reject it.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from skillmeld.merge.frontmatter import tool_tokens
from skillmeld.models import (
    API_DESCRIPTION_LIMIT,
    COMPATIBILITY_LIMIT,
    NAME_LIMIT,
    SPEC_NAME_RE,
    MergeResult,
    ScanFinding,
    SkillDoc,
)
from skillmeld.security.rules import PORTABILITY, Severity

PORTABLE = "portable"
DEGRADES = "degrades"
CLAUDE_ONLY = "claude-only"

# Claude Code's own frontmatter fields that the Agent Skills spec does not define.
CLAUDE_ONLY_FIELDS = ("disallowed-tools", "disable-model-invocation")

# Tool names Claude Code pre-approves through ``allowed-tools``; other agents do not enforce the
# field, so a pre-approval naming these buys nothing there.
_CLAUDE_TOOLS = frozenset(
    {
        "Agent",
        "Bash",
        "Edit",
        "Glob",
        "Grep",
        "MultiEdit",
        "NotebookEdit",
        "Read",
        "Skill",
        "Task",
        "TodoWrite",
        "WebFetch",
        "WebSearch",
        "Write",
    }
)

# ``$ARGUMENTS`` (with an optional index) and ``${CLAUDE_*}`` are flagged anywhere; a bare ``$1``
# is flagged only outside fenced code, where it is far more likely plain shell than a Claude slot.
_CLAUDE_ARGUMENTS = re.compile(r"\$ARGUMENTS(?:\[\d+\])?")
_CLAUDE_ENV = re.compile(r"\$\{CLAUDE_[A-Z_]+\}|\$CLAUDE_[A-Z_]+\b")
_POSITIONAL = re.compile(r"(?<![\w$])\$\d+\b")
_COMMAND_INLINE = re.compile(r"!`[^`\n]+`")
_FENCE = re.compile(r"^\s*```")
_COMMAND_FENCE = re.compile(r"^\s*```!")
_MAX_FILE_BYTES = 2_000_000
_MAX_PER_RULE = 5


class PortabilityReport(BaseModel):
    skill: str
    verdict: str = PORTABLE
    findings: list[ScanFinding] = Field(default_factory=list)


def lint_skill(
    doc: SkillDoc, *, support_files: list[tuple[str, Path]] | None = None
) -> PortabilityReport:
    """Lint one emitted skill: its frontmatter, its body, and the support files it carries."""
    name = str(doc.frontmatter.get("name", doc.source.name))
    findings: list[ScanFinding] = []
    findings.extend(_lint_frontmatter(doc, name))
    findings.extend(_lint_text("body", doc.body))
    for rel, path in support_files or []:
        text = _read_text(path)
        if text is not None:
            findings.extend(_lint_text(rel, text))
    return PortabilityReport(skill=name, verdict=_verdict(findings), findings=findings)


def lint_set(
    result: MergeResult, carry: dict[str, list[tuple[str, Path]]] | None = None
) -> list[PortabilityReport]:
    """Lint every emitted skill, the orchestrator first, in the order emit writes them."""
    carry = carry or {}
    skills = list(result.skills)
    if result.orchestrator is not None:
        skills = [result.orchestrator, *skills]
    reports: list[PortabilityReport] = []
    for skill in skills:
        name = str(skill.doc.frontmatter.get("name", skill.doc.source.name))
        reports.append(lint_skill(skill.doc, support_files=carry.get(name)))
    return reports


def portability_warnings(reports: list[PortabilityReport]) -> list[str]:
    """One line per skill that is not fully portable, for an emit's ``warnings``."""
    lines: list[str] = []
    for report in reports:
        if report.verdict == PORTABLE:
            continue
        reasons = "; ".join(_short(finding) for finding in report.findings)
        lines.append(f"{report.skill}: {report.verdict} on other agents: {reasons}")
    return lines


def portability_section(reports: list[PortabilityReport]) -> list[str]:
    """The ``## Portability`` lines for PROVENANCE.md. Deterministic given the reports."""
    lines: list[str] = []
    for report in reports:
        lines.append(f"- **{report.skill}**: {report.verdict}")
        lines.extend(
            f"  - {finding.rule_id} ({finding.locus}): {finding.message}"
            for finding in report.findings
        )
    return lines


def _lint_frontmatter(doc: SkillDoc, name: str) -> list[ScanFinding]:
    findings: list[ScanFinding] = []
    present = [field for field in CLAUDE_ONLY_FIELDS if _present(doc.frontmatter.get(field))]
    if present:
        findings.append(
            _finding(
                "claude-only-frontmatter",
                Severity.LOW,
                "frontmatter",
                f"{', '.join(present)} sit outside the Agent Skills spec; spec-only surfaces leave "
                "them out (Claude Code and the marketplace keep them) and other agents ignore them",
            )
        )
    tools = [
        token
        for token in tool_tokens(doc.frontmatter.get("allowed-tools", ""))
        if token.split("(", 1)[0] in _CLAUDE_TOOLS or token.startswith("mcp__")
    ]
    if tools:
        findings.append(
            _finding(
                "tool-names",
                Severity.LOW,
                "frontmatter",
                f"allowed-tools pre-approves Claude Code tools ({', '.join(tools[:5])}); other "
                "agents do not enforce the field, so the skill runs there without pre-approval",
            )
        )
    description = str(doc.frontmatter.get("description", ""))
    if len(description) > API_DESCRIPTION_LIMIT:
        findings.append(
            _finding(
                "description-over-spec",
                Severity.INFO,
                "frontmatter",
                f"description is {len(description)} chars; the Agent Skills spec caps it at "
                f"{API_DESCRIPTION_LIMIT}, and agents budget their skill listing on it",
            )
        )
    if not SPEC_NAME_RE.match(name) or len(name) > NAME_LIMIT:
        findings.append(
            _finding(
                "name-format",
                Severity.INFO,
                "frontmatter",
                f"name {name!r} is not 1-{NAME_LIMIT} lowercase alphanumerics joined by single "
                "hyphens, so a spec validator rejects it and it cannot match its directory",
            )
        )
    compatibility = str(doc.frontmatter.get("compatibility", ""))
    if len(compatibility) > COMPATIBILITY_LIMIT:
        findings.append(
            _finding(
                "compatibility-over-500",
                Severity.INFO,
                "frontmatter",
                f"compatibility is {len(compatibility)} chars; the spec caps it at "
                f"{COMPATIBILITY_LIMIT}",
            )
        )
    return findings


def _lint_text(where: str, text: str) -> list[ScanFinding]:
    findings: list[ScanFinding] = []
    variables: list[tuple[int, str]] = []
    commands: list[tuple[int, str]] = []
    in_fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        if _COMMAND_FENCE.match(line):
            commands.append((number, "```!"))
        if _FENCE.match(line):
            in_fence = not in_fence
        for match in _CLAUDE_ARGUMENTS.finditer(line):
            variables.append((number, match.group()))
        for match in _CLAUDE_ENV.finditer(line):
            variables.append((number, match.group()))
        if not in_fence:
            for match in _POSITIONAL.finditer(line):
                variables.append((number, match.group()))
        for match in _COMMAND_INLINE.finditer(line):
            commands.append((number, match.group()))
    for number, token in variables[:_MAX_PER_RULE]:
        other = (
            "Kiro's CLI also substitutes it"
            if token.startswith("$ARGUMENTS")
            else "no other agent substitutes it"
        )
        findings.append(
            _finding(
                "claude-body-variable",
                Severity.LOW,
                f"{where}:{number}",
                f"{token} is a Claude Code substitution; {other}, so the text is passed literally",
            )
        )
    for number, token in commands[:_MAX_PER_RULE]:
        findings.append(
            _finding(
                "claude-command-block",
                Severity.LOW,
                f"{where}:{number}",
                f"{token} is Claude Code dynamic context injection; no other agent runs it, so the "
                "command output never reaches the model there",
            )
        )
    return findings


def _verdict(findings: list[ScanFinding]) -> str:
    rules = {finding.rule_id for finding in findings}
    env_only = any(
        finding.rule_id == "portability:claude-body-variable" and "CLAUDE_" in finding.message
        for finding in findings
    )
    if "portability:claude-command-block" in rules or env_only:
        return CLAUDE_ONLY
    if any(finding.severity == Severity.LOW for finding in findings):
        return DEGRADES
    return PORTABLE


def _finding(slug: str, severity: Severity, locus: str, message: str) -> ScanFinding:
    return ScanFinding(
        rule_id=f"portability:{slug}",
        category=PORTABILITY,
        severity=severity,
        locus=locus,
        message=message,
    )


def _short(finding: ScanFinding) -> str:
    rule = finding.rule_id.split(":", 1)[1]
    return f"{rule} at {finding.locus}" if finding.locus != "frontmatter" else rule


def _present(value: object) -> bool:
    return value is True or (isinstance(value, str) and bool(value.strip()))


def _read_text(path: Path) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > _MAX_FILE_BYTES or b"\x00" in raw[:8192]:
        return None
    return raw.decode("utf-8", errors="replace")
