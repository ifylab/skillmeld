# Changelog

All notable changes to skillmeld are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- A `marketplace` emit surface that packages the merged set as a `strict:false` Claude Code plugin
  marketplace (`.claude-plugin/marketplace.json` plus the skills tree and `PROVENANCE.md`), ready to
  host and install with `/plugin marketplace add`.
- `emit marketplace --plugin-name` to set the plugin entry's name. Without it, a multi-skill set now
  defaults to the composed skills' names joined, instead of the generic `orchestrator` slug.
- `eval` speaks the skill-creator interchange formats: `eval run --write-evals` exports the query
  set as a portable `evals.json`, `eval improve --history` keeps a `history.json` improvement
  ledger, and `--ingest-source-evals` reads a source skill's bundled evals as extra train-side
  trigger queries (never held out, so the leakage gate and the improve selection stay clean).

### Changed

- README places skillmeld among the newer composition tools (AgentSkillOS, SkillComposer) and
  links the Agent Skills spec home ([agentskills.io](https://agentskills.io)).

### Fixed

- `eval` now accepts `--sources` (parity with `merge` and `emit`), so a source whose `SKILL.md`
  omits `name:` is verified under its catalog identity instead of failing the byte-trace check.
- The independent routing cross-check no longer routes near-miss queries on generic programming
  vocabulary alone ("write", "python", "code", ...), and a token shared by every child carries no
  routing weight — `independent_trigger` and `routing_disagreements` stay high-precision.

## [0.1.0] - 2026-06-13

First public release. The full pipeline is implemented and tested: intake, repo
grounding, discovery over a signed catalog, selection (at most three), a tri-state
security gate, the eight-step byte-traceable merge engine, evaluation, and packaging
for Claude Code / claude.ai / the API with provenance.

[Unreleased]: https://github.com/ifylab/skillmeld/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ifylab/skillmeld/releases/tag/v0.1.0
