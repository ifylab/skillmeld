# SPDX-License-Identifier: Apache-2.0
"""Step 7 — partition kept atoms into <=3 skills, constraint-aware and deterministic.

Partition is a total function (each atom lands in exactly one skill). The clustering never
co-locates two atoms that still conflict (a resolved conflict could otherwise reappear once
two groups share a file), and it is fully deterministic (fixed merge order, lexicographic
ties). The orchestrator is emitted only when there is more than one skill, and it routes to
every skill in the set so no part is left unreachable.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from skillmeld.models import Conflict

DEFAULT_LIMIT = 3


class Cluster(BaseModel):
    label: str
    atom_ids: list[str] = Field(default_factory=list)


class Partition(BaseModel):
    clusters: list[Cluster] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def partition(
    kept: list[str],
    groups: dict[str, list[str]],
    *,
    conflicts: list[Conflict] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> Partition:
    """Cluster the kept atoms (grouped by label) into <=``limit`` conflict-free skills."""
    kept_set = set(kept)
    clusters = [
        Cluster(label=label, atom_ids=sorted(a for a in members if a in kept_set))
        for label, members in sorted(groups.items())
    ]
    clusters = [c for c in clusters if c.atom_ids]
    blocked = _must_not_link(conflicts or [], kept_set)

    warnings: list[str] = []
    while len(clusters) > limit:
        merged = _merge_one(clusters, blocked)
        if merged is None:
            warnings.append(
                f"cannot reduce to {limit} skills without co-locating a conflict; "
                f"emitting {len(clusters)}"
            )
            break
        clusters = merged
    return Partition(clusters=clusters, warnings=warnings)


def routes_for(partition_result: Partition) -> list[tuple[str, str]]:
    """One route per skill: (label, skill_label). Every skill in the set is reachable."""
    return [(cluster.label, cluster.label) for cluster in partition_result.clusters]


def _must_not_link(conflicts: list[Conflict], kept: set[str]) -> set[frozenset[str]]:
    return {
        frozenset({c.atom_a, c.atom_b}) for c in conflicts if c.atom_a in kept and c.atom_b in kept
    }


def _merge_one(clusters: list[Cluster], blocked: set[frozenset[str]]) -> list[Cluster] | None:
    """Merge the two smallest mergeable clusters. Deterministic; None when none can merge."""
    order = sorted(
        range(len(clusters)), key=lambda i: (len(clusters[i].atom_ids), clusters[i].label)
    )
    for pos_a in range(len(order)):
        for pos_b in range(pos_a + 1, len(order)):
            i, j = order[pos_a], order[pos_b]
            if _can_merge(clusters[i], clusters[j], blocked):
                fused = Cluster(
                    label=_join_labels(clusters[i].label, clusters[j].label),
                    atom_ids=sorted(clusters[i].atom_ids + clusters[j].atom_ids),
                )
                rest = [c for k, c in enumerate(clusters) if k not in (i, j)]
                return sorted([*rest, fused], key=lambda c: c.label)
    return None


def _can_merge(a: Cluster, b: Cluster, blocked: set[frozenset[str]]) -> bool:
    return not any(frozenset({x, y}) in blocked for x in a.atom_ids for y in b.atom_ids)


def _join_labels(a: str, b: str) -> str:
    return " + ".join(sorted({a, b}))
