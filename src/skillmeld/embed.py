# SPDX-License-Identifier: Apache-2.0
"""Optional local embeddings (model2vec/potion) for near-duplicate flagging at scale.

Off by default: the host Claude groups in-context. This is the offline / large-input fallback.
"""

from __future__ import annotations


def embed(texts: list[str]) -> list[list[float]]:
    """Embed texts with a vendored static model. Lazy-loaded; optional dependency."""
    raise NotImplementedError
