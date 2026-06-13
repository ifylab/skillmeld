# Contributing to skillmeld

Thanks for looking. skillmeld is part of the [.ify](https://ifylab.dev) project.

## Development

Requires [uv](https://docs.astral.sh/uv/).

    uv sync
    uv run ruff check .
    uv run ruff format --check .
    uv run ty check
    uv run pytest

## Layout

    src/skillmeld/      the engine: a deterministic Python package that makes no model calls.
                        merge/ is the core eight-step merge; eval/ emit/ security/ registries/
                        split the rest by concern.
    skills/skillmeld/   the Claude Code skill that drives the engine (SKILL.md + scripts/run.sh).
    .claude-plugin/     plugin + marketplace manifests, so the skill installs as a plugin.
    tests/              unit and golden tests, with fixtures/.
    hosted/             placeholder for the hosted-catalog build (not built yet).

The package sits under `src/` (the "src layout") on purpose: it is not importable just because
its folder happens to be the working directory, so tests and tools run against the installed
package rather than the in-tree copy. Packaging mistakes show up immediately instead of hiding.

## Terms

Contributions are accepted under the project's Apache 2.0 license (inbound = outbound); no separate contributor agreement is required. Add an SPDX header to new source files:

    # SPDX-License-Identifier: Apache-2.0

See [LICENSE](LICENSE) and [NOTICE](NOTICE).
