# SPDX-License-Identifier: Apache-2.0
"""Deterministic structural-quality scoring for an emitted skill. No model calls.

Hard issues (over-length name/description, a reserved word, forbidden characters) gate; the
strong/weak directive-marker ratio is a soft quality signal that surfaces but never blocks.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from skillmeld.models import API_DESCRIPTION_LIMIT, CLAUDE_CODE_ROUTING_LIMIT, SkillDoc

NAME_LIMIT = 64
RESERVED_NAME_WORDS = ("claude", "anthropic")
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

    The description budget is surface-aware. Over the Claude Code routing cap it is truncated on
    every surface and loses routing signal — a hard issue. Between the API authoring cap and that
    routing cap it still fits Claude Code, but the API ``/v1/skills`` surface would reject it — a
    non-blocking warning, since skillmeld's primary install target is Claude Code.
    """
    name = str(doc.frontmatter.get("name", doc.source.name))
    description = str(doc.frontmatter.get("description", ""))
    issues: list[str] = []
    warnings: list[str] = []

    if len(name) > NAME_LIMIT:
        issues.append(f"name exceeds {NAME_LIMIT} chars")
    if any(word in name.lower() for word in RESERVED_NAME_WORDS):
        issues.append("name contains a reserved word (claude/anthropic)")
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
            "so the API /v1/skills surface would reject it"
        )
    if _HTML_TAG.search(_CODE_SPAN.sub(" ", doc.body)):
        issues.append("body contains an unescaped html-like tag")
    bad_keys = sorted(set(doc.frontmatter) - ALLOWED_FRONTMATTER)
    if bad_keys:
        issues.append(f"unknown frontmatter keys: {', '.join(bad_keys)}")

    strong = len(_STRONG.findall(doc.body))
    weak = len(_WEAK.findall(doc.body))
    return QualityReport(
        skill=name,
        name_chars=len(name),
        description_chars=len(description),
        body_lines=doc.body.count("\n"),
        strong_markers=strong,
        weak_markers=weak,
        marker_ratio=round(strong / (strong + weak + 1), 3),
        issues=issues,
        warnings=warnings,
        passed=not issues,
    )
