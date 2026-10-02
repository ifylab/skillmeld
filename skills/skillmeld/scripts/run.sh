#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Locate the skillmeld engine and run it: the installed CLI on PATH, else this checkout when it
# is the skillmeld repository (a clone or the Claude Code plugin cache), else uv's tool runner.
# SKILLMELD_ENGINE=path|project|uvx forces one route (for tests and debugging).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"

is_checkout() {
  [ -f "$ROOT/pyproject.toml" ] && grep -q '^name = "skillmeld"' "$ROOT/pyproject.toml"
}

case "${SKILLMELD_ENGINE:-auto}" in
  path) exec skillmeld "$@" ;;
  project) exec uv run --project "$ROOT" python -m skillmeld "$@" ;;
  uvx) exec uv tool run skillmeld "$@" ;;
  auto) ;;
  *) echo "skillmeld: SKILLMELD_ENGINE must be path, project or uvx" >&2; exit 2 ;;
esac

if command -v skillmeld >/dev/null 2>&1; then
  exec skillmeld "$@"
fi
if is_checkout && command -v uv >/dev/null 2>&1; then
  exec uv run --project "$ROOT" python -m skillmeld "$@"
fi
if command -v uv >/dev/null 2>&1; then
  exec uv tool run skillmeld "$@"
fi
echo "skillmeld: no engine found. Install it with: uv tool install skillmeld (or pipx install skillmeld)" >&2
exit 127
