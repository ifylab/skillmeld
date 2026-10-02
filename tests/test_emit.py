# SPDX-License-Identifier: Apache-2.0
"""W7 emit tests: SKILL.md rendering, the three surfaces, and the provenance sidecar."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from skillmeld.emit.package import (
    default_plugin_name,
    emit_api_payload,
    emit_blockers,
    emit_claude_code,
    emit_claudeai_zip,
    emit_marketplace,
    marketplace_name_blocker,
    render_skill_md,
)
from skillmeld.emit.provenance import build_provenance
from skillmeld.merge.dedupe import collapse
from skillmeld.merge.group import default_grouping
from skillmeld.merge.parse import parse_skill
from skillmeld.merge.partition import partition
from skillmeld.merge.prune import prune_and_close
from skillmeld.merge.synthesize import assemble, slug
from skillmeld.models import LicenseInfo, MergeResult, SkillDoc, SkillSource, UseCaseProfile

SKILL_A = SkillDoc(
    source=SkillSource(
        name="retriever", url="https://github.com/x/retriever", license=LicenseInfo(spdx_id="MIT")
    ),
    body="# Retriever\n\nRetrieve documents.\n\n- Always validate the query first.\n",
)
SKILL_B = SkillDoc(
    source=SkillSource(
        name="reviewer",
        url="https://github.com/x/reviewer",
        license=LicenseInfo(spdx_id="Apache-2.0"),
    ),
    body=(
        "# Reviewer\n\nReview documents.\n\n"
        "- Always validate the query first.\n- Flag low-quality matches.\n"
    ),
)
PROFILE = UseCaseProfile(summary="Retrieve and review documents.", tasks=["retrieve", "review"])
WHEN = "2026-06-10T00:00:00+00:00"


def _merge() -> tuple[MergeResult, list[SkillDoc]]:
    sources = [SKILL_A, SKILL_B]
    survivors = collapse([a for s in sources for a in parse_skill(s)]).survivors
    grouping = default_grouping(survivors)
    pruned = prune_and_close(survivors, PROFILE)
    part = partition(pruned.kept, grouping.groups)
    result = assemble(part, {a.id: a for a in survivors}, kinds=grouping.kinds)
    return result, sources


def test_render_skill_md_has_frontmatter_and_body() -> None:
    doc = SkillDoc(
        source=SkillSource(name="x"),
        frontmatter={"name": "ifc-qto", "description": "Quantity takeoff."},
        body="# IFC\n\nDo the takeoff.\n",
    )
    text = render_skill_md(doc)
    assert text.startswith("---\nname: ifc-qto\ndescription: Quantity takeoff.\n---\n")
    assert "# IFC" in text


def test_emit_claude_code_writes_tree(tmp_path: Path) -> None:
    from skillmeld.emit.package import default_plugin_name

    result, sources = _merge()
    written = emit_claude_code(result, tmp_path, sources=sources, generated_at=WHEN)
    # Per-set provenance name: a second set emitted into the same shared skills dir must not
    # clobber this one's provenance.
    set_name = default_plugin_name(result)
    assert any(p.endswith(f"PROVENANCE-{set_name}.md") for p in written)
    assert not (tmp_path / "PROVENANCE.md").exists()
    skill_files = [p for p in written if p.endswith("SKILL.md")]
    assert skill_files
    for path in skill_files:
        assert Path(path).read_text().startswith("---\nname:")


def test_emit_claudeai_zip_is_valid() -> None:
    result, sources = _merge()
    data = emit_claudeai_zip(result, sources=sources, generated_at=WHEN)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        assert "PROVENANCE.md" in names
        assert any(n.startswith("skills/") and n.endswith("SKILL.md") for n in names)


def test_emit_api_payload_shape() -> None:
    result, _ = _merge()
    payloads = emit_api_payload(result)
    assert payloads
    for payload in payloads:
        assert set(payload) == {"name", "display_name", "description", "content"}
        assert payload["content"].startswith("---\nname:")


def test_provenance_lists_sources_and_changes() -> None:
    result, sources = _merge()
    text = build_provenance(result, sources, generated_at=WHEN)
    assert "## Sources" in text
    assert "retriever" in text and "reviewer" in text
    assert "MIT" in text and "Apache-2.0" in text
    assert "Deduplicated:" in text
    assert WHEN in text


def test_provenance_is_deterministic() -> None:
    result, sources = _merge()
    a = build_provenance(result, sources, generated_at=WHEN)
    b = build_provenance(result, sources, generated_at=WHEN)
    assert a == b


def test_render_includes_license_and_apply_stamps_from_source() -> None:
    from skillmeld.emit.package import apply_source_licenses
    from skillmeld.models import AssembledSkill

    doc = SkillDoc(
        source=SkillSource(name="x"),
        frontmatter={"name": "x", "description": "d", "license": "MIT"},
        body="# X\n",
    )
    assert "license: MIT" in render_skill_md(doc)

    child = AssembledSkill(
        doc=SkillDoc(
            source=SkillSource(name="comp"),
            frontmatter={"name": "comp", "description": "d"},
            body="# C\n",
        )
    )
    result = MergeResult(skills=[child])
    src = SkillDoc(source=SkillSource(name="comp", license=LicenseInfo(spdx_id="MIT")), body="x")
    apply_source_licenses(result, [src])
    assert result.skills[0].doc.frontmatter["license"] == "MIT"


def test_emit_carries_only_referenced_support_files(tmp_path: Path) -> None:
    from skillmeld.emit.package import emit_claude_code, plan_support_carry
    from skillmeld.models import AssembledSkill

    bundle = tmp_path / "retriever"
    (bundle / "references").mkdir(parents=True)
    (bundle / "SKILL.md").write_text(
        "---\nname: retriever\n---\n# R\n\nRead references/guide.md.\n"
    )
    (bundle / "references" / "guide.md").write_text("# Guide\n")
    (bundle / "references" / "unused.md").write_text("# Unused\n")

    child = AssembledSkill(
        doc=SkillDoc(
            source=SkillSource(name="retriever"),
            frontmatter={"name": "retriever", "description": "Retrieve docs."},
            body="# R\n\nRead references/guide.md for the rules.\n",
        )
    )
    result = MergeResult(skills=[child])
    src = SkillDoc(source=SkillSource(name="retriever"), body="x")

    carry = plan_support_carry(result, [src], [str(bundle)])
    assert carry["retriever"] == [
        ("references/guide.md", (bundle / "references/guide.md").resolve())
    ]

    out = tmp_path / "out"
    emit_claude_code(result, out, sources=[src], generated_at=WHEN, carry=carry)
    assert (out / "retriever" / "references" / "guide.md").is_file()
    assert not (out / "retriever" / "references" / "unused.md").exists()


def test_emit_blockers_flags_empty_descriptions_then_clears() -> None:
    result, _ = _merge()
    # Children start description-less; the orchestrator already carries a templated one.
    blockers = emit_blockers(result)
    assert blockers
    assert all("description is empty" in blocker for blocker in blockers)
    assert not any(blocker.startswith("orchestrator:") for blocker in blockers)
    for skill in result.skills:
        skill.doc.frontmatter["description"] = "A clear, specific description for triggering."
    assert emit_blockers(result) == []


def test_routing_truncation_warns_over_the_claude_code_cap() -> None:
    from skillmeld.emit.package import api_description_warnings, routing_truncation_warnings
    from skillmeld.models import AssembledSkill

    doc = SkillDoc(
        source=SkillSource(name="big"),
        frontmatter={"name": "big", "description": "z" * 1600},
        body="# Big\n",
    )
    result = MergeResult(skills=[AssembledSkill(doc=doc)])
    assert any("big" in w and "truncates" in w for w in routing_truncation_warnings(result))
    # 1600 also blows the 1024 API cap.
    assert any("/v1/skills" in w for w in api_description_warnings(result))


def test_api_description_warns_in_band_while_routing_stays_clean() -> None:
    from skillmeld.emit.package import api_description_warnings, routing_truncation_warnings
    from skillmeld.models import AssembledSkill

    # 1200 chars: rejected by the API surface, fine for Claude Code.
    doc = SkillDoc(
        source=SkillSource(name="mid"),
        frontmatter={"name": "mid", "description": "z" * 1200},
        body="# Mid\n",
    )
    result = MergeResult(skills=[AssembledSkill(doc=doc)])
    assert any("/v1/skills" in w for w in api_description_warnings(result))
    assert routing_truncation_warnings(result) == []


def test_no_budget_warnings_for_a_short_description() -> None:
    from skillmeld.emit.package import api_description_warnings, routing_truncation_warnings
    from skillmeld.models import AssembledSkill

    doc = SkillDoc(
        source=SkillSource(name="ok"),
        frontmatter={"name": "ok", "description": "Compose community skills for a use case."},
        body="# OK\n",
    )
    result = MergeResult(skills=[AssembledSkill(doc=doc)])
    assert routing_truncation_warnings(result) == []
    assert api_description_warnings(result) == []


def _read_manifest(out_dir: Path) -> dict:
    return json.loads((out_dir / ".claude-plugin" / "marketplace.json").read_text())


def test_emit_marketplace_writes_tree_and_manifest(tmp_path: Path) -> None:
    result, sources = _merge()
    written = emit_marketplace(
        result,
        tmp_path,
        sources=sources,
        generated_at=WHEN,
        marketplace_name="my-skills",
        owner={"name": "me"},
    )
    assert any(p.endswith("PROVENANCE.md") for p in written)
    skill_files = [p for p in written if p.endswith("SKILL.md")]
    assert skill_files
    assert all("/skills/" in p for p in skill_files)
    assert (tmp_path / ".claude-plugin" / "marketplace.json").is_file()


def test_emit_marketplace_has_no_plugin_json(tmp_path: Path) -> None:
    result, sources = _merge()
    emit_marketplace(
        result,
        tmp_path,
        sources=sources,
        generated_at=WHEN,
        marketplace_name="my-skills",
        owner={"name": "me"},
    )
    # strict:false owns the definition; a component-declaring plugin.json beside it fails to load.
    assert not list(tmp_path.rglob("plugin.json"))


def test_emit_marketplace_manifest_schema(tmp_path: Path) -> None:
    result, sources = _merge()
    emit_marketplace(
        result,
        tmp_path,
        sources=sources,
        generated_at=WHEN,
        marketplace_name="my-skills",
        owner={"name": "me"},
    )
    manifest = _read_manifest(tmp_path)
    assert manifest["name"] == "my-skills"
    assert manifest["owner"]["name"] == "me"
    assert manifest["metadata"]["version"] == "0.1.0"
    assert manifest["metadata"]["description"]
    assert "description" not in manifest  # top-level moved into the metadata wrapper
    entry = manifest["plugins"][0]
    assert entry["name"]
    assert entry["source"] == "./"
    assert entry["strict"] is False
    assert isinstance(entry["skills"], list) and entry["skills"]
    assert entry["version"] == "0.1.0"  # plugin update compares version strings


def test_emit_marketplace_skills_paths_match_tree(tmp_path: Path) -> None:
    result, sources = _merge()
    emit_marketplace(
        result,
        tmp_path,
        sources=sources,
        generated_at=WHEN,
        marketplace_name="my-skills",
        owner={"name": "me"},
    )
    for rel in _read_manifest(tmp_path)["plugins"][0]["skills"]:
        assert (tmp_path / rel.removeprefix("./") / "SKILL.md").is_file()


def test_emit_marketplace_license_follows_engine_resolution(tmp_path: Path) -> None:
    from skillmeld.models import AssembledSkill, MergePlan

    child = AssembledSkill(
        doc=SkillDoc(
            source=SkillSource(name="a", license=LicenseInfo(spdx_id="MIT")),
            frontmatter={"name": "a", "description": "d"},
            body="# A\n",
        )
    )
    result = MergeResult(
        skills=[child], plan=MergePlan(license_resolution=LicenseInfo(spdx_id="MIT"))
    )
    src = SkillDoc(source=SkillSource(name="a", license=LicenseInfo(spdx_id="MIT")), body="x")
    emit_marketplace(
        result,
        tmp_path,
        sources=[src],
        generated_at=WHEN,
        marketplace_name="m",
        owner={"name": "o"},
    )
    assert _read_manifest(tmp_path)["plugins"][0]["license"] == "MIT"


def test_emit_marketplace_omits_license_when_set_is_unknown(tmp_path: Path) -> None:
    # Regression: an MIT source mixed with an unlicensed one resolves to unknown, so the manifest
    # must NOT claim MIT — the engine's combine() rule (one unlicensed part dominates to unknown).
    from skillmeld.models import AssembledSkill, MergePlan

    child = AssembledSkill(
        doc=SkillDoc(
            source=SkillSource(name="a", license=LicenseInfo(spdx_id="MIT")),
            frontmatter={"name": "a", "description": "d"},
            body="# A\n",
        )
    )
    result = MergeResult(
        skills=[child], plan=MergePlan(license_resolution=LicenseInfo(spdx_id=None))
    )
    src = SkillDoc(source=SkillSource(name="a", license=LicenseInfo(spdx_id="MIT")), body="x")
    emit_marketplace(
        result,
        tmp_path,
        sources=[src],
        generated_at=WHEN,
        marketplace_name="m",
        owner={"name": "o"},
    )
    assert "license" not in _read_manifest(tmp_path)["plugins"][0]


def test_emit_marketplace_is_deterministic(tmp_path: Path) -> None:
    result, sources = _merge()
    emit_marketplace(
        result,
        tmp_path / "a",
        sources=sources,
        generated_at=WHEN,
        marketplace_name="m",
        owner={"name": "o"},
    )
    emit_marketplace(
        result,
        tmp_path / "b",
        sources=sources,
        generated_at=WHEN,
        marketplace_name="m",
        owner={"name": "o"},
    )
    assert _read_manifest(tmp_path / "a") == _read_manifest(tmp_path / "b")


def test_marketplace_name_blocker_refuses_reserved_names() -> None:
    assert marketplace_name_blocker("claude-community") is not None


def test_default_plugin_name_joins_children_when_orchestrated() -> None:
    result, _ = _merge()
    assert result.orchestrator is not None  # the fixture is a multi-skill set
    assert len(result.skills) >= 2
    expected = "-".join(
        slug(str(s.doc.frontmatter.get("name", s.doc.source.name))) for s in result.skills
    )
    name = default_plugin_name(result)
    assert name == expected
    assert name != "orchestrator"


def test_default_plugin_name_uses_the_sole_skill_when_single() -> None:
    from skillmeld.models import AssembledSkill

    child = AssembledSkill(
        doc=SkillDoc(
            source=SkillSource(name="retriever"),
            frontmatter={"name": "retriever", "description": "d"},
            body="# R\n",
        )
    )
    result = MergeResult(skills=[child])
    assert result.orchestrator is None
    assert default_plugin_name(result) == "retriever"


def test_emit_marketplace_defaults_plugin_name_to_children(tmp_path: Path) -> None:
    result, sources = _merge()
    emit_marketplace(
        result,
        tmp_path,
        sources=sources,
        generated_at=WHEN,
        marketplace_name="my-skills",
        owner={"name": "me"},
    )
    entry = _read_manifest(tmp_path)["plugins"][0]
    assert entry["name"] == default_plugin_name(result)
    assert entry["name"] != "orchestrator"


def test_emit_marketplace_honors_explicit_plugin_name(tmp_path: Path) -> None:
    result, sources = _merge()
    emit_marketplace(
        result,
        tmp_path,
        sources=sources,
        generated_at=WHEN,
        marketplace_name="my-skills",
        owner={"name": "me"},
        plugin_name="my-plugin",
    )
    assert _read_manifest(tmp_path)["plugins"][0]["name"] == "my-plugin"
    assert marketplace_name_blocker("agent-skills") is not None
    assert marketplace_name_blocker("my-cool-skills") is None


def test_api_support_file_warnings_name_the_files() -> None:
    from pathlib import Path as P

    from skillmeld.emit.package import api_support_file_warnings

    carry = {
        "skill-creator": [("references/schemas.md", P("/x")), ("assets/a.html", P("/y"))],
        "bare": [],
    }
    warnings = api_support_file_warnings(carry)
    assert len(warnings) == 1
    assert "references/schemas.md" in warnings[0] and "Files API" in warnings[0]


def test_emit_blockers_refuses_a_non_spec_name() -> None:
    result, _ = _merge()
    for skill in result.skills:
        skill.doc.frontmatter["description"] = "Does the thing."
    if result.orchestrator is not None:
        result.orchestrator.doc.frontmatter["description"] = "Routes."
    assert emit_blockers(result) == []
    result.skills[0].doc.frontmatter["name"] = "Bad Name"
    blockers = emit_blockers(result)
    assert len(blockers) == 1 and "Bad Name" in blockers[0] and "directory" in blockers[0]


# --- 0.5.0: spec-only skills tree, install fan-out, Agent Plugins package -------------------


def _described() -> tuple[MergeResult, list[SkillDoc]]:
    result, sources = _merge()
    for skill in result.skills:
        skill.doc.frontmatter["description"] = "Retrieves and reviews documents for a query."
    return result, sources


def test_emit_skills_writes_spec_only_tree_with_support_files_and_provenance(
    tmp_path: Path,
) -> None:
    from skillmeld.emit.package import emit_skills

    result, sources = _described()
    first = result.skills[0]
    name = slug(str(first.doc.frontmatter["name"]))
    first.doc.frontmatter["disallowed-tools"] = "Bash"
    first.doc.frontmatter["allowed-tools"] = "Read"
    helper = tmp_path / "src" / "references" / "guide.md"
    helper.parent.mkdir(parents=True)
    helper.write_text("# Guide\n", encoding="utf-8")
    out = tmp_path / "out"
    written, sidecars = emit_skills(
        result,
        out,
        sources=sources,
        generated_at=WHEN,
        carry={name: [("references/guide.md", helper)]},
    )
    assert sidecars == []
    text = (out / name / "SKILL.md").read_text(encoding="utf-8")
    assert "allowed-tools: Read" in text and "disallowed-tools" not in text
    assert (out / name / "references" / "guide.md").read_text(encoding="utf-8") == "# Guide\n"
    assert any(p.endswith(f"PROVENANCE-{default_plugin_name(result)}.md") for p in written)
    assert str(out / name / "references" / "guide.md") in written


def test_emit_skills_is_byte_identical_to_the_claudeai_zip_skill_md(tmp_path: Path) -> None:
    from skillmeld.emit.package import emit_skills

    result, sources = _described()
    result.skills[0].doc.frontmatter["disable-model-invocation"] = True
    out = tmp_path / "out"
    emit_skills(result, out, sources=sources, generated_at=WHEN)
    archive = zipfile.ZipFile(
        io.BytesIO(emit_claudeai_zip(result, sources=sources, generated_at=WHEN))
    )
    for skill in result.skills:
        name = slug(str(skill.doc.frontmatter["name"]))
        assert (out / name / "SKILL.md").read_bytes() == archive.read(f"skills/{name}/SKILL.md")


def test_emit_skills_is_deterministic(tmp_path: Path) -> None:
    from skillmeld.emit.package import emit_skills

    result, sources = _described()
    a, b = tmp_path / "a", tmp_path / "b"
    emit_skills(result, a, sources=sources, generated_at=WHEN, codex_sidecar=True)
    emit_skills(result, b, sources=sources, generated_at=WHEN, codex_sidecar=True)
    files_a = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
    assert files_a == files_b
    assert all((a / rel).read_bytes() == (b / rel).read_bytes() for rel in files_a)
    assert any(rel.as_posix().endswith("agents/openai.yaml") for rel in files_a)


def test_install_targets_copies_per_agent_and_keeps_claude_fields_only_for_claude(
    tmp_path: Path,
) -> None:
    from skillmeld.emit.package import install_targets
    from skillmeld.emit.targets import resolve_targets

    result, sources = _described()
    first = result.skills[0]
    first.doc.frontmatter["disallowed-tools"] = "Bash"
    name = slug(str(first.doc.frontmatter["name"]))
    project, home = tmp_path / "proj", tmp_path / "home"
    targets = resolve_targets(["claude-code", "codex", "cursor"], scope="project")
    report = install_targets(
        result,
        targets=targets,
        project_root=project,
        home=home,
        sources=sources,
        generated_at=WHEN,
        codex_sidecar=True,
    )
    claude = (project / ".claude" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    shared = (project / ".agents" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    assert "disallowed-tools: Bash" in claude and "disallowed-tools" not in shared
    assert (project / ".agents" / "skills" / name / "agents" / "openai.yaml").is_file()
    assert not (project / ".claude" / "skills" / name / "agents").exists()
    assert not (project / ".cursor").exists()
    assert [item.agents for item in report.installed] == [["claude-code"], ["codex", "cursor"]]
    assert all(
        (Path(item.path) / f"PROVENANCE-{default_plugin_name(result)}.md").is_file()
        for item in report.installed
    )
    assert report.overwritten == [] and len(report.sidecars) == len(result.skills) + 1
    assert not any(Path(p).is_symlink() for p in report.written)


def test_install_targets_refuses_existing_dir_without_force_and_preflights(
    tmp_path: Path,
) -> None:
    from skillmeld.emit.package import InstallConflict, install_targets
    from skillmeld.emit.targets import resolve_targets

    result, sources = _described()
    name = slug(str(result.skills[0].doc.frontmatter["name"]))
    project, home = tmp_path / "proj", tmp_path / "home"
    stale = project / ".agents" / "skills" / name
    stale.mkdir(parents=True)
    (stale / "old.txt").write_text("stale\n", encoding="utf-8")
    targets = resolve_targets(["claude-code", "codex"], scope="project")
    try:
        install_targets(
            result,
            targets=targets,
            project_root=project,
            home=home,
            sources=sources,
            generated_at=WHEN,
        )
    except InstallConflict as exc:
        assert exc.paths == [str(stale)]
    else:
        raise AssertionError("expected InstallConflict")
    # Pre-flight: the conflict-free Claude target was not written either.
    assert not (project / ".claude").exists()
    report = install_targets(
        result,
        targets=targets,
        project_root=project,
        home=home,
        sources=sources,
        generated_at=WHEN,
        force=True,
    )
    assert report.overwritten == [str(stale)]
    assert not (stale / "old.txt").exists() and (stale / "SKILL.md").is_file()
    assert (project / ".claude" / "skills" / name / "SKILL.md").is_file()


def test_install_targets_user_scope_uses_home(tmp_path: Path) -> None:
    from skillmeld.emit.package import install_targets
    from skillmeld.emit.targets import resolve_targets

    result, sources = _described()
    name = slug(str(result.skills[0].doc.frontmatter["name"]))
    project, home = tmp_path / "proj", tmp_path / "home"
    report = install_targets(
        result,
        targets=resolve_targets(["codex", "factory"], scope="user"),
        project_root=project,
        home=home,
        sources=sources,
        generated_at=WHEN,
    )
    assert (home / ".agents" / "skills" / name / "SKILL.md").is_file()
    assert not project.exists()
    assert report.installed[0].scope == "user"
    assert report.installed[0].agents == ["codex", "factory"]


def test_emit_plugin_writes_schema_name_and_spec_only_skills(tmp_path: Path) -> None:
    from skillmeld.emit.package import AGENT_PLUGIN_SCHEMA, emit_plugin

    result, sources = _described()
    result.skills[0].doc.frontmatter["disable-model-invocation"] = True
    out = tmp_path / "plugin"
    written = emit_plugin(
        result,
        out,
        sources=sources,
        generated_at=WHEN,
        plugin_name="doc-tools",
        version="1.2.0",
        owner={"name": "me"},
    )
    manifest = json.loads((out / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["$schema"] == AGENT_PLUGIN_SCHEMA
    assert manifest["name"] == "doc-tools" and manifest["version"] == "1.2.0"
    assert manifest["author"] == {"name": "me"} and "license" not in manifest
    tree = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    for skill in result.skills:
        name = slug(str(skill.doc.frontmatter["name"]))
        assert f"skills/{name}/SKILL.md" in tree
        text = (out / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert "disable-model-invocation" not in text
    assert "PROVENANCE.md" in tree
    assert not (out / ".claude-plugin").exists() and not (out / ".codex-plugin").exists()
    assert not (out / ".agents").exists()
    assert sorted(written) == written and str(out / "plugin.json") in written


def test_emit_plugin_codex_marketplace_license_and_compat(tmp_path: Path) -> None:
    from skillmeld.emit.package import emit_plugin

    result, sources = _described()
    result.plan.license_resolution = LicenseInfo(spdx_id="MIT")
    out = tmp_path / "plugin"
    emit_plugin(
        result,
        out,
        sources=sources,
        generated_at=WHEN,
        plugin_name="doc-tools",
        version="0.1.0",
        owner={"name": "me"},
        codex_marketplace="my-skills",
        codex_compat=True,
        codex_sidecar=True,
    )
    manifest = json.loads((out / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["license"] == "MIT"
    compat = json.loads((out / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert "$schema" not in compat and compat["name"] == "doc-tools"
    market = json.loads(
        (out / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
    )
    assert market["name"] == "my-skills"
    assert market["plugins"] == [{"name": "doc-tools", "source": {"source": "local", "path": "./"}}]
    name = slug(str(result.skills[0].doc.frontmatter["name"]))
    assert (out / "skills" / name / "agents" / "openai.yaml").is_file()
    assert "## Sidecars" in (out / "PROVENANCE.md").read_text(encoding="utf-8")


def test_emit_plugin_rejects_a_bad_plugin_name(tmp_path: Path) -> None:
    import pytest

    from skillmeld.emit.package import emit_plugin

    result, sources = _described()
    with pytest.raises(ValueError, match="plugin name"):
        emit_plugin(
            result,
            tmp_path,
            sources=sources,
            generated_at=WHEN,
            plugin_name="Bad_Name",
            version="0.1.0",
            owner={"name": "me"},
        )


def test_emit_marketplace_output_is_unchanged_after_refactor(tmp_path: Path) -> None:
    result, sources = _described()
    result.skills[0].doc.frontmatter["disallowed-tools"] = "Bash"
    written = emit_marketplace(
        result,
        tmp_path,
        sources=sources,
        generated_at=WHEN,
        marketplace_name="my-skills",
        owner={"name": "me"},
    )
    assert result.orchestrator is not None
    emitted = [result.orchestrator, *result.skills]
    names = [slug(str(s.doc.frontmatter["name"])) for s in emitted]
    expected = sorted(
        [str(tmp_path / "PROVENANCE.md"), str(tmp_path / ".claude-plugin" / "marketplace.json")]
        + [str(tmp_path / "skills" / name / "SKILL.md") for name in names]
    )
    assert written == expected
    child = slug(str(result.skills[0].doc.frontmatter["name"]))
    kept = (tmp_path / "skills" / child / "SKILL.md").read_text(encoding="utf-8")
    assert "disallowed-tools: Bash" in kept
    manifest = _read_manifest(tmp_path)
    assert manifest["plugins"][0]["skills"] == [f"./skills/{name}" for name in names]


def test_support_carry_refuses_a_parent_segment_even_inside_the_bundle(tmp_path: Path) -> None:
    from skillmeld.emit.package import plan_support_carry

    result, sources = _described()
    first = result.skills[0]
    name = slug(str(first.doc.frontmatter["name"]))
    bundle = tmp_path / name
    (bundle / "references").mkdir(parents=True)
    (bundle / "SKILL.md").write_text("---\nname: x\n---\n# X\n", encoding="utf-8")
    (bundle / "references" / "ok.md").write_text("# ok\n", encoding="utf-8")
    first.doc.body += "\nSee references/ok.md and references/../SKILL.md too.\n"
    first.doc.source.name = name
    others = [s for s in sources if slug(s.source.name) != name]
    docs = [first.doc, *others]
    dirs = [str(bundle), *[str(tmp_path / slug(s.source.name)) for s in others]]
    for other in others:
        (tmp_path / slug(other.source.name)).mkdir(exist_ok=True)
    carry = plan_support_carry(result, docs, dirs)
    assert [ref for ref, _ in carry.get(name, [])] == ["references/ok.md"]


def test_install_force_replaces_a_symlinked_skill_without_following_it(tmp_path: Path) -> None:
    from skillmeld.emit.package import InstallConflict, install_targets
    from skillmeld.emit.targets import resolve_targets

    result, sources = _described()
    name = slug(str(result.skills[0].doc.frontmatter["name"]))
    project, home = tmp_path / "proj", tmp_path / "home"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("keep\n", encoding="utf-8")
    root = project / ".agents" / "skills"
    root.mkdir(parents=True)
    (root / name).symlink_to(elsewhere, target_is_directory=True)
    targets = resolve_targets(["codex"], scope="project")
    try:
        install_targets(
            result,
            targets=targets,
            project_root=project,
            home=home,
            sources=sources,
            generated_at=WHEN,
        )
    except InstallConflict as exc:
        assert exc.paths == [str(root / name)]
    else:
        raise AssertionError("expected InstallConflict on a symlinked skill")
    report = install_targets(
        result,
        targets=targets,
        project_root=project,
        home=home,
        sources=sources,
        generated_at=WHEN,
        force=True,
    )
    assert report.overwritten == [str(root / name)]
    assert not (root / name).is_symlink() and (root / name / "SKILL.md").is_file()
    assert (elsewhere / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    # A dangling link counts as occupied too, and is replaced the same way.
    (root / name).rename(root / "moved")
    (root / name).symlink_to(tmp_path / "gone")
    report = install_targets(
        result,
        targets=targets,
        project_root=project,
        home=home,
        sources=sources,
        generated_at=WHEN,
        force=True,
    )
    assert str(root / name) in report.overwritten and (root / name / "SKILL.md").is_file()
