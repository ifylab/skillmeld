# SPDX-License-Identifier: Apache-2.0
"""Step 8 — the safety net: reconstruct every output byte from a declared layout, exactly.

The verifier re-parses the source skills itself (never trusting data handed down the pipeline),
then asserts each emitted body equals ``render(layout)`` over those re-parsed atoms, byte for
byte. Fuzzy matching is forbidden — substring, normalized, or near matches would be the exact
loophole that lets invented text through. If reconstruction is byte-equal, every output byte is
provably either a verbatim source atom or a closed-vocabulary scaffold token.
"""

from __future__ import annotations

from skillmeld.merge.parse import parse_skill
from skillmeld.merge.synthesize import render_body, render_orchestrator
from skillmeld.models import Atom, MergeResult, SkillDoc

_SKILL_SCAFFOLDS = frozenset({"sep"})
_ORCH_SCAFFOLDS = frozenset({"orch-intro", "route"})


def verify(result: MergeResult, sources: list[SkillDoc]) -> list[str]:
    """Return problems (empty means OK). The merge is rejected on any non-empty result."""
    problems: list[str] = []
    source_index = _source_index(sources)

    # Body byte-traceability is what this verifier enforces. The description is authored by the
    # eval loop behind its own gates (leakage, structural quality, held-out non-regression) and
    # re-scanned at emit time, so it is deliberately out of scope here.
    seen: dict[str, int] = {}
    for index, skill in enumerate(result.skills):
        layout = skill.layout
        renderable = True
        for item in layout:
            if item.role == "source":
                if item.atom_id not in source_index:
                    problems.append(f"skill {index}: atom {item.atom_id!r} is not in any source")
                    renderable = False
                seen[item.atom_id] = seen.get(item.atom_id, 0) + 1
            elif item.template_id not in _SKILL_SCAFFOLDS:
                problems.append(f"skill {index}: unknown scaffold {item.template_id!r}")
                renderable = False
        if renderable and render_body(layout, source_index) != skill.doc.body:
            problems.append(f"skill {index}: body is not byte-reconstructible from its layout")

    problems.extend(_verify_orchestrator(result))
    problems.extend(_verify_partition_total(seen))
    problems.extend(_verify_drop_reasons(result))
    return problems


def _verify_orchestrator(result: MergeResult) -> list[str]:
    problems: list[str] = []
    expected_orchestrator = len(result.skills) > 1
    if expected_orchestrator and result.orchestrator is None:
        problems.append("orchestrator missing for a multi-skill output")
    if not expected_orchestrator and result.orchestrator is not None:
        problems.append("orchestrator emitted for a single-skill output")
    if result.orchestrator is not None:
        for item in result.orchestrator.layout:
            if item.role == "scaffold" and item.template_id not in _ORCH_SCAFFOLDS:
                problems.append(f"orchestrator: unknown scaffold {item.template_id!r}")
        reconstructed = render_orchestrator(result.routing_table)
        if reconstructed != result.orchestrator.doc.body:
            problems.append("orchestrator body is not reconstructible from the routing table")
    return problems


def _verify_partition_total(seen: dict[str, int]) -> list[str]:
    duplicated = sorted(atom_id for atom_id, count in seen.items() if count > 1)
    return [f"atom {atom_id!r} appears in more than one skill" for atom_id in duplicated]


def _verify_drop_reasons(result: MergeResult) -> list[str]:
    plan = result.plan
    missing = [atom_id for atom_id in plan.dropped if atom_id not in plan.drop_reasons]
    return [f"dropped atom {atom_id!r} has no recorded reason" for atom_id in sorted(missing)]


def _source_index(sources: list[SkillDoc]) -> dict[str, Atom]:
    index: dict[str, Atom] = {}
    for source in sources:
        for atom in parse_skill(source):
            index[atom.id] = atom
    return index
