#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Thin entrypoint: run the skillmeld engine via uv from the plugin root.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
exec uv run --project "$ROOT" python -m skillmeld "$@"
