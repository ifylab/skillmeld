# SPDX-License-Identifier: Apache-2.0
"""W6 eval tests: structural quality, trigger scoring, leakage gate, gated description edits."""

from __future__ import annotations

import pytest

from skillmeld.eval.evaluate import apply_description_edit, evaluate
from skillmeld.eval.leakage import held_out_leaks
from skillmeld.eval.quality import score_quality
from skillmeld.eval.route import route_queries
from skillmeld.eval.strategy import STRATEGIES, TaskPassRateStrategy
from skillmeld.eval.trigger import TriggerJudgment, TriggerQuery, score_trigger, split
from skillmeld.merge.dedupe import collapse
from skillmeld.merge.group import default_grouping
from skillmeld.merge.parse import parse_skill
from skillmeld.merge.partition import partition
from skillmeld.merge.prune import prune_and_close
from skillmeld.merge.synthesize import assemble
from skillmeld.models import MergeResult, SkillDoc, SkillSource, UseCaseProfile

SKILL_A = SkillDoc(
    source=SkillSource(name="retriever"),
    body="# Retriever\n\nRetrieve documents.\n\n- Always validate the query first.\n",
)
SKILL_B = SkillDoc(
    source=SkillSource(name="reviewer"),
    body="# Reviewer\n\nReview documents.\n\n- Never skip the quality check.\n",
)
PROFILE = UseCaseProfile(summary="Retrieve and review documents.", tasks=["retrieve", "review"])


def _merge() -> tuple[MergeResult, list[SkillDoc]]:
    sources = [SKILL_A, SKILL_B]
    atoms = [a for s in sources for a in parse_skill(s)]
    survivors = collapse(atoms).survivors
    grouping = default_grouping(survivors)
    pruned = prune_and_close(survivors, PROFILE)
    part = partition(pruned.kept, grouping.groups)
    result = assemble(part, {a.id: a for a in survivors}, kinds=grouping.kinds)
    return result, sources


# --- quality ----------------------------------------------------------------------------


def test_quality_flags_reserved_name() -> None:
    doc = SkillDoc(source=SkillSource(name="x"), frontmatter={"name": "claude-helper"}, body="hi\n")
    report = score_quality(doc)
    assert not report.passed
    assert any("reserved word" in issue for issue in report.issues)


def test_quality_flags_overlong_description() -> None:
    doc = SkillDoc(source=SkillSource(name="x"), frontmatter={"description": "z" * 2000}, body="hi")
    assert not score_quality(doc).passed


def test_quality_description_in_api_band_warns_but_passes() -> None:
    # Between the API authoring cap (1024) and the Claude Code routing cap (1536): valid on Claude
    # Code so it passes, but warned because the /v1/skills surface would reject it.
    doc = SkillDoc(source=SkillSource(name="x"), frontmatter={"description": "z" * 1200}, body="hi")
    report = score_quality(doc)
    assert report.passed
    assert not report.issues
    assert any("API authoring cap" in warning for warning in report.warnings)


def test_quality_description_over_routing_cap_is_a_hard_issue() -> None:
    # Over the Claude Code routing cap (1536): truncated on every surface, so it hard-fails.
    doc = SkillDoc(source=SkillSource(name="x"), frontmatter={"description": "z" * 1600}, body="hi")
    report = score_quality(doc)
    assert not report.passed
    assert any("routing cap" in issue for issue in report.issues)


def test_quality_clean_skill_passes() -> None:
    doc = SkillDoc(
        source=SkillSource(name="retriever"),
        frontmatter={
            "name": "retriever",
            "description": "Retrieve and rank documents for a query.",
        },
        body=SKILL_A.body,
    )
    report = score_quality(doc)
    assert report.passed
    assert report.strong_markers >= 1


def test_quality_flags_empty_description() -> None:
    report = score_quality(SKILL_A)  # no description in frontmatter
    assert not report.passed
    assert any("description is empty" in issue for issue in report.issues)


