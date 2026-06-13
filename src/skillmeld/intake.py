# SPDX-License-Identifier: Apache-2.0
"""Intake: normalize a described use case and flag when it is too thin to act on.

Deterministic only. The host Claude does the clarifying conversation; this just normalizes the
text and signals whether the engine has enough to ground on, so the skill knows when to ask a
question or two instead of guessing.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

_MIN_WORDS = 4
_VAGUE = frozenset(
    {
        "help",
        "stuff",
        "things",
        "thing",
        "something",
        "anything",
        "skills",
        "skill",
        "do",
        "some",
        "get",
        "make",
        "want",
        "need",
        "please",
        "the",
        "a",
        "an",
        "for",
        "with",
        "and",
        "me",
        "my",
    }
)


class Intake(BaseModel):
    use_case: str = ""
    word_count: int = 0
    thin: bool = True
    reasons: list[str] = Field(default_factory=list)


def intake(use_case: str) -> Intake:
    """Normalize a use case and decide whether it is thin (needs a clarifying question)."""
    normalized = re.sub(r"\s+", " ", use_case).strip()
    words = [w for w in re.findall(r"[a-z0-9]+", normalized.lower())]
    reasons: list[str] = []
    if len(words) < _MIN_WORDS:
        reasons.append("too short to infer a use case")
    content_words = [w for w in words if w not in _VAGUE]
    if not content_words:
        reasons.append("no concrete task named")
    return Intake(
        use_case=normalized,
        word_count=len(words),
        thin=bool(reasons),
        reasons=reasons,
    )
