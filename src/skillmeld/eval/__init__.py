# SPDX-License-Identifier: Apache-2.0
"""Task-anchored evaluation and a conservative, pluggable improve loop.

The public entry points live in ``evaluate`` (scoring + the gated description edit) and
``strategy`` (the pluggable improve strategies).
"""

from __future__ import annotations
