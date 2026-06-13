# SPDX-License-Identifier: Apache-2.0
"""Step 4 — detect structural conflicts (Python) and merge Claude's adjudication.

Python flags high-precision structural conflicts: antonymic directives on the same subject,
numeric-bound clashes on the same key, and same-trigger/different-action pairs. The host
Claude then adjudicates the flagged pairs (picking a winner) and may add ``semantic``
conflicts it spots — but it can never make a structural conflict disappear. That
non-dismissibility is what stops a hostile skill's contradiction from being "adjudicated away".
"""

from __future__ import annotations

import re

from skillmeld.models import Atom, AtomKind, Conflict

_POSITIVE = re.compile(r"\b(always|must|use|do|enable|require|prefer|ensure)\b", re.IGNORECASE)
_NEGATIVE = re.compile(
    r"\b(never|don'?t|do not|avoid|disable|forbid|must not|should not)\b", re.IGNORECASE
)
_NUMERIC = re.compile(r"\b([a-z][a-z _-]{2,}?)\s*(?:[:=]|to|of|is|=|at)?\s*(\d+(?:\.\d+)?)\b", re.I)
_STOP_SUBJECT = frozenset(
    {"always", "never", "must", "should", "do", "not", "dont", "avoid", "use", "the", "a", "an"}
)


def detect_structural(
    atoms: list[Atom], kinds: dict[str, AtomKind] | None = None
) -> list[Conflict]:
    """Flag structural conflicts between atoms. Deterministic; order-stable by atom id."""
    kind_of = kinds or {}

    def kind(atom: Atom) -> AtomKind | None:
        return kind_of.get(atom.id, atom.kind or atom.detected_kind)

    directives = [a for a in atoms if kind(a) is AtomKind.directive]
    triggers = [a for a in atoms if kind(a) is AtomKind.trigger]

    conflicts: list[Conflict] = []
    conflicts.extend(_polarity_conflicts(directives))
    conflicts.extend(_numeric_conflicts(directives))
    conflicts.extend(_trigger_conflicts(triggers))
    conflicts.sort(key=lambda c: (c.atom_a, c.atom_b, c.type))
    return conflicts


def merge_adjudication(structural: list[Conflict], adjudicated: list[Conflict]) -> list[Conflict]:
    """Combine structural conflicts with Claude's adjudication. Structural are non-dismissible.

    Claude may set a ``winner`` on a structural conflict and may add ``semantic`` conflicts,
    but every structural pair must survive. A missing structural pair is a hard error.
    """
    adjudicated_by_pair = {(_pair(c)): c for c in adjudicated}
    out: list[Conflict] = []
    for conflict in structural:
        verdict = adjudicated_by_pair.get(_pair(conflict))
        winner = verdict.winner if verdict is not None else None
        if winner is not None and winner not in (conflict.atom_a, conflict.atom_b):
            raise ConflictError(
                f"adjudicated winner {winner!r} is not part of conflict "
                f"{conflict.atom_a}|{conflict.atom_b}"
            )
        out.append(conflict.model_copy(update={"winner": winner}))
    for conflict in adjudicated:
        if conflict.source == "semantic" and _pair(conflict) not in {_pair(c) for c in out}:
            out.append(conflict.model_copy(update={"source": "semantic"}))
    out.sort(key=lambda c: (c.atom_a, c.atom_b, c.type))
    return out


class ConflictError(ValueError):
    """An adjudication response was inconsistent with the structural conflict set."""


def _polarity_conflicts(directives: list[Atom]) -> list[Conflict]:
    conflicts: list[Conflict] = []
    for first, second in _pairs(directives):
        a_pos, a_neg = _polarity(first.text)
        b_pos, b_neg = _polarity(second.text)
        opposed = (a_pos and b_neg) or (a_neg and b_pos)
        if opposed and _subject(first.text) & _subject(second.text):
            conflicts.append(_conflict(first, second, "antonymic-directive"))
    return conflicts


def _numeric_conflicts(directives: list[Atom]) -> list[Conflict]:
    conflicts: list[Conflict] = []
    bounds = [(atom, dict(_numbers(atom.text))) for atom in directives]
    for (a_atom, a_nums), (b_atom, b_nums) in _pairs(bounds):
        shared = set(a_nums) & set(b_nums)
        if any(a_nums[key] != b_nums[key] for key in shared):
            conflicts.append(_conflict(a_atom, b_atom, "numeric-bound"))
    return conflicts


def _trigger_conflicts(triggers: list[Atom]) -> list[Conflict]:
    conflicts: list[Conflict] = []
    for first, second in _pairs(triggers):
        if first.skill != second.skill and first.norm_key == second.norm_key:
            conflicts.append(_conflict(first, second, "same-trigger"))
    return conflicts


def _pairs(items: list) -> list[tuple]:
    return [(items[i], items[j]) for i in range(len(items)) for j in range(i + 1, len(items))]


def _conflict(a: Atom, b: Atom, kind: str) -> Conflict:
    first, second = sorted((a.id, b.id))
    return Conflict(atom_a=first, atom_b=second, type=kind, source="structural")


def _pair(conflict: Conflict) -> tuple[str, str]:
    first, second = sorted((conflict.atom_a, conflict.atom_b))
    return (first, second)


def _polarity(text: str) -> tuple[bool, bool]:
    return bool(_POSITIVE.search(text)), bool(_NEGATIVE.search(text))


def _subject(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOP_SUBJECT}


def _numbers(text: str) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for key, value in _NUMERIC.findall(text):
        out.append((key.strip().lower(), float(value)))
    return out
