# SPDX-License-Identifier: Apache-2.0
"""Trigger-eval scoring for orchestrator routing. Python scores; the host Claude judges.

The host Claude reports, for each query, which skill the orchestrator routed it to. Python
holds the query set, makes a deterministic train/held-out split, and computes pass-rates.
Selection happens on the held-out split so description tuning can't overfit the train queries.
A trigger query passes when it routes to its expected skill; a near-miss passes when it
correctly routes nowhere.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

HELD_OUT_FRACTION = 0.4


class TriggerQuery(BaseModel):
    id: str
    text: str
    kind: Literal["trigger", "near-miss"]
    expected_skill: str | None = None


class TriggerJudgment(BaseModel):
    query_id: str
    routed_skill: str | None = None


class TriggerScore(BaseModel):
    total: int = 0
    pass_rate: float = 0.0
    train_pass_rate: float = 0.0
    held_out_pass_rate: float = 0.0
    train_ids: list[str] = Field(default_factory=list)
    held_out_ids: list[str] = Field(default_factory=list)
    failed_ids: list[str] = Field(default_factory=list)


def split(
    queries: list[TriggerQuery], held_out_fraction: float = HELD_OUT_FRACTION
) -> tuple[list[str], list[str]]:
    """Deterministic train/held-out split by sorted id; every Nth query is held out."""
    ordered = sorted(q.id for q in queries)
    if not ordered:
        return [], []
    stride = max(2, round(1 / held_out_fraction)) if held_out_fraction > 0 else 0
    held_out = [qid for index, qid in enumerate(ordered) if stride and index % stride == 0]
    train = [qid for qid in ordered if qid not in set(held_out)]
    return train, held_out


def score_trigger(queries: list[TriggerQuery], judgments: list[TriggerJudgment]) -> TriggerScore:
    """Score routing judgments against expected outcomes. Selection metric is held-out pass-rate."""
    routed = {j.query_id: j.routed_skill for j in judgments}
    train_ids, held_out_ids = split(queries)
    held_out_set = set(held_out_ids)

    failed: list[str] = []
    train_pass = train_total = held_pass = held_total = 0
    for query in queries:
        ok = _passes(query, routed.get(query.id))
        if not ok:
            failed.append(query.id)
        if query.id in held_out_set:
            held_total += 1
            held_pass += int(ok)
        else:
            train_total += 1
            train_pass += int(ok)

    total = len(queries)
    passed = sum(1 for q in queries if _passes(q, routed.get(q.id)))
    return TriggerScore(
        total=total,
        pass_rate=_ratio(passed, total),
        train_pass_rate=_ratio(train_pass, train_total),
        held_out_pass_rate=_ratio(held_pass, held_total),
        train_ids=train_ids,
        held_out_ids=held_out_ids,
        failed_ids=sorted(failed),
    )


def _passes(query: TriggerQuery, routed_skill: str | None) -> bool:
    if query.kind == "trigger":
        return routed_skill == query.expected_skill
    return routed_skill is None


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 3) if denominator else 0.0
