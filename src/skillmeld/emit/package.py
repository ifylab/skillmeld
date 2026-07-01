# SPDX-License-Identifier: Apache-2.0
"""Package a merged set for each surface: Claude Code plugin tree, claude.ai zip, API payload.

The emitted ``SKILL.md`` is frontmatter plus the byte-traceable body; the body is never
rewritten here. ``PROVENANCE.md`` rides alongside as the trust artifact. Cross-surface sync
does not exist upstream, so each surface is emitted explicitly.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import yaml

from skillmeld.emit.provenance import build_provenance
from skillmeld.merge.pipeline import support_references
from skillmeld.merge.synthesize import slug
from skillmeld.models import (
    API_DESCRIPTION_LIMIT,
    CLAUDE_CODE_ROUTING_LIMIT,
    RESERVED_MARKETPLACE_NAMES,
    AssembledSkill,
    MergeResult,
    SkillDoc,
)


def render_skill_md(doc: SkillDoc) -> str:
    """Render a SKILL.md: frontmatter then the verbatim body (never rewritten here).

    Frontmatter order is fixed: name, description, license, then the carried source fields
    (compatibility, allowed-tools, disallowed-tools, disable-model-invocation, metadata).
    """
    name = str(doc.frontmatter.get("name", doc.source.name))
    description = str(doc.frontmatter.get("description", ""))
    license_id = str(doc.frontmatter.get("license", "")).strip()
    front = f"---\nname: {name}\n"
    if description:
        front += f"description: {description}\n"
    if license_id:
        front += f"license: {license_id}\n"
    front += _render_carried(doc.frontmatter)
    front += "---\n"
    body = doc.body if doc.body.startswith("\n") else "\n" + doc.body
    return front + body


def _render_carried(frontmatter: dict[str, object]) -> str:
    """Render the carried frontmatter fields in a fixed order, deterministically."""
    lines: list[str] = []
    for field in ("compatibility", "allowed-tools", "disallowed-tools"):
        value = str(frontmatter.get(field, "")).strip()
        if value:
            lines.append(f"{field}: {value}")
    if frontmatter.get("disable-model-invocation") is True:
        lines.append("disable-model-invocation: true")
    metadata = frontmatter.get("metadata")
    if isinstance(metadata, dict) and metadata:
        block = yaml.safe_dump(metadata, default_flow_style=False, sort_keys=True).rstrip("\n")
        lines.append("metadata:\n" + "\n".join(f"  {line}" for line in block.splitlines()))
    return "".join(f"{line}\n" for line in lines)


def _carried_present(value: object) -> bool:
    return value is True or (isinstance(value, str) and bool(value.strip()))


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


def marketplace_name_blocker(name: str) -> str | None:
    """Reason a marketplace name must be refused, or None if usable.

    The caller normalizes the name to kebab-case first (via ``slug``); the remaining hard rule is
    the reserved-name list — official Anthropic namespaces Claude Code refuses to add.
    """
    if name in RESERVED_MARKETPLACE_NAMES:
        return f"marketplace name '{name}' is reserved for official use"
    return None


def default_plugin_name(result: MergeResult) -> str:
    """A meaningful default name for the marketplace plugin entry.

    With an orchestrator present the primary skill's name is the generic ``orchestrator`` routing
    label, which makes a poor plugin name; name the plugin after the composed child skills instead
    (their slugs joined). A single-skill set keeps that skill's own name.
    """
    if result.orchestrator is not None and result.skills:
        return "-".join(
            slug(str(skill.doc.frontmatter.get("name", skill.doc.source.name)))
            for skill in result.skills
        )
    primary = _emitted_skills(result)[0]
    return slug(str(primary.doc.frontmatter.get("name", primary.doc.source.name)))


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


def emit_marketplace(
    result: MergeResult,
    out_dir: Path,
    *,
    sources: list[SkillDoc],
    generated_at: str,
    marketplace_name: str,
    owner: dict[str, str],
    plugin_name: str | None = None,
    carry: dict[str, list[tuple[str, Path]]] | None = None,
) -> list[str]:
    """Write a Claude Code plugin marketplace (``strict: false``).

    Layout: ``skills/<name>/SKILL.md`` per skill, ``PROVENANCE.md`` at the root, and
    ``.claude-plugin/marketplace.json``. The entry is ``strict: false`` — it owns the whole
    definition — so **no** ``plugin.json`` is written: a component-declaring ``plugin.json``
    alongside a strict:false entry is a hard load conflict. PROVENANCE.md sits at the plugin root
    and is copied along with the skill when the plugin is installed.
    """
    carry = carry or {}
    written: list[str] = []
    skill_paths: list[str] = []
    for skill in _emitted_skills(result):
        name = slug(str(skill.doc.frontmatter.get("name", skill.doc.source.name)))
        target = out_dir / "skills" / name / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_skill_md(skill.doc), encoding="utf-8")
        written.append(str(target))
        skill_paths.append(f"./skills/{name}")
        for rel, source_file in carry.get(name, []):
            dest = out_dir / "skills" / name / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(source_file.read_bytes())
            written.append(str(dest))

    provenance = out_dir / "PROVENANCE.md"
    provenance.write_text(
        build_provenance(result, sources, generated_at=generated_at), encoding="utf-8"
    )
    written.append(str(provenance))

    manifest = _marketplace_manifest(
        result,
        marketplace_name=marketplace_name,
        owner=owner,
        plugin_name=plugin_name,
        skill_paths=skill_paths,
    )
    manifest_path = out_dir / ".claude-plugin" / "marketplace.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    written.append(str(manifest_path))
    return sorted(written)


def _marketplace_manifest(
    result: MergeResult,
    *,
    marketplace_name: str,
    owner: dict[str, str],
    plugin_name: str | None,
    skill_paths: list[str],
) -> dict[str, object]:
    """Build the marketplace.json dict: one ``strict: false`` plugin exposing every emitted skill.

    Plugin name/description come from the primary skill (the orchestrator if present, else the sole
    skill). ``license`` is the engine's combined resolution (``plan.license_resolution``) — one
    unlicensed source resolves the whole set to unknown, so the field is omitted rather than
    asserting a license the set does not cleanly carry. ``version`` is omitted so the git commit
    SHA drives updates once the user hosts the marketplace.
    """
    primary = _emitted_skills(result)[0]
    name = plugin_name or default_plugin_name(result)
    entry: dict[str, object] = {
        "name": name,
        "source": "./",
        "strict": False,
        "skills": skill_paths,
    }
    description = str(primary.doc.frontmatter.get("description", "")).strip()
    if description:
        entry["description"] = description
    license_id = result.plan.license_resolution.spdx_id
    if license_id:
        entry["license"] = license_id
    return {
        "name": marketplace_name,
        "owner": owner,
        "description": "Composed by skillmeld from existing community skills.",
        "plugins": [entry],
    }


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


def api_surface_warnings(result: MergeResult) -> list[str]:
    """Claude-Code-only frontmatter the API ``/v1/skills`` surface does not enforce.

    The fields stay carried verbatim in the uploaded SKILL.md content, but the API ignores tool
    and invocation frontmatter, so a tool-restricted or non-invocable composed skill is not
    constrained there. Surface it so the gap is a known trade-off, not silent.
    """
    warnings: list[str] = []
    for skill in _emitted_skills(result):
        name = str(skill.doc.frontmatter.get("name", skill.doc.source.name))
        carried = [
            field
            for field in ("allowed-tools", "disallowed-tools", "disable-model-invocation")
            if _carried_present(skill.doc.frontmatter.get(field))
        ]
        if carried:
            warnings.append(
                f"{name}: {', '.join(carried)} carried in SKILL.md but the API surface does not "
                "enforce tool or invocation frontmatter"
            )
    return warnings


def routing_truncation_warnings(result: MergeResult) -> list[str]:
    """Descriptions over the Claude Code routing cap, which get truncated in the skill listing.

    Claude Code shows ``description`` + ``when_to_use`` combined and truncates past
    ``maxSkillDescriptionChars`` (1536). skillmeld emits no ``when_to_use``, so the description
    alone is budgeted; past the cap Claude drops the tail — the keywords that make the skill
    trigger — with no error. The Claude Code tree and the claude.ai zip both feed that listing, so
    surface it before install rather than let routing signal vanish silently.
    """
    warnings: list[str] = []
    for skill in _emitted_skills(result):
        name = str(skill.doc.frontmatter.get("name", skill.doc.source.name))
        chars = len(str(skill.doc.frontmatter.get("description", "")))
        if chars > CLAUDE_CODE_ROUTING_LIMIT:
            warnings.append(
                f"{name}: description is {chars} chars; Claude Code truncates the routing text at "
                f"{CLAUDE_CODE_ROUTING_LIMIT} (maxSkillDescriptionChars), dropping the last "
                f"{chars - CLAUDE_CODE_ROUTING_LIMIT} — lead with the key use case to keep it"
            )
    return warnings


def api_description_warnings(result: MergeResult) -> list[str]:
    """Descriptions the API ``/v1/skills`` surface rejects (``description`` max 1024 chars)."""
    warnings: list[str] = []
    for skill in _emitted_skills(result):
        name = str(skill.doc.frontmatter.get("name", skill.doc.source.name))
        chars = len(str(skill.doc.frontmatter.get("description", "")))
        if chars > API_DESCRIPTION_LIMIT:
            warnings.append(
                f"{name}: description is {chars} chars; the API /v1/skills surface rejects "
                f"descriptions over {API_DESCRIPTION_LIMIT} chars"
            )
    return warnings
