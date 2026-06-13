# SPDX-License-Identifier: Apache-2.0
"""Tests for the discovery prefilter: scoring, evidence, blocked exclusion, determinism."""

from __future__ import annotations

from skillmeld.discovery import discover
from skillmeld.models import CatalogEntry, SkillSource, UseCaseProfile


def _entry(
    entry_id: str,
    name: str,
    description: str = "",
    tags: list[str] | None = None,
    languages: list[str] | None = None,
    stars: int | None = None,
    repo: str = "acme/skills",
    bundle_hash: str = "",
) -> CatalogEntry:
    return CatalogEntry(
        id=entry_id,
        source=SkillSource(name=name, repo=repo, stars=stars),
        description=description,
        tags=tags or [],
        languages=languages or [],
        bundle_hash=bundle_hash,
    )


PROFILE = UseCaseProfile(
    summary="Automate IFC model checks for structural engineering.",
    languages=["Python"],
    frameworks=[],
    conventions=["tests"],
    tasks=["quantity takeoff from IFC models"],
)

CATALOG = [
    _entry(
        "ddc/skills:ifc/quantity-takeoff",
        "ifc-quantity-takeoff",
        "Quantity takeoff and BoQ extraction from IFC models.",
        tags=["ifc", "qto"],
        languages=["Python"],
        stars=210,
        repo="ddc/skills",
        bundle_hash="hash-qto",
    ),
    _entry(
        "compdesigners/skills:revit-dynamo",
        "revit-dynamo",
        "Automate Revit workflows with Dynamo graphs.",
        tags=["revit", "dynamo"],
        languages=["Python", "C#"],
        stars=95,
        repo="compdesigners/skills",
    ),
    _entry(
        "acme/skills:react-components",
        "react-components",
        "Build accessible React component libraries.",
        tags=["react"],
        languages=["TypeScript"],
        stars=320,
    ),
    _entry(
        "acme/skills:speckle-sync",
        "speckle-interop",
        "Exchange model data between AEC tools through Speckle.",
        tags=["speckle"],
        languages=["Python"],
        stars=31,
    ),
    _entry(
        "anthropics/skills:pdf",
        "pdf",
        "Read, create, and edit PDF files.",
        tags=["pdf"],
        stars=4800,
        repo="anthropics/skills",
    ),
]


def test_best_match_ranks_first_with_evidence() -> None:
    result = discover(PROFILE, CATALOG)
    top = result.candidates[0]
    assert top.entry.id == "ddc/skills:ifc/quantity-takeoff"
    assert "language:python" in top.matched
    assert "task:ifc" in top.matched
    assert "task:takeoff" in top.matched
    assert result.considered == 5


def test_zero_score_entries_are_excluded() -> None:
    result = discover(PROFILE, CATALOG)
    ids = [candidate.entry.id for candidate in result.candidates]
    assert "acme/skills:react-components" not in ids
    assert "anthropics/skills:pdf" not in ids


def test_blocked_entries_never_surface() -> None:
    result = discover(PROFILE, CATALOG, blocked={"hash-qto"})
    ids = [candidate.entry.id for candidate in result.candidates]
    assert "ddc/skills:ifc/quantity-takeoff" not in ids
    assert result.excluded_blocked == 1


def test_limit_caps_the_shortlist() -> None:
    result = discover(PROFILE, CATALOG, limit=1)
    assert len(result.candidates) == 1
    assert result.considered == 5


def test_deterministic_output() -> None:
    first = discover(PROFILE, CATALOG)
    second = discover(PROFILE, CATALOG)
    assert first.model_dump() == second.model_dump()


def test_repeated_tokens_do_not_inflate_score() -> None:
    stuffed = _entry(
        "x/skills:stuffed",
        "ifc-ifc-ifc",
        "ifc ifc ifc ifc takeoff takeoff quantity quantity",
        languages=["Python"],
    )
    plain = _entry(
        "x/skills:plain",
        "ifc-helper",
        "Quantity takeoff for ifc.",
        languages=["Python"],
    )
    result = discover(PROFILE, [stuffed, plain])
    scores = {candidate.entry.id: candidate.score for candidate in result.candidates}
    assert scores["x/skills:stuffed"] == scores["x/skills:plain"]


def test_specific_match_outranks_broad_grabbag() -> None:
    profile = UseCaseProfile(
        summary="write python for a grasshopper script component",
        languages=["Python"],
        frameworks=["Grasshopper"],
        tasks=["python in a grasshopper script component"],
    )
    precise = _entry(
        "r/p:precise", "rhino-skill", "Write Python for a Grasshopper script component."
    )
    broad = _entry(
        "r/b:broad",
        "everything",
        "Grasshopper grasshopper parametric design optimization analysis simulation grasshopper.",
    )
    # Both mention grasshopper (common); only `precise` mentions python/script/component (rare).
    result = discover(profile, [precise, broad])
    assert next(c.entry.id for c in result.candidates) == "r/p:precise"


def test_ties_break_by_stars_then_name() -> None:
    low = _entry("x/skills:b-low", "b-ifc", "ifc", stars=1)
    high = _entry("x/skills:a-high", "a-ifc", "ifc", stars=50)
    unstarred = _entry("x/skills:c-none", "c-ifc", "ifc")
    result = discover(PROFILE, [low, unstarred, high])
    assert [c.entry.id for c in result.candidates] == [
        "x/skills:a-high",
        "x/skills:b-low",
        "x/skills:c-none",
    ]