def test_quality_allows_code_operators_but_flags_html_tags() -> None:
    code_body = (
        "# Skill\n\nGuidance.\n\n"
        "```python\nif count < 1 or ratio > 5:\n    items = List<int>()\n```\n"
        "Pipeline: status -> canvas -> solve. Aspect <5 is fine.\n"
    )
    ok = SkillDoc(
        source=SkillSource(name="x"),
        frontmatter={"name": "x", "description": "Writes code with operators."},
        body=code_body,
    )
    ok_report = score_quality(ok)
    assert not any("html-like tag" in issue for issue in ok_report.issues)
    assert ok_report.passed

    tagged = SkillDoc(
        source=SkillSource(name="y"),
        frontmatter={"name": "y", "description": "Has a real tag."},
        body="# Skill\n\nPut <div>content</div> in the page.\n",
    )
    report = score_quality(tagged)
    assert any("html-like tag" in issue for issue in report.issues)
    assert not report.passed


# --- trigger scoring + split ------------------------------------------------------------

QUERIES = [
    TriggerQuery(id="q1", text="find me documents", kind="trigger", expected_skill="retriever"),
    TriggerQuery(id="q2", text="search the corpus", kind="trigger", expected_skill="retriever"),
    TriggerQuery(id="q3", text="check document quality", kind="trigger", expected_skill="reviewer"),
    TriggerQuery(id="q4", text="order me a pizza", kind="near-miss", expected_skill=None),
]


def test_split_is_deterministic_and_disjoint() -> None:
    train, held = split(QUERIES)
    assert set(train).isdisjoint(held)
    assert set(train) | set(held) == {"q1", "q2", "q3", "q4"}
    assert split(QUERIES) == split(list(reversed(QUERIES)))


def test_trigger_score_rewards_correct_routing() -> None:
    judgments = [
        TriggerJudgment(query_id="q1", routed_skill="retriever"),
        TriggerJudgment(query_id="q2", routed_skill="retriever"),
        TriggerJudgment(query_id="q3", routed_skill="reviewer"),
        TriggerJudgment(query_id="q4", routed_skill=None),
    ]
    assert score_trigger(QUERIES, judgments).pass_rate == 1.0


def test_trigger_score_penalizes_misroute_and_false_trigger() -> None:
    judgments = [
        TriggerJudgment(query_id="q1", routed_skill="reviewer"),  # misroute
        TriggerJudgment(query_id="q4", routed_skill="retriever"),  # near-miss falsely triggered
    ]
    score = score_trigger(QUERIES, judgments)
    assert score.pass_rate < 1.0
    assert "q1" in score.failed_ids and "q4" in score.failed_ids


# --- leakage ----------------------------------------------------------------------------


def test_leakage_detects_held_out_query_in_body() -> None:
    result, _ = _merge()
    leaky = TriggerQuery(id="qx", text="Retrieve documents", kind="trigger")
    leaks = held_out_leaks(result, [leaky], ["qx"])
    assert "qx" in leaks


def test_no_leakage_for_absent_query() -> None:
    result, _ = _merge()
    clean = TriggerQuery(id="qy", text="completely unrelated phrase about cars", kind="trigger")
    assert held_out_leaks(result, [clean], ["qy"]) == []


# --- evaluate orchestration -------------------------------------------------------------


def test_evaluate_flags_missing_descriptions_then_passes_when_authored() -> None:
    result, sources = _merge()
    # A fresh merge leaves child descriptions empty on purpose — not yet shippable.
    fresh = evaluate(result, sources)
    assert not fresh.passed
    assert fresh.verifier_problems == []
    assert any("description is empty" in issue for q in fresh.quality for issue in q.issues)
    # Once the host Claude authors each description, the set passes.
    for skill in result.skills:
        name = skill.doc.frontmatter["name"]
        skill.doc.frontmatter["description"] = f"Handles {name} tasks for the user's project."
    authored = evaluate(result, sources)
    assert authored.passed, [issue for q in authored.quality for issue in q.issues]
    assert len(authored.quality) == len(result.skills)


def test_merge_gives_the_orchestrator_a_nonempty_description() -> None:
    result, _ = _merge()
    assert result.orchestrator is not None
    description = str(result.orchestrator.doc.frontmatter["description"])
    assert description.strip()
    # The router's trigger surface names every route it covers.
    assert result.routing_table
    for route in result.routing_table:
        assert route.label in description


