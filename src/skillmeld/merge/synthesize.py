# SPDX-License-Identifier: Apache-2.0
"""Step 7 (assembly) — render emitted skills strictly from a declared atom layout.

A body is built only from its layout: verbatim source atoms plus closed-vocabulary scaffold
tokens (a separator, the orchestrator's frozen routing templates). Nothing is free-authored,
so the verifier can reconstruct every byte. Each child's ``description`` is left empty on
purpose — authoring it is the eval loop's job (W6), behind its own guardrails. The orchestrator
gets a frozen-template description here (it routes, it does not act), so the router always has a
trigger surface; the eval loop may still refine it.
"""

from __future__ import annotations

import re

from skillmeld.merge.partition import Cluster, Partition, routes_for
from skillmeld.models import (
    AssembledAtom,
    AssembledSkill,
    Atom,
    AtomKind,
    MergePlan,
    MergeResult,
    RouteEntry,
    SkillDoc,
    SkillSource,
)

# Closed scaffold vocabulary. Slots bind only to structured values, never free prose.
SEP = "\n"
ORCH_INTRO = "# Orchestrator\n\nRoute each request to the matching skill.\n\n"
ROUTE_TEMPLATE = "- When the task involves {label}, use the {skill_name} skill.\n"
SCAFFOLD_TEXT = {"sep": SEP, "orch-intro": ORCH_INTRO}

# The orchestrator's description is a frozen template filled only with the route labels — the
# same closed-vocabulary discipline as its body. It gives the router a non-empty trigger surface
# by default (a skill with no description never fires in Claude Code); the eval loop may refine it.
ORCH_DESCRIPTION = (
    "Coordinates a set of related skills and routes each request to the matching one. "
    "Use when a task may involve: {labels}."
)


def slug(label: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return cleaned[:64] or "skill"


def render_body(layout: list[AssembledAtom], atoms_by_id: dict[str, Atom]) -> str:
    """Reconstruct a skill body from its layout. The single render path; verify reuses it."""
    parts: list[str] = []
    for item in layout:
        if item.role == "source":
            parts.append(atoms_by_id[item.atom_id].text)
        else:
            parts.append(SCAFFOLD_TEXT[item.template_id or ""])
    return "".join(parts)


def render_orchestrator(routes: list[RouteEntry]) -> str:
    """Reconstruct the orchestrator body from its routing table and frozen templates only."""
    body = ORCH_INTRO
    for route in routes:
        body += ROUTE_TEMPLATE.format(label=route.label, skill_name=route.skill_name)
    return body


def assemble(
    partition_result: Partition,
    atoms_by_id: dict[str, Atom],
    *,
    kinds: dict[str, AtomKind] | None = None,
    plan: MergePlan | None = None,
) -> MergeResult:
    """Build the merge result: one assembled skill per cluster, plus an orchestrator if >1."""
    kind_map = kinds or {}
    skills = [
        _assemble_skill(cluster, atoms_by_id, kind_map) for cluster in partition_result.clusters
    ]

    orchestrator: AssembledSkill | None = None
    routing_table: list[RouteEntry] = []
    if len(skills) > 1:
        for label, skill_label in routes_for(partition_result):
            routing_table.append(
                RouteEntry(template_id="route", label=label, skill_name=slug(skill_label))
            )
        orchestrator = _assemble_orchestrator(routing_table)

    return MergeResult(
        skills=skills,
        orchestrator=orchestrator,
        routing_table=routing_table,
        plan=plan or MergePlan(),
    )


def _assemble_skill(
    cluster: Cluster, atoms_by_id: dict[str, Atom], kinds: dict[str, AtomKind]
) -> AssembledSkill:
    ordered = sorted(
        (a for a in cluster.atom_ids if a in atoms_by_id),
        key=lambda i: (atoms_by_id[i].skill, atoms_by_id[i].source_order, i),
    )
    layout: list[AssembledAtom] = []
    for position, atom_id in enumerate(ordered):
        if position > 0:
            layout.append(AssembledAtom(atom_id="", role="scaffold", template_id="sep"))
        layout.append(
            AssembledAtom(
                atom_id=atom_id,
                kind=kinds.get(atom_id, atoms_by_id[atom_id].detected_kind),
                role="source",
            )
        )
    body = render_body(layout, atoms_by_id)
    name = slug(cluster.label)
    doc = SkillDoc(
        source=SkillSource(name=name),
        frontmatter={"name": name, "description": ""},
        body=body,
    )
    return AssembledSkill(doc=doc, layout=layout)


def _assemble_orchestrator(routes: list[RouteEntry]) -> AssembledSkill:
    body = render_orchestrator(routes)
    layout = [AssembledAtom(atom_id="", role="scaffold", template_id="orch-intro")]
    layout += [AssembledAtom(atom_id="", role="scaffold", template_id="route") for _ in routes]
    description = ORCH_DESCRIPTION.format(labels=", ".join(route.label for route in routes))
    doc = SkillDoc(
        source=SkillSource(name="orchestrator"),
        frontmatter={"name": "orchestrator", "description": description},
        body=body,
    )
    return AssembledSkill(doc=doc, layout=layout)
