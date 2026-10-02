# SPDX-License-Identifier: Apache-2.0
"""Package a merged set for each surface: spec-only skills tree (and its install fan-out), Agent
Plugins package, Claude Code tree, claude.ai zip, API payload, Claude Code plugin marketplace.

The emitted ``SKILL.md`` is frontmatter plus the byte-traceable body; the body is never
rewritten here, on any surface. ``PROVENANCE.md`` rides alongside as the trust artifact. Every
surface renders through the one ``render_skill_md``; the spec-only surfaces differ only in the
frontmatter fields they leave out. Cross-surface sync does not exist upstream, so each surface is
emitted explicitly.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

import yaml
from pydantic import BaseModel, Field

from skillmeld.emit.portability import PortabilityReport
from skillmeld.emit.provenance import build_provenance
from skillmeld.emit.sidecar import SIDECAR_PATH, write_codex_sidecar
from skillmeld.emit.targets import ResolvedTarget
from skillmeld.merge.pipeline import support_references
from skillmeld.merge.synthesize import slug
from skillmeld.models import (
    API_DESCRIPTION_LIMIT,
    CLAUDE_CODE_ROUTING_LIMIT,
    NAME_LIMIT,
    RESERVED_MARKETPLACE_NAMES,
    SPEC_NAME_RE,
    AssembledSkill,
    MergeResult,
    SkillDoc,
)

# Frontmatter the Agent Skills spec allows. Claude Code reads its own wider dialect and ignores
# unknown fields; a claude.ai upload, the Skills API and package_skill.py refuse anything else.
SPEC_ONLY_DROPPED = ("disallowed-tools", "disable-model-invocation")

# Agent Plugins 1.0.0 (https://agent-plugins.org/specification, read 2026-09-30): a root
# plugin.json with ``$schema`` and ``name`` required; skills are the immediate children of
# ``skills/`` that hold a SKILL.md; unknown ``extensions`` namespaces must be ignored by clients.
AGENT_PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
PLUGIN_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
SIDECAR_NOTE = (
    f"{SIDECAR_PATH} beside each skill: Codex UI metadata and invocation policy derived from the "
    "skill's name and description only, never body text; other agents ignore the directory"
)


class InstallConflict(Exception):
    """An install target already holds a skill directory the set would overwrite."""

    def __init__(self, paths: list[str]) -> None:
        self.paths = paths
        super().__init__("; ".join(paths))


class InstalledTarget(BaseModel):
    agents: list[str] = Field(default_factory=list)
    scope: str = "project"
    path: str = ""
    skills: list[str] = Field(default_factory=list)


class InstallReport(BaseModel):
    installed: list[InstalledTarget] = Field(default_factory=list)
    overwritten: list[str] = Field(default_factory=list)
    sidecars: list[str] = Field(default_factory=list)
    written: list[str] = Field(default_factory=list)


def render_skill_md(doc: SkillDoc, *, spec_only: bool = False) -> str:
    """Render a SKILL.md: frontmatter then the verbatim body (never rewritten here).

    Frontmatter order is fixed: name, description, license, then the carried source fields
    (compatibility, allowed-tools, disallowed-tools, disable-model-invocation, metadata).
    ``spec_only`` leaves out the two Claude-Code-only fields for the surfaces that refuse them.
    """
    name = str(doc.frontmatter.get("name", doc.source.name))
    description = str(doc.frontmatter.get("description", ""))
    license_id = str(doc.frontmatter.get("license", "")).strip()
    front = f"---\nname: {name}\n"
    if description:
        front += f"description: {description}\n"
    if license_id:
        front += f"license: {license_id}\n"
    front += _render_carried(doc.frontmatter, spec_only=spec_only)
    front += "---\n"
    body = doc.body if doc.body.startswith("\n") else "\n" + doc.body
    return front + body


def _render_carried(frontmatter: dict[str, object], *, spec_only: bool = False) -> str:
    """Render the carried frontmatter fields in a fixed order, deterministically."""
    lines: list[str] = []
    fields = (
        ("compatibility", "allowed-tools")
        if spec_only
        else (
            "compatibility",
            "allowed-tools",
            "disallowed-tools",
        )
    )
    for field in fields:
        value = str(frontmatter.get(field, "")).strip()
        if value:
            lines.append(f"{field}: {value}")
    if not spec_only and frontmatter.get("disable-model-invocation") is True:
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
    orchestrator — must carry a non-empty description, or it never triggers once installed, and a
    name an agent can load from the directory it is written under (the Agent Skills spec's name
    rules). Both hold even if the eval loop was skipped, so the install gate cannot be bypassed by
    omission.
    """
    blockers: list[str] = []
    for skill in _emitted_skills(result):
        name = str(skill.doc.frontmatter.get("name", skill.doc.source.name))
        if not str(skill.doc.frontmatter.get("description", "")).strip():
            blockers.append(f"{name}: description is empty")
        if len(name) > NAME_LIMIT or not SPEC_NAME_RE.match(name):
            blockers.append(
                f"{name}: name is not 1-{NAME_LIMIT} lowercase alphanumerics joined by single "
                "hyphens, so it cannot match the directory it would be written under"
            )
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
            # The reference is also the destination path under the emitted skill, so a `..`
            # segment that still resolves inside the source bundle (`references/../SKILL.md`)
            # must not be carried either: it would land on top of the rendered SKILL.md.
            if ".." not in PurePosixPath(ref).parts
            and (resolved := (bundle / ref).resolve()).is_file()
            and bundle in resolved.parents
        ]
        if files:
            carry[name] = files
    return carry


