# SPDX-License-Identifier: Apache-2.0
"""Intake tests: normalization and the thin-use-case flag."""

from __future__ import annotations

from skillmeld.intake import intake


def test_concrete_use_case_is_not_thin() -> None:
    result = intake("Extract a quantity takeoff from IFC models and validate them")
    assert not result.thin
    assert result.word_count >= 4
    assert result.reasons == []


def test_short_use_case_is_thin() -> None:
    result = intake("help me")
    assert result.thin
    assert any("short" in reason for reason in result.reasons)


def test_vague_use_case_is_thin() -> None:
    result = intake("do some skills stuff things")
    assert result.thin
    assert any("concrete" in reason for reason in result.reasons)


def test_normalizes_whitespace() -> None:
    result = intake("  retrieve   documents\n\nfor a query  ")
    assert result.use_case == "retrieve documents for a query"
