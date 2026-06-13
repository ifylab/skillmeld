# SPDX-License-Identifier: Apache-2.0
"""Tests for selection validation: hallucinated ids die here, limits hold, warnings surface."""

from __future__ import annotations

import pytest

from skillmeld.models import Candidate, CatalogEntry, SkillSource
from skillmeld.select import SelectionError, select


def _candidate(entry_id: str, repo: str) -> Candidate:
    entry = CatalogEntry(id=entry_id, source=SkillSource(name=entry_id, repo=repo))
    return Candidate(entry=entry, score=1.0)


CANDIDATES = [
    _candidate("a", "one/skills"),
    _candidate("b", "two/skills"),
    _candidate("c", "two/skills"),
    _candidate("d", "three/skills"),
]


def test_valid_pick_preserves_ranked_order() -> None:
    selection = select(CANDIDATES, ["d", "a"])
    assert [candidate.entry.id for candidate in selection.chosen] == ["d", "a"]
    assert selection.warnings == []


def test_unknown_id_is_rejected() -> None:
    with pytest.raises(SelectionError, match="not in the candidate set"):
        select(CANDIDATES, ["a", "made-up-skill"])


def test_over_limit_is_rejected() -> None:
    with pytest.raises(SelectionError, match="limit is 3"):
        select(CANDIDATES, ["a", "b", "c", "d"])


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(SelectionError, match="duplicate"):
        select(CANDIDATES, ["a", "a"])


def test_empty_choice_is_rejected() -> None:
    with pytest.raises(SelectionError, match="no skills chosen"):
        select(CANDIDATES, [])


def test_same_repo_picks_are_warned() -> None:
    selection = select(CANDIDATES, ["b", "c"])
    assert len(selection.warnings) == 1
    assert "b and c" in selection.warnings[0]
    assert "two/skills" in selection.warnings[0]
