# Changelog

All notable changes to skillmeld are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- The quality gate no longer hard-fails a skill whose body carries an unescaped html-like tag;
  the finding surfaces as a warning instead. Hard issues now cover only what the composition
  itself authors (name, description, frontmatter) — composed bodies are byte-traced from source
  skills and improves are description-only, so a body finding had no in-engine remediation and
  blocked `eval run` from ever reaching `passed: true` on an affected set.

### Security

- `cryptography` raised to 50.0 — earlier versions expose a timing oracle in PKCS#7
  decryption, an API skillmeld never calls.

## [0.2.0] - 2026-07-26

### Added

- The hosted data layer is live: `catalog sync` now works out of the box against
  `data.ifylab.dev/skillmeld` — an Ed25519-signed manifest verified against an embedded public
  key, a hash-pinned catalog, and an advisory verdict index in which every published skill is
  pre-scanned (known-BLOCK bundles are dropped at discovery before anyone sees them). Rebuilt
  weekly by CI.
- `build-catalog` builds and signs the production artifacts (crawl, fetch-verify, scan, sign)
  with the signing key from `SKILLMELD_SIGNING_KEY`, and `catalog sync --base-url` points the
  client at an alternate hosted endpoint.
- `emit api` output now carries the pinned Skills API beta headers (`beta_headers`), the
  provenance text with a sharing-scope section (`provenance_md`), a standing warning that a
  `/v1/skills` upload is workspace-wide, and `requires_confirmation: true` when the merge plan
  holds a REVIEW frontmatter verdict. It also warns when a composed skill carries support files,
  which the `/v1/skills` payload cannot include — those travel separately via the Files API.
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

- The catalog crawl resolves every source repo to its commit SHA at build time, so a published
  catalog's fetch URLs and pinned hashes stay consistent no matter what the branch does
  afterwards.
- `emit claude-code` names the provenance file `PROVENANCE-<set>.md`, so two composed sets
  emitted into one shared skills directory keep separate provenance instead of silently
  overwriting each other's.
- README places skillmeld among the newer composition tools (AgentSkillOS, SkillComposer) and
  links the Agent Skills spec home ([agentskills.io](https://agentskills.io)).

### Fixed

- `eval improve` without `--baseline-judgments`/`--candidate-judgments` returns the CLI's JSON
  error contract instead of a Python traceback.
- Passing `ground`'s full output to `--profile` fails loudly with the fix named, instead of
  silently validating an empty profile that turned pruning into a no-op.
- Framework detection matches exact package names, so a dependency like `license-expression`
  no longer misreads as Express.
- The catalog crawl reads a license file inside the skill's own folder when the repo has none at
  the root, so a source licensed per-skill no longer resolves license-unknown and drags a whole
  composed set to unknown.
- `eval` now accepts `--sources` (parity with `merge` and `emit`), so a source whose `SKILL.md`
  omits `name:` is verified under its catalog identity instead of failing the byte-trace check.
- The independent routing cross-check no longer routes near-miss queries on generic programming
  vocabulary alone ("write", "python", "code", ...), and a token shared by every child carries no
  routing weight — `independent_trigger` and `routing_disagreements` stay high-precision.

### Security

- `cryptography` raised to 49.0 — wheels before 48.0.1 bundled a vulnerable OpenSSL.

## [0.1.0] - 2026-06-13

First public release. The full pipeline is implemented and tested: intake, repo
grounding, discovery over a signed catalog, selection (at most three), a tri-state
security gate, the eight-step byte-traceable merge engine, evaluation, and packaging
for Claude Code / claude.ai / the API with provenance.

[0.2.0]: https://github.com/ifylab/skillmeld/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ifylab/skillmeld/releases/tag/v0.1.0
