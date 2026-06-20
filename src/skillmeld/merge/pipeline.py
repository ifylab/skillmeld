# SPDX-License-Identifier: Apache-2.0
"""Drive the eight merge steps end to end. Pure and deterministic given its inputs.

The host Claude's judgment enters as optional data — a grouping map and an adjudication list.
Absent them, a deterministic default grouping runs (useful for tests and the dry path). The
result is always verified before it is returned; a non-empty problem list means reject.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from skillmeld.merge.conflicts import detect_structural, merge_adjudication
from skillmeld.merge.dedupe import collapse
from skillmeld.merge.frontmatter import carry_frontmatter
from skillmeld.merge.group import Assignment, default_grouping, validate_grouping
from skillmeld.merge.parse import parse_skill
from skillmeld.merge.partition import partition
from skillmeld.merge.prune import build_edges, prune_and_close
from skillmeld.merge.reconcile import reconcile
from skillmeld.merge.synthesize import assemble
from skillmeld.merge.verify import verify
from skillmeld.models import Conflict, MergePlan, MergeResult, SkillDoc, SkillSource, UseCaseProfile
from skillmeld.security.license import combine
from skillmeld.security.scan import verdict_from


class MergeRun(BaseModel):
    result: MergeResult
    problems: list[str] = Field(default_factory=list)


def run_merge(
    sources: list[SkillDoc],
    profile: UseCaseProfile,
    *,
    assignments: dict[str, Assignment] | None = None,
    adjudication: list[Conflict] | None = None,
    source_rank: dict[str, int] | None = None,
) -> MergeRun:
    """Run parse -> ... -> verify. Returns the merged result plus any verifier problems."""
    atoms = [atom for source in sources for atom in parse_skill(source)]
    deduped = collapse(atoms)
    survivors = deduped.survivors

    grouping = (
        validate_grouping(survivors, assignments)
        if assignments is not None
        else default_grouping(survivors)
    )
    structural = detect_structural(survivors, grouping.kinds)
    conflicts = merge_adjudication(structural, adjudication or [])
    reconciled = reconcile(conflicts, survivors, source_rank=source_rank)
    losers = set(reconciled.losers)

    edges = build_edges(survivors)
    pruned = prune_and_close(survivors, profile, edges=edges, losers=losers)
    part = partition(pruned.kept, grouping.groups, conflicts=conflicts)

    atoms_by_id = {atom.id: atom for atom in survivors}
    plan = MergePlan(
        kept=pruned.kept,
        dropped=pruned.dropped,
        drop_reasons=pruned.drop_reasons,
        deduped=sorted(deduped.collapsed),
        conflicts_resolved=reconciled.resolved,
        license_resolution=combine([source.source.license for source in sources])[0],
        warnings=[*pruned.warnings, *part.warnings, *reconciled_unresolved(reconciled.unresolved)],
    )
    result = assemble(part, atoms_by_id, kinds=grouping.kinds, plan=plan)
    carry = carry_frontmatter(result, sources)
    plan.frontmatter_findings = carry.findings
    plan.frontmatter_verdict = verdict_from(carry.findings)
    plan.warnings.extend(carry.notes)
    plan.warnings.extend(_dangling_reference_warnings(result))
    return MergeRun(result=result, problems=verify(result, sources))


# Support files a skill keeps beside its SKILL.md (references/, resources/, ...). Emit carries
# only the ones a body actually points to; any it cannot resolve to a source file stays a warning.
_SUPPORT_REF = re.compile(r"(?<![\w./-])(?:references|resources|assets|scripts|examples)/[\w./-]+")


def support_references(body: str) -> list[str]:
    """Relative support-file paths a body points to, in order, deduplicated."""
    seen: list[str] = []
    for ref in _SUPPORT_REF.findall(body):
        if ref not in seen:
            seen.append(ref)
    return seen


def _dangling_reference_warnings(result: MergeResult) -> list[str]:
    skills = [*result.skills, *([result.orchestrator] if result.orchestrator else [])]
    refs = {ref for skill in skills for ref in support_references(skill.doc.body)}
    return [
        f"body references support file {ref!r}; emit carries it when the source bundle has it"
        for ref in sorted(refs)
    ]


def reconciled_unresolved(unresolved: list[Conflict]) -> list[str]:
    return [
        f"unresolved conflict {c.atom_a}|{c.atom_b} ({c.type}) needs adjudication"
        for c in unresolved
    ]


def load_bundle(path: Path) -> SkillDoc:
    """Load a bundle's SKILL.md into a SkillDoc, splitting frontmatter without a YAML dep."""
    skill_md = path / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    name = str(frontmatter.get("name") or path.name)
    return SkillDoc(source=SkillSource(name=name), frontmatter=dict(frontmatter), body=body)


def _split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Split a SKILL.md into (frontmatter, body).

    Flat ``key: value`` lines are parsed leniently — tolerant of colons inside a value, the common
    case for descriptions. A ``key:`` with no inline value followed by indented lines is a nested
    block (e.g. ``metadata``) and is parsed with ``yaml.safe_load`` — never ``yaml.load``, since
    sources are untrusted community content. A malformed or non-collection block degrades to an
    empty value rather than raising or leaking sub-keys to the top level.
    """
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    body = text[end + 5 :]
    lines = text[4:end].splitlines()
    frontmatter: dict[str, object] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#") or line[:1].isspace():
            i += 1
            continue
        key, sep, value = line.partition(":")
        key = key.strip()
        if not sep or not key:
            i += 1
            continue
        value = value.strip()
        if value:
            frontmatter[key] = value.strip("\"'")
            i += 1
            continue
        block: list[str] = []
        j = i + 1
        while j < len(lines) and (not lines[j].strip() or lines[j][:1].isspace()):
            block.append(lines[j])
            j += 1
        nested = _parse_nested_block(block)
        frontmatter[key] = nested if nested is not None else ""
        i = j
    return frontmatter, body


def _parse_nested_block(block: list[str]) -> object | None:
    """Parse an indented YAML sub-block to a dict/list, or None if it holds nothing usable."""
    if not any(line.strip() for line in block):
        return None
    try:
        loaded = yaml.safe_load(textwrap.dedent("\n".join(block)))
    except yaml.YAMLError:
        return None
    return loaded if isinstance(loaded, (dict, list)) else None
