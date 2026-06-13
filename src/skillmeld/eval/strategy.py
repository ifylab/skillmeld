# SPDX-License-Identifier: Apache-2.0
"""Pluggable improve strategies behind a stable interface with mandatory safety gates.

Every strategy, present or future, must clear the same gates: the body is byte-traceable
(unchanged unless the strategy is explicitly allowed to re-select existing atoms), held-out
queries do not leak, and structural quality holds. The conservative default edits the
description only. The task-pass-rate strategy is registered but inactive — it needs a safe
task-execution sandbox and ships as the first post-launch plug-in, behind these same gates.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from skillmeld.eval.leakage import held_out_leaks
from skillmeld.eval.quality import score_quality
from skillmeld.eval.trigger import TriggerQuery
from skillmeld.merge.verify import verify
from skillmeld.models import MergeResult, SkillDoc


class GateResult(BaseModel):
    passed: bool = True
    reasons: list[str] = Field(default_factory=list)


class ImproveStrategy(Protocol):
    name: str
    allows_body_change: bool

    def gate(
        self,
        before: MergeResult,
        after: MergeResult,
        sources: list[SkillDoc],
        queries: list[TriggerQuery],
        held_out_ids: list[str],
    ) -> GateResult: ...


class ConservativeStrategy:
    """Description edits only. Bodies must stay byte-identical; all output stays traceable."""

    name = "conservative"
    allows_body_change = False

    def gate(
        self,
        before: MergeResult,
        after: MergeResult,
        sources: list[SkillDoc],
        queries: list[TriggerQuery],
        held_out_ids: list[str],
    ) -> GateResult:
        reasons: list[str] = []
        if _bodies(before) != _bodies(after):
            reasons.append("body changed; the conservative strategy may edit descriptions only")
        reasons.extend(f"verifier: {p}" for p in verify(after, sources))
        leaks = held_out_leaks(after, queries, held_out_ids)
        if leaks:
            reasons.append(f"held-out queries leak into the skill: {', '.join(leaks)}")
        # Block only quality issues this edit introduces. A sibling skill still awaiting its own
        # description (an "empty" issue present before the edit) must not block authoring this one.
        pre_existing = _quality_issues(before)
        for skill, issue in _quality_issues(after):
            if (skill, issue) not in pre_existing:
                reasons.append(f"quality[{skill}]: {issue}")
        return GateResult(passed=not reasons, reasons=reasons)


class TaskPassRateStrategy:
    """Post-launch: execute use-case tasks in a sandbox and require beating the inputs.

    Registered so the interface is real, but inactive — invoking it raises until the safe
    task-execution sandbox lands. It will clear the same mandatory gates as every strategy.
    """

    name = "task-pass-rate"
    allows_body_change = False

    def gate(self, *args: object, **kwargs: object) -> GateResult:
        raise NotImplementedError(
            "task-pass-rate eval ships post-launch; it needs a safe task-execution sandbox"
        )


STRATEGIES: dict[str, ImproveStrategy] = {
    ConservativeStrategy.name: ConservativeStrategy(),
    TaskPassRateStrategy.name: TaskPassRateStrategy(),
}
DEFAULT_STRATEGY = ConservativeStrategy.name


def _bodies(result: MergeResult) -> list[str]:
    bodies = [skill.doc.body for skill in result.skills]
    if result.orchestrator is not None:
        bodies.append(result.orchestrator.doc.body)
    return bodies


def _quality_issues(result: MergeResult) -> set[tuple[str, str]]:
    """Every (skill-name, issue) across the emitted set, orchestrator included."""
    docs = [skill.doc for skill in result.skills]
    if result.orchestrator is not None:
        docs.append(result.orchestrator.doc)
    issues: set[tuple[str, str]] = set()
    for doc in docs:
        report = score_quality(doc)
        issues.update((report.skill, issue) for issue in report.issues)
    return issues
