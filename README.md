# skillmeld

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
- **emit** packages the result for Claude Code, claude.ai, or the API, with a `PROVENANCE.md` recording where every part came from.

## Try it

skillmeld runs in Claude Code. Install [uv](https://docs.astral.sh/uv/), then:

```sh
git clone https://github.com/ifylab/skillmeld && cd skillmeld
uv run skillmeld --help
```

The engine is exercised directly from the CLI (each command prints JSON):

```sh
uv run skillmeld ground .                       # scan a repo into a profile
uv run skillmeld scan path/to/skill --license   # security- and license-scan a bundle
uv run skillmeld merge --bundles a/ b/ --profile profile.json
```

As a skill, add the marketplace and install the plugin, then invoke it with your use case:

```
/plugin marketplace add ./skillmeld
/plugin install skillmeld@ifylab
/skillmeld I get IFC models from architects and need a quantity takeoff plus validation
```

## Status

In active development, built in the open one piece at a time. The discovery, security, merge, evaluation, and packaging stages are implemented and tested; the hosted catalog and the curated AEC corpus are coming next.

## Stack

Python 3.12, managed with uv. Ruff for lint and format, ty for type-checking, pytest for tests.

```sh
uv run ruff check . && uv run ty check && uv run pytest
```

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE). Contributions are accepted under the same license; no separate contributor agreement is required.
