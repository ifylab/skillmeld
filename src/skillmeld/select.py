# SPDX-License-Identifier: Apache-2.0
"""Selection: validate the host Claude's pick down to at most three candidates.

The host Claude returns ids only, in ranked order. We reject anything that does not name an
existing candidate — a hallucinated id dies here — and keep the judgment auditable.
"""

from __future__ import annotations

from collections.abc import Sequence

from skillmeld.models import Candidate, Selection

DEFAULT_LIMIT = 3


class SelectionError(ValueError):
    """The pick failed validation: unknown id, duplicate, empty, or over the limit."""


def select(
    candidates: list[Candidate], chosen_ids: Sequence[str], limit: int = DEFAULT_LIMIT
) -> Selection:
    """Resolve chosen ids against the candidate set, preserving the given (ranked) order."""
    if not chosen_ids:
        raise SelectionError("no skills chosen")
    if len(chosen_ids) > limit:
        raise SelectionError(f"chose {len(chosen_ids)} skills; the limit is {limit}")
    duplicates = sorted({i for i in chosen_ids if chosen_ids.count(i) > 1})
    if duplicates:
        raise SelectionError(f"duplicate ids chosen: {', '.join(duplicates)}")
    by_id = {candidate.entry.id: candidate for candidate in candidates}
    unknown = [chosen for chosen in chosen_ids if chosen not in by_id]
    if unknown:
        raise SelectionError(
            f"ids not in the candidate set: {', '.join(unknown)} — choose only listed ids"
        )
    chosen = [by_id[chosen] for chosen in chosen_ids]
    return Selection(chosen=chosen, warnings=_repo_warnings(chosen))


def _repo_warnings(chosen: list[Candidate]) -> list[str]:
    """Flag picks that share a source repo: usually overlapping content from one author."""
    by_repo: dict[str, list[str]] = {}
    for candidate in chosen:
        repo = candidate.entry.source.repo
        if repo:
            by_repo.setdefault(repo, []).append(candidate.entry.id)
    return [
        f"{' and '.join(ids)} share the source repo {repo}"
        for repo, ids in sorted(by_repo.items())
        if len(ids) > 1
    ]
