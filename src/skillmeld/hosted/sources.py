# SPDX-License-Identifier: Apache-2.0
"""The curated source list the production catalog is built from.

Membership is a deliberate decision, not a crawl of everything: each repo here is crawled,
hash-pinned, and security-scanned into the published catalog and verdict index. Extend by
adding an ``owner/name`` and rebuilding.
"""

from __future__ import annotations

PRODUCTION_REPOS: list[str] = [
    "anthropics/skills",
    "obra/superpowers",
]
