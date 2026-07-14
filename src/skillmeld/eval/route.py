# SPDX-License-Identifier: Apache-2.0
"""Independent engine-side routing: route trigger queries against descriptions, no host, no LLM.

The trigger eval otherwise scores the routing the host Claude reports about itself. This module
routes the same queries deterministically against the child skills' own descriptions, so the
improve gate can cross-check that self-report instead of trusting it. A query routes to the
child whose name and description best match its terms (IDF-weighted, so rare terms discriminate
exactly as in discovery), but only when one child clearly wins; a tie or no match routes nowhere,
which is the correct outcome for a near-miss query. Generic programming vocabulary carries no
routing weight, so a route commits on what is distinctive about a child, never on tokens any
code-flavored query would share with it.
"""

from __future__ import annotations

import math

from skillmeld.discovery import tokenize
from skillmeld.eval.trigger import TriggerJudgment, TriggerQuery
from skillmeld.models import MergeResult

# Language/code-artifact and write-vocabulary tokens that programming skills and their near-miss
# queries share regardless of domain: a near-miss like "write a python flask database query"
# latches onto a Python-flavored child on these tokens alone. Removed from queries and surfaces
# in routing only — discovery ranks with its own stopword list and must not move. Not action
# discriminators (find/search/review/check), which real triggers route on.
_GENERIC_CODE_STOPLIST = frozenset(
    {
        "code",
        "codes",
        "coding",
        "program",
        "programs",
        "programming",
        "python",
        "script",
        "scripts",
        "scripting",
        "write",
        "writes",
        "writing",
        "written",
    }
)


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
    tokens = _routing_tokens(query.text)
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


def _routing_tokens(text: str) -> set[str]:
    """Tokens that may carry a route: the shared tokenizer minus the generic-code vocabulary."""
    return tokenize(text) - _GENERIC_CODE_STOPLIST


def _surfaces(result: MergeResult) -> list[tuple[str, set[str]]]:
    """Each child's routing surface: the tokens of its name and description (its trigger text)."""
    surfaces: list[tuple[str, set[str]]] = []
    for skill in result.skills:
        name = str(skill.doc.frontmatter.get("name", "") or "")
        description = str(skill.doc.frontmatter.get("description", "") or "")
        surfaces.append((name, _routing_tokens(f"{name} {description}")))
    return surfaces


def _idf(surfaces: list[tuple[str, set[str]]], queries: list[TriggerQuery]) -> dict[str, float]:
    """Inverse document frequency per query token across the child surfaces.

    A term shared by every child does not distinguish them; a term unique to one does — so a
    token present in every one of multiple surfaces weights zero and can never tip a route by
    itself. A single-child set has nothing to distinguish between, and keeps the plain weighting
    so a genuine match still routes. This mirrors discovery's ranking: routing keys on what is
    distinctive about each skill rather than on boilerplate they share.
    """
    total = len(surfaces)
    needed: set[str] = set()
    for query in queries:
        needed |= _routing_tokens(query.text)
    idf: dict[str, float] = {}
    for token in needed:
        frequency = sum(1 for _, surface in surfaces if token in surface)
        if not frequency or (frequency == total and total > 1):
            idf[token] = 0.0
        else:
            idf[token] = math.log(1 + total / frequency)
    return idf
