# SPDX-License-Identifier: Apache-2.0
"""Step 5 — reconcile conflicts by an explicit precedence policy, severity-aware first.

For each conflict, Python resolves a winner by visible precedence: a safety asymmetry wins
first (the lower-severity atom is kept and Claude is never consulted), then use-case
relevance, declared source priority, specificity, recency. Only genuine same-severity,
same-precedence ties reach Claude, whose pick must already be a member of the conflict. The
loser is recorded so closure can never silently re-admit it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from skillmeld.models import Atom, Conflict

_SEVERITY_RANK = {None: 0, "info": 1, "low": 2, "medium": 3, "high": 4, "critical": 5}


class ReconcileResult(BaseModel):
    survivors: list[str] = Field(default_factory=list)
    losers: list[str] = Field(default_factory=list)
    resolved: list[Conflict] = Field(default_factory=list)
    unresolved: list[Conflict] = Field(default_factory=list)


def reconcile(
    conflicts: list[Conflict],
    atoms: list[Atom],
    *,
    source_rank: dict[str, int] | None = None,
) -> ReconcileResult:
    """Resolve each conflict to a kept survivor and a dropped loser. Deterministic.

    ``source_rank`` maps a skill name to its selection priority (0 = highest). A conflict
    Python cannot resolve and Claude did not adjudicate is returned in ``unresolved``.
    """
    rank = source_rank or {}
    by_id = {atom.id: atom for atom in atoms}
    losers: set[str] = set()
    resolved: list[Conflict] = []
    unresolved: list[Conflict] = []

    for conflict in conflicts:
        a = by_id.get(conflict.atom_a)
        b = by_id.get(conflict.atom_b)
        if a is None or b is None:
            unresolved.append(conflict)
            continue
        winner = _resolve(conflict, a, b, rank)
        if winner is None:
            unresolved.append(conflict)
            continue
        loser = b if winner is a else a
        losers.add(loser.id)
        resolved.append(conflict.model_copy(update={"winner": winner.id}))

    survivors = [atom.id for atom in atoms if atom.id not in losers]
    return ReconcileResult(
        survivors=survivors,
        losers=sorted(losers),
        resolved=resolved,
        unresolved=unresolved,
    )


def _resolve(conflict: Conflict, a: Atom, b: Atom, rank: dict[str, int]) -> Atom | None:
    """Severity asymmetry first, then Claude's adjudicated winner, then deterministic precedence."""
    a_sev = _SEVERITY_RANK.get(a.max_severity, 0)
    b_sev = _SEVERITY_RANK.get(b.max_severity, 0)
    if a_sev != b_sev:
        return a if a_sev < b_sev else b  # keep the lower-severity atom

    if conflict.winner == a.id:
        return a
    if conflict.winner == b.id:
        return b

    a_key = _precedence_key(a, rank)
    b_key = _precedence_key(b, rank)
    if a_key != b_key:
        return a if a_key < b_key else b
    return None  # a genuine tie Claude did not break


def _precedence_key(atom: Atom, rank: dict[str, int]) -> tuple[int, int, int]:
    # Lower is better: declared source priority, then specificity (longer = more specific),
    # then source order as a stable final tiebreak.
    return (rank.get(atom.skill, 99), -len(atom.text), atom.source_order)