# --- gated description edit -------------------------------------------------------------


def test_description_edit_accepted_when_safe() -> None:
    result, sources = _merge()
    baseline = [TriggerJudgment(query_id="q1", routed_skill="retriever")]
    candidate = [TriggerJudgment(query_id="q1", routed_skill="retriever")]
    edited, decision = apply_description_edit(
        result,
        sources,
        0,
        "Retrieve and rank documents for a query.",
        queries=QUERIES,
        baseline_judgments=baseline,
        candidate_judgments=candidate,
    )
    assert decision.accepted, decision.reasons
    assert str(edited.skills[0].doc.frontmatter["description"]).startswith("Retrieve and rank")


def test_description_edit_rejected_on_held_out_regression() -> None:
    result, sources = _merge()
    held = split(QUERIES)[1][0]
    baseline = [TriggerJudgment(query_id=held, routed_skill="retriever")]
    candidate = [TriggerJudgment(query_id=held, routed_skill=None)]  # now fails held-out
    queries = [
        TriggerQuery(id=held, text="find documents", kind="trigger", expected_skill="retriever")
    ]
    _, decision = apply_description_edit(
        result,
        sources,
        0,
        "A worse description.",
        queries=queries,
        baseline_judgments=baseline,
        candidate_judgments=candidate,
    )
    assert not decision.accepted
    assert any("regress" in reason for reason in decision.reasons)


def test_description_edit_can_target_orchestrator() -> None:
    result, sources = _merge()
    assert result.orchestrator is not None
    edited, decision = apply_description_edit(
        result,
        sources,
        "orchestrator",
        "Route retrieval and review requests to the matching skill.",
        queries=QUERIES,
        baseline_judgments=[],
        candidate_judgments=[],
    )
    assert decision.accepted, decision.reasons
    assert edited.orchestrator is not None
    assert "Route retrieval and review" in str(edited.orchestrator.doc.frontmatter["description"])


def test_description_edit_not_blocked_by_empty_sibling() -> None:
    result, sources = _merge()
    assert len(result.skills) >= 2  # skill 1's description stays empty during this edit
    baseline = [TriggerJudgment(query_id="q1", routed_skill="retriever")]
    edited, decision = apply_description_edit(
        result,
        sources,
        0,
        "Retrieve and rank documents for a query.",
        queries=QUERIES,
        baseline_judgments=baseline,
        candidate_judgments=baseline,
    )
    assert decision.accepted, decision.reasons
    assert str(edited.skills[1].doc.frontmatter["description"]) == ""


def test_description_edit_rejects_an_unknown_target() -> None:
    result, sources = _merge()
    _, decision = apply_description_edit(
        result,
        sources,
        99,
        "unreachable",
        queries=QUERIES,
        baseline_judgments=[],
        candidate_judgments=[],
    )
    assert not decision.accepted
    assert any("no skill at target" in reason for reason in decision.reasons)


def test_task_pass_rate_strategy_is_registered_but_inactive() -> None:
    assert "task-pass-rate" in STRATEGIES
    with pytest.raises(NotImplementedError):
        TaskPassRateStrategy().gate()


# --- independent engine-side routing (honesty cross-check) ------------------------------

# Distinctive descriptions: each query's discriminating term lands on exactly one child, so the
# deterministic router has a clear winner. 'documents'/'corpus' route to the first child;
# 'check'/'quality' route to the second.
RETRIEVER_DESC = "Find, search, and retrieve documents from the corpus for a query."
REVIEWER_DESC = "Review writing and check quality."


def _authored_route_result() -> tuple[MergeResult, list[SkillDoc], tuple[str, str]]:
    result, sources = _merge()
    a = str(result.skills[0].doc.frontmatter["name"])
    b = str(result.skills[1].doc.frontmatter["name"])
    result.skills[0].doc.frontmatter["description"] = RETRIEVER_DESC
    result.skills[1].doc.frontmatter["description"] = REVIEWER_DESC
    return result, sources, (a, b)


