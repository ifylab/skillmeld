---
name: skillmeld
description: "Discovers existing community skills for a described use case and merges the best two or three into one coherent, deduplicated, security-scanned skill set tailored to the user's project. Use when someone wants to assemble or compose skills for a workflow, combine existing skills instead of writing one from scratch, or build a tailored skillset from community sources. Grounds in the user's repo, scans every candidate before use, and shows provenance plus a review before installing."
license: Apache-2.0
compatibility: Needs uv or the skillmeld CLI on PATH. Runs in any agent that loads Agent Skills.
---

# skillmeld

This skill is the front-end that drives the `skillmeld` Python engine. The engine is
deterministic and makes no model calls; you supply the judgment and gate every side effect on
the user's approval.

## What this does

Turns a described use case (plus the user's repo) into a coherent skill set assembled from existing community skills: discover candidates, security-scan them, merge the best two or three, and install with the user's approval. Composes existing skills; never writes new instructions from scratch. The output is a plain Agent Skills tree, so it installs into Claude Code, Codex, Cursor, Gemini CLI, Copilot and any other agent that reads `SKILL.md`.

## How it runs

The deterministic engine is invoked from this skill via:

    bash <skill-dir>/scripts/run.sh <command> [args...]

where `<skill-dir>` is the directory holding this SKILL.md: `scripts/run.sh` sits beside this file, so resolve it from the location your skills listing gave for `skillmeld` (search for this file if the listing showed none). `run.sh` needs bash and finds the engine in this order: the `skillmeld` command on PATH (a `uv tool install skillmeld` or `pipx install skillmeld`), then this checkout when it is the skillmeld repository (a clone or the Claude Code plugin cache), then `uv tool run skillmeld`; with none of those it stops and says how to install. The steps below use the `run.sh` form.

Each command prints JSON to stdout. This skill reads that JSON and supplies the judgment steps (grouping atoms, adjudicating conflicts, choosing among existing options) in-session. The engine itself makes no model calls.

## Install this skill

- Claude Code: `/plugin marketplace add ifylab/skillmeld` then `/plugin install skillmeld@ifylab`.
- Any other agent: install the engine (`uv tool install skillmeld` or `pipx install skillmeld`), then `skillmeld skill-install` writes this skill to `.agents/skills/skillmeld/` in the project (`--scope user` for `~/.agents/skills/skillmeld/`), which Codex, Cursor, Gemini CLI, Copilot, Windsurf, OpenCode, Goose, Amp, Junie and Roo read. Kiro and Factory read their own folders at project scope: pass `--dir .kiro/skills` or `--dir .factory/skills`. A clone's `skills/skillmeld/` directory or `npx skills add ifylab/skillmeld` (Node) are the same files.
- Invoke it as `/skillmeld` in Claude Code and Cursor, `$skillmeld` in Codex, or by asking for the skillmeld skill in Gemini CLI, Copilot and the rest; every agent also activates it on its own when a request matches the description above. A new skill is picked up when the agent next starts.

Verified in Claude Code and Codex (install, discovery and a run) and in Gemini CLI (install and discovery); Cursor, Copilot, Windsurf, OpenCode, Goose, Amp, Kiro, Junie, Factory and Roo are documented from their own skill docs. When you need to name the agent you are running in for `--install-for`, use: Claude Code `claude-code`, Codex `codex`, Cursor `cursor`, Gemini CLI `gemini-cli`, GitHub Copilot `github-copilot`, Windsurf `windsurf`, OpenCode `opencode`, Goose `goose`, Amp `amp`, Kiro `kiro`, Junie `junie`, Factory `factory`, Roo Code `roo`.

## Flow

1. Intake — `run.sh intake "<use case>"` normalizes the request and flags whether it is `thin`.
   Treat `thin` as a floor, not a ceiling: the engine only sees length and vague words, never
   domain ambiguity. Ask at most one or two scoping questions when EITHER the request is `thin`
   OR you recognize a material fork the engine cannot — a choice that changes which skills are
   relevant or what the output must cover (a target runtime, platform, or framework with
   incompatible variants is the usual case). Otherwise echo the understood goal and move on.
   Keep it to genuine forks; never interrogate.
2. Ground — `run.sh ground <repo>` collects deterministic evidence and a partial profile.
   Complete the profile yourself from the session: write `summary` (2-3 sentences) and
   `tasks` (3-6 representative tasks in the user's words), then save the completed profile
   JSON to a temp file. The evidence also carries `instructions_excerpt` (the head of the
   repo's AGENTS.md, CLAUDE.md, GEMINI.md or Copilot instructions, whichever it has) for the
   conventions it already states, and `agents` (the agents the repo is set up for, from its
   `.claude/`, `.cursor/`, `.agents/skills/`, `AGENTS.md` and similar markers), which is the
   default install target at Stop 2.
3. Discover — `run.sh catalog sync` first refreshes the signed catalog (fast when fresh;
   offline it falls back to the last verified sync). The trust model: verification happens at
   sync time, not on every read — `catalog verify` strictly re-checks the cached snapshot
   offline, while `discover` trusts the last verified sync. Then `run.sh discover --profile
   <profile.json>` prefilters it and prints scored candidates with per-match evidence
   (`matched`). Skills already blocked by the verdict index are dropped before anyone sees
   them.
4. Rank + select — rank the candidates by fit to the use case. Read each candidate's name,
   description, tags, and `matched` evidence; ignore `files`. Answer with existing candidate
   ids only — never invent an id — best first, at most three. Then
   `run.sh select --candidates <discover.json> --choose id1,id2` (ids exactly as discover printed
   them, `owner/repo:path`) validates the pick and
   surfaces warnings (for example, two picks from the same source repo).
5. Fetch — `run.sh fetch --selection <select.json>` downloads only the chosen bundles and
   verifies every file against the hash the signed catalog pinned; a mismatch refuses the
   bundle. Run this only after the user has seen the shortlist. Carry the exact `path` values
   fetch returns into `scan` and `merge` — the cache is content-addressed and shared across
   runs, so globbing the bundles directory will pull in other selections' skills. Map each path
   by the `id` fetch reports next to it, never by directory order.
6. Security gate — `run.sh scan <bundle> [--sources <discover.json>]` for each: PASS proceeds,
   REVIEW is surfaced for a decision, BLOCK is refused. REVIEW is the usual verdict for a popular
   skill that calls the network or reads files, not a defect: read the named findings, decide,
   and record the decision in the review card. Every scan also names the executable scripts a
   bundle ships (`core:ships-scripts`, informational) so the card can say what will run. Pass
   `--sources` so a skill whose repo license the catalog already knows is not flagged
   license-unknown just because the LICENSE file stayed out of the bundle.
7. Merge — `run.sh merge --bundles <dir>... --profile <profile.json>` runs the eight-step
   engine: parse, dedupe, group, conflict-detect, reconcile, prune, partition, and verify. You
   supply the judgment the engine asks for and nothing more:
   - Grouping is optional. To group and label the atoms yourself, pass
     `--grouping <file.json>` mapping each atom id to `{group, kind}` — ids only, drawn from the
     parsed atoms. A Python-detected directive can never be relabelled to a softer kind; the
     engine forces it back and tells you. Omit the flag to let the engine group by source.
   - Conflict adjudication is optional. The engine flags structural conflicts; to pick a winner
     or add a semantic one, pass `--adjudication <file.json>` (a list of conflicts). You can
     never make a flagged structural conflict disappear.
   The result carries a `plan` (what was kept, dropped, deduped, and why) and a `problems` list.
   `problems` MUST be empty — a non-empty list means the byte-traceability verifier rejected the
   merge; never install a rejected result. The merge also carries each source's tool and invocation
   frontmatter onto the children, reconciled (allowed-tools narrowed to the intersection,
   disallowed-tools unioned, disable-model-invocation honored); when that drops a pre-approved tool
   or leaves a child non-invocable, `plan.frontmatter_verdict` is `review` and
   `plan.frontmatter_findings` says why. Hold the consolidated review until the set is complete
   (after step 8).
8. Author descriptions + evaluate — the merge leaves every child skill's `description` empty on
   purpose (it never invents text), so each one must be authored before it can ship; a skill
   with no description never triggers in any agent. For each child, write a short, trigger-
   friendly description and gate it through
   `run.sh eval improve --result <merge.json> --bundles <dir>... --skill <index|orchestrator>
   --description "..."` with the trigger
   queries and routing judgments (`--queries`, `--baseline-judgments`, and
   `--candidate-judgments` are all required) — an edit is accepted only if structural quality holds, no
   held-out query leaks, and the held-out routing pass-rate does not regress — measured both from
   your reported routing and from an independent engine-side pass that routes the queries against
   the descriptions, so acceptance never rests on your self-report. Phrase each description with
   the literal words a user would say; the independent router keys on them. Keep it
   within the routing budget — the Agent Skills spec caps a description at 1024 characters and
   every agent budgets its skill listing on it (Claude Code truncates at 1536 in its listing,
   `skillListingMaxDescChars`; the whole listing shares 1% of the context window unless
   `skillListingBudgetFraction` raises it; Codex caps the listing at 2% of context), so lead
   with the key use case. The orchestrator
   ships with a templated routing description already; refine it the same way (`--skill
   orchestrator`) only if needed. Pass `--sources <discover.json>` to `eval improve` and `eval run`
   (the same JSON you gave merge) so the verifier resolves each source's catalog identity — without
   it, a source whose `SKILL.md` omits `name:` fails the byte-trace check. Then
   `run.sh eval run --result <merge.json> --bundles <dir>...` must report `passed: true` over the
   set — pass it `--judgments` along with `--queries`: the
   reported-routing gate scores zero without your judgments even when `independent_trigger` is
   perfect. Quality `warnings` never block `passed`; relay them in the review below. A body
   warning (an unescaped html-like tag inherited from a source) has no in-engine fix — bodies are
   byte-traced from sources — so do not spend improve rounds trying to clear it. `eval run` also
   reports `portability`, one verdict per skill for agents other than Claude Code: `portable`
   (loads unchanged), `degrades` (a Claude-only field or the arguments placeholder is ignored
   there), or
   `claude-only` (the body relies on Claude Code substitution, its skill-directory variable or
   an inline `!`-prefixed command block, which other agents pass through as literal text). It is advisory,
   never a gate, and bodies are never rewritten to change it. Both commands
   list `leaky_ids`: trigger queries that spell out their target skill's compound name ("ifc
   quantity takeoff" for `ifc-quantity-takeoff`) route trivially and inflate the pass-rate, so
   rewrite them the way a user would ask and read `held_out_pass_rate_strict` for the rate
   without them; `eval improve` repeats the warning in `warnings`.
   Optional interchange: `eval improve --history <path>` keeps a portable `history.json` ledger of
   the accepted and rejected edits, and `eval run --write-evals <path>` exports the query set as a
   portable `evals.json` (both skill-creator formats). When a fetched source bundles its own evals
   (`evals/evals.json`), `--ingest-source-evals` folds them in as extra train-side trigger queries
   targeting that skill — they never enter the held-out split, so the leakage gate and the improve
   selection stay on your own queries.
   With the set now complete, show the user the plan and the authored descriptions as one
   consolidated review before writing anything.
9. Emit — `run.sh emit [surface] --result <merge.json> --bundles <dir>...` packages the result;
   install only after the user approves. Emit refuses any skill (child or orchestrator) whose
   description is still empty or whose name cannot match its directory, so a set can never ship
   dead even if this step was rushed. Surfaces:
   - `skills` (the default): the Agent Skills tree, `<out>/<name>/SKILL.md` per skill with the
     spec's frontmatter only, carried support files, and `PROVENANCE-<set>.md`. This is what every
     agent loads unchanged. `--install-for <agents>` copies it into each agent's own directory
     (see Stop 2); `--codex-sidecar` adds `agents/openai.yaml` beside each skill for Codex, derived
     from the name and description; `--agents-md <path>` adds or refreshes a marker-delimited
     block in that file naming the installed skills and where each agent reads them.
   - `plugin`: an Agent Plugins 1.0.0 package (`plugin.json`, `skills/<name>/`, `PROVENANCE.md`)
     the cross-vendor plugin format; `--codex-marketplace` adds `.agents/plugins/marketplace.json`
     so `codex plugin marketplace add <dir>` finds it.
   - `claude-code` (the same tree with Claude Code's own frontmatter fields kept), `claudeai`
     (zip), `api` (`/v1/skills` payload), and `marketplace` (a `strict:false` Claude Code plugin
     marketplace the user can host and `/plugin marketplace add`).
   Each returns `warnings` to relay before install. `skills`, `plugin`, `claudeai` and `api` carry
   the portability verdicts (`portability`) and a warning line per skill that is not fully
   portable. Every surface flags a description over the 1536-char Claude Code listing cap;
   `emit api` flags one over the 1024-char spec cap (the upload is rejected); `emit api` and
   `emit claudeai` name any `disallowed-tools` or `disable-model-invocation` they left out —
   those fields sit outside the Agent Skills spec and an upload refuses a SKILL.md that carries
   them (the claude-code and marketplace emits keep them); `allowed-tools` stays, but no agent
   other than Claude Code enforces it. `emit api` also reports that no `anthropic-beta` header is
   required (`beta_headers` is empty; `legacy_beta_headers` lists the two identifiers older
   clients may still send), the provenance text to keep with the upload (`provenance_md`), and a
   standing warning that a `/v1/skills` upload is workspace-wide — every member of the workspace
   can invoke it. When the output says `requires_confirmation: true`, or any scan in the run came
   back REVIEW, name the finding and get the user's explicit confirmation before uploading.
   `emit marketplace` and `emit plugin` default the plugin name, marketplace name and owner to
   the skill's slug and warn when they do (pass `--plugin-name`, `--marketplace-name` and
   `--owner-name` to set them); a name reserved for official use is refused.

Every atom in the merged output traces byte-for-byte to a source skill; the engine invents no
instruction text. Nothing is fetched, merged, or installed without showing the user what will
happen and getting approval.

## User experience

Two human stops on the happy path; everything else streams as narrated progress.

- **Stream progress, never go silent.** After each engine call, narrate one line of state —
  `Found 11 -> 5 PASS, 2 REVIEW, 4 dropped (1 blocked) -> drafting merge...`. A multi-turn run
  should never look hung.
- **Stop 1 — the merge-plan review** (the plan moment, before anything is written). Present a
  single consolidated card, not per-skill or per-finding prompts:
  - each emitted skill and the description it will trigger on (children authored, orchestrator
    templated), so the user sees what fires before it is installed;
  - what is kept, with per-part provenance (which source each part came from) and licenses;
  - what was deduped or dropped, and why (name the decision, not just the outcome);
  - the consolidated security verdict, with any REVIEW finding named for the exact skill and
    line (`pdf-helper reads ~/.aws/credentials, line 34`), not boilerplate, and the scripts each
    bundle ships;
  - any frontmatter REVIEW from `plan.frontmatter_findings` (a source's pre-approved tool dropped
    in the intersection, or a child left non-invocable), named for the skill it affects;
  - one portability line from `eval run` (`ifc-qto portable; gh-script degrades: allowed-tools
    names Bash, not enforced outside Claude Code`), and for a `claude-only` skill the plain
    sentence that its body relies on Claude Code substitution and other agents read that text
    literally;
  - the license resolution and a coarse confidence band;
  - where it will be installed: the agents from `ground`'s `agents` list (or the agent you are
    running in when the list is empty) and the directory each one reads.
  Actions: Approve and install / Adjust / Dry-run / Cancel.
- **Stop 2 — second-layer scan and write** (the install/trust gate). First
  `run.sh emit skills --result <merge.json> --bundles <dir>... --out <scratch>` writes the tree
  to a scratch directory; re-scan each emitted skill there (`run.sh scan <scratch>/<name>`); a
  BLOCK refuses the install. Only after the user accepts, run the same emit again with
  `--install-for <agents>` (comma-separated: `claude-code`, `codex`, `cursor`, `gemini-cli`,
  `github-copilot`, `windsurf`, `opencode`, `goose`, `amp`, `kiro`, `junie`, `factory`, `roo`,
  `agents` for the bare `.agents/skills/` folder, or `all`). Agents that read the shared
  `.agents/skills/` folder (Codex, Cursor, Gemini CLI, Copilot, Windsurf, OpenCode, Goose, Amp,
  Junie, Roo) get one copy there; Claude Code gets `.claude/skills/` with its own frontmatter
  fields kept; Kiro gets `.kiro/skills/` and Factory `.factory/skills/` (Factory reads the shared
  folder at user scope). `--native` writes each agent's own folder instead of the shared one, for
  a user who keeps them separate. One `--scope` per run (`project`, the default, or `user` for
  the home-directory folders); `--project-root` names the project when you are not running from
  it. The emit refuses to replace a skill directory that
  already exists; pass `--force` only when the user has confirmed the overwrite, and relay
  `overwritten`. Relay the `installed` paths per agent verbatim; each install root also gets the
  set's `PROVENANCE-<set>.md` (the per-set name keeps one composed set's provenance from
  overwriting another's in a shared directory). Add `--agents-md AGENTS.md` when the repo has
  an AGENTS.md or the user asks; it adds or refreshes one marker-delimited block and touches
  nothing else in the file. Add `--codex-sidecar` when Codex is a target and the user wants the
  Codex UI metadata.
- **A BLOCK is never one-click overridable.** REVIEW is the only interactive security stop;
  BLOCK is refused and excluded before the user chooses. Keep BLOCK rare and high-precision so
  REVIEW prompts stay trusted.
- **Close by making the user smarter, not just handing over an artifact:** one line on why each
  skill was picked, the 2-3 bullet "what was merged and why" reflection, and a pointer to
  the provenance file and the sources. Everything deep (full findings, per-line evidence, raw
  scores) lives behind "show details".

A non-interactive escape for CI (`--yes` / `--all`) is not built yet; when it is, it may skip the
REVIEW prompt but will never bypass a BLOCK.

## Data contracts

The JSON shapes you author by hand, so you do not have to read the engine source:

- **Profile** (`ground` prints a partial one; complete `summary` + `tasks`):
  `{"summary": "...", "languages": ["Python"], "frameworks": ["Grasshopper"], "conventions": [], "tasks": ["..."]}`.
  Discovery weights matches by inverse document frequency, so a precise term ("script component")
  pulls more than a broad one ("python") — phrase `tasks` with the specific words the use case
  turns on.
- **Discover/select candidates**: `discover` prints `{"candidates": [...], ...}`. Each candidate
  is `{"score": N, "matched": [...], "entry": {...}}` — the skill's fields are nested under
  `entry`: `entry.id`, `entry.description`, `entry.source.license.spdx_id`, `entry.files`. Rank by
  reading `entry` + `matched`. `select --choose` wants the **exact** `entry.id`, which for a
  monorepo skill is `owner/repo:path/to/skill` (bare `owner/repo` only for a single-skill repo).
- **Eval queries** (`eval run`/`improve --queries`): a list of
  `{"id": "q1", "text": "...", "kind": "trigger" | "near-miss", "expected_skill": "<name|null>"}`.
  A `trigger` must route to `expected_skill`; a `near-miss` must route nowhere. The split is
  deterministic — every Nth id by sorted order is held out — and selection is on the held-out
  pass-rate, so an edit must improve genuine routing, not memorize the train queries.
- **Routing judgments** (`--judgments`, `--baseline-judgments`, `--candidate-judgments`): a list of
  `{"query_id": "q1", "routed_skill": "<name|null>"}` — your report of where the orchestrator sent
  each query, before and after the edit. The engine also routes the queries itself against the
  descriptions as a cross-check: `eval run` adds an `independent_trigger` score and any
  `routing_disagreements`, and `eval improve` rejects an edit whose independent held-out routing
  regresses even when your reported routing held.
- **Eval interchange** (`--ingest-source-evals`, `--write-evals`, `--history`): skillmeld speaks
  the skill-creator schemas. `evals.json` is
  `{"skill_name": ..., "evals": [{"id", "prompt", "expected_output", "files", "expectations"}]}`
  (the docs' `query`/`expected_behavior` shape is accepted on ingest); `history.json` is the
  improve ledger — a `v0` baseline, then one iteration per `eval improve` graded `won`/`lost`,
  with `current_best` tracking the accepted chain. Ingested queries are listed as
  `ingested_query_ids` in the `eval run` report and always land train-side. `--write-evals`
  keeps your query numbering when the ids carry unique digits (`q7` exports as case `7`).
- **Catalog status** (`catalog status`): before any sync the shape is
  `{"cached": false, "cache_dir": "..."}`; after one it is `{"cached": true, "cache_dir": "...",
  "generated_at": "...", "key_id": "...", "artifacts": [...]}` — the shape varies by state on
  purpose, so key off `cached`.
- **Carrying source identity (`--sources`)**: `merge`, `emit`, and `eval` all accept
  `--sources <discover.json>` to re-attach what discovery knew about each source (matched by bundle
  hash): its license and its catalog name. Pass it so the plan and `PROVENANCE.md` show the real
  license instead of "unknown", and so `eval`'s verifier matches a source whose `SKILL.md` omits
  `name:` (which otherwise loads under its bundle-hash dir name and fails the byte-trace check). A
  merged set is only as licensed as its least-licensed part: one unlicensed source resolves the
  whole set to unknown — surface that.
- **Merge result shape**: `merge` prints `{"result": ..., "problems": [...]}`. Inside `result`,
  each child is `skills[i].doc` with `frontmatter.{name, description}` and `body`; the router is
  `orchestrator.doc`; `plan` carries `kept`/`dropped`/`drop_reasons`/`conflicts_resolved`/
  `license_resolution`/`warnings`, plus `frontmatter_verdict` (`pass`/`review`) and
  `frontmatter_findings` for any carried-frontmatter REVIEW. A child may also carry
  `frontmatter.{allowed-tools, disallowed-tools, disable-model-invocation, compatibility, metadata}`
  reconciled from its sources. The description you author goes in `frontmatter.description`.
- **Chaining `eval improve`**: each call returns `{"decision": ..., "result": ...}` with the *whole*
  updated set. To author several descriptions, feed the returned `result` into the next
  `improve` so edits accumulate; author the children one at a time, then run `eval run` over the
  final result.
- **Ground evidence**: `ground` prints `{"profile": ..., "evidence": ...}`; `evidence` carries
  `instructions_file` + `instructions_excerpt` (the first 40 lines of the repo's agent
  instructions) and `agent_dirs` + `agents` (the markers found and the install targets they
  imply, in the order `--install-for` takes them).
- **Emit skills output**: `{"surface": "skills", "written": [...], "installed": [{"agents":
  [...], "scope": "project", "path": "...", "skills": [...]}], "overwritten": [...], "sidecars":
  [...], "agents_md": {"path": ..., "action": "created" | "replaced" | "appended"} | null,
  "portability": [{"skill", "verdict", "findings"}], "warnings": [...]}`. An install conflict is
  an `{"error": ...}` naming the existing directories and `--force`.

`eval` and `emit` take either the bare `result` object or the full `{result, problems}` (the
loaders accept both). A "coarse confidence band" on the review card is your judgment to add, not a
field the engine fills — `plan.confidence` stays null unless you set it.
