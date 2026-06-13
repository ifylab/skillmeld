# SPDX-License-Identifier: Apache-2.0
"""Step 3 — validate the host Claude's grouping + labelling of atoms.

Claude returns ``{atom_id: {group, kind}}`` over the deduped survivors. Python validates far
beyond "the id exists": completeness (every atom assigned), partition (each atom in exactly
one group), kind monotonicity (a Python-detected directive can never be relabelled to dodge
conflict detection or pruning), and security monotonicity (Claude never moves an atom's
finding severity). One input skill is assumed hostile, so these guards are load-bearing.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from skillmeld.models import Atom, AtomKind


class GroupingError(ValueError):
    """The grouping response violated completeness, partition, or a monotonicity guard."""


class Assignment(BaseModel):
    group: str
    kind: AtomKind


class Grouping(BaseModel):
    groups: dict[str, list[str]] = Field(default_factory=dict)
    kinds: dict[str, AtomKind] = Field(default_factory=dict)
    forced: list[str] = Field(default_factory=list)


def validate_grouping(atoms: list[Atom], assignments: dict[str, Assignment]) -> Grouping:
    """Validate Claude's grouping against ``atoms``; force directive kinds; return the grouping."""
    atom_ids = {atom.id for atom in atoms}
    assigned = set(assignments)

    missing = atom_ids - assigned
    if missing:
        raise GroupingError(f"atoms left ungrouped: {', '.join(sorted(missing))}")
    extra = assigned - atom_ids
    if extra:
        raise GroupingError(f"grouping names unknown atoms: {', '.join(sorted(extra))}")

    by_id = {atom.id: atom for atom in atoms}
    groups: dict[str, list[str]] = {}
    kinds: dict[str, AtomKind] = {}
    forced: list[str] = []
    for atom_id in sorted(assignments):
        assignment = assignments[atom_id]
        kind = _enforce_kind_monotonicity(by_id[atom_id], assignment.kind, forced)
        kinds[atom_id] = kind
        groups.setdefault(assignment.group, []).append(atom_id)

    for members in groups.values():
        members.sort()
    return Grouping(groups=dict(sorted(groups.items())), kinds=kinds, forced=forced)


def default_grouping(atoms: list[Atom]) -> Grouping:
    """A deterministic Python grouping (by source skill). Fallback + test convenience."""
    assignments = {
        atom.id: Assignment(group=atom.skill, kind=atom.detected_kind or AtomKind.context)
        for atom in atoms
    }
    return validate_grouping(atoms, assignments)


def _enforce_kind_monotonicity(atom: Atom, claude_kind: AtomKind, forced: list[str]) -> AtomKind:
    """A detected directive stays a directive; Claude may only refine non-directive kinds."""
    if atom.detected_kind is AtomKind.directive and claude_kind is not AtomKind.directive:
        forced.append(atom.id)
        return AtomKind.directive
    return claude_kind
