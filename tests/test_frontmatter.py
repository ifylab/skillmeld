# SPDX-License-Identifier: Apache-2.0
"""D1: carry and reconcile source frontmatter through the merge, and verify it byte-traces."""

from __future__ import annotations

from skillmeld.emit.package import api_surface_warnings, render_skill_md
from skillmeld.merge.frontmatter import carry_frontmatter, reconcile_frontmatter
from skillmeld.merge.parse import parse_skill
from skillmeld.merge.pipeline import _split_frontmatter, run_merge
from skillmeld.merge.verify import verify
from skillmeld.models import (
    AssembledAtom,
    AssembledSkill,
    MergeResult,
    SkillDoc,
    SkillSource,
    UseCaseProfile,
    Verdict,
)
from skillmeld.security.scan import verdict_from

PROFILE = UseCaseProfile(
    summary="Retrieve and review documents for a query.",
    tasks=["retrieve documents", "review document quality"],
)


def _doc(
    name: str, frontmatter: dict[str, object], body: str = "# H\n\nText for the body.\n"
) -> SkillDoc:
    return SkillDoc(source=SkillSource(name=name), frontmatter=frontmatter, body=body)


# --- reconcile policy (the pure function) -----------------------------------------------


def test_allowed_tools_intersects_and_flags_drop() -> None:
    a = _doc("a", {"allowed-tools": "Read Bash(git:*)"})
    b = _doc("b", {"allowed-tools": "Read"})
    result = reconcile_frontmatter([a, b])
    assert result.fields["allowed-tools"] == "Read"
    assert any(f.rule_id == "merge:allowed-tools-narrowed" for f in result.findings)
    assert "Bash(git:*)" in result.findings[0].message
    assert verdict_from(result.findings) == Verdict.REVIEW


def test_allowed_tools_single_source_carried_without_review() -> None:
    result = reconcile_frontmatter([_doc("a", {"allowed-tools": "Read Bash(git:*)"})])
    assert result.fields["allowed-tools"] == "Bash(git:*) Read"  # canonical sorted form
    assert result.findings == []


def test_allowed_tools_yaml_list_and_internal_space_token() -> None:
    result = reconcile_frontmatter([_doc("a", {"allowed-tools": ["Bash(git add *)", "Read"]})])
    assert result.fields["allowed-tools"] == "Bash(git add *) Read"


def test_disallowed_tools_union() -> None:
    a = _doc("a", {"disallowed-tools": "WebFetch"})
    b = _doc("b", {"disallowed-tools": "AskUserQuestion"})
    result = reconcile_frontmatter([a, b])
    assert result.fields["disallowed-tools"] == "AskUserQuestion WebFetch"


def test_disable_invocation_honored_and_flagged() -> None:
    # flat-string form (what the lenient frontmatter parser yields) is treated as truthy
    result = reconcile_frontmatter([_doc("a", {"disable-model-invocation": "true"}), _doc("b", {})])
    assert result.fields["disable-model-invocation"] is True
    assert any(f.rule_id == "merge:invocation-disabled" for f in result.findings)
    assert verdict_from(result.findings) == Verdict.REVIEW


def test_compatibility_joined_distinct() -> None:
    a = _doc("a", {"compatibility": "Requires git"})
    b = _doc("b", {"compatibility": "Requires python 3.12"})
    result = reconcile_frontmatter([a, b])
    assert result.fields["compatibility"] == "Requires git; Requires python 3.12"


def test_metadata_key_union_first_source_wins_on_conflict() -> None:
    a = _doc("a", {"metadata": {"author": "x", "version": "1.0"}})
    b = _doc("b", {"metadata": {"author": "y", "topic": "ifc"}})
    result = reconcile_frontmatter([a, b])
    assert result.fields["metadata"] == {"author": "x", "version": "1.0", "topic": "ifc"}
    assert any("author" in note for note in result.notes)


def test_no_carryable_fields_is_empty() -> None:
    result = reconcile_frontmatter([_doc("a", {"name": "a", "description": "d"})])
    assert result.fields == {}
    assert result.findings == []


# --- the frontmatter parser (nested YAML, safe_load) ------------------------------------


def test_split_frontmatter_parses_nested_metadata_and_tolerates_colons() -> None:
    text = (
        "---\n"
        "name: x\n"
        "description: Does a thing: even with a colon\n"
        "metadata:\n"
        "  author: org\n"
        '  version: "1.0"\n'
        "---\n"
        "# Body\n"
    )
    frontmatter, body = _split_frontmatter(text)
    assert frontmatter["name"] == "x"
    assert frontmatter["description"] == "Does a thing: even with a colon"
    assert frontmatter["metadata"] == {"author": "org", "version": "1.0"}
    assert body == "# Body\n"


