# SPDX-License-Identifier: Apache-2.0
"""Discovery: deterministic prefilter of the catalog against a use-case profile.

Transparent token matching with recorded evidence per match; the host Claude ranks the
surviving shortlist in-context. No embeddings, no model calls. Entries already carrying a
BLOCK verdict in the cached index are dropped before the user ever sees them.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Set

from skillmeld.models import Candidate, CatalogEntry, DiscoveryResult, UseCaseProfile

DEFAULT_LIMIT = 50

_WEIGHTS: dict[str, float] = {
    "language": 3.0,
    "framework": 2.0,
    "convention": 1.0,
    "task": 1.0,
    "term": 0.5,
}

_TOKEN = re.compile(r"[a-z0-9.+#-]+")

_STOPWORDS = frozenset(
    {
        "a",
        "against",
        "an",
        "and",
        "are",
        "but",
        "can",
        "for",
        "from",
        "get",
        "has",
        "have",
        "how",
        "into",
        "its",
        "make",
        "need",
        "needs",
        "new",
        "not",
        "off",
        "one",
        "our",
        "out",
        "over",
        "run",
        "that",
        "the",
        "their",
        "them",
        "then",
        "this",
        "use",
        "used",
        "uses",
        "using",
        "want",
        "what",
        "when",
        "with",
        "work",
        "you",
        "your",
    }
)


def discover(
    profile: UseCaseProfile,
    catalog: list[CatalogEntry],
    *,
    blocked: Set[str] = frozenset(),
    limit: int = DEFAULT_LIMIT,
) -> DiscoveryResult:
    """Prefilter the catalog: score by token match, record evidence, cap at ``limit``.

    Output order is deterministic (score, then stars, then name, then id). Ranking by
    use-case fit within the shortlist is the host Claude's judgment, not ours.
    """
    needles = _profile_needles(profile)
    haystacks = [_entry_haystack(entry) for entry in catalog]
    idf = _idf(needles, haystacks)
    candidates: list[Candidate] = []
    excluded_blocked = 0
    for entry, haystack in zip(catalog, haystacks, strict=True):
        if entry.bundle_hash and entry.bundle_hash in blocked:
            excluded_blocked += 1
            continue
        score, matched = _match(haystack, needles, idf)
        if score > 0:
            candidates.append(Candidate(entry=entry, score=round(score, 3), matched=matched))
    candidates.sort(
        key=lambda c: (
            -c.score,
            -(c.entry.source.stars if c.entry.source.stars is not None else -1),
            c.entry.source.name.lower(),
            c.entry.id,
        )
    )
    return DiscoveryResult(
        candidates=candidates[:limit],
        considered=len(catalog),
        excluded_blocked=excluded_blocked,
    )


def _profile_needles(profile: UseCaseProfile) -> list[tuple[str, float, list[str]]]:
    """Expand the profile into labelled needle groups, deduplicated and order-stable."""
    task_tokens: set[str] = set()
    for task in profile.tasks:
        task_tokens |= tokenize(task)
    groups = [
        ("language", _dedupe(profile.languages)),
        ("framework", _dedupe(profile.frameworks)),
        ("convention", _dedupe(profile.conventions)),
        ("task", sorted(task_tokens)),
        ("term", sorted(tokenize(profile.summary))),
    ]
    return [(label, _WEIGHTS[label], needles) for label, needles in groups]


def _idf(
    needle_groups: list[tuple[str, float, list[str]]], haystacks: list[set[str]]
) -> dict[str, float]:
    """Inverse document frequency per needle: a term in few entries discriminates more than one
    in many. This stops a broad grab-bag skill that matches common terms ("python", a framework
    name) from outranking the precise skill that matches the rare, specific terms the use case
    actually turns on."""
    total = len(haystacks)
    scores: dict[str, float] = {}
    for _, _, needles in needle_groups:
        for needle in needles:
            if needle in scores:
                continue
            frequency = sum(1 for haystack in haystacks if _hit(needle, haystack))
            scores[needle] = math.log(1 + total / frequency) if frequency else 0.0
    return scores


def _match(
    haystack: set[str], needle_groups: list[tuple[str, float, list[str]]], idf: dict[str, float]
) -> tuple[float, list[str]]:
    """Score one entry. Each needle counts at most once (stuffing a description gains nothing),
    weighted by its label and its inverse document frequency across the catalog."""
    score = 0.0
    matched: list[str] = []
    for label, weight, needles in needle_groups:
        for needle in needles:
            if _hit(needle, haystack):
                score += weight * idf.get(needle, 0.0)
                matched.append(f"{label}:{needle}")
    return score, sorted(matched)


def _entry_haystack(entry: CatalogEntry) -> set[str]:
    exact = {_norm(v) for v in entry.languages} | {_norm(t) for t in entry.tags}
    text = tokenize(f"{entry.source.name} {entry.description}")
    return exact | text


def _hit(needle: str, haystack: set[str]) -> bool:
    if needle in haystack:
        return True
    parts = tokenize(needle)
    return bool(parts) and parts <= haystack


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        norm = _norm(value)
        if norm and norm not in seen:
            seen.append(norm)
    return seen


def _norm(value: str) -> str:
    return value.strip().lower()


def tokenize(text: str) -> set[str]:
    """Lowercase word tokens, stopwords removed; the shared tokenizer for token matching."""
    found: set[str] = set()
    for raw in _TOKEN.findall(text.lower()):
        token = raw.strip(".-")
        if not token or token in _STOPWORDS:
            continue
        if len(token) >= 3 or "#" in token or "+" in token:
            found.add(token)
    return found
