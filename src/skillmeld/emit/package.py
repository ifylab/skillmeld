# SPDX-License-Identifier: Apache-2.0
"""Package a merged set for each surface: Claude Code plugin tree, claude.ai zip, API payload.

The emitted ``SKILL.md`` is frontmatter plus the byte-traceable body; the body is never
rewritten here. ``PROVENANCE.md`` rides alongside as the trust artifact. Cross-surface sync
does not exist upstream, so each surface is emitted explicitly.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from skillmeld.emit.provenance import build_provenance
from skillmeld.merge.pipeline import support_references
from skillmeld.merge.synthesize import slug
from skillmeld.models import AssembledSkill, MergeResult, SkillDoc


def render_skill_md(doc: SkillDoc) -> str:
    """Render a SKILL.md: frontmatter (name, optional description, license) then verbatim body."""
    name = str(doc.frontmatter.get("name", doc.source.name))
    description = str(doc.frontmatter.get("description", ""))
    license_id = str(doc.frontmatter.get("license", "")).strip()
    front = f"---\nname: {name}\n"
    if description:
        front += f"description: {description}\n"
    if license_id:
        front += f"license: {license_id}\n"
    front += "---\n"
    body = doc.body if doc.body.startswith("\n") else "\n" + doc.body
    return front + body


def apply_source_licenses(result: MergeResult, sources: list[SkillDoc]) -> None:
    """Stamp each child's frontmatter with its source SPDX (matched by name).

    Emit writes no LICENSE file, so re-scanning an emitted skill would otherwise read license
    unknown even for a known source. Carrying the SPDX in frontmatter keeps the Stop-2 re-scan
    honest. Unlicensed sources are left blank, so they still surface for a license decision.
    """
    by_name = {slug(str(source.source.name)): source.source.license.spdx_id for source in sources}
    for skill in result.skills:
        name = slug(str(skill.doc.frontmatter.get("name", skill.doc.source.name)))
        spdx = by_name.get(name)
        if spdx:
            skill.doc.frontmatter["license"] = spdx


def _emitted_skills(result: MergeResult) -> list[AssembledSkill]:
    skills = list(result.skills)
    if result.orchestrator is not None:
        skills = [result.orchestrator, *skills]
    return skills


def emit_blockers(result: MergeResult) -> list[str]:
    """Reasons the set must not be packaged. Empty means emittable.

    The hard backstop against shipping a dead skill: every emitted skill — children and the
    orchestrator — must carry a non-empty description, or it never triggers once installed. This
    holds even if the eval loop was skipped, so the install gate cannot be bypassed by omission.
    """
    blockers: list[str] = []
    for skill in _emitted_skills(result):
        name = str(skill.doc.frontmatter.get("name", skill.doc.source.name))
        if not str(skill.doc.frontmatter.get("description", "")).strip():
            blockers.append(f"{name}: description is empty")
    return blockers


def plan_support_carry(
    result: MergeResult, sources: list[SkillDoc], bundle_dirs: list[str]
) -> dict[str, list[tuple[str, Path]]]:
    """Per child, the support files its body references that resolve to a real source file.

    A child maps to its source bundle by slugged name; only files the body points to, that exist
    under that bundle and do not escape it (no traversal), are carried. Anything unresolved stays
    a merge warning rather than shipping a dead link or an off-bundle file.
    """
    by_name = {
        slug(str(source.source.name)): Path(bundle).resolve()
        for source, bundle in zip(sources, bundle_dirs, strict=True)
    }
    carry: dict[str, list[tuple[str, Path]]] = {}
    for skill in result.skills:
        name = slug(str(skill.doc.frontmatter.get("name", skill.doc.source.name)))
        bundle = by_name.get(name)
        if bundle is None:
            continue
        files = [
            (ref, resolved)
            for ref in support_references(skill.doc.body)
            if (resolved := (bundle / ref).resolve()).is_file() and bundle in resolved.parents
        ]
        if files:
            carry[name] = files
    return carry


def emit_claude_code(
    result: MergeResult,
    out_dir: Path,
    *,
    sources: list[SkillDoc],
    generated_at: str,
    carry: dict[str, list[tuple[str, Path]]] | None = None,
) -> list[str]:
    """Write a Claude Code skills tree: ``<out>/<name>/SKILL.md`` per skill + PROVENANCE.md."""
    carry = carry or {}
    written: list[str] = []
    for skill in _emitted_skills(result):
        name = slug(str(skill.doc.frontmatter.get("name", skill.doc.source.name)))
        target = out_dir / name / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_skill_md(skill.doc), encoding="utf-8")
        written.append(str(target))
        for rel, source_file in carry.get(name, []):
            dest = out_dir / name / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(source_file.read_bytes())
            written.append(str(dest))
    provenance = out_dir / "PROVENANCE.md"
    provenance.write_text(
        build_provenance(result, sources, generated_at=generated_at), encoding="utf-8"
    )
    written.append(str(provenance))
    return sorted(written)


def emit_claudeai_zip(
    result: MergeResult,
    *,
    sources: list[SkillDoc],
    generated_at: str,
    carry: dict[str, list[tuple[str, Path]]] | None = None,
) -> bytes:
    """Build a claude.ai-style zip: each skill under ``skills/<name>/`` plus PROVENANCE.md."""
    carry = carry or {}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for skill in _emitted_skills(result):
            name = slug(str(skill.doc.frontmatter.get("name", skill.doc.source.name)))
            archive.writestr(f"skills/{name}/SKILL.md", render_skill_md(skill.doc))
            for rel, source_file in carry.get(name, []):
                archive.writestr(f"skills/{name}/{rel}", source_file.read_bytes())
        archive.writestr(
            "PROVENANCE.md", build_provenance(result, sources, generated_at=generated_at)
        )
    return buffer.getvalue()


def emit_api_payload(result: MergeResult) -> list[dict[str, str]]:
    """Build per-skill payloads for the API ``/v1/skills`` upload surface."""
    payloads: list[dict[str, str]] = []
    for skill in _emitted_skills(result):
        name = str(skill.doc.frontmatter.get("name", skill.doc.source.name))
        payloads.append(
            {
                "name": slug(name),
                "display_name": name,
                "description": str(skill.doc.frontmatter.get("description", "")),
                "content": render_skill_md(skill.doc),
            }
        )
    return payloads