def _write_skill_tree(
    result: MergeResult,
    root: Path,
    *,
    spec_only: bool,
    sources: list[SkillDoc],
    generated_at: str,
    carry: dict[str, list[tuple[str, Path]]] | None,
    provenance: Path,
    portability: list[PortabilityReport] | None = None,
    sidecars: bool = False,
) -> tuple[list[str], list[str]]:
    """Write ``<root>/<name>/SKILL.md`` per skill, carried support files, optional Codex
    sidecars, and the provenance file. Returns (every path written, sidecar paths)."""
    carry = carry or {}
    written: list[str] = []
    sidecar_paths: list[str] = []
    for skill in _emitted_skills(result):
        name = slug(str(skill.doc.frontmatter.get("name", skill.doc.source.name)))
        skill_dir = root / name
        target = skill_dir / "SKILL.md"
        skill_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(render_skill_md(skill.doc, spec_only=spec_only), encoding="utf-8")
        written.append(str(target))
        for rel, source_file in carry.get(name, []):
            dest = skill_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(source_file.read_bytes())
            written.append(str(dest))
        if sidecars:
            sidecar = write_codex_sidecar(skill.doc, skill_dir)
            written.append(sidecar)
            sidecar_paths.append(sidecar)
    provenance.parent.mkdir(parents=True, exist_ok=True)
    provenance.write_text(
        build_provenance(
            result,
            sources,
            generated_at=generated_at,
            portability=portability,
            sidecars=[SIDECAR_NOTE] if sidecars else None,
        ),
        encoding="utf-8",
    )
    written.append(str(provenance))
    return sorted(written), sidecar_paths


def emit_claude_code(
    result: MergeResult,
    out_dir: Path,
    *,
    sources: list[SkillDoc],
    generated_at: str,
    carry: dict[str, list[tuple[str, Path]]] | None = None,
    portability: list[PortabilityReport] | None = None,
) -> list[str]:
    """Write a Claude Code skills tree: ``<out>/<name>/SKILL.md`` per skill + provenance.

    Keeps Claude Code's own frontmatter fields. The provenance file is named
    ``PROVENANCE-<set>.md`` because ``--out`` is commonly a shared skills directory holding
    earlier installs — a fixed name would silently clobber another composed set's provenance.
    """
    written, _ = _write_skill_tree(
        result,
        out_dir,
        spec_only=False,
        sources=sources,
        generated_at=generated_at,
        carry=carry,
        provenance=out_dir / f"PROVENANCE-{default_plugin_name(result)}.md",
        portability=portability,
    )
    return written


