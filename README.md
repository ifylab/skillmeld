# skillmeld

<!-- A demo GIF of a real /skillmeld run is planned as the README hero. -->

[![CI](https://github.com/ifylab/skillmeld/actions/workflows/ci.yml/badge.svg)](https://github.com/ifylab/skillmeld/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/skillmeld)](https://pypi.org/project/skillmeld/)
[![License: Apache-2.0](https://img.shields.io/github/license/ifylab/skillmeld)](https://github.com/ifylab/skillmeld/blob/main/LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)

Describe what you want to do, point skillmeld at your repo, and it finds existing community skills for the job, security-scans them, and merges the best two or three into one coherent skill set tailored to your project — instead of writing one from scratch.

It runs on your own coding agent, grounds in your repo, and shows you what it pulled, what it found, and why before anything is installed. The output is a plain [Agent Skills](https://agentskills.io) tree: Claude Code reads it from `.claude/skills/`, and Codex, Cursor, Gemini CLI, Copilot and the other agents that read `.agents/skills/` pick up the same files unchanged. It builds on the existing skills ecosystem (the open standard, community marketplaces, and registries) rather than replacing it.

## What makes it different

skillmeld composes; it does not generate. Every line in a merged skill traces byte-for-byte back to a source skill — a deterministic verifier enforces this, so the tool can never invent an instruction. Every source is security-scanned before it is merged, since community skills are untrusted input, and the support files a skill ships (references, scripts, assets) travel with it. The hard, mechanical work (parsing, scanning, deduplicating, conflict detection, packaging) runs as deterministic Python that makes zero model calls. Your agent supplies the judgment; the engine supplies the guarantees. What comes out is the spec's `SKILL.md`, so it installs anywhere skills are read, and a portability lint says, per skill, how it behaves on agents other than the one that composed it.

Composition tools are appearing on other layers too: [AgentSkillOS](https://github.com/ynulihao/AgentSkillOS) retrieves skills from a large pool and chains them into runtime workflows, and [SkillComposer](https://arxiv.org/abs/2606.06079) has a model generate and evolve its own skills. skillmeld's job is different: it merges skills other people wrote — untrusted input — into one reviewed, deduplicated set before anything runs, with a security verdict on every source and a byte trace on every line.

## How it works

A skill, `/skillmeld`, drives a Python engine through one pipeline from whichever agent you run it in:

```
intake -> ground -> discover -> select (<=3) -> fetch -> security gate -> merge -> eval -> emit
```

- **intake** normalizes the request and says when it is too thin to act on.
- **ground** scans your repo into a use-case profile, locally.
- **discover** syncs a signed catalog of community skills (Ed25519-verified, hash-pinned, cached locally) and your agent ranks the shortlist.
- **select** takes at most three candidates, a separate cap from the three-skill output.
- **fetch** downloads only the chosen bundles and checks every file against the hash the catalog pinned.
- **security gate** scans every candidate (PASS / REVIEW / BLOCK) before you see it, and again after merge. REVIEW is the normal outcome for a skill that calls the network or reads files: the findings are named for you to decide on, and a BLOCK is never overridable.
- **merge** parses each skill into byte-exact atoms, deduplicates, resolves conflicts, prunes to your use case, and partitions the result into at most three skills behind a thin routing orchestrator. A verifier proves every output atom traces to a source.
- **eval** scores the set with no model calls: structural quality, byte-traceability, held-out trigger routing and a leakage check; every description edit is gated on it.
- **emit** packages the result as a plain Agent Skills tree and installs it into each agent's own directory (`--install-for codex,claude-code,...`), as an Agent Plugins package, or for the Claude surfaces (a Claude Code tree, a claude.ai zip, the API, a plugin marketplace), always with a provenance record (`PROVENANCE.md`, or `PROVENANCE-<set>.md` beside a skills tree) of where every part came from and how portable each skill is.

## Install

One engine, two ways to get the skill that drives it. The engine is a Python package; install it with [uv](https://docs.astral.sh/uv/) or [pipx](https://pipx.pypa.io/), either of which puts the `skillmeld` command on your PATH:

```sh
uv tool install skillmeld    # or: pipx install skillmeld
skillmeld --help
```

In Claude Code, add the skill as a plugin, with no clone (this route needs uv, since the plugin runs the engine from its cache):

```
/plugin marketplace add ifylab/skillmeld
/plugin install skillmeld@ifylab
```

In Codex, Gemini CLI, Cursor, Copilot or any other agent that reads `.agents/skills/`, write the skill from the installed package:

```sh
skillmeld skill-install                 # -> .agents/skills/skillmeld/ in the current project
skillmeld skill-install --scope user    # -> ~/.agents/skills/skillmeld/ for every project
```

The same files are `skills/skillmeld/` in this repository, so a clone or `npx skills add ifylab/skillmeld` (Node) works too. The skill's `scripts/run.sh` needs bash and finds the engine on PATH; a new skill is picked up when the agent next starts (Codex refreshes between turns). Gemini CLI loads project skills only from a trusted folder.

Then invoke it: `/skillmeld <use case>` in Claude Code and Cursor, `$skillmeld <use case>` in Codex, or just ask Gemini CLI, Copilot and the rest to use the skillmeld skill; all of them also pick it up on their own when a request matches its description.

Works with: verified in Claude Code and Codex (install, discovery and a run of the skill) and in Gemini CLI (install and discovery); documented for Cursor, Copilot, Windsurf, OpenCode, Goose, Amp, Kiro, Junie, Factory and Roo from their own skill docs. Where a composed set lands with `--install-for`: one shared copy in `.agents/skills/` for Codex, Cursor, Gemini CLI, Copilot, Windsurf, OpenCode, Goose, Amp, Junie and Roo; `.claude/skills/` for Claude Code; `.factory/skills/` and `.kiro/skills/` for Factory and Kiro (Factory reads `~/.agents/skills/` at user scope); `--native` writes each agent's own folder instead of the shared one.

The command line runs the deterministic stages one at a time, which suits scripts and CI. The judgment steps (completing the profile, ranking candidates, adjudicating conflicts, authoring descriptions) belong to the skill, driven by your agent.

## Quickstart

As a skill, describe the use case inside the project it is for:

```
/skillmeld I review pull requests for a Python service and want one consistent code-review routine
```

Or drive the stages from the CLI. Every command prints JSON; the two marked steps need your own
judgment in between (that is the part the skill does for you). From the package root of a clone,
`uv run skillmeld` is the same command:

```sh
skillmeld catalog sync                                      # fetch and verify the hosted catalog
skillmeld ground . > ground.json                            # repo evidence + a partial profile
#   complete profile.json from ground.json: add "summary" and "tasks" in your own words
skillmeld discover --profile profile.json > discover.json   # scored candidates
skillmeld select --candidates discover.json --choose <id>,<id> > select.json
skillmeld fetch --selection select.json > fetch.json        # hash-verified bundles; note each "path"
skillmeld scan <bundle> --license --sources discover.json   # PASS / REVIEW / BLOCK, per bundle
skillmeld merge --bundles <bundle> <bundle> --profile profile.json --sources discover.json > merge.json
#   author each child's "description" in merge.json (the merge leaves it empty on purpose)
skillmeld emit --result merge.json --bundles <bundle> <bundle> --out out/ --install-for codex,claude-code
```

The last line writes the composed set as a spec-only skills tree under `out/` and copies it into `.agents/skills/` for Codex and `.claude/skills/` for Claude Code under the current directory (`--project-root` to change it, `--scope user` for the home-directory folders), one copy per directory, never symlinks, refusing to replace a skill that is already there unless you pass `--force`.

The full pipeline — `intake`, `discover`, `select`, `fetch`, the eval loop, and every JSON contract these
commands exchange — is walked step by step in [skills/skillmeld/SKILL.md](https://github.com/ifylab/skillmeld/blob/main/skills/skillmeld/SKILL.md).
Offline, `skillmeld dev-catalog` builds the same signed catalog locally from repos you name. Two build-time
scouts feed the curated source list: `skillmeld skillsmp-scout` searches the SkillsMP registry
and `skillmeld awesome-scout` reads a curated awesome-list; both print candidates, and membership stays a
hand-made decision.

## What it isn't

- **Not a generator.** It assembles existing skills; it never authors new instructions. A convention no source skill covers is yours to add, not a gap skillmeld fills.
- **Not a catalog.** It composes from community marketplaces and registries rather than being one.
- **Not a model.** The engine makes zero LLM calls; the judgment comes from your own agent, on your tokens.
- **Not a translator.** A merged body is the sources' text, byte for byte. If a source leans on one agent's substitution syntax, the portability lint says so; skillmeld does not rewrite it.

## Acknowledgements

skillmeld stands on the open [Agent Skills](https://agentskills.io) ecosystem — the skill format, the community marketplaces, and the registries that publish and share skills. It composes that work; it does not replace it. Security scanning leans on [bandit](https://github.com/PyCQA/bandit), with optional [semgrep](https://semgrep.dev/), [gitleaks](https://github.com/gitleaks/gitleaks) and [NVIDIA SkillSpector](https://github.com/nvidia/skillspector) when present.

## Status

In active development, built in the open one piece at a time. The discovery, security, merge, evaluation, and packaging stages are implemented and tested, and discovery runs against a hosted signed catalog rebuilt weekly by CI from Anthropic's, Google's, Microsoft's and obra's skill repositories — every published skill is crawled at a pinned commit and pre-scanned into an advisory verdict index. Since 0.5.0 the output and the skill itself run across coding agents, not only Claude Code. The curated AEC corpus is coming next. See the [changelog](https://github.com/ifylab/skillmeld/blob/main/CHANGELOG.md).

## Stack

Python 3.12, managed with uv. Ruff for lint and format, ty for type-checking, pytest for tests.

```sh
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest
```

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](https://github.com/ifylab/skillmeld/blob/main/CONTRIBUTING.md). Contributions are accepted under the project's Apache 2.0 license (inbound = outbound); no separate contributor agreement is required.

## License

Apache License 2.0 — see [LICENSE](https://github.com/ifylab/skillmeld/blob/main/LICENSE) and [NOTICE](https://github.com/ifylab/skillmeld/blob/main/NOTICE).
