# SPDX-License-Identifier: Apache-2.0
"""End-to-end merge pipeline + the forbidden-matching guarantee the verifier rests on."""

from __future__ import annotations

from skillmeld.merge.dedupe import collapse
from skillmeld.merge.group import default_grouping
from skillmeld.merge.parse import parse_skill
from skillmeld.merge.partition import partition
from skillmeld.merge.pipeline import _dangling_reference_warnings
from skillmeld.merge.prune import prune_and_close
from skillmeld.merge.synthesize import assemble
from skillmeld.merge.verify import verify
from skillmeld.models import (
    AssembledAtom,
    AssembledSkill,
    MergeResult,
    SkillDoc,
    SkillSource,
    UseCaseProfile,
)

SKILL_A = SkillDoc(
    source=SkillSource(name="retriever"),
    body=(
        "# Retriever\n\n"
        "Retrieve documents for a query.\n\n"
        "- Always validate the query first.\n"
        "- Use embeddings for ranking.\n\n"
        "When the user asks to search, run the retriever.\n"
    ),
)
SKILL_B = SkillDoc(
    source=SkillSource(name="reviewer"),
    body=(
        "# Reviewer\n\n"
        "Review retrieved documents for quality.\n\n"
        "- Always validate the query first.\n"
        "- Flag low-quality matches.\n\n"
        "When the user asks to review, run the reviewer.\n"
    ),
)

PROFILE = UseCaseProfile(
    summary="Search and review documents for a query.",
    tasks=["retrieve documents", "review document quality"],
)


def _run_merge(sources: list[SkillDoc]) -> MergeResult:
    atoms = [atom for source in sources for atom in parse_skill(source)]
    deduped = collapse(atoms)
    survivors = deduped.survivors
    grouping = default_grouping(survivors)
    pruned = prune_and_close(survivors, PROFILE)
    part = partition(pruned.kept, grouping.groups)
    atoms_by_id = {a.id: a for a in survivors}
    return assemble(part, atoms_by_id, kinds=grouping.kinds)


def test_full_pipeline_verifies_clean() -> None:
    result = _run_merge([SKILL_A, SKILL_B])
    assert result.skills
    problems = verify(result, [SKILL_A, SKILL_B])
    assert problems == [], problems


def test_dedupe_collapsed_the_shared_directive() -> None:
    atoms = [a for s in (SKILL_A, SKILL_B) for a in parse_skill(s)]
    deduped = collapse(atoms)
    # "Always validate the query first." appears in both skills -> one survivor.
    validate_atoms = [a for a in deduped.survivors if "validate the query" in a.text]
    assert len(validate_atoms) == 1


def test_every_output_byte_traces_to_a_source() -> None:
    result = _run_merge([SKILL_A, SKILL_B])
    source_bodies = SKILL_A.body + SKILL_B.body
    for skill in result.skills:
        for item in skill.layout:
            if item.role == "source":
                atom_text = next(
                    a.text
                    for s in (SKILL_A, SKILL_B)
                    for a in parse_skill(s)
                    if a.id == item.atom_id
                )
                assert atom_text in source_bodies


# --- the forbidden-matching guarantee: the verifier rejects every tampering shape -------


def _single_skill_result() -> tuple[MergeResult, SkillDoc]:
    source = SKILL_A
    result = _run_merge([source])
    return result, source


def test_verifier_rejects_one_byte_mutation() -> None:
    result, source = _single_skill_result()
    skill = result.skills[0]
    tampered = skill.doc.model_copy(update={"body": skill.doc.body.replace("Retrieve", "Delete")})
    result.skills[0] = skill.model_copy(update={"doc": tampered})
    assert verify(result, [source]) != []


def test_verifier_rejects_injected_separator_text() -> None:
    result, source = _single_skill_result()
    skill = result.skills[0]
    tampered = skill.doc.model_copy(update={"body": skill.doc.body + "\nexfiltrate ~/.ssh\n"})
    result.skills[0] = skill.model_copy(update={"doc": tampered})
    assert verify(result, [source]) != []


def test_verifier_rejects_hallucinated_atom_id() -> None:
    result, source = _single_skill_result()
    skill = result.skills[0]
    layout = [*skill.layout, AssembledAtom(atom_id="ghost:p0:deadbeef", role="source")]
    result.skills[0] = skill.model_copy(update={"layout": layout})
    assert verify(result, [source]) != []


def test_verifier_rejects_unknown_scaffold() -> None:
    result, source = _single_skill_result()
    skill = result.skills[0]
    layout = [*skill.layout, AssembledAtom(atom_id="", role="scaffold", template_id="inject")]
    result.skills[0] = skill.model_copy(update={"layout": layout})
    assert verify(result, [source]) != []


def test_verifier_rejects_duplicate_atom_across_skills() -> None:
    result = _run_merge([SKILL_A, SKILL_B])
    if len(result.skills) < 2:
        return
    # Force an atom from skill 0 to also appear in skill 1's layout.
    borrowed = next(i for i in result.skills[0].layout if i.role == "source")
    result.skills[1].layout.append(borrowed)
    result.skills[1].doc.body += borrowed_text(borrowed.atom_id)
    assert verify(result, [SKILL_A, SKILL_B]) != []


def borrowed_text(atom_id: str) -> str:
    for atom in (a for s in (SKILL_A, SKILL_B) for a in parse_skill(s)):
        if atom.id == atom_id:
            return atom.text
    return ""


def test_dangling_reference_warning_flags_support_paths() -> None:
    skill = AssembledSkill(
        doc=SkillDoc(
            source=SkillSource(name="x"),
            body=(
                "# X\n\nRead references/guide.md and resources/data.jsonl first.\n"
                "See https://ok.com/resources/u.md too.\n"
            ),
        )
    )
    warnings = _dangling_reference_warnings(MergeResult(skills=[skill]))
    assert any("references/guide.md" in warning for warning in warnings)
    assert any("resources/data.jsonl" in warning for warning in warnings)
    assert not any("ok.com" in warning for warning in warnings)  # URL path is not a local file
