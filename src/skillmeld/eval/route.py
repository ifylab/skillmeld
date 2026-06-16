# SPDX-License-Identifier: Apache-2.0
"""Independent engine-side routing: route trigger queries against descriptions, no host, no LLM.

The trigger eval otherwise scores the routing the host Claude reports about itself. This module
routes the same queries deterministically against the child skills' own descriptions, so the
improve gate can cross-check that self-report instead of trusting it. A query routes to the
child whose name and description best match its terms (IDF-weighted, so rare terms discriminate
exactly as in discovery), but only when one child clearly wins; a tie or no match routes nowhere,
which is the correct outcome for a near-miss query.
"""

from __future__ import annotations

import math

from skillmeld.discovery import tokenize
from skillmeld.eval.trigger import TriggerJudgment, TriggerQuery
from skillmeld.models import MergeResult


def route_queries(result: MergeResult, queries: list[TriggerQuery]) -> list[TriggerJudgment]:
    """Route each query to the best-matching child skill, or nowhere. Deterministic, no model."""
    surfaces = _surfaces(result)
    idf = _idf(surfaces, queries)
    return [
        TriggerJudgment(query_id=query.id, routed_skill=_route_one(query, surfaces, idf))
        for query in queries
    ]


def _route_one(
    query: TriggerQuery, surfaces: list[tuple[str, set[str]]], idf: dict[str, float]
) -> str | None:
    """Route one query: commit to the top child only when it strictly beats every other."""
    tokens = tokenize(query.text)
    scored = sorted(
        ((_score(tokens, surface, idf), name) for name, surface in surfaces),
        key=lambda pair: (-pair[0], pair[1]),
    )
    if not scored:
        return None
    top_score, top_name = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if top_score <= 0.0 or top_score <= runner_up:
        return None
    return top_name


def _score(tokens: set[str], surface: set[str], idf: dict[str, float]) -> float:
    return sum(idf.get(token, 0.0) for token in tokens if token in surface)


def _surfaces(result: MergeResult) -> list[tuple[str, set[str]]]:
    """Each child's routing surface: the tokens of its name and description (its trigger text)."""
    surfaces: list[tuple[str, set[str]]] = []
    for skill in result.skills:
        name = str(skill.doc.frontmatter.get("name", "") or "")
        description = str(skill.doc.frontmatter.get("description", "") or "")
        surfaces.append((name, tokenize(f"{name} {description}")))
    return surfaces


def _idf(surfaces: list[tuple[str, set[str]]], queries: list[TriggerQuery]) -> dict[str, float]:
    """Inverse document frequency per query token across the child surfaces.

    A term shared by every child does not distinguish them; a term unique to one does. With only a
    handful of skills the effect is mild but real, and it mirrors discovery's ranking so routing
    keys on what is distinctive about each skill rather than on boilerplate they share.
    """
    total = len(surfaces)
    needed: set[str] = set()
    for query in queries:
        needed |= tokenize(query.text)
    idf: dict[str, float] = {}
    for token in needed:
        frequency = sum(1 for _, surface in surfaces if token in surface)
        idf[token] = math.log(1 + total / frequency) if frequency else 0.0
    return idf
