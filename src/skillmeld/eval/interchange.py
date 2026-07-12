# SPDX-License-Identifier: Apache-2.0
"""skill-creator eval interchange: read and write ``evals.json`` and ``history.json``.

skillmeld's eval artifacts are its own shapes (TriggerQuery, TriggerScore, EditDecision). The
skill-creator reference schemas are the de-facto interchange format for skill evals, so this
module maps between the two: export the eval query set as a portable ``evals.json``, keep a
portable improvement ledger in ``history.json``, and ingest a source skill's bundled evals as
extra train-side trigger queries. Ingested queries are pinned out of the held-out split
(``trigger.split``); text ingested here feeds evaluation only and never flows into composed
skill output.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field, ValidationError

from skillmeld.eval.evaluate import EditDecision
from skillmeld.eval.trigger import TriggerQuery

# Where a skill keeps its bundled evals: `evals/evals.json` is skill-creator's location; a bare
# root-level `evals.json` is tolerated because community skills predate the convention.
_EVALS_LOCATIONS = ("evals/evals.json", "evals.json")


class InterchangeError(RuntimeError):
    """An evals.json/history.json artifact could not be read or written."""


class EvalCase(BaseModel):
    """One case in skill-creator's ``evals.json``. ``id`` is optional on ingest only."""

    id: int | None = None
    prompt: str
    expected_output: str = ""
    files: list[str] = Field(default_factory=list)
    expectations: list[str] = Field(default_factory=list)


class EvalsFile(BaseModel):
    skill_name: str
    evals: list[EvalCase] = Field(default_factory=list)


class HistoryIteration(BaseModel):
    version: str
    parent: str | None = None
    expectation_pass_rate: float = 0.0
    grading_result: Literal["baseline", "won", "lost", "tie"] = "baseline"
    is_current_best: bool = False


class HistoryFile(BaseModel):
    started_at: str
    skill_name: str
    current_best: str
    iterations: list[HistoryIteration] = Field(default_factory=list)


def load_source_evals(bundle_dir: Path) -> list[EvalCase]:
    """Read a source skill's bundled evals, or [] when it ships none.

    Accepts the skill-creator ``evals.json`` shape and the platform-docs best-practices shape
    (``query``/``expected_behavior``), which maps onto it field-for-field.
    """
    for location in _EVALS_LOCATIONS:
        path = bundle_dir / location
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise InterchangeError(f"bundled evals not readable: {path}: {exc}") from exc
            return _cases_from(data, path)
    return []


def _cases_from(data: object, path: Path) -> list[EvalCase]:
    items: Sequence[object]
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        evals = cast("dict[str, object]", data).get("evals")
        items = evals if isinstance(evals, list) else [data]
    else:
        raise InterchangeError(f"bundled evals have no recognizable shape: {path}")
    cases: list[EvalCase] = []
    for item in items:
        if not isinstance(item, dict):
            raise InterchangeError(f"bundled eval case is not an object: {path}")
        mapped = dict(item)
        if "prompt" not in mapped and "query" in mapped:
            mapped["prompt"] = mapped.pop("query")
        if "expectations" not in mapped and "expected_behavior" in mapped:
            mapped["expectations"] = mapped.pop("expected_behavior")
        mapped.pop("skills", None)
        try:
            cases.append(EvalCase.model_validate(mapped))
        except ValidationError as exc:
            raise InterchangeError(f"bundled eval case is invalid: {path}: {exc}") from exc
    return cases


def queries_from_cases(cases: list[EvalCase], target_skill: str) -> list[TriggerQuery]:
    """Turn ingested cases into trigger queries targeting the skill they shipped with.

    Every query is ``origin="source"``, which pins it to the train side of the split — its text
    typically quotes the skill it came from, so it must never become a held-out metric.
    """
    queries: list[TriggerQuery] = []
    for index, case in enumerate(cases, start=1):
        text = case.prompt.strip()
        if not text:
            continue
        queries.append(
            TriggerQuery(
                id=f"src-{target_skill}-{index}",
                text=text,
                kind="trigger",
                expected_skill=target_skill,
                origin="source",
            )
        )
    return queries


def dump_evals(queries: list[TriggerQuery], skill_name: str, path: Path) -> EvalsFile:
    """Export the eval query set as a skill-creator ``evals.json`` at ``path``.

    The export is the interchange view of the trigger eval: what each prompt is expected to
    route to. skill-creator keeps this file at ``evals/evals.json`` inside the skill directory.
    """
    cases = [
        _interchange_case(number, query)
        for number, query in enumerate(sorted(queries, key=lambda q: q.id), start=1)
    ]
    document = EvalsFile(skill_name=skill_name, evals=cases)
    _write_json(document.model_dump(), path, "evals.json")
    return document


def _interchange_case(number: int, query: TriggerQuery) -> EvalCase:
    if query.kind == "near-miss":
        return EvalCase(
            id=number,
            prompt=query.text,
            expected_output="No skill in the composed set triggers; the request is out of scope.",
            expectations=["No composed skill triggers on the prompt"],
        )
    target = query.expected_skill or ""
    expectations = [f"The composed set routes the prompt to {target}"]
    if query.origin != "host":
        expectations.append(f"Ingested from the {target} skill's bundled evals")
    return EvalCase(
        id=number,
        prompt=query.text,
        expected_output=f"The orchestrator routes this to {target}.",
        expectations=expectations,
    )


def load_history(path: Path) -> HistoryFile | None:
    """Load an existing ``history.json`` ledger, or None when the file does not exist yet."""
    if not path.exists():
        return None
    try:
        return HistoryFile.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise InterchangeError(f"history.json not readable: {path}: {exc}") from exc


def append_history(
    history: HistoryFile | None, *, skill_name: str, decision: EditDecision, started_at: str
) -> HistoryFile:
    """Append one improve outcome to the ledger; the v0 baseline is created on first use.

    An accepted edit grades ``won`` and becomes ``current_best``; a rejected one grades ``lost``
    and leaves the best untouched. ``tie`` is accepted on load for interchange but never emitted:
    an equal-rate edit that clears the gates is an acceptance here. Pass-rates are the held-out
    rates the decision already carries; ``started_at`` comes from the caller so the ledger stays
    deterministic under test.
    """
    if history is None:
        history = HistoryFile(
            started_at=started_at,
            skill_name=skill_name,
            current_best="v0",
            iterations=[
                HistoryIteration(
                    version="v0",
                    expectation_pass_rate=decision.before_held_out,
                    grading_result="baseline",
                    is_current_best=True,
                )
            ],
        )
    if history.skill_name != skill_name:
        raise InterchangeError(
            f"history.json tracks {history.skill_name!r}, not {skill_name!r}; use a separate ledger"
        )
    version = f"v{len(history.iterations)}"
    entry = HistoryIteration(
        version=version,
        parent=history.current_best,
        expectation_pass_rate=decision.after_held_out,
        grading_result="won" if decision.accepted else "lost",
        is_current_best=decision.accepted,
    )
    if decision.accepted:
        for iteration in history.iterations:
            iteration.is_current_best = False
        history.current_best = version
    history.iterations.append(entry)
    return history


def dump_history(history: HistoryFile, path: Path) -> None:
    """Write the ledger to ``path`` (skill-creator keeps it at the workspace root)."""
    _write_json(history.model_dump(), path, "history.json")


def _write_json(payload: dict[str, object], path: Path, label: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise InterchangeError(f"{label} not writable: {path}: {exc}") from exc
