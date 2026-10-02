# Changelog

All notable changes to skillmeld are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-09-30

### Added

- `emit skills`, now the default surface: the Agent Skills tree every reader loads unchanged,
  rendered with the spec's frontmatter only, with carried support files and the set's
  `PROVENANCE-<set>.md`. `--install-for <agents>` copies it into each agent's own directory:
  one copy in the shared `.agents/skills/` folder for Codex, Cursor, Gemini CLI, Copilot,
  Windsurf, OpenCode, Goose, Amp, Junie and Roo, `.claude/skills/` for Claude Code with its own
  frontmatter fields kept, and the native folders for Factory and Kiro; `--scope user` writes
  the home-directory folders instead. Copies, never symlinks; an existing skill directory is
  refused unless `--force`, which reports what it replaced. `--native` writes each agent's own
  directory instead of the shared one.
- A portability lint on every spec-only surface and in `eval run`: one verdict per skill for
  agents other than Claude Code, `portable`, `degrades` (a Claude-only frontmatter field or
  `$ARGUMENTS` is ignored there) or `claude-only` (the body relies on Claude Code substitution
  or an inline command block, passed through as literal text elsewhere), with `portability:*`
  findings that name the line. Advisory only; it never gates and never rewrites a body.
  PROVENANCE.md gains a Portability section.
- `emit plugin`: an Agent Plugins 1.0.0 package (`plugin.json`, `skills/<name>/`,
  `PROVENANCE.md`), the cross-vendor plugin layout; `--codex-marketplace` adds
  `.agents/plugins/marketplace.json` so `codex plugin marketplace add <dir>` followed by
  `codex plugin add <name>@<name>` installs it (verified with Codex CLI 0.159; the manifest
  validates against the published 1.0.0 schema), and `--codex-compat` mirrors the manifest to
  `.codex-plugin/plugin.json`.
- `--codex-sidecar` (skills and plugin surfaces) writes `agents/openai.yaml` beside each skill
  for Codex, derived from the skill's name and description only.
- `--agents-md <path>` (skills surface) adds or refreshes one marker-delimited block naming the
  installed skills and the directory each agent reads; a re-run replaces its own block and
  touches nothing else in the file.
- `skill-install` writes the `/skillmeld` driver skill from the installed package into an
  agent's skills folder, `.agents/skills/skillmeld/` by default (`--scope user` for the home
  folder, `--dir` for an agent's own), so a PyPI install is enough to run the skill in Codex,
  Gemini CLI, Cursor and the rest with no clone and no Node. The skill ships inside the wheel.
- The quality gate applies the Agent Skills spec's name rules (lowercase alphanumerics joined
  by single hyphens, at most 64 characters) and warns on `compatibility` over 500 characters
  and on Claude-only frontmatter; `emit` refuses a name that cannot match its directory, as it
  refuses an empty description.
- The catalog crawls `google/skills` (Apache-2.0) and `microsoft/skills` (MIT) alongside
  `anthropics/skills` and `obra/superpowers`; a skill's frontmatter `license:` is read as the
  last resort when a repository has no LICENSE file; each entry lists the agent-specific
  `sidecars` it ships.
- `ground` reads the repository's agent instructions (AGENTS.md first, then CLAUDE.md,
  GEMINI.md or the Copilot instructions) into `instructions_excerpt`, and reports the agents the
  repository is set up for in `agents`, from its `.claude/`, `.cursor/`, `.agents/skills/`,
  `AGENTS.md` and similar markers.

### Changed

- The `/skillmeld` skill runs from any agent that loads Agent Skills: its frontmatter is the
  spec's, it no longer depends on a Claude Code variable to find its script, and `scripts/run.sh`
  finds the engine on PATH, in the checkout, or through `uv tool run`. Verified in Claude Code
  (2.1.286) and Codex CLI (0.160): install, discovery and a run of the skill; in Gemini CLI
  (0.62): install and discovery. `skillmeld skill-install` or a copy of `skills/skillmeld/` to
  `.agents/skills/skillmeld/` installs it outside Claude Code.
