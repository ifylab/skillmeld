# SPDX-License-Identifier: Apache-2.0
"""W7 emit tests: SKILL.md rendering, the three surfaces, and the provenance sidecar."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from skillmeld.emit.package import (
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
from skillmeld.merge.synthesize import assemble
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
    result, sources = _merge()
    written = emit_claude_code(result, tmp_path, sources=sources, generated_at=WHEN)
    assert any(p.endswith("PROVENANCE.md") for p in written)
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
    entry = manifest["plugins"][0]
    assert entry["name"]
    assert entry["source"] == "./"
    assert entry["strict"] is False
    assert isinstance(entry["skills"], list) and entry["skills"]
    assert "version" not in entry


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
    assert marketplace_name_blocker("agent-skills") is not None
    assert marketplace_name_blocker("my-cool-skills") is None
