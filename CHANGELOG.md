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

### Fixed

- `eval` now accepts `--sources` (parity with `merge` and `emit`), so a source whose `SKILL.md`
  omits `name:` is verified under its catalog identity instead of failing the byte-trace check.

## [0.1.0] - 2026-06-13

First public release. The full pipeline is implemented and tested: intake, repo
grounding, discovery over a signed catalog, selection (at most three), a tri-state
security gate, the eight-step byte-traceable merge engine, evaluation, and packaging
for Claude Code / claude.ai / the API with provenance.

[Unreleased]: https://github.com/ifylab/skillmeld/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ifylab/skillmeld/releases/tag/v0.1.0
