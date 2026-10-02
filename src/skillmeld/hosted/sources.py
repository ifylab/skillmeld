# SPDX-License-Identifier: Apache-2.0
"""The curated source list the production catalog is built from.

Membership is a deliberate decision, not a crawl of everything: each repo here is crawled,
hash-pinned, and security-scanned into the published catalog and verdict index. Extend by
adding an ``owner/name`` and rebuilding.
"""

from __future__ import annotations

PRODUCTION_REPOS: list[str] = [
    # Anthropic's reference skills: skills/<name>/, per-skill LICENSE.txt (Apache-2.0 for most;
    # docx, pdf, pptx and xlsx are source-available and resolve license-unknown). 2026-09-30.
    "anthropics/skills",
    # obra/superpowers: skills/<name>/, MIT at the root. 2026-09-30.
    "obra/superpowers",
    # Google's official skills: skills/<category>/<name>/, Apache-2.0 at the root, about 155
    # skills, no agent-specific sidecars. 2026-09-30.
    "google/skills",
    # Microsoft's skills: .github/plugins/<plugin>/skills/<name>/, MIT at the root, about 205
    # skills; the crawler finds every SKILL.md wherever it sits. 2026-09-30.
    "microsoft/skills",
]
