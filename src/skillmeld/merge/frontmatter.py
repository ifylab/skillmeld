# SPDX-License-Identifier: Apache-2.0
"""Carry and reconcile a child's source frontmatter (tools, invocation, compatibility, metadata).

``synthesize`` emits only name+description; this pass restores the security- and provenance-
relevant fields a source declared, reconciled across the sources a merged child actually draws
from. The reconciliation is a pure function shared with the verifier, so what is emitted and what
is checked agree by construction — an emitted field no source justifies, or a tampered value,
cannot pass. Reconciliation is deterministic and ordered only by each source's position in the
``sources`` list (never by anything the pipeline hands down), so the verifier, re-parsing the
sources itself, recomputes the identical result.

Policy (locked 2026-06-19):
  - allowed-tools  : intersection across declaring sources. allowed-tools is pre-approval, not a
                     sandbox ("every tool remains callable"), so the safe direction is fewer
                     pre-approvals = more prompts. Any tool in the union but not the intersection
                     is dropped from pre-approval -> REVIEW.
  - disallowed-tools: union across declaring sources (a real blocklist; most-restrictive wins).
  - disable-model-invocation: honored if any source sets it -> the child is non-invocable, which
                     the orchestrator cannot auto-route -> REVIEW.
  - compatibility  : distinct values joined; informational note when more than one.
  - metadata       : key-union; on a key clash the earlier source in ``sources`` order wins -> note.
  - license is intentionally NOT handled here; it keeps its existing combine()/apply path.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from skillmeld.merge.parse import parse_skill
from skillmeld.models import AssembledAtom, MergeResult, ScanFinding, SkillDoc
from skillmeld.security.rules import Severity

ALLOWED_TOOLS = "allowed-tools"
DISALLOWED_TOOLS = "disallowed-tools"
DISABLE_INVOCATION = "disable-model-invocation"
COMPATIBILITY = "compatibility"
METADATA = "metadata"

# Fields this pass owns end to end (carry + verify). ``license`` is deliberately excluded — it
# keeps its existing most-restrictive combine() resolution and emit-time stamping.
CARRYABLE: tuple[str, ...] = (
    ALLOWED_TOOLS,
    DISALLOWED_TOOLS,
    DISABLE_INVOCATION,
    COMPATIBILITY,
    METADATA,
)

_COMPOSITION = "composition"
# A tool token: an identifier with an optional parenthesised spec, e.g. ``Bash(git add *)``.
# The parentheses are matched whole, so a documented internal space never splits a token.
_TOOL = re.compile(r"[A-Za-z_]\w*(?:\([^)]*\))?")


class ReconciledFrontmatter(BaseModel):
    fields: dict[str, object] = Field(default_factory=dict)
    findings: list[ScanFinding] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class FrontmatterCarry(BaseModel):
    findings: list[ScanFinding] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _present(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None and bool(value)


def _truthy(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _tool_tokens(value: object) -> list[str]:
    """Normalise an allowed/disallowed-tools value: space/comma string, or a YAML list."""
    if isinstance(value, (list, tuple)):
        return [token for token in (str(item).strip() for item in value) if token]
    return _TOOL.findall(str(value))


def _finding(rule_id: str, message: str) -> ScanFinding:
    return ScanFinding(
        rule_id=rule_id,
        category=_COMPOSITION,
        severity=Severity.MEDIUM,
        locus="frontmatter",
        message=message,
    )


def reconcile_frontmatter(contributing: list[SkillDoc]) -> ReconciledFrontmatter:
    """The carried frontmatter for a child drawing on ``contributing`` (in sources order).

    Pure and deterministic. Shared by ``carry_frontmatter`` (which applies ``fields``) and the
    verifier (which recomputes and compares).
    """
    fields: dict[str, object] = {}
    findings: list[ScanFinding] = []
    notes: list[str] = []

    allow = [
        _tool_tokens(doc.frontmatter[ALLOWED_TOOLS])
        for doc in contributing
        if ALLOWED_TOOLS in doc.frontmatter and _present(doc.frontmatter[ALLOWED_TOOLS])
    ]
    if allow:
        sets = [set(tokens) for tokens in allow]
        intersection = set(sets[0])
        union: set[str] = set()
        for token_set in sets:
            intersection &= token_set
            union |= token_set
        if intersection:
            fields[ALLOWED_TOOLS] = " ".join(sorted(intersection))
        dropped = sorted(union - intersection)
        if dropped:
            findings.append(
                _finding(
                    "merge:allowed-tools-narrowed",
                    "allowed-tools narrowed to the cross-source intersection; "
                    f"dropped from pre-approval: {', '.join(dropped)}",
                )
            )

    deny = [
        _tool_tokens(doc.frontmatter[DISALLOWED_TOOLS])
        for doc in contributing
        if DISALLOWED_TOOLS in doc.frontmatter and _present(doc.frontmatter[DISALLOWED_TOOLS])
    ]
    if deny:
        blocked: set[str] = set()
        for tokens in deny:
            blocked.update(tokens)
        if blocked:
            ordered = sorted(blocked)
            fields[DISALLOWED_TOOLS] = " ".join(ordered)
            notes.append(
                f"disallowed-tools combined (most-restrictive union): {', '.join(ordered)}"
            )

    disablers = sorted(
        {
            doc.source.name
            for doc in contributing
            if _truthy(doc.frontmatter.get(DISABLE_INVOCATION))
        }
    )
    if disablers:
        fields[DISABLE_INVOCATION] = True
        findings.append(
            _finding(
                "merge:invocation-disabled",
                f"disable-model-invocation honored from {', '.join(disablers)}; "
                "the orchestrator cannot auto-route a non-invocable child — review",
            )
        )

    compat: list[str] = []
    for doc in contributing:
        value = doc.frontmatter.get(COMPATIBILITY)
        if _present(value):
            text = str(value).strip()
            if text and text not in compat:
                compat.append(text)
    if compat:
        fields[COMPATIBILITY] = "; ".join(compat)
        if len(compat) > 1:
            notes.append("compatibility combined from multiple sources")

    metadata: dict[str, object] = {}
    for doc in contributing:
        source_meta = doc.frontmatter.get(METADATA)
        if not isinstance(source_meta, dict):
            continue
        for raw_key, value in source_meta.items():
            key = str(raw_key)
            if key in metadata:
                if metadata[key] != value:
                    notes.append(
                        f"metadata key {key!r} differs across sources; kept first source's value"
                    )
                continue
            metadata[key] = value
    if metadata:
        fields[METADATA] = metadata

    return ReconciledFrontmatter(fields=fields, findings=findings, notes=notes)


def atom_skill_index(sources: list[SkillDoc]) -> dict[str, str]:
    """Map each source atom id to its skill, re-parsed (never trusting handed-down data)."""
    index: dict[str, str] = {}
    for source in sources:
        for atom in parse_skill(source):
            index[atom.id] = atom.skill
    return index


def contributing_sources(
    layout: list[AssembledAtom], atom_skill: dict[str, str], sources: list[SkillDoc]
) -> list[SkillDoc]:
    """The source docs a child draws from, in ``sources`` order (deterministic for the verifier)."""
    names = {
        atom_skill[item.atom_id]
        for item in layout
        if item.role == "source" and item.atom_id in atom_skill
    }
    return [doc for doc in sources if doc.source.name in names]


def carry_frontmatter(result: MergeResult, sources: list[SkillDoc]) -> FrontmatterCarry:
    """Apply each child's reconciled source frontmatter in place; return findings + notes."""
    atom_skill = atom_skill_index(sources)
    findings: list[ScanFinding] = []
    notes: list[str] = []
    for skill in result.skills:
        contributing = contributing_sources(skill.layout, atom_skill, sources)
        reconciled = reconcile_frontmatter(contributing)
        skill.doc.frontmatter.update(reconciled.fields)
        findings.extend(reconciled.findings)
        notes.extend(reconciled.notes)
    return FrontmatterCarry(findings=findings, notes=notes)
