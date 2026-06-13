# SPDX-License-Identifier: Apache-2.0
"""Tests for merge steps 2-4: dedupe, group/label validation, structural conflict detection."""

from __future__ import annotations

import pytest

from skillmeld.merge.conflicts import ConflictError, detect_structural, merge_adjudication
from skillmeld.merge.dedupe import collapse
from skillmeld.merge.group import (
    Assignment,
    GroupingError,
    default_grouping,
    validate_grouping,
)
from skillmeld.merge.parse import atom_id, norm_key
from skillmeld.models import Atom, AtomKind, Conflict


def _atom(
    skill: str,
    path: str,
    text: str,
    kind: AtomKind = AtomKind.context,
    order: int = 0,
) -> Atom:
    return Atom(
        id=atom_id(skill, path, text),
        skill=skill,
        path=path,
        text=text,
        detected_kind=kind,
        source_order=order,
        norm_key=norm_key(text),
    )


# --- step 2: dedupe ---------------------------------------------------------------------


def test_collapse_merges_normalized_duplicates() -> None:
    atoms = [
        _atom("a", "p0", "Use TLS for all connections.", order=0),
        _atom("b", "p0", "use   tls for all connections", order=0),
        _atom("a", "p1", "Validate inputs.", order=1),
    ]
    result = collapse(atoms)
    assert len(result.survivors) == 2
    assert len(result.collapsed) == 1
    survivor_ids = {a.id for a in result.survivors}
    assert all(target in survivor_ids for target in result.collapsed.values())


def test_collapse_survivor_is_verbatim_not_normalized() -> None:
    atoms = [
        _atom("b", "p0", "use tls", order=0),
        _atom("a", "p0", "Use TLS.", order=0),
    ]
    result = collapse(atoms)
    # survivor sorts by skill name first -> "a" wins, keeping its exact bytes
    assert result.survivors[0].text == "Use TLS."


def test_collapse_is_deterministic() -> None:
    atoms = [_atom("a", f"p{i}", f"Line {i}.", order=i) for i in range(5)]
    assert collapse(atoms).model_dump() == collapse(list(reversed(atoms))).model_dump()


# --- step 3: group/label validation -----------------------------------------------------


def test_default_grouping_groups_by_skill() -> None:
    atoms = [_atom("a", "p0", "x"), _atom("a", "p1", "y"), _atom("b", "p0", "z")]
    grouping = default_grouping(atoms)
    assert set(grouping.groups) == {"a", "b"}
    assert len(grouping.groups["a"]) == 2


def test_grouping_rejects_incomplete_assignment() -> None:
    atoms = [_atom("a", "p0", "x"), _atom("a", "p1", "y")]
    assignments = {atoms[0].id: Assignment(group="g", kind=AtomKind.context)}
    with pytest.raises(GroupingError, match="ungrouped"):
        validate_grouping(atoms, assignments)


def test_grouping_rejects_unknown_atom() -> None:
    atoms = [_atom("a", "p0", "x")]
    assignments = {
        atoms[0].id: Assignment(group="g", kind=AtomKind.context),
        "made:up:id": Assignment(group="g", kind=AtomKind.context),
    }
    with pytest.raises(GroupingError, match="unknown atoms"):
        validate_grouping(atoms, assignments)


def test_grouping_forces_directive_kind() -> None:
    directive = _atom("a", "p0", "Always validate inputs.", kind=AtomKind.directive)
    assignments = {directive.id: Assignment(group="g", kind=AtomKind.example)}
    grouping = validate_grouping([directive], assignments)
    assert grouping.kinds[directive.id] is AtomKind.directive  # downgrade refused
    assert directive.id in grouping.forced


def test_grouping_allows_non_directive_refinement() -> None:
    atom = _atom("a", "p0", "Background context here.", kind=AtomKind.context)
    assignments = {atom.id: Assignment(group="g", kind=AtomKind.trigger)}
    grouping = validate_grouping([atom], assignments)
    assert grouping.kinds[atom.id] is AtomKind.trigger
    assert atom.id not in grouping.forced


# --- step 4: structural conflict detection ----------------------------------------------


def test_antonymic_directives_conflict() -> None:
    atoms = [
        _atom("a", "p0", "Always use tabs for indentation.", kind=AtomKind.directive),
        _atom("b", "p0", "Never use tabs for indentation.", kind=AtomKind.directive),
    ]
    conflicts = detect_structural(atoms)
    assert any(c.type == "antonymic-directive" for c in conflicts)
    assert all(c.source == "structural" for c in conflicts)


def test_numeric_bound_clash_conflicts() -> None:
    atoms = [
        _atom("a", "p0", "Keep line length to 88 characters.", kind=AtomKind.directive),
        _atom("b", "p0", "Keep line length to 100 characters.", kind=AtomKind.directive),
    ]
    conflicts = detect_structural(atoms)
    assert any(c.type == "numeric-bound" for c in conflicts)


def test_same_trigger_different_skill_conflicts() -> None:
    atoms = [
        _atom("a", "p0", "When the user asks for a report.", kind=AtomKind.trigger),
        _atom("b", "p0", "when the user asks for a report", kind=AtomKind.trigger),
    ]
    conflicts = detect_structural(atoms)
    assert any(c.type == "same-trigger" for c in conflicts)


def test_no_false_conflict_between_unrelated_directives() -> None:
    atoms = [
        _atom("a", "p0", "Always validate inputs.", kind=AtomKind.directive),
        _atom("b", "p0", "Prefer composition over inheritance.", kind=AtomKind.directive),
    ]
    assert detect_structural(atoms) == []


def test_structural_conflicts_are_non_dismissible() -> None:
    atoms = [
        _atom("a", "p0", "Always use tabs.", kind=AtomKind.directive),
        _atom("b", "p0", "Never use tabs.", kind=AtomKind.directive),
    ]
    structural = detect_structural(atoms)
    # Claude returns an empty adjudication (tries to dismiss) -> the pair still survives.
    merged = merge_adjudication(structural, [])
    assert len(merged) == len(structural)


def test_adjudication_sets_winner() -> None:
    atoms = [
        _atom("a", "p0", "Always use tabs.", kind=AtomKind.directive),
        _atom("b", "p0", "Never use tabs.", kind=AtomKind.directive),
    ]
    structural = detect_structural(atoms)
    pair = structural[0]
    verdict = pair.model_copy(update={"winner": pair.atom_a})
    merged = merge_adjudication(structural, [verdict])
    assert merged[0].winner == pair.atom_a


def test_adjudication_rejects_foreign_winner() -> None:
    atoms = [
        _atom("a", "p0", "Always use tabs.", kind=AtomKind.directive),
        _atom("b", "p0", "Never use tabs.", kind=AtomKind.directive),
    ]
    structural = detect_structural(atoms)
    bad = structural[0].model_copy(update={"winner": "outsider:atom:id"})
    with pytest.raises(ConflictError, match="not part of conflict"):
        merge_adjudication(structural, [bad])


def test_adjudication_adds_semantic_conflicts() -> None:
    structural: list[Conflict] = []
    semantic = Conflict(atom_a="a:p0:1", atom_b="b:p0:2", type="semantic", source="semantic")
    merged = merge_adjudication(structural, [semantic])
    assert len(merged) == 1
    assert merged[0].source == "semantic"
