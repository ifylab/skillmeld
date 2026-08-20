# Changelog

All notable changes to skillmeld are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-08-19

### Added

- Four core rules close gaps a new taxonomy coverage benchmark surfaced: cron and login/startup
  persistence, ClickFix-style paste-to-fix lures, and directed false reassurance ("tell the user
  it is safe"). The benchmark maps every publicly named category of agentskill.sh's threat model
  to at least one live rule and carries OWASP Agentic Skills Top 10 ids as an advisory
  cross-reference (the OWASP list is in pre-ratification review, so its ids are never a stored
  schema key).
- NVIDIA SkillSpector joins semgrep and gitleaks as a PATH-optional, escalate-only scanner
  adapter. It always runs `--no-llm` (scanned content never leaves the machine; its supply-chain
  check may send dependency names — never contents — to OSV.dev, with a bundled fallback), its
  CRITICAL findings cap at REVIEW like every adapter, and its categories fold onto the gate's
  taxonomy.
- The weekly catalog build now consults scancode-toolkit for license texts the lightweight
  fingerprints cannot identify, so gold-standard SPDX detection is baked into the signed catalog
  while the client stays dependency-light.
- `emit marketplace` gained `--marketplace-version` and `--owner-url`. The manifest now matches
  the shape Claude Code marketplaces ship (a `metadata` wrapper with description + version), and
  the version also lands on the plugin entry so `claude plugin update` can see a re-composition.
- The SkillsMP adapter is real: `skillsmp-scout --queries ...` runs authed, budget-capped,
  paginated breadth discovery over the SkillsMP registry and prints candidate `owner/name`
  repos ranked by stars. Candidates only — catalog membership stays a hand-curated decision —
  and the undocumented response schema is pinned by fixture so upstream drift fails loudly.

### Changed

- A scan that runs without an optional scanner now says so with a visible notice instead of a
  quiet `absent` version entry; a security gate announces reduced coverage.
- `scan --license --sources` distinguishes the two license-unknown cases: the catalog also has
  no SPDX for the source (unknown is the settled state) versus the bundle simply not being in
  the provided sources.
- The merge plan's support-file reference warnings collapse into one aggregate line listing
  every referenced file, so boilerplate cannot drown a real warning.
- `eval --help` groups shared, run-only, and improve-only flags; `catalog --help` documents the
  trust model (verification at sync/verify time; `discover` trusts the last verified sync).
- `eval run --write-evals` keeps the caller's query numbering whenever the ids carry unique
  digits, instead of always renumbering the exported cases.
- Quality-gate body warnings cite the line numbers of unescaped html-like tags.
- Spec refresh: the Skills API is GA (`skills-2025-10-02` and `code-execution-2025-08-25` are
  optional opt-ins now; `files-api-2025-04-14` is still required when files move), and Claude
  Code renamed its routing-budget setting to `skillListingMaxDescChars` (same 1536 default) —
  constants, warning text, and docs updated.

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
