# SPDX-License-Identifier: Apache-2.0
"""Smoke tests: the package imports and the CLI builds and handles the empty invocation."""

from __future__ import annotations

import skillmeld
from skillmeld.cli import build_parser, main


def test_version_present() -> None:
    assert skillmeld.__version__


def test_parser_builds() -> None:
    assert build_parser() is not None


def test_cli_no_command_returns_usage() -> None:
    assert main([]) == 2
