# SPDX-License-Identifier: Apache-2.0
"""Orchestrate the conservative eval: structural quality, byte-traceability, leakage, triggers.

``evaluate`` scores a merged set (no model calls). ``apply_description_edit`` is the single
mutation the conservative improve loop allows — it gates a host-Claude-authored description
through the strategy and accepts it only if the held-out trigger pass-rate does not regress.
Bodies never change here; authoring is confined to the description and gated every time.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from skillmeld.eval.leakage import held_out_leaks
from skillmeld.eval.quality import QualityReport, score_quality
from skillmeld.eval.strategy import DEFAULT_STRATEGY, STRATEGIES
from skillmeld.eval.trigger import TriggerJudgment, TriggerQuery, TriggerScore, score_trigger, split
from skillmeld.merge.verify import verify
from skillmeld.models import MergeResult, SkillDoc


class EvalReport(BaseModel):
    quality: list[QualityReport] = Field(default_factory=list)
    trigger: TriggerScore | None = None
    leakage: list[str] = Field(default_factory=list)
    verifier_problems: list[str] = Field(default_factory=list)
    passed: bool = True


class EditDecision(BaseModel):
    accepted: bool = False
    reasons: list[str] = Field(default_factory=list)
    before_held_out: float = 0.0
    after_held_out: float = 0.0


def evaluate(
    result: MergeResult,
    sources: list[SkillDoc],
    *,
    queries: list[TriggerQuery] | None = None,
    judgments: list[TriggerJudgment] | None = None,
) -> EvalReport:
    """Score a merged set across every mandatory gate. ``passed`` requires all of them clean."""
    quality = [score_quality(skill.doc) for skill in result.skills]
    problems = verify(result, sources)
    trigger: TriggerScore | None = None
    leaks: list[str] = []
    if queries:
        trigger = score_trigger(queries, judgments or [])
        leaks = held_out_leaks(result, queries, trigger.held_out_ids)
    passed = all(report.passed for report in quality) and not problems and not leaks
    return EvalReport(
        quality=quality,
        trigger=trigger,
        leakage=leaks,
        verifier_problems=problems,
        passed=passed,
    )


def apply_description_edit(
    result: MergeResult,
    sources: list[SkillDoc],
    target: int | str,
    new_description: str,
    *,
    queries: list[TriggerQuery],
    baseline_judgments: list[TriggerJudgment],
    candidate_judgments: list[TriggerJudgment],
    strategy: str = DEFAULT_STRATEGY,
) -> tuple[MergeResult, EditDecision]:
    """Gate a description edit. Returns the edited result if accepted, else the original.

    ``target`` is a child-skill index, or the literal ``"orchestrator"`` to edit the router.
    """
    after = result.model_copy(deep=True)
    doc = _resolve_target(after, target)
    if doc is None:
        return result, EditDecision(accepted=False, reasons=[f"no skill at target {target!r}"])
    doc.frontmatter["description"] = new_description

    _, held_out_ids = split(queries)
    gate = STRATEGIES[strategy].gate(result, after, sources, queries, held_out_ids)

    before = score_trigger(queries, baseline_judgments).held_out_pass_rate
    candidate = score_trigger(queries, candidate_judgments).held_out_pass_rate
    regressed = candidate < before

    reasons = list(gate.reasons)
    if regressed:
        reasons.append(f"held-out pass-rate regressed ({before} -> {candidate})")
    accepted = gate.passed and not regressed
    decision = EditDecision(
        accepted=accepted,
        reasons=reasons,
        before_held_out=before,
        after_held_out=candidate,
    )
    return (after if accepted else result), decision


def _resolve_target(result: MergeResult, target: int | str) -> SkillDoc | None:
    """Resolve an edit target to the doc it names: a child index or the orchestrator."""
    if isinstance(target, str) and target == "orchestrator":
        return result.orchestrator.doc if result.orchestrator is not None else None
    if isinstance(target, int) and 0 <= target < len(result.skills):
        return result.skills[target].doc
    return None