def emit_skills(
    result: MergeResult,
    out_dir: Path,
    *,
    sources: list[SkillDoc],
    generated_at: str,
    carry: dict[str, list[tuple[str, Path]]] | None = None,
    codex_sidecar: bool = False,
    portability: list[PortabilityReport] | None = None,
) -> tuple[list[str], list[str]]:
    """Write the spec-only skills tree: what every Agent Skills reader loads unchanged.

    Same layout as the Claude Code tree, rendered with the spec's six frontmatter fields only.
    Returns (every path written, Codex sidecar paths).
    """
    return _write_skill_tree(
        result,
        out_dir,
        spec_only=True,
        sources=sources,
        generated_at=generated_at,
        carry=carry,
        provenance=out_dir / f"PROVENANCE-{default_plugin_name(result)}.md",
        portability=portability,
        sidecars=codex_sidecar,
    )


def install_targets(
    result: MergeResult,
    *,
    targets: list[ResolvedTarget],
    project_root: Path,
    home: Path,
    sources: list[SkillDoc],
    generated_at: str,
    carry: dict[str, list[tuple[str, Path]]] | None = None,
    force: bool = False,
    codex_sidecar: bool = False,
    portability: list[PortabilityReport] | None = None,
) -> InstallReport:
    """Copy the set into each resolved target directory, rendered in that target's dialect.

    Pre-flights every target first: an existing skill directory (or a symlink by that name, which
    is how some installers place skills) stops the whole install (nothing written) unless
    ``force``, which removes exactly that entry, rewrites it, and reports it as overwritten. A
    symlink is unlinked, never followed, so nothing outside the target directory is touched.
    Copies, never symlinks. Codex sidecars go only into directories Codex reads.
    """
    names = [
        slug(str(skill.doc.frontmatter.get("name", skill.doc.source.name)))
        for skill in _emitted_skills(result)
    ]
    roots = [(target, _target_root(target, project_root, home)) for target in targets]
    existing = [str(root / name) for _, root in roots for name in names if _occupied(root / name)]
    if existing and not force:
        raise InstallConflict(existing)

    report = InstallReport(overwritten=existing)
    for target, root in roots:
        for name in names:
            _remove_entry(root / name)
        written, sidecars = _write_skill_tree(
            result,
            root,
            spec_only=target.spec_only,
            sources=sources,
            generated_at=generated_at,
            carry=carry,
            provenance=root / f"PROVENANCE-{default_plugin_name(result)}.md",
            portability=portability,
            sidecars=codex_sidecar and "codex" in target.agents,
        )
        report.installed.append(
            InstalledTarget(
                agents=list(target.agents), scope=target.scope, path=str(root), skills=names
            )
        )
        report.written.extend(written)
        report.sidecars.extend(sidecars)
    return report


def _target_root(target: ResolvedTarget, project_root: Path, home: Path) -> Path:
    if target.path_rel.startswith("~/"):
        return home / target.path_rel[2:]
    return project_root / target.path_rel


def _occupied(path: Path) -> bool:
    """Whether anything sits at ``path``: a directory, a file, or a symlink, dangling or not."""
    return path.is_symlink() or path.exists()


