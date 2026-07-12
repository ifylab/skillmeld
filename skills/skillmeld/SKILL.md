---
name: skillmeld
description: "Discovers existing community skills for a described use case and merges the best two or three into one coherent, deduplicated, security-scanned skill set tailored to the user's project. Use when someone wants to assemble or compose skills for a workflow, combine existing skills instead of writing one from scratch, or build a tailored skillset from community sources. Grounds in the user's repo, scans every candidate before use, and shows provenance plus a review before installing."
---

# skillmeld

This skill is the front-end that drives the bundled `skillmeld` Python engine. The engine is
deterministic and makes no model calls; you supply the judgment and gate every side effect on
the user's approval.

## What this does

Turns a described use case (plus the user's repo) into a coherent skill set assembled from existing community skills: discover candidates, security-scan them, merge the best two or three, and install with the user's approval. Composes existing skills; never writes new instructions from scratch.

## How it runs

The deterministic engine is invoked from this skill via:

    bash "${CLAUDE_SKILL_DIR}/scripts/run.sh" <command> [args...]

`run.sh` is a thin wrapper; equivalently, run `uv run skillmeld <command>` from the package root. The steps below use the `run.sh` form.

Each command prints JSON to stdout. This skill reads that JSON and supplies the judgment steps (grouping atoms, adjudicating conflicts, choosing among existing options) in-session. The engine itself makes no model calls.

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
   JSON to a temp file.
3. Discover — `run.sh discover --profile <profile.json>` prefilters the synced catalog and
   prints scored candidates with per-match evidence (`matched`). Skills already blocked by
   the verdict index are dropped before anyone sees them.
4. Rank + select — rank the candidates by fit to the use case. Read each candidate's name,
   description, tags, and `matched` evidence; ignore `files`. Answer with existing candidate
   ids only — never invent an id — best first, at most three. Then
   `run.sh select --candidates <discover.json> --choose id1,id2` validates the pick and
   surfaces warnings (for example, two picks from the same source repo).
5. Fetch — `run.sh fetch --selection <select.json>` downloads only the chosen bundles and
   verifies every file against the hash the signed catalog pinned; a mismatch refuses the
   bundle. Run this only after the user has seen the shortlist. Carry the exact `path` values
   fetch returns into `scan` and `merge` — the cache is content-addressed and shared across
   runs, so globbing the bundles directory will pull in other selections' skills. Map each path
   by the `id` fetch reports next to it, never by directory order.
6. Security gate — `run.sh scan <bundle> [--sources <discover.json>]` for each: PASS proceeds,
   REVIEW is surfaced for a decision, BLOCK is refused. Pass `--sources` so a skill whose repo
   license the catalog already knows is not flagged license-unknown just because the LICENSE file
   stayed out of the bundle.
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
   with no description never triggers in Claude Code. For each child, write a short, trigger-
   friendly description and gate it through
   `run.sh eval improve --skill <index|orchestrator> --description "..."` with the trigger
   queries and routing judgments — an edit is accepted only if structural quality holds, no
   held-out query leaks, and the held-out routing pass-rate does not regress — measured both from
   your reported routing and from an independent engine-side pass that routes the queries against
   the descriptions, so acceptance never rests on your self-report. Phrase each description with
   the literal words a user would say; the independent router keys on them. Keep it
   within the routing budget — Claude Code truncates the description at 1536 characters in its skill
   listing and the API surface caps it at 1024, so lead with the key use case. The orchestrator
   ships with a templated routing description already; refine it the same way (`--skill
   orchestrator`) only if needed. Pass `--sources <discover.json>` to `eval improve` and `eval run`
   (the same JSON you gave merge) so the verifier resolves each source's catalog identity — without
   it, a source whose `SKILL.md` omits `name:` fails the byte-trace check. Then `run.sh eval run`
   must report `passed: true` over the set.
   Optional interchange: `eval improve --history <path>` keeps a portable `history.json` ledger of
   the accepted and rejected edits, and `eval run --write-evals <path>` exports the query set as a
   portable `evals.json` (both skill-creator formats). When a fetched source bundles its own evals
   (`evals/evals.json`), `--ingest-source-evals` folds them in as extra train-side trigger queries
   targeting that skill — they never enter the held-out split, so the leakage gate and the improve
   selection stay on your own queries.
   With the set now complete, show the user the plan and the authored descriptions as one
   consolidated review before writing anything.
9. Emit — `run.sh emit <surface>` packages the result; install only after the user approves. Emit
   refuses any skill (child or orchestrator) whose description is still empty, so a set can never
   ship dead even if this step was rushed. Surfaces: `claude-code` (skills tree), `claudeai` (zip),
   `api` (`/v1/skills` payload), and `marketplace` (a `strict:false` Claude Code plugin marketplace
   the user can host and `/plugin marketplace add`). Each returns `warnings` to relay before install:
   `emit claude-code`, `emit claudeai`, and `emit marketplace` flag any description over the
   1536-char Claude Code routing cap (truncated in the skill listing, so routing keywords are lost);
   `emit api` flags a description over the 1024-char `/v1/skills` cap (the upload is rejected), plus
   any tool or invocation frontmatter that surface does not enforce. `emit marketplace` defaults the
   marketplace name and owner to the skill's slug and warns when it does (pass `--marketplace-name`
   and `--owner-name` to set them); it refuses a name reserved for official use.

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
    line (`pdf-helper reads ~/.aws/credentials, line 34`), not boilerplate;
  - any frontmatter REVIEW from `plan.frontmatter_findings` (a source's pre-approved tool dropped
    in the intersection, or a child left non-invocable), named for the skill it affects;
  - the license resolution and a coarse confidence band.
  Actions: Approve and install / Adjust / Dry-run / Cancel.
- **Stop 2 — second-layer scan and write** (the install/trust gate). Re-scan the merged
  artifact (`run.sh scan <merged-bundle>`); a BLOCK here refuses the install. Write to
  `.claude/skills/<name>/` with `SKILL.md`, supporting files, and `PROVENANCE.md` only after
  the user accepts.
- **A BLOCK is never one-click overridable.** REVIEW is the only interactive security stop;
  BLOCK is refused and excluded before the user chooses. Keep BLOCK rare and high-precision so
  REVIEW prompts stay trusted.
- **Close by making the user smarter, not just handing over an artifact:** one line on why each
  skill was picked, the 2-3 bullet "what was merged and why" reflection, and a pointer to
  `PROVENANCE.md` and the sources. Everything deep (full findings, per-line evidence, raw
  scores) lives behind "show details".

A non-interactive escape (`--yes` / `--all`, when wired) may skip the REVIEW prompt for CI, but
never bypasses a BLOCK.

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
  `ingested_query_ids` in the `eval run` report and always land train-side.
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

`eval` and `emit` take either the bare `result` object or the full `{result, problems}` (the
loaders accept both). A "coarse confidence band" on the review card is your judgment to add, not a
field the engine fills — `plan.confidence` stays null unless you set it.
