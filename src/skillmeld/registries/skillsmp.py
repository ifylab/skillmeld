# SPDX-License-Identifier: Apache-2.0
"""Build-time adapter for SkillsMP search. Used by the hosted build pipeline, not at runtime."""

from __future__ import annotations

from skillmeld.models import SkillSource


def search(query: str, limit: int = 100) -> list[SkillSource]:
    """Page SkillsMP search results. Build-time only; rate-limited."""
    raise NotImplementedError