- The skill's second stop writes the set to a scratch tree, re-scans it, and installs with
  `emit skills --install-for`, defaulting to the agents the repository already uses.
- `emit` with no surface means `emit skills`. The claude.ai and API emits report the portability
  verdicts. The Claude Code and marketplace emits are unchanged.
- README and the skill describe the output as a plain Agent Skills tree that installs into any
  reader, not a Claude Code artifact.

## [0.4.0] - 2026-09-26

### Added

- skillmeld is on PyPI: `uv tool install skillmeld` (or `pipx install skillmeld`) puts the
  `skillmeld` command on your PATH. Releases publish from the version tag through PyPI trusted
  publishing; the sdist and wheel carry only the package, its tests and the top-level docs.
- The Claude Code plugin installs straight from GitHub: `/plugin marketplace add ifylab/skillmeld`
  fetches the repository into the plugin cache and the skill runs the engine from there. No clone.
- `awesome-scout`: build-time discovery over a curated awesome-list (default:
  VoltAgent/awesome-agent-skills). Every linked repository not already in the catalog, ranked by
  GitHub stars. Candidates only; membership in the source list stays a hand-made decision.
- Scans name the executable script files a bundle ships (`core:ships-scripts`, informational).
  Skills that bundle scripts carried a vulnerability about twice as often in a 2026 study of 31k
  community skills (arXiv 2601.10338), so the review card can say what will run.
- Trigger scoring lists `leaky_ids`, the queries that spell out their target skill's compound
  name and so route trivially, and a `held_out_pass_rate_strict` without them; `eval improve`
  repeats the warning.
- `skillsmp-scout` reports the remaining daily quota from the API's headers and stops with a
  plain message when the quota is exhausted.

### Changed

- Discovery matches a hyphenated name on its parts too, so `weekly-production-review` is
  reachable from "weekly review" and not only from the exact compound.
- A host referenced from many files collapses into one finding per host and rule, with a count,
  instead of one finding per line.
- SkillSpector findings carry the tool's pattern, explanation and matched text; its JSON has no
  `message` field, so earlier versions showed only the category. The category fold covers all 18
  categories of v2.12, an empty scan is reported as a notice, and the adapter is verified against
  v2.12.0.
- The claude.ai zip and the `/v1/skills` payload render frontmatter within the Agent Skills spec:
  `disallowed-tools` and `disable-model-invocation` are left out, named in `warnings`, because an
  upload refuses a SKILL.md that carries them. The Claude Code and marketplace emits keep them.
- `emit api` reports no required `anthropic-beta` header: the Skills API, the code execution tool
  and the Files API are all GA. `beta_headers` is empty and `legacy_beta_headers` names the two
  identifiers still accepted as opt-ins.
- The SkillsMP scout pages at the documented maximum of 50 per request.
- SKILL.md says that REVIEW is the usual verdict for a skill that calls the network or reads
  files, and names the two Claude Code settings that govern the skill-listing budget.

### Fixed

- A multi-line frontmatter `description` (a `>` or `|` block, a quoted or a plain continuation)
  is read whole. Earlier versions kept the block marker or nothing, which made such skills
  unreachable by discovery.
- A trailing period on a frontmatter `license:` value (`MIT.`) no longer breaks the SPDX id.

### Security

- `anyio` raised to 4.15.1, past two advisories fixed in 4.14.2 (IDNA host-name matching in TLS
  streams, and process-pool workers blocking on stderr). skillmeld does not use either path; the
  package arrives through httpx.

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

[0.5.0]: https://github.com/ifylab/skillmeld/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/ifylab/skillmeld/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/ifylab/skillmeld/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/ifylab/skillmeld/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ifylab/skillmeld/releases/tag/v0.1.0
