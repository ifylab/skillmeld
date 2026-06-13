# SPDX-License-Identifier: Apache-2.0
"""Step 2 — collapse exact-duplicate atoms by their normalized key.

Only exact (normalized) duplicates collapse here; semantic near-duplicates are the host
Claude's judgment in step 3 ("candidates, never auto-drop"). There is no cosine/embedding
step — embeddings are off by default, so a similarity threshold here would be dead code or a
determinism break. The survivor is one concrete atom's verbatim bytes, never the normalized
key, so byte-traceability is preserved.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from skillmeld.models import Atom


class DedupeResult(BaseModel):
    survivors: list[Atom] = Field(default_factory=list)
    collapsed: dict[str, str] = Field(default_factory=dict)


def collapse(atoms: list[Atom]) -> DedupeResult:
    """Group atoms by ``norm_key``; keep one deterministic survivor per group.

    ``collapsed`` maps each dropped atom id to the survivor it folded into. Order is stable:
    survivors sort by skill name, then source order, then id.
    """
    groups: dict[str, list[Atom]] = {}
    for atom in atoms:
        groups.setdefault(atom.norm_key, []).append(atom)

    survivors: list[Atom] = []
    collapsed: dict[str, str] = {}
    for members in groups.values():
        ordered = sorted(members, key=_precedence)
        survivor = ordered[0]
        survivors.append(survivor)
        for other in ordered[1:]:
            collapsed[other.id] = survivor.id
    survivors.sort(key=_precedence)
    return DedupeResult(survivors=survivors, collapsed=collapsed)


def _precedence(atom: Atom) -> tuple[str, int, str]:
    return (atom.skill, atom.source_order, atom.id)
