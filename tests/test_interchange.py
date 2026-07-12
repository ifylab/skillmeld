# SPDX-License-Identifier: Apache-2.0
"""Eval interchange tests: evals.json/history.json round-trips, ingest mapping, split pinning."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skillmeld.eval.evaluate import EditDecision, evaluate
from skillmeld.eval.interchange import (
    EvalCase,
    InterchangeError,
    append_history,
    dump_evals,
    dump_history,
    load_history,
    load_source_evals,
    queries_from_cases,
)
from skillmeld.eval.trigger import TriggerQuery, split
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


# --- load_source_evals -------------------------------------------------------------------


def test_load_source_evals_reads_skill_creator_shape(tmp_path: Path) -> None:
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()
    (evals_dir / "evals.json").write_text(
        json.dumps(
            {
                "skill_name": "retriever",
                "evals": [
                    {
                        "id": 1,
                        "prompt": "find the design documents",
                        "expected_output": "Documents are retrieved.",
                        "expectations": ["Retrieves every matching document"],
                    }
                ],
            }
        )
    )
    cases = load_source_evals(tmp_path)
    assert len(cases) == 1
    assert cases[0].prompt == "find the design documents"
    assert cases[0].expectations == ["Retrieves every matching document"]


def test_load_source_evals_maps_best_practices_shape(tmp_path: Path) -> None:
    # The platform-docs shape uses query/expected_behavior; both map onto the canonical fields.
    (tmp_path / "evals.json").write_text(
        json.dumps(
            [
                {
                    "skills": ["retriever"],
                    "query": "search the corpus for specs",
                    "files": ["test-files/spec.md"],
                    "expected_behavior": ["Searches the corpus", "Returns the spec"],
                }
            ]
        )
    )
    cases = load_source_evals(tmp_path)
    assert len(cases) == 1
    assert cases[0].prompt == "search the corpus for specs"
    assert cases[0].expectations == ["Searches the corpus", "Returns the spec"]
    assert cases[0].files == ["test-files/spec.md"]


def test_load_source_evals_absent_is_empty(tmp_path: Path) -> None:
    assert load_source_evals(tmp_path) == []


def test_load_source_evals_malformed_raises(tmp_path: Path) -> None:
    (tmp_path / "evals.json").write_text("not json {")
    with pytest.raises(InterchangeError, match="not readable"):
        load_source_evals(tmp_path)
    (tmp_path / "evals.json").write_text('"just a string"')
    with pytest.raises(InterchangeError, match="no recognizable shape"):
        load_source_evals(tmp_path)


# --- queries_from_cases + split pinning ---------------------------------------------------


def test_queries_from_cases_targets_the_source_skill() -> None:
    cases = [EvalCase(prompt="find documents"), EvalCase(prompt="   ")]
    queries = queries_from_cases(cases, "retriever")
    assert len(queries) == 1  # the blank prompt is skipped
    query = queries[0]
    assert query.id == "src-retriever-1"
    assert query.kind == "trigger"
    assert query.expected_skill == "retriever"
    assert query.origin == "source"


HOST_QUERIES = [
    TriggerQuery(id="q1", text="find me documents", kind="trigger", expected_skill="retriever"),
    TriggerQuery(id="q2", text="search the corpus", kind="trigger", expected_skill="retriever"),
    TriggerQuery(id="q3", text="check document quality", kind="trigger", expected_skill="reviewer"),
    TriggerQuery(id="q4", text="order me a pizza", kind="near-miss", expected_skill=None),
]


def test_split_pins_ingested_queries_to_train() -> None:
    ingested = queries_from_cases([EvalCase(prompt="always validate the query first")], "retriever")
    train, held_out = split([*HOST_QUERIES, *ingested])
    # The held-out stride runs over host queries exactly as before; ingested ids always train.
    assert held_out == split(HOST_QUERIES)[1]
    assert ingested[0].id in train
    assert ingested[0].id not in held_out


def test_split_with_only_host_queries_is_unchanged() -> None:
    train, held_out = split(HOST_QUERIES)
    assert set(train).isdisjoint(held_out)
    assert set(train) | set(held_out) == {"q1", "q2", "q3", "q4"}


def test_ingested_text_quoting_a_skill_body_never_leaks() -> None:
    # SKILL_A's body contains this exact directive; as a host query it could be held out and
    # trip the leakage gate. Ingested queries are train-pinned, so the gate stays clean.
    result, sources = _merge()
    ingested = queries_from_cases(
        [EvalCase(prompt="Always validate the query first.")], "retriever"
    )
    report = evaluate(result, sources, queries=[*HOST_QUERIES, *ingested])
    assert report.leakage == []
    assert report.ingested_query_ids == [ingested[0].id]


# --- dump_evals ---------------------------------------------------------------------------


def test_dump_evals_writes_the_interchange_shape(tmp_path: Path) -> None:
    ingested = queries_from_cases([EvalCase(prompt="fetch the specs")], "retriever")
    out = tmp_path / "evals" / "evals.json"
    document = dump_evals([HOST_QUERIES[0], HOST_QUERIES[3], *ingested], "orchestrator", out)

    data = json.loads(out.read_text())
    assert data["skill_name"] == "orchestrator"
    assert [case["id"] for case in data["evals"]] == [1, 2, 3]
    by_prompt = {case["prompt"]: case for case in data["evals"]}
    trigger = by_prompt["find me documents"]
    assert trigger["expected_output"] == "The orchestrator routes this to retriever."
    assert trigger["expectations"] == ["The composed set routes the prompt to retriever"]
    near_miss = by_prompt["order me a pizza"]
    assert "No skill" in near_miss["expected_output"]
    src = by_prompt["fetch the specs"]
    assert any("bundled evals" in line for line in src["expectations"])
    assert document.evals[0].id == 1


# --- history ------------------------------------------------------------------------------


def test_history_first_append_creates_baseline_and_won() -> None:
    decision = EditDecision(accepted=True, before_held_out=0.5, after_held_out=1.0)
    ledger = append_history(
        None, skill_name="orchestrator", decision=decision, started_at="2026-07-10T00:00:00+00:00"
    )
    assert ledger.started_at == "2026-07-10T00:00:00+00:00"
    assert [i.version for i in ledger.iterations] == ["v0", "v1"]
    v0, v1 = ledger.iterations
    assert (v0.grading_result, v0.parent, v0.expectation_pass_rate) == ("baseline", None, 0.5)
    assert (v1.grading_result, v1.parent, v1.expectation_pass_rate) == ("won", "v0", 1.0)
    assert ledger.current_best == "v1"
    assert [i.is_current_best for i in ledger.iterations] == [False, True]


def test_history_rejected_edit_keeps_the_best() -> None:
    won = EditDecision(accepted=True, before_held_out=0.5, after_held_out=1.0)
    lost = EditDecision(accepted=False, before_held_out=1.0, after_held_out=0.25)
    ledger = append_history(
        None, skill_name="orchestrator", decision=won, started_at="2026-07-10T00:00:00+00:00"
    )
    ledger = append_history(
        ledger, skill_name="orchestrator", decision=lost, started_at="ignored-after-creation"
    )
    assert [i.version for i in ledger.iterations] == ["v0", "v1", "v2"]
    v2 = ledger.iterations[-1]
    assert (v2.grading_result, v2.parent, v2.is_current_best) == ("lost", "v1", False)
    assert ledger.current_best == "v1"
    assert ledger.started_at == "2026-07-10T00:00:00+00:00"


def test_history_wrong_skill_raises() -> None:
    decision = EditDecision(accepted=False)
    ledger = append_history(
        None, skill_name="orchestrator", decision=decision, started_at="2026-07-10T00:00:00+00:00"
    )
    with pytest.raises(InterchangeError, match="separate ledger"):
        append_history(
            ledger,
            skill_name="other-set",
            decision=decision,
            started_at="2026-07-10T00:00:00+00:00",
        )


def test_history_round_trips_through_disk(tmp_path: Path) -> None:
    decision = EditDecision(accepted=True, before_held_out=0.0, after_held_out=0.75)
    ledger = append_history(
        None, skill_name="orchestrator", decision=decision, started_at="2026-07-10T00:00:00+00:00"
    )
    path = tmp_path / "history.json"
    dump_history(ledger, path)
    assert load_history(path) == ledger
    assert load_history(tmp_path / "absent.json") is None


def test_load_history_malformed_raises(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text('{"started_at": 3}')
    with pytest.raises(InterchangeError, match="not readable"):
        load_history(path)
