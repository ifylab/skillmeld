# skillmeld

<!-- A demo GIF of a real /skillmeld run is planned as the README hero. -->

[![CI](https://github.com/ifylab/skillmeld/actions/workflows/ci.yml/badge.svg)](https://github.com/ifylab/skillmeld/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/skillmeld)](https://pypi.org/project/skillmeld/)
[![License: Apache-2.0](https://img.shields.io/github/license/ifylab/skillmeld)](https://github.com/ifylab/skillmeld/blob/main/LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)

Describe what you want to do, point skillmeld at your repo, and it finds existing community skills for the job, security-scans them, and merges the best two or three into one coherent skill set tailored to your project — instead of writing one from scratch.

It runs on your own Claude in Claude Code, grounds in your repo, and shows you what it pulled, what it found, and why before anything is installed. It builds on the existing skills ecosystem (the [open standard](https://agentskills.io), community marketplaces, and registries) rather than replacing it.

## What makes it different

skillmeld composes; it does not generate. Every line in a merged skill traces byte-for-byte back to a source skill — a deterministic verifier enforces this, so the tool can never invent an instruction. The hard, mechanical work (parsing, security scanning, deduplicating, conflict detection, packaging) runs as deterministic Python that makes zero model calls. Your Claude supplies the judgment; the engine supplies the guarantees.

Composition tools are appearing on other layers too: [AgentSkillOS](https://github.com/ynulihao/AgentSkillOS) retrieves skills from a large pool and chains them into runtime workflows, and [SkillComposer](https://arxiv.org/abs/2606.06079) has a model generate and evolve its own skills. skillmeld's job is different: it merges skills other people wrote — untrusted input — into one reviewed, deduplicated set before anything runs, with a security verdict on every source and a byte trace on every line.

## How it works

A Claude Code skill drives a bundled Python engine through one pipeline:

```
intake -> ground -> discover -> select (<=3) -> fetch -> security gate -> merge -> eval -> emit
```

- **intake** normalizes the request and says when it is too thin to act on.
- **ground** scans your repo into a use-case profile, locally.
- **discover** syncs a signed catalog of community skills (Ed25519-verified, hash-pinned, cached locally) and your Claude ranks the shortlist.
- **select** takes at most three candidates, a separate cap from the three-skill output.
- **fetch** downloads only the chosen bundles and checks every file against the hash the catalog pinned.
- **security gate** scans every candidate (PASS / REVIEW / BLOCK) before you see it, and again after merge. REVIEW is the normal outcome for a skill that calls the network or reads files: the findings are named for you to decide on, and a BLOCK is never overridable.
- **merge** parses each skill into byte-exact atoms, deduplicates, resolves conflicts, prunes to your use case, and partitions the result into at most three skills behind a thin routing orchestrator. A verifier proves every output atom traces to a source.
- **eval** scores the set with no model calls: structural quality, byte-traceability, held-out trigger routing and a leakage check; every description edit is gated on it.
- **emit** packages the result for Claude Code, a claude.ai zip, the API, or a Claude Code plugin marketplace, with a provenance record (`PROVENANCE.md`, or `PROVENANCE-<set>.md` beside a Claude Code skills tree) of where every part came from.

## Install

Two ways in, one engine. The plugin path needs [uv](https://docs.astral.sh/uv/) on your PATH; the command-line path needs uv or pipx.

As a Claude Code plugin, the `/skillmeld` skill, with no clone:

```
/plugin marketplace add ifylab/skillmeld
/plugin install skillmeld@ifylab
```

Claude Code fetches the repository into its plugin cache, and the skill runs the engine from there.

As a command-line tool, from PyPI:

```sh
uv tool install skillmeld    # or: pipx install skillmeld
skillmeld --help
```

The command line runs the deterministic stages one at a time, which suits scripts and CI. The judgment steps (completing the profile, ranking candidates, adjudicating conflicts, authoring descriptions) belong to the skill, driven by your Claude.

## Quickstart

As a skill, describe the use case inside the project it is for:

```
/skillmeld I review pull requests for a Python service and want one consistent code-review routine
```

Or exercise individual stages directly from the CLI. Each line stands alone and prints JSON;
`ground` prints a partial profile whose `summary` and `tasks` you complete before `discover` reads
it. From the package root of a clone, `uv run skillmeld` is the same command:

```sh
skillmeld catalog sync                   # fetch and verify the hosted catalog
skillmeld ground .                       # scan a repo into a profile
skillmeld scan path/to/skill --license   # security- and license-scan a bundle
skillmeld merge --bundles a/ b/ --profile profile.json
```

The full pipeline — `intake`, `discover`, `select`, `fetch`, the eval loop, and every JSON contract these
commands exchange — is walked step by step in [skills/skillmeld/SKILL.md](https://github.com/ifylab/skillmeld/blob/main/skills/skillmeld/SKILL.md).
Offline, `skillmeld dev-catalog` builds the same signed catalog locally from repos you name. Two build-time
scouts feed the curated source list: `skillmeld skillsmp-scout` searches the SkillsMP registry
and `skillmeld awesome-scout` reads a curated awesome-list; both print candidates, and membership stays a
hand-made decision.

## What it isn't

- **Not a generator.** It assembles existing skills; it never authors new instructions. A convention no source skill covers is yours to add, not a gap skillmeld fills.
- **Not a catalog.** It composes from community marketplaces and registries rather than being one.
- **Not a model.** The engine makes zero LLM calls; the judgment comes from your own Claude, on your tokens.

## Acknowledgements

skillmeld stands on the open [Agent Skills](https://agentskills.io) ecosystem — the skill format, the community marketplaces, and the registries that publish and share skills. It composes that work; it does not replace it. Security scanning leans on [bandit](https://github.com/PyCQA/bandit), with optional [semgrep](https://semgrep.dev/), [gitleaks](https://github.com/gitleaks/gitleaks) and [NVIDIA SkillSpector](https://github.com/nvidia/skillspector) when present.

## Status

In active development, built in the open one piece at a time. The discovery, security, merge, evaluation, and packaging stages are implemented and tested, and discovery runs against a hosted signed catalog rebuilt weekly by CI — every published skill is crawled at a pinned commit and pre-scanned into an advisory verdict index. The curated AEC corpus is coming next. See the [changelog](https://github.com/ifylab/skillmeld/blob/main/CHANGELOG.md).

## Stack

Python 3.12, managed with uv. Ruff for lint and format, ty for type-checking, pytest for tests.

```sh
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest
```

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](https://github.com/ifylab/skillmeld/blob/main/CONTRIBUTING.md). Contributions are accepted under the project's Apache 2.0 license (inbound = outbound); no separate contributor agreement is required.

## License

Apache License 2.0 — see [LICENSE](https://github.com/ifylab/skillmeld/blob/main/LICENSE) and [NOTICE](https://github.com/ifylab/skillmeld/blob/main/NOTICE).
