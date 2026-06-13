# SPDX-License-Identifier: Apache-2.0
"""Focused tests for the adversarial-hardened steps: reconcile, prune+closure, partition."""

from __future__ import annotations

import pytest

from skillmeld.merge.parse import atom_id, norm_key
from skillmeld.merge.partition import partition, routes_for
from skillmeld.merge.prune import PruneError, prune_and_close
from skillmeld.merge.reconcile import reconcile
from skillmeld.models import Atom, AtomKind, Conflict, DependencyEdge, UseCaseProfile


def _atom(skill: str, path: str, text: str, *, severity: str | None = None, order: int = 0) -> Atom:
    return Atom(
        id=atom_id(skill, path, text),
        skill=skill,
        path=path,
        text=text,
        detected_kind=AtomKind.directive,
        max_severity=severity,
        source_order=order,
        norm_key=norm_key(text),
    )


# --- reconcile --------------------------------------------------------------------------


def test_reconcile_keeps_lower_severity_regardless_of_winner() -> None:
    safe = _atom("a", "p0", "Always validate input.", severity=None)
    risky = _atom("b", "p0", "Never validate input.", severity="critical")
    # Even if Claude names the risky atom the winner, severity asymmetry overrides it.
    conflict = Conflict(
        atom_a=min(safe.id, risky.id),
        atom_b=max(safe.id, risky.id),
        type="antonymic-directive",
        source="structural",
        winner=risky.id,
    )
    result = reconcile([conflict], [safe, risky])
    assert safe.id in result.survivors
    assert risky.id in result.losers


def test_reconcile_honors_source_rank_on_equal_severity() -> None:
    a = _atom("alpha", "p0", "Use four-space indent.")
    b = _atom("beta", "p0", "Use tab indent.")
    conflict = Conflict(atom_a=min(a.id, b.id), atom_b=max(a.id, b.id), type="antonymic-directive")
    result = reconcile([conflict], [a, b], source_rank={"alpha": 0, "beta": 1})
    assert a.id in result.survivors and b.id in result.losers


def test_reconcile_honors_claude_winner_on_equal_severity() -> None:
    a = _atom("x", "p0", "Option A here today.")
    b = _atom("y", "p0", "Option B here today.")
    conflict = Conflict(
        atom_a=min(a.id, b.id), atom_b=max(a.id, b.id), type="antonymic-directive", winner=b.id
    )
    result = reconcile([conflict], [a, b])
    assert b.id in result.survivors and a.id in result.losers


def test_reconcile_leaves_genuine_tie_unresolved() -> None:
    a = _atom("x", "p0", "Same length text.", order=0)
    b = _atom("x", "p1", "Same length text!", order=0)
    conflict = Conflict(atom_a=min(a.id, b.id), atom_b=max(a.id, b.id), type="antonymic-directive")
    result = reconcile([conflict], [a, b])
    assert result.unresolved and not result.resolved


# --- prune + closure --------------------------------------------------------------------

PROFILE = UseCaseProfile(summary="ifc takeoff", tasks=["quantity takeoff from ifc"])


def test_closure_keeps_out_of_scope_dependency() -> None:
    relevant = _atom("s", "p0", "Run the ifc takeoff.")
    helper = _atom("s", "p1", "Internal helper unrelated to scope.")
    edge = DependencyEdge(
        src_atom_id=relevant.id, target_ref=helper.id, resolved_atom_id=helper.id, kind="atom"
    )
    result = prune_and_close([relevant, helper], PROFILE, edges=[edge])
    assert helper.id in result.kept  # kept only because the relevant atom depends on it


def test_kept_depends_on_loser_is_a_hard_error() -> None:
    relevant = _atom("s", "p0", "Run the ifc takeoff.")
    loser = _atom("s", "p1", "A dropped conflicting directive.")
    edge = DependencyEdge(
        src_atom_id=relevant.id, target_ref=loser.id, resolved_atom_id=loser.id, kind="atom"
    )
    with pytest.raises(PruneError, match="conflict-loser"):
        prune_and_close([relevant, loser], PROFILE, edges=[edge], losers={loser.id})


def test_closure_terminates_on_a_cycle() -> None:
    a = _atom("s", "p0", "Run the ifc takeoff.")
    b = _atom("s", "p1", "Cycle partner.")
    edges = [
        DependencyEdge(src_atom_id=a.id, target_ref=b.id, resolved_atom_id=b.id, kind="atom"),
        DependencyEdge(src_atom_id=b.id, target_ref=a.id, resolved_atom_id=a.id, kind="atom"),
    ]
    result = prune_and_close([a, b], PROFILE, edges=edges)
    assert a.id in result.kept and b.id in result.kept  # no infinite loop


def test_out_of_scope_atom_is_dropped_with_reason() -> None:
    relevant = _atom("s", "p0", "Run the ifc takeoff.")
    off_topic = _atom("s", "p1", "Bake a chocolate cake.")
    result = prune_and_close([relevant, off_topic], PROFILE)
    assert off_topic.id in result.dropped
    assert result.drop_reasons[off_topic.id] == "out-of-scope"


# --- partition --------------------------------------------------------------------------


def test_partition_keeps_three_or_fewer_groups() -> None:
    groups = {"g1": ["a"], "g2": ["b"], "g3": ["c"]}
    result = partition(["a", "b", "c"], groups)
    assert len(result.clusters) == 3


def test_partition_merges_down_to_limit_deterministically() -> None:
    groups = {"g1": ["a"], "g2": ["b"], "g3": ["c"], "g4": ["d"], "g5": ["e"]}
    kept = ["a", "b", "c", "d", "e"]
    first = partition(kept, groups)
    second = partition(kept, dict(reversed(list(groups.items()))))
    assert len(first.clusters) == 3
    assert [c.atom_ids for c in first.clusters] == [c.atom_ids for c in second.clusters]


def test_partition_will_not_co_locate_a_conflict() -> None:
    groups = {"g1": ["a"], "g2": ["b"], "g3": ["c"], "g4": ["d"]}
    kept = ["a", "b", "c", "d"]
    # Every cross-group pair conflicts, so no merge into 3 is possible without co-location.
    conflicts = [
        Conflict(atom_a=x, atom_b=y, type="antonymic-directive")
        for x in kept
        for y in kept
        if x < y
    ]
    result = partition(kept, groups, conflicts=conflicts, limit=3)
    assert len(result.clusters) == 4  # stayed split rather than co-locate
    assert result.warnings


def test_router_reaches_every_skill() -> None:
    groups = {"alpha": ["a"], "beta": ["b"]}
    result = partition(["a", "b"], groups)
    labels = [label for label, _ in routes_for(result)]
    assert labels == ["alpha", "beta"]  # one route per skill, none left unreachable
