# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record the retry waits instead of sleeping through them."""
    recorded: list[float] = []
    monkeypatch.setattr("skillmeld.registries.net.sleep", recorded.append)
    return recorded
