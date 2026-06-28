# skillmeld

<!-- A demo GIF of a real /skillmeld run (the Grasshopper conflict beat) is planned as the README hero — see workbook/Plan/repo-presentation.md and roadmap.md. -->

[![CI](https://github.com/ifylab/skillmeld/actions/workflows/ci.yml/badge.svg)](https://github.com/ifylab/skillmeld/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/github/license/ifylab/skillmeld)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
<!-- On PyPI publish, add: [![PyPI](https://img.shields.io/pypi/v/skillmeld)](https://pypi.org/project/skillmeld/) -->

Describe what you want to do, point skillmeld at your repo, and it finds existing community skills for the job, security-scans them, and merges the best two or three into one coherent skill set tailored to your project — instead of writing one from scratch.

It runs on your own Claude in Claude Code, grounds in your repo, and shows you what it pulled, what it found, and why before anything is installed. It builds on the existing skills ecosystem (the open standard, community marketplaces, and registries) rather than replacing it.

## What makes it different

skillmeld composes; it does not generate. Every line in a merged skill traces byte-for-byte back to a source skill — a deterministic verifier enforces this, so the tool can never invent an instruction. The hard, mechanical work (parsing, security scanning, deduplicating, conflict detection, packaging) runs as deterministic Python that makes zero model calls. Your Claude supplies the judgment; the engine supplies the guarantees.

## How it works

A Claude Code skill drives a bundled Python engine through one pipeline:

```
intake -> ground -> discover -> select (<=3) -> security gate -> merge -> eval -> emit
```

- **ground** scans your repo into a use-case profile, locally.
- **discover** prefilters a signed catalog of community skills and your Claude ranks the shortlist.
- **security gate** scans every candidate (PASS / REVIEW / BLOCK) before you see it, and again after merge.
- **merge** parses each skill into byte-exact atoms, deduplicates, resolves conflicts, prunes to your use case, and partitions the result into at most three skills behind a thin routing orchestrator. A verifier proves every output atom traces to a source.
- **emit** packages the result for Claude Code, a claude.ai zip, the API, or a Claude Code plugin marketplace, with a `PROVENANCE.md` recording where every part came from.

## Install

skillmeld runs in Claude Code and needs [uv](https://docs.astral.sh/uv/). Clone it and check it runs:

```sh
git clone https://github.com/ifylab/skillmeld && cd skillmeld
uv run skillmeld --help
```

A PyPI release for one-line install (`uv tool install skillmeld` / `pipx install skillmeld`) is planned.

## Quickstart

As a skill, add the marketplace, install the plugin, and invoke it with your use case:

```
/plugin marketplace add ./skillmeld
/plugin install skillmeld@ifylab
/skillmeld I get IFC models from architects and need a quantity takeoff plus validation
```

Or exercise the engine directly from the CLI (each command prints JSON):

```sh
uv run skillmeld ground .                       # scan a repo into a profile
uv run skillmeld scan path/to/skill --license   # security- and license-scan a bundle
uv run skillmeld merge --bundles a/ b/ --profile profile.json
```

## What it isn't

- **Not a generator.** It assembles existing skills; it never authors new instructions. A convention no source skill covers is yours to add, not a gap skillmeld fills.
- **Not a catalog.** It composes from community marketplaces and registries rather than being one.
- **Not a model.** The engine makes zero LLM calls; the judgment comes from your own Claude, on your tokens.

## Acknowledgements

skillmeld stands on the open Agent Skills ecosystem — the skill format, the community marketplaces, and the registries that publish and share skills. It composes that work; it does not replace it. Security scanning leans on [bandit](https://github.com/PyCQA/bandit), with optional [semgrep](https://semgrep.dev/) and [gitleaks](https://github.com/gitleaks/gitleaks) when present.

## Status

In active development, built in the open one piece at a time. The discovery, security, merge, evaluation, and packaging stages are implemented and tested; the hosted catalog and the curated AEC corpus are coming next. See the [changelog](CHANGELOG.md).

## Stack

Python 3.12, managed with uv. Ruff for lint and format, ty for type-checking, pytest for tests.

```sh
uv run ruff check . && uv run ty check && uv run pytest
```

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Contributions are accepted under the project's Apache 2.0 license (inbound = outbound); no separate contributor agreement is required.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
