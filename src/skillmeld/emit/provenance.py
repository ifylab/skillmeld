# SPDX-License-Identifier: Apache-2.0
"""Build the PROVENANCE.md sidecar: per-part attribution, licenses, and the merge summary.

Provenance is first-class because skillmeld merges untrusted skills: the sidecar is the durable
trust-and-learning artifact, recording which source every emitted atom came from, the licenses
in play, and what was deduped or dropped and why.
"""

from __future__ import annotations

from skillmeld.models import AssembledAtom, MergeResult, SkillDoc


def build_provenance(result: MergeResult, sources: list[SkillDoc], *, generated_at: str) -> str:
    """Render PROVENANCE.md for a merged set. Deterministic given its inputs."""
    lines = [
        "# Provenance",
        "",
        f"Generated {generated_at}. Composed by skillmeld from existing community skills; "
        "every instruction in the output traces byte-for-byte to one of the sources below.",
        "",
        "## Sources",
        "",
    ]
    for source in sorted(sources, key=lambda s: s.source.name):
        license_id = source.source.license.spdx_id or "license unknown"
        location = source.source.url or source.source.repo or "local"
        lines.append(f"- **{source.source.name}** ({license_id}) — {location}")

    lines += ["", "## Composition", "", f"Emitted {len(result.skills)} skill(s):", ""]
    for skill in result.skills:
        name = str(skill.doc.frontmatter.get("name", skill.doc.source.name))
        contributors = _contributors(skill.layout)
        lines.append(f"- **{name}** — atoms from: {', '.join(contributors) or 'n/a'}")
    if result.orchestrator is not None:
        lines.append("- **orchestrator** — routing only (generated from frozen templates)")

    carried = _frontmatter_section(result)
    if carried:
        lines += ["", "## Frontmatter carried", "", *carried]

    plan = result.plan
    lines += [
        "",
        "## What changed",
        "",
        f"- Deduplicated: {len(plan.deduped)} duplicate atom(s) collapsed to one copy.",
        f"- Dropped: {len(plan.dropped)} atom(s) ({_drop_summary(plan.drop_reasons)}).",
        f"- Conflicts resolved: {len(plan.conflicts_resolved)}.",
    ]
    if plan.warnings:
        lines += ["", "## Warnings", ""]
        lines += [f"- {warning}" for warning in plan.warnings]
    return "\n".join(lines) + "\n"


def _frontmatter_section(result: MergeResult) -> list[str]:
    """Per-child carried frontmatter plus any review reasons the merge raised. Deterministic."""
    fields = ("allowed-tools", "disallowed-tools", "disable-model-invocation", "compatibility")
    lines: list[str] = []
    for skill in result.skills:
        name = str(skill.doc.frontmatter.get("name", skill.doc.source.name))
        parts: list[str] = []
        for field in fields:
            value = skill.doc.frontmatter.get(field)
            if value is True:
                parts.append(f"{field}: true")
            elif isinstance(value, str) and value.strip():
                parts.append(f"{field}: {value.strip()}")
        metadata = skill.doc.frontmatter.get("metadata")
        if isinstance(metadata, dict) and metadata:
            parts.append(f"metadata: {', '.join(sorted(str(key) for key in metadata))}")
        if parts:
            lines.append(f"- **{name}** — {'; '.join(parts)}")
    for finding in result.plan.frontmatter_findings:
        lines.append(f"- _review_: {finding.message}")
    return lines


def _contributors(layout: list[AssembledAtom]) -> list[str]:
    names: list[str] = []
    for item in layout:
        if item.role == "source" and item.atom_id:
            skill = item.atom_id.split(":", 1)[0]
            if skill not in names:
                names.append(skill)
    return sorted(names)


def _drop_summary(reasons: dict[str, str]) -> str:
    counts: dict[str, int] = {}
    for reason in reasons.values():
        counts[reason] = counts.get(reason, 0) + 1
    if not counts:
        return "none"
    return ", ".join(f"{count} {reason}" for reason, count in sorted(counts.items()))
