# SPDX-License-Identifier: Apache-2.0
"""Deterministic structural-quality scoring for an emitted skill. No model calls.

Hard issues gate only surfaces the composition itself authors (name, description, frontmatter).
Inherited body content never blocks: bodies are byte-traced from sources and improves are
description-only, so a body finding has no in-engine remediation — it surfaces as a warning
(html-like tags) or a soft signal (the strong/weak directive-marker ratio) instead.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from skillmeld.models import (
    API_DESCRIPTION_LIMIT,
    CLAUDE_CODE_ROUTING_LIMIT,
    COMPATIBILITY_LIMIT,
    NAME_LIMIT,
    SPEC_NAME_RE,
    SkillDoc,
)

RESERVED_NAME_WORDS = ("claude", "anthropic")
# Claude Code's own fields: carried on Claude surfaces, left out of spec-only ones.
CLAUDE_ONLY_FRONTMATTER = ("disallowed-tools", "disable-model-invocation")
ALLOWED_FRONTMATTER = frozenset(
    {
        "name",
        "description",
        "license",
        "compatibility",
        "allowed-tools",
        "disallowed-tools",
        "disable-model-invocation",
        "metadata",
    }
)

_STRONG = re.compile(r"\b(must|always|never|do not|don'?t|required|ensure|shall)\b", re.IGNORECASE)
_WEAK = re.compile(
    r"\b(maybe|consider|try to|should probably|if possible|optionally|perhaps)\b", re.IGNORECASE
)

# Catch genuine unescaped markup (`<div>`, `</tag>`, `<!--`), not the `<`/`>` of code. Composing
# code-writing skills means bodies are full of `count < 1`, `aspect <5`, `->` arrows, `List<int>`;
# those are not tags. Fenced and inline code is stripped first, then only tag-shaped spans flag.
_CODE_SPAN = re.compile(r"```.*?```|`[^`]*`", re.DOTALL)
_HTML_TAG = re.compile(r"<[/!]?[a-zA-Z][^<>]*>")


class QualityReport(BaseModel):
    skill: str
    name_chars: int = 0
    description_chars: int = 0
    body_lines: int = 0
    strong_markers: int = 0
    weak_markers: int = 0
    marker_ratio: float = 0.0
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    passed: bool = True


def score_quality(doc: SkillDoc) -> QualityReport:
    """Score one skill's structure. ``passed`` is False when any hard issue is present.

    The name rules are the Agent Skills spec's: the composition authors the name, so a name that
    cannot match its emitted directory is a hard issue. The description budget is surface-aware.
    Over the Claude Code routing cap it is truncated on every surface and loses routing signal — a
    hard issue. Between the spec's 1024-char cap (the API authoring cap) and that routing cap it
    still fits Claude Code, but every other agent and the ``/v1/skills`` surface stop at the spec
    cap — a non-blocking warning.
    """
    name = str(doc.frontmatter.get("name", doc.source.name))
    description = str(doc.frontmatter.get("description", ""))
    issues: list[str] = []
    warnings: list[str] = []

    if len(name) > NAME_LIMIT:
        issues.append(f"name exceeds {NAME_LIMIT} chars")
    elif not SPEC_NAME_RE.match(name):
        issues.append(
            "name is not lowercase alphanumerics joined by single hyphens (Agent Skills spec), "
            "so it cannot match its emitted directory"
        )
    if any(word in name.lower() for word in RESERVED_NAME_WORDS):
        issues.append("name contains a reserved word (claude/anthropic)")
    compatibility = str(doc.frontmatter.get("compatibility", ""))
    if len(compatibility) > COMPATIBILITY_LIMIT:
        warnings.append(
            f"compatibility is {len(compatibility)} chars, over the spec's "
            f"{COMPATIBILITY_LIMIT}-char cap (inherited from a source)"
        )
    claude_only = [f for f in CLAUDE_ONLY_FRONTMATTER if _carried(doc.frontmatter.get(f))]
    if claude_only:
        warnings.append(
            f"{', '.join(claude_only)} sit outside the Agent Skills spec; kept on Claude Code "
            "surfaces, left out of spec-only ones"
        )
    if not description.strip():
        issues.append("description is empty (a skill with no description never triggers)")
    if len(description) > CLAUDE_CODE_ROUTING_LIMIT:
        issues.append(
            f"description is {len(description)} chars, over the {CLAUDE_CODE_ROUTING_LIMIT}-char "
            "Claude Code routing cap; it is truncated on every surface and loses routing signal"
        )
    elif len(description) > API_DESCRIPTION_LIMIT:
        warnings.append(
            f"description is {len(description)} chars; within the {CLAUDE_CODE_ROUTING_LIMIT}-char "
            f"Claude Code routing cap but over the {API_DESCRIPTION_LIMIT}-char API authoring cap, "
            "which is also the Agent Skills spec cap, so the API /v1/skills surface would reject "
            "it and other agents truncate it"
        )
    stripped = _CODE_SPAN.sub(lambda match: "\n" * match.group().count("\n"), doc.body)
    tag_lines = [
        number
        for number, line in enumerate(stripped.splitlines(), start=1)
        if _HTML_TAG.search(line)
    ]
    if tag_lines:
        cited = ", ".join(str(number) for number in tag_lines[:5])
        extra = f" and {len(tag_lines) - 5} more" if len(tag_lines) > 5 else ""
        warnings.append(f"body contains an unescaped html-like tag (line {cited}{extra})")
    bad_keys = sorted(set(doc.frontmatter) - ALLOWED_FRONTMATTER)
    if bad_keys:
        issues.append(f"unknown frontmatter keys: {', '.join(bad_keys)}")

    strong = len(_STRONG.findall(doc.body))
    weak = len(_WEAK.findall(doc.body))
    return _report(name, description, doc.body, strong, weak, issues, warnings)


def _carried(value: object) -> bool:
    return value is True or (isinstance(value, str) and bool(value.strip()))


def _report(
    name: str,
    description: str,
    body: str,
    strong: int,
    weak: int,
    issues: list[str],
    warnings: list[str],
) -> QualityReport:
    return QualityReport(
        skill=name,
        name_chars=len(name),
        description_chars=len(description),
        body_lines=body.count("\n"),
        strong_markers=strong,
        weak_markers=weak,
        marker_ratio=round(strong / (strong + weak + 1), 3),
        issues=issues,
        warnings=warnings,
        passed=not issues,
    )