def _remove_entry(path: Path) -> None:
    """Remove the one entry a forced install replaces: unlink a link or file, remove a directory."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


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
            archive.writestr(f"skills/{name}/SKILL.md", render_skill_md(skill.doc, spec_only=True))
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
    version: str = "0.1.0",
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
    written, skill_paths = _write_plugin_tree(
        result,
        out_dir,
        spec_only=False,
        sources=sources,
        generated_at=generated_at,
        carry=carry,
    )

    manifest = _marketplace_manifest(
        result,
        marketplace_name=marketplace_name,
        owner=owner,
        version=version,
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


def _write_plugin_tree(
    result: MergeResult,
    out_dir: Path,
    *,
    spec_only: bool,
    sources: list[SkillDoc],
    generated_at: str,
    carry: dict[str, list[tuple[str, Path]]] | None,
    portability: list[PortabilityReport] | None = None,
    sidecars: bool = False,
) -> tuple[list[str], list[str]]:
    """Write ``skills/<name>/`` per skill plus a root ``PROVENANCE.md``: the plugin-shaped tree
    both the Claude marketplace and the Agent Plugins package are built on. Returns (every path
    written, ``./skills/<name>`` entries in emit order)."""
    written, _ = _write_skill_tree(
        result,
        out_dir / "skills",
        spec_only=spec_only,
        sources=sources,
        generated_at=generated_at,
        carry=carry,
        provenance=out_dir / "PROVENANCE.md",
        portability=portability,
        sidecars=sidecars,
    )
    skill_paths = [
        f"./skills/{slug(str(skill.doc.frontmatter.get('name', skill.doc.source.name)))}"
        for skill in _emitted_skills(result)
    ]
    return written, skill_paths


def emit_plugin(
    result: MergeResult,
    out_dir: Path,
    *,
    sources: list[SkillDoc],
    generated_at: str,
    plugin_name: str,
    version: str,
    owner: dict[str, str],
    carry: dict[str, list[tuple[str, Path]]] | None = None,
    codex_marketplace: str | None = None,
    codex_compat: bool = False,
    codex_sidecar: bool = False,
    portability: list[PortabilityReport] | None = None,
) -> list[str]:
    """Write an Agent Plugins 1.0.0 package: ``plugin.json``, ``skills/<name>/``, ``PROVENANCE.md``.

    The skills are rendered spec-only. ``codex_marketplace`` adds ``.agents/plugins/
    marketplace.json`` naming this directory as a local plugin, the shape Codex marketplaces
    read; ``codex_compat`` mirrors the manifest to ``.codex-plugin/plugin.json``. Never writes
    ``.claude-plugin/``; the Claude marketplace is its own surface.
    """
    if not PLUGIN_NAME_RE.match(plugin_name):
        raise ValueError(
            f"plugin name {plugin_name!r} must be 1-64 lowercase alphanumerics, hyphens or periods"
        )
    written, _ = _write_plugin_tree(
        result,
        out_dir,
        spec_only=True,
        sources=sources,
        generated_at=generated_at,
        carry=carry,
        portability=portability,
        sidecars=codex_sidecar,
    )
    manifest = _plugin_manifest(result, plugin_name=plugin_name, version=version, owner=owner)
    written.append(
        _write_json(out_dir / "plugin.json", {"$schema": AGENT_PLUGIN_SCHEMA, **manifest})
    )
    if codex_compat:
        written.append(_write_json(out_dir / ".codex-plugin" / "plugin.json", manifest))
    if codex_marketplace is not None:
        marketplace = {
            "name": codex_marketplace,
            "interface": {"displayName": codex_marketplace},
            "plugins": [{"name": plugin_name, "source": {"source": "local", "path": "./"}}],
        }
        written.append(
            _write_json(out_dir / ".agents" / "plugins" / "marketplace.json", marketplace)
        )
    return sorted(written)


def _plugin_manifest(
    result: MergeResult, *, plugin_name: str, version: str, owner: dict[str, str]
) -> dict[str, object]:
    primary = _emitted_skills(result)[0]
    manifest: dict[str, object] = {
        "name": plugin_name,
        "version": version,
        "description": str(primary.doc.frontmatter.get("description", "")).strip(),
        "author": owner,
        "keywords": ["agent-skills", "skillmeld"],
    }
    license_id = result.plan.license_resolution.spdx_id
    if license_id:
        manifest["license"] = license_id
    return manifest


def _write_json(path: Path, payload: Mapping[str, object]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)


def _marketplace_manifest(
    result: MergeResult,
    *,
    marketplace_name: str,
    owner: dict[str, str],
    version: str,
    plugin_name: str | None,
    skill_paths: list[str],
) -> dict[str, object]:
    """Build the marketplace.json dict: one ``strict: false`` plugin exposing every emitted skill.

    Plugin name/description come from the primary skill (the orchestrator if present, else the sole
    skill). ``license`` is the engine's combined resolution (``plan.license_resolution``) — one
    unlicensed source resolves the whole set to unknown, so the field is omitted rather than
    asserting a license the set does not cleanly carry. ``version`` lands on both the marketplace
    ``metadata`` and the plugin entry: ``claude plugin update`` compares version strings, so a
    re-composition must bump it to reach installed users.
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
    entry["version"] = version
    return {
        "name": marketplace_name,
        "owner": owner,
        "metadata": {
            "description": "Composed by skillmeld from existing community skills.",
            "version": version,
        },
        "plugins": [entry],
    }