def test_route_queries_routes_each_query_by_description() -> None:
    result, _, (a, b) = _authored_route_result()
    queries = [
        TriggerQuery(id="q1", text="find me documents", kind="trigger", expected_skill=a),
        TriggerQuery(id="q2", text="search the corpus", kind="trigger", expected_skill=a),
        TriggerQuery(id="q3", text="check document quality", kind="trigger", expected_skill=b),
        TriggerQuery(id="q4", text="order me a pizza", kind="near-miss", expected_skill=None),
    ]
    judgments = route_queries(result, queries)
    routed = {j.query_id: j.routed_skill for j in judgments}
    assert routed == {"q1": a, "q2": a, "q3": b, "q4": None}
    assert score_trigger(queries, judgments).pass_rate == 1.0


def test_route_queries_cannot_route_to_an_empty_description() -> None:
    # A fresh merge leaves every child description empty; an undescribed skill never triggers.
    result, _ = _merge()
    query = TriggerQuery(id="q1", text="find me documents", kind="trigger")
    assert route_queries(result, [query])[0].routed_skill is None


def test_route_queries_routes_nowhere_without_a_clear_winner() -> None:
    result, _ = _merge()
    shared = "Handle documents for the project."
    result.skills[0].doc.frontmatter["description"] = shared
    result.skills[1].doc.frontmatter["description"] = shared
    query = TriggerQuery(id="q1", text="process the documents", kind="trigger")
    # 'documents' matches both children equally — an ambiguous tie routes nowhere.
    assert route_queries(result, [query])[0].routed_skill is None


def test_evaluate_reports_independent_routing_and_flags_disagreement() -> None:
    result, sources, (a, b) = _authored_route_result()
    queries = [
        TriggerQuery(id="q1", text="find me documents", kind="trigger", expected_skill=a),
        TriggerQuery(id="q3", text="check document quality", kind="trigger", expected_skill=b),
    ]
    # The host claims q1 went to the wrong child; the engine routes it correctly and disagrees.
    host = [
        TriggerJudgment(query_id="q1", routed_skill=b),
        TriggerJudgment(query_id="q3", routed_skill=b),
    ]
    report = evaluate(result, sources, queries=queries, judgments=host)
    assert report.independent_trigger is not None
    assert report.independent_trigger.pass_rate == 1.0
    assert report.routing_disagreements == ["q1"]


def test_description_edit_rejected_when_independent_routing_regresses() -> None:
    # The host reports good routing before and after, but the edited description routes the
    # held-out query worse on its own terms — the independent check rejects what the host blessed.
    result, sources = _merge()
    a = str(result.skills[0].doc.frontmatter["name"])
    result.skills[0].doc.frontmatter["description"] = "Find and retrieve documents for a query."
    held = TriggerQuery(id="q1", text="find documents", kind="trigger", expected_skill=a)
    host = [TriggerJudgment(query_id="q1", routed_skill=a)]
    edited, decision = apply_description_edit(
        result,
        sources,
        0,
        "Helps with miscellaneous tasks.",
        queries=[held],
        baseline_judgments=host,
        candidate_judgments=host,  # host insists routing is unchanged and fine
    )
    assert not decision.accepted
    assert decision.before_independent == 1.0
    assert decision.after_independent == 0.0
    assert any("independent routing regressed" in reason for reason in decision.reasons)
    # Rejected, so the good description is preserved.
    assert str(edited.skills[0].doc.frontmatter["description"]).startswith("Find and retrieve")


def test_description_edit_accepted_when_independent_routing_holds() -> None:
    # A genuine rewording that keeps the trigger terms must still clear the independent check.
    result, sources = _merge()
    a = str(result.skills[0].doc.frontmatter["name"])
    result.skills[0].doc.frontmatter["description"] = "Find and retrieve documents."
    held = TriggerQuery(id="q1", text="find documents", kind="trigger", expected_skill=a)
    host = [TriggerJudgment(query_id="q1", routed_skill=a)]
    edited, decision = apply_description_edit(
        result,
        sources,
        0,
        "Find, retrieve, and rank documents for a query.",
        queries=[held],
        baseline_judgments=host,
        candidate_judgments=host,
    )
    assert decision.accepted, decision.reasons
    assert decision.before_independent == 1.0
    assert decision.after_independent == 1.0
    assert "rank" in str(edited.skills[0].doc.frontmatter["description"])