def test_split_frontmatter_safe_load_refuses_python_tag() -> None:
    text = (
        "---\n"
        "name: x\n"
        "metadata:\n"
        "  evil: !!python/object/apply:os.system ['echo pwned']\n"
        "---\n"
        "body\n"
    )
    frontmatter, _ = _split_frontmatter(text)
    # safe_load rejects the construct tag; the block degrades to empty, nothing is executed.
    assert frontmatter["metadata"] == ""


# --- carry over a multi-source child + the merge verdict --------------------------------


def test_carry_intersects_a_multi_source_child() -> None:
    a = _doc("alpha", {"allowed-tools": "Read Bash(git:*)"}, body="# Alpha\n\nAlpha line here.\n")
    b = _doc("beta", {"allowed-tools": "Read"}, body="# Beta\n\nBeta line here.\n")
    sources = [a, b]
    a_atoms = parse_skill(a)
    b_atoms = parse_skill(b)
    layout = [
        AssembledAtom(atom_id=a_atoms[0].id, role="source"),
        AssembledAtom(atom_id="", role="scaffold", template_id="sep"),
        AssembledAtom(atom_id=b_atoms[0].id, role="source"),
    ]
    child = AssembledSkill(
        doc=SkillDoc(
            source=SkillSource(name="merged"),
            frontmatter={"name": "merged", "description": ""},
            body="x",
        ),
        layout=layout,
    )
    result = MergeResult(skills=[child])
    carry = carry_frontmatter(result, sources)
    assert child.doc.frontmatter["allowed-tools"] == "Read"
    assert any(f.rule_id == "merge:allowed-tools-narrowed" for f in carry.findings)
    assert verdict_from(carry.findings) == Verdict.REVIEW


# --- the pipeline carries fields and the verifier accepts them --------------------------

ALPHA = SkillDoc(
    source=SkillSource(name="alpha"),
    frontmatter={"allowed-tools": "Read"},
    body="# Alpha\n\nRetrieve documents for a query.\n\n- Always validate the query first.\n",
)
BETA = SkillDoc(
    source=SkillSource(name="beta"),
    frontmatter={"allowed-tools": "Read"},
    body="# Beta\n\nReview retrieved documents for quality.\n\n- Flag low-quality matches.\n",
)


def test_pipeline_carries_allowed_tools_and_verifies_clean() -> None:
    run = run_merge([ALPHA, BETA], PROFILE)
    assert run.problems == [], run.problems
    carried = [s for s in run.result.skills if "allowed-tools" in s.doc.frontmatter]
    assert carried
    assert all(s.doc.frontmatter["allowed-tools"] == "Read" for s in carried)


def test_verifier_rejects_invented_tool() -> None:
    run = run_merge([ALPHA, BETA], PROFILE)
    assert run.problems == []
    run.result.skills[0].doc.frontmatter["allowed-tools"] = "Read Bash(rm:*)"
    assert verify(run.result, [ALPHA, BETA]) != []


def test_verifier_rejects_invented_field() -> None:
    run = run_merge([ALPHA, BETA], PROFILE)
    assert run.problems == []
    run.result.skills[0].doc.frontmatter["disable-model-invocation"] = True
    assert verify(run.result, [ALPHA, BETA]) != []


# --- emit renders carried fields, API surface warns -------------------------------------


def test_render_skill_md_includes_carried_fields() -> None:
    doc = SkillDoc(
        source=SkillSource(name="x"),
        frontmatter={
            "name": "x",
            "description": "d",
            "allowed-tools": "Read",
            "disable-model-invocation": True,
            "metadata": {"author": "org"},
        },
        body="# X\n",
    )
    text = render_skill_md(doc)
    assert "allowed-tools: Read" in text
    assert "disable-model-invocation: true" in text
    assert "metadata:\n  author: org" in text


def test_api_surface_warns_on_tool_frontmatter() -> None:
    child = AssembledSkill(
        doc=SkillDoc(
            source=SkillSource(name="x"),
            frontmatter={"name": "x", "description": "d", "allowed-tools": "Read"},
            body="# X\n",
        )
    )
    warnings = api_surface_warnings(MergeResult(skills=[child]))
    assert warnings and "allowed-tools" in warnings[0]