API_SHARING_NOTE = (
    "a /v1/skills upload is workspace-wide: every member of the workspace can invoke the "
    "skill, not just the uploader"
)


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
                "content": render_skill_md(skill.doc, spec_only=True),
            }
        )
    return payloads


def api_surface_warnings(result: MergeResult) -> list[str]:
    """Frontmatter the claude.ai and ``/v1/skills`` surfaces drop or do not enforce.

    ``disallowed-tools`` and ``disable-model-invocation`` sit outside the Agent Skills spec, and
    an upload refuses a SKILL.md that carries them, so the spec-only render leaves them out;
    ``allowed-tools`` is in the spec and stays, but neither surface enforces tool frontmatter.
    A tool-restricted or non-invocable composed skill is therefore not constrained there — surface
    it so the gap is a known trade-off, not silent.
    """
    warnings: list[str] = []
    for skill in _emitted_skills(result):
        name = str(skill.doc.frontmatter.get("name", skill.doc.source.name))
        dropped = [f for f in SPEC_ONLY_DROPPED if _carried_present(skill.doc.frontmatter.get(f))]
        kept = _carried_present(skill.doc.frontmatter.get("allowed-tools"))
        if dropped:
            warnings.append(
                f"{name}: {', '.join(dropped)} left out of this surface (outside the Agent Skills "
                "spec, the upload refuses them; the claude-code and marketplace emits keep them) "
                "— the surface does not enforce tool or invocation frontmatter"
            )
        elif kept:
            warnings.append(
                f"{name}: allowed-tools carried in SKILL.md but the surface does not enforce "
                "tool frontmatter"
            )
    return warnings


def api_support_file_warnings(carry: dict[str, list[tuple[str, Path]]]) -> list[str]:
    """Support files the ``/v1/skills`` payload cannot carry (each entry is SKILL.md text only)."""
    warnings: list[str] = []
    for name, files in sorted(carry.items()):
        if files:
            rels = ", ".join(rel for rel, _ in files)
            warnings.append(
                f"{name}: support file(s) {rels} are not in the /v1/skills payload; "
                "upload them separately via the Files API"
            )
    return warnings


def routing_truncation_warnings(result: MergeResult) -> list[str]:
    """Descriptions over the Claude Code routing cap, which get truncated in the skill listing.

    Claude Code shows ``description`` + ``when_to_use`` combined and truncates past
    ``skillListingMaxDescChars`` (1536). skillmeld emits no ``when_to_use``, so the description
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
                f"{CLAUDE_CODE_ROUTING_LIMIT} (skillListingMaxDescChars), dropping the last "
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
