# SPDX-License-Identifier: Apache-2.0
"""Step 6 — scope-prune to the profile, then take the dependency closure. One fixed point.

Relevance is scored per atom (group membership can't smuggle an off-topic atom through the
prune). The precedence lattice is explicit: conflict-resolution > closure > prune. The seed is
relevant atoms minus conflict-losers; closure keeps a dependency even if out-of-scope, but a
kept atom depending on a conflict-loser is a hard error surfaced for re-adjudication, never a
silent re-admit. Closure is iterative with a visited-set, so dependency cycles terminate.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from skillmeld.models import Atom, AtomKind, DependencyEdge, UseCaseProfile

_TOKEN = re.compile(r"[a-z0-9]+")
_RESOURCE_REF = re.compile(
    r"(references|scripts|assets)/[\w./-]+|\b[\w-]+\.(?:json|ya?ml|md|csv)\b"
)


class PruneResult(BaseModel):
    kept: list[str] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)
    drop_reasons: dict[str, str] = Field(default_factory=dict)
    resources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PruneError(ValueError):
    """A kept atom depends on a conflict-loser; the merge must re-adjudicate."""


def build_edges(atoms: list[Atom]) -> list[DependencyEdge]:
    """Resolve dependency-ref atoms into edges. Resource paths resolve; atom refs stay open."""
    edges: list[DependencyEdge] = []
    for atom in atoms:
        if (atom.kind or atom.detected_kind) is not AtomKind.dependency_ref:
            continue
        for match in _RESOURCE_REF.findall(atom.text):
            target = match[0] if isinstance(match, tuple) else match
            if target:
                edges.append(
                    DependencyEdge(src_atom_id=atom.id, target_ref=target, kind="resource")
                )
    return edges


def prune_and_close(
    atoms: list[Atom],
    profile: UseCaseProfile,
    *,
    edges: list[DependencyEdge] | None = None,
    losers: set[str] | None = None,
) -> PruneResult:
    """Keep profile-relevant atoms plus their dependency closure; never re-admit a loser."""
    dropped_losers = losers or set()
    by_id = {atom.id: atom for atom in atoms}
    needles = _profile_needles(profile)

    relevant = {
        atom.id for atom in atoms if atom.id not in dropped_losers and _is_relevant(atom, needles)
    }
    atom_edges, resources, warnings = _partition_edges(edges or [], by_id, relevant)

    keep = set(relevant)
    frontier = list(relevant)
    while frontier:
        current = frontier.pop()
        for target in atom_edges.get(current, ()):  # closure over resolved atom deps
            if target in dropped_losers:
                raise PruneError(
                    f"kept atom {current} depends on conflict-loser {target}; re-adjudicate"
                )
            if target not in keep:
                keep.add(target)
                frontier.append(target)

    kept = sorted(keep)
    dropped = sorted(by_id.keys() - keep)
    reasons = {
        atom_id: ("conflict-loser" if atom_id in dropped_losers else "out-of-scope")
        for atom_id in dropped
    }
    return PruneResult(
        kept=kept,
        dropped=dropped,
        drop_reasons=reasons,
        resources=sorted(resources),
        warnings=sorted(warnings),
    )


def _partition_edges(
    edges: list[DependencyEdge], by_id: dict[str, Atom], relevant: set[str]
) -> tuple[dict[str, list[str]], set[str], list[str]]:
    atom_edges: dict[str, list[str]] = {}
    resources: set[str] = set()
    warnings: list[str] = []
    for edge in edges:
        if edge.kind == "resource":
            if edge.src_atom_id in relevant:
                resources.add(edge.target_ref)
        elif edge.kind == "atom" and edge.resolved_atom_id in by_id:
            atom_edges.setdefault(edge.src_atom_id, []).append(edge.resolved_atom_id)
        else:
            warnings.append(f"unresolved dependency {edge.target_ref!r} from {edge.src_atom_id}")
    return atom_edges, resources, warnings


def _profile_needles(profile: UseCaseProfile) -> set[str]:
    needles: set[str] = set()
    for value in (*profile.languages, *profile.frameworks, *profile.conventions):
        needles |= _tokens(value)
    needles |= _tokens(profile.summary)
    for task in profile.tasks:
        needles |= _tokens(task)
    return needles


def _is_relevant(atom: Atom, needles: set[str]) -> bool:
    if not needles:
        return True  # no profile signal -> keep everything (caller may narrow later)
    return bool(_tokens(atom.text) & needles)


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if len(t) >= 3}
