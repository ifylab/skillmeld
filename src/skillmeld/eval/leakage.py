# SPDX-License-Identifier: Apache-2.0
"""Leakage gate: held-out eval queries must not appear verbatim in the skill text.

If a held-out query's text is embedded in a skill body or description, the trigger-eval score
is contaminated (the skill can match the query by memorisation, not by genuine routing). The
gate is a deterministic substring check over normalized text.
"""

from __future__ import annotations

from skillmeld.eval.trigger import TriggerQuery
from skillmeld.merge.parse import norm_key
from skillmeld.models import MergeResult


def held_out_leaks(
    result: MergeResult, queries: list[TriggerQuery], held_out_ids: list[str]
) -> list[str]:
    """Return the ids of held-out queries whose text leaks into any emitted skill or description."""
    held_out = {q.id: q for q in queries if q.id in set(held_out_ids)}
    haystack = _corpus(result)
    return sorted(qid for qid, query in held_out.items() if _leaks(query.text, haystack))


def _corpus(result: MergeResult) -> str:
    parts: list[str] = []
    skills = list(result.skills)
    if result.orchestrator is not None:
        skills.append(result.orchestrator)
    for skill in skills:
        parts.append(skill.doc.body)
        parts.append(str(skill.doc.frontmatter.get("description", "")))
    return norm_key(" \n ".join(parts))


def _leaks(query_text: str, haystack: str) -> bool:
    needle = norm_key(query_text)
    return len(needle) >= 8 and needle in haystack
