# SPDX-License-Identifier: Apache-2.0
"""Command-line surface. Each subcommand prints JSON to stdout; the skill drives these."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from skillmeld import __version__
from skillmeld.discovery import DEFAULT_LIMIT, discover
from skillmeld.grounding import profile_from, scan
from skillmeld.models import Candidate, MergeResult, SkillDoc, UseCaseProfile
from skillmeld.select import SelectionError, select


def _emit(payload: dict[str, object]) -> int:
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def _not_implemented(command: str) -> int:
    return _emit({"status": "not-implemented", "command": command})


def _error(message: str) -> int:
    json.dump({"error": message}, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 1


def _read_text(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    return Path(source).read_text(encoding="utf-8")


def _skill_target(value: str) -> int | str:
    """Parse ``--skill``: a child index, or the literal ``orchestrator``."""
    if value == "orchestrator":
        return value
    try:
        return int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--skill must be an integer index or 'orchestrator'"
        ) from exc


def _carry_catalog_sources(
    bundles: list[str], docs: list[SkillDoc], sources_path: str
) -> str | None:
    """Carry catalog provenance (license, repo, url) onto loaded bundles, matched by bundle hash.

    The merge and emit surfaces re-load bundles from disk, which lose the licenses discovery knew.
    A discover/select JSON re-attaches them so the plan and PROVENANCE show the real per-source
    license instead of collapsing every source to unknown. Returns an error string, or None on ok.
    """
    from skillmeld.security.verdict import dir_bundle_hash

    try:
        by_hash = {
            candidate.entry.bundle_hash: candidate.entry.source
            for candidate in _candidates_from(_read_text(sources_path))
            if candidate.entry.bundle_hash
        }
    except (OSError, ValueError, ValidationError) as exc:
        return f"sources not readable: {exc}"
    for bundle, doc in zip(bundles, docs, strict=True):
        catalog_source = by_hash.get(dir_bundle_hash(Path(bundle)))
        if catalog_source is not None:
            doc.source = catalog_source
    return None


def _candidates_from(text: str) -> list[Candidate]:
    """Accept discover output, select output, or a bare candidate list."""
    data = json.loads(text)
    if isinstance(data, dict):
        items = data.get("candidates") or data.get("chosen")
    elif isinstance(data, list):
        items = data
    else:
        items = None
    if not isinstance(items, list):
        raise ValueError("no candidates found in input")
    return [Candidate.model_validate(item) for item in items]


def _cmd_intake(use_case: str) -> int:
    from skillmeld.intake import intake

    return _emit(intake(use_case).model_dump())


def _cmd_ground(repo: str) -> int:
    target = Path(repo)
    if not target.exists():
        return _error(f"path not found: {repo}")
    evidence = scan(target)
    profile = profile_from(evidence)
    return _emit({"profile": profile.model_dump(), "evidence": evidence.model_dump()})


def _cmd_catalog(action: str) -> int:
    from skillmeld.registries import catalog_client as cat

    cache = cat.cache_root()
    if action == "status":
        manifest = cat.cached_manifest(cache)
        if manifest is None:
            return _emit({"cached": False, "cache_dir": str(cache)})
        return _emit(
            {
                "cached": True,
                "cache_dir": str(cache),
                "generated_at": manifest.generated_at,
                "key_id": manifest.key_id,
                "artifacts": [artifact.name for artifact in manifest.artifacts],
            }
        )
    try:
        manifest = cat.load_snapshot(cache) if action == "verify" else cat.sync()
    except (cat.CatalogError, OSError) as exc:
        return _error(str(exc))
    return _emit(
        {
            "action": action,
            "ok": True,
            "generated_at": manifest.generated_at,
            "artifacts": [artifact.name for artifact in manifest.artifacts],
        }
    )


def _cmd_discover(profile_path: str, catalog_path: str | None, limit: int) -> int:
    from skillmeld.registries import catalog as catalog_data
    from skillmeld.registries.catalog_client import CatalogError

    try:
        profile = UseCaseProfile.model_validate_json(_read_text(profile_path))
    except (OSError, ValidationError) as exc:
        return _error(f"profile not readable: {exc}")
    try:
        if catalog_path is not None:
            document = catalog_data.load_catalog_file(Path(catalog_path))
            source = "local-file (unsigned, development only)"
        else:
            document = catalog_data.load_catalog()
            source = "signed-cache"
        blocked = catalog_data.load_blocked_hashes()
    except CatalogError as exc:
        return _error(str(exc))
    result = discover(profile, document.entries, blocked=blocked, limit=limit)
    payload = result.model_dump()
    payload["catalog"] = {"generated_at": document.generated_at, "source": source}
    return _emit(payload)


def _cmd_select(candidates_path: str, choose: str) -> int:
    try:
        candidates = _candidates_from(_read_text(candidates_path))
    except (OSError, ValueError, ValidationError) as exc:
        return _error(f"candidates not readable: {exc}")
    chosen_ids = [part.strip() for part in choose.split(",") if part.strip()]
    try:
        selection = select(candidates, chosen_ids)
    except SelectionError as exc:
        return _error(str(exc))
    return _emit(selection.model_dump())


def _cmd_scan(skill: str, include_license: bool, sources_path: str | None = None) -> int:
    from skillmeld.models import ScanFinding
    from skillmeld.registries.catalog_client import CatalogError
    from skillmeld.security import license as license_check
    from skillmeld.security import verdict as verdict_mod
    from skillmeld.security.rules import META, Severity
    from skillmeld.security.scan import scan_bundle, verdict_from
    from skillmeld.security.verdict import dir_bundle_hash

    path = Path(skill)
    if not path.is_dir():
        return _error(f"not a directory: {skill}")
    report = scan_bundle(path)
    try:
        hosted = verdict_mod.lookup(report.bundle_hash)
    except CatalogError as exc:
        hosted = None
        report.findings.append(
            ScanFinding(
                rule_id="core:scan-notice",
                category=META,
                severity=Severity.INFO,
                locus="-",
                message=f"verdict index unreadable: {exc}",
            )
        )
    report = verdict_mod.reconcile(report, hosted)
    if include_license:
        info, license_findings = license_check.detect_bundle(path)
        # A bundle rarely ships its repo's LICENSE file, so "unknown" here is often a false gate.
        # When --sources carries the catalog's known SPDX for this bundle, trust it and drop the
        # license-unknown finding — the catalog read the repo LICENSE the bundle left behind.
        if sources_path is not None and info.spdx_id is None:
            try:
                by_hash = {
                    candidate.entry.bundle_hash: candidate.entry.source.license
                    for candidate in _candidates_from(_read_text(sources_path))
                    if candidate.entry.bundle_hash
                }
            except (OSError, ValueError, ValidationError) as exc:
                return _error(f"sources not readable: {exc}")
            catalog_license = by_hash.get(dir_bundle_hash(path))
            if catalog_license is not None and catalog_license.spdx_id is not None:
                info = catalog_license
                license_findings = [
                    finding
                    for finding in license_findings
                    if finding.rule_id != "core:license-unknown"
                ]
        findings = [*report.findings, *license_findings]
        report = report.model_copy(
            update={
                "license": info,
                "findings": findings,
                "verdict": verdict_mod.worse(report.verdict, verdict_from(findings)),
            }
        )
    return _emit(report.model_dump())


def _cmd_merge(
    bundles: list[str],
    profile_path: str,
    grouping_path: str | None,
    adjudication_path: str | None,
    sources_path: str | None = None,
) -> int:
    from skillmeld.merge.group import Assignment
    from skillmeld.merge.pipeline import load_bundle, run_merge
    from skillmeld.models import Conflict

    try:
        profile = UseCaseProfile.model_validate_json(_read_text(profile_path))
    except (OSError, ValidationError) as exc:
        return _error(f"profile not readable: {exc}")
    sources = []
    for bundle in bundles:
        path = Path(bundle)
        if not (path / "SKILL.md").is_file():
            return _error(f"bundle has no SKILL.md: {bundle}")
        sources.append(load_bundle(path))

    if sources_path is not None:
        error = _carry_catalog_sources(bundles, sources, sources_path)
        if error is not None:
            return _error(error)

    assignments: dict[str, Assignment] | None = None
    if grouping_path is not None:
        try:
            raw = json.loads(_read_text(grouping_path))
            assignments = {key: Assignment.model_validate(value) for key, value in raw.items()}
        except (OSError, ValueError, ValidationError) as exc:
            return _error(f"grouping not readable: {exc}")
    adjudication: list[Conflict] | None = None
    if adjudication_path is not None:
        try:
            items = json.loads(_read_text(adjudication_path))
            adjudication = [Conflict.model_validate(item) for item in items]
        except (OSError, ValueError, ValidationError) as exc:
            return _error(f"adjudication not readable: {exc}")

    run = run_merge(sources, profile, assignments=assignments, adjudication=adjudication)
    return _emit(run.model_dump())


def _load_merge_result(path: str) -> MergeResult:
    data = json.loads(_read_text(path))
    if isinstance(data, dict) and "result" in data:
        data = data["result"]
    return MergeResult.model_validate(data)


def _load_sources(bundles: list[str]) -> list[SkillDoc]:
    from skillmeld.merge.pipeline import load_bundle

    return [load_bundle(Path(bundle)) for bundle in bundles]


def _cmd_eval(args: argparse.Namespace) -> int:
    from skillmeld.eval.evaluate import apply_description_edit, evaluate
    from skillmeld.eval.trigger import TriggerJudgment, TriggerQuery

    try:
        result = _load_merge_result(args.result)
        sources = _load_sources(args.bundles)
    except (OSError, ValueError, ValidationError) as exc:
        return _error(f"inputs not readable: {exc}")

    # Align source identity to the catalog, exactly as merge/emit do. Without this, a source whose
    # SKILL.md omits `name:` loads under its bundle-hash dir name, so re-parsing here yields atom
    # ids that the byte-traceability verifier cannot match against the catalog-named merge result.
    if getattr(args, "sources", None) is not None:
        error = _carry_catalog_sources(args.bundles, sources, args.sources)
        if error is not None:
            return _error(error)

    queries = None
    if getattr(args, "queries", None):
        queries = [TriggerQuery.model_validate(q) for q in json.loads(_read_text(args.queries))]

    if args.action == "run":
        judgments = None
        if getattr(args, "judgments", None):
            judgments = [
                TriggerJudgment.model_validate(j) for j in json.loads(_read_text(args.judgments))
            ]
        report = evaluate(result, sources, queries=queries, judgments=judgments)
        return _emit(report.model_dump())

    if queries is None:
        return _error("eval improve requires --queries")
    baseline = [
        TriggerJudgment.model_validate(j) for j in json.loads(_read_text(args.baseline_judgments))
    ]
    candidate = [
        TriggerJudgment.model_validate(j) for j in json.loads(_read_text(args.candidate_judgments))
    ]
    edited, decision = apply_description_edit(
        result,
        sources,
        args.skill,
        args.description,
        queries=queries,
        baseline_judgments=baseline,
        candidate_judgments=candidate,
    )
    return _emit({"decision": decision.model_dump(), "result": edited.model_dump()})


def _cmd_dev_catalog(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    import httpx

    from skillmeld.hosted.build_catalog import build_dev_catalog

    generated_at = args.generated_at or datetime.now(UTC).isoformat(timespec="seconds")
    out = Path(args.out)
    try:
        manifest, public_hex = build_dev_catalog(args.repos, out, generated_at=generated_at)
    except (OSError, httpx.HTTPError) as exc:
        return _error(f"dev-catalog build failed: {exc}")
    return _emit(
        {
            "out": str(out),
            "generated_at": manifest.generated_at,
            "artifacts": [artifact.name for artifact in manifest.artifacts],
            "dev_pubkey": public_hex,
            "hint": f"export SKILLMELD_DEV_PUBKEY={public_hex}",
        }
    )


def _cmd_emit(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from skillmeld.emit.package import (
        api_description_warnings,
        api_surface_warnings,
        apply_source_licenses,
        emit_api_payload,
        emit_blockers,
        emit_claude_code,
        emit_claudeai_zip,
        emit_marketplace,
        marketplace_name_blocker,
        plan_support_carry,
        routing_truncation_warnings,
    )

    try:
        result = _load_merge_result(args.result)
        sources = _load_sources(args.bundles)
    except (OSError, ValueError, ValidationError) as exc:
        return _error(f"inputs not readable: {exc}")

    if args.sources is not None:
        error = _carry_catalog_sources(args.bundles, sources, args.sources)
        if error is not None:
            return _error(error)

    blockers = emit_blockers(result)
    if blockers:
        return _error("refusing to emit a skill with no description: " + "; ".join(blockers))
    apply_source_licenses(result, sources)
    generated_at = args.generated_at or datetime.now(UTC).isoformat(timespec="seconds")

    if args.surface == "api":
        return _emit(
            {
                "surface": "api",
                "skills": emit_api_payload(result),
                "warnings": api_surface_warnings(result) + api_description_warnings(result),
            }
        )

    out = args.out
    if out is None:
        return _error(f"emit {args.surface} requires --out")
    carry = plan_support_carry(result, sources, args.bundles)
    routing_warnings = routing_truncation_warnings(result)
    if args.surface == "claudeai":
        data = emit_claudeai_zip(result, sources=sources, generated_at=generated_at, carry=carry)
        Path(out).write_bytes(data)
        return _emit(
            {"surface": "claudeai", "zip": out, "bytes": len(data), "warnings": routing_warnings}
        )
    if args.surface == "marketplace":
        from skillmeld.merge.synthesize import slug

        primary = result.orchestrator or (result.skills[0] if result.skills else None)
        if primary is None:
            return _error("nothing to emit: the merge result has no skills")
        plugin_name = slug(str(primary.doc.frontmatter.get("name", primary.doc.source.name)))
        warnings = list(routing_warnings)

        if args.marketplace_name:
            marketplace_name = slug(args.marketplace_name)
            if marketplace_name != args.marketplace_name:
                warnings.append(
                    f"marketplace name normalized to '{marketplace_name}' (must be kebab-case)"
                )
        else:
            marketplace_name = plugin_name
            warnings.append(
                f"marketplace name defaulted to '{marketplace_name}'; "
                "pass --marketplace-name to set your namespace"
            )
        reserved = marketplace_name_blocker(marketplace_name)
        if reserved is not None:
            return _error(reserved + "; pass a different --marketplace-name")

        if args.owner_name:
            owner = {"name": args.owner_name}
        else:
            owner = {"name": marketplace_name}
            warnings.append(
                f"owner name defaulted to '{marketplace_name}'; "
                "pass --owner-name to set the maintainer"
            )
        if args.owner_email:
            owner["email"] = args.owner_email

        written = emit_marketplace(
            result,
            Path(out),
            sources=sources,
            generated_at=generated_at,
            marketplace_name=marketplace_name,
            owner=owner,
            plugin_name=plugin_name,
            carry=carry,
        )
        return _emit({"surface": "marketplace", "written": written, "warnings": warnings})

    written = emit_claude_code(
        result, Path(out), sources=sources, generated_at=generated_at, carry=carry
    )
    return _emit({"surface": "claude-code", "written": written, "warnings": routing_warnings})


def _cmd_fetch(selection_path: str) -> int:
    from skillmeld.registries.fetch import FetchError, fetch_bundle

    try:
        candidates = _candidates_from(_read_text(selection_path))
    except (OSError, ValueError, ValidationError) as exc:
        return _error(f"selection not readable: {exc}")
    if not candidates:
        return _error("selection holds no candidates")
    bundles: list[dict[str, object]] = []
    try:
        for candidate in candidates:
            path = fetch_bundle(candidate.entry)
            bundles.append(
                {
                    "id": candidate.entry.id,
                    "bundle_hash": candidate.entry.bundle_hash,
                    "path": str(path),
                }
            )
    except FetchError as exc:
        return _error(str(exc))
    return _emit({"bundles": bundles})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skillmeld",
        description="Discover existing community skills and merge the best into one tailored set.",
    )
    parser.add_argument("--version", action="version", version=f"skillmeld {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    catalog = sub.add_parser("catalog", help="Fetch and verify the hosted data layer.")
    catalog.add_argument("action", choices=["sync", "verify", "status"])

    intake = sub.add_parser("intake", help="Normalize a use case and flag if it is too thin.")
    intake.add_argument("use_case")

    ground = sub.add_parser("ground", help="Scan a repo into a use-case profile.")
    ground.add_argument("repo")

    dev_catalog = sub.add_parser(
        "dev-catalog", help="Build a local dev-signed catalog from GitHub repos (no ops)."
    )
    dev_catalog.add_argument("--repos", nargs="+", required=True, help="owner/name repos to crawl.")
    dev_catalog.add_argument("--out", required=True, help="Output cache directory.")
    dev_catalog.add_argument("--generated-at", help="Override the catalog timestamp (for tests).")

    discover_parser = sub.add_parser("discover", help="Find candidate skills for the profile.")
    discover_parser.add_argument(
        "--profile", required=True, help="Use-case profile JSON path, or - for stdin."
    )
    discover_parser.add_argument(
        "--catalog", help="Local catalog JSON file (unsigned; development only)."
    )
    discover_parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help="Shortlist size cap."
    )

    select_parser = sub.add_parser("select", help="Choose up to three candidates.")
    select_parser.add_argument(
        "--candidates", required=True, help="Discover output path, or - for stdin."
    )
    select_parser.add_argument(
        "--choose", required=True, help="Comma-separated candidate ids, ranked best first."
    )

    fetch_parser = sub.add_parser("fetch", help="Download and verify the selected bundles.")
    fetch_parser.add_argument(
        "--selection", required=True, help="Select output path, or - for stdin."
    )

    scan_parser = sub.add_parser("scan", help="Security- and license-scan a skill.")
    scan_parser.add_argument("skill")
    scan_parser.add_argument("--license", action="store_true", help="Include license detection.")
    scan_parser.add_argument(
        "--sources", help="discover/select JSON; trust the catalog SPDX over a missing LICENSE."
    )

    merge = sub.add_parser("merge", help="Merge selected skill bundles into one tailored set.")
    merge.add_argument("--bundles", nargs="+", required=True, help="Bundle directories to merge.")
    merge.add_argument("--profile", required=True, help="Use-case profile JSON path, or -.")
    merge.add_argument("--grouping", help="Host-Claude grouping JSON ({atom_id: {group, kind}}).")
    merge.add_argument("--adjudication", help="Host-Claude conflict adjudication JSON (list).")
    merge.add_argument(
        "--sources", help="discover/select JSON; carries catalog licenses into the provenance."
    )

    evaluate = sub.add_parser("eval", help="Evaluate or improve a merged set (no model calls).")
    evaluate.add_argument("action", choices=["run", "improve"])
    evaluate.add_argument("--result", required=True, help="Merge result/run JSON path, or -.")
    evaluate.add_argument("--bundles", nargs="+", required=True, help="Source bundle directories.")
    evaluate.add_argument("--queries", help="Trigger-eval queries JSON (list).")
    evaluate.add_argument("--judgments", help="Routing judgments JSON for eval run (list).")
    evaluate.add_argument(
        "--skill",
        type=_skill_target,
        default=0,
        help="Child index or 'orchestrator' to edit (improve).",
    )
    evaluate.add_argument("--description", default="", help="Candidate description (improve).")
    evaluate.add_argument("--baseline-judgments", help="Baseline routing judgments (improve).")
    evaluate.add_argument("--candidate-judgments", help="Candidate routing judgments (improve).")
    evaluate.add_argument(
        "--sources",
        help="discover/select JSON; align source identity to the catalog (as merge/emit).",
    )

    emit = sub.add_parser("emit", help="Package the merged set for a surface.")
    emit.add_argument("surface", choices=["claude-code", "claudeai", "api", "marketplace"])
    emit.add_argument("--result", required=True, help="Merge result/run JSON path, or -.")
    emit.add_argument("--bundles", nargs="+", required=True, help="Source bundle directories.")
    emit.add_argument("--out", help="Output dir (claude-code, marketplace) or zip path (claudeai).")
    emit.add_argument("--generated-at", help="Override the provenance timestamp (for tests).")
    emit.add_argument(
        "--sources", help="discover/select JSON; carries catalog licenses into PROVENANCE."
    )
    emit.add_argument(
        "--marketplace-name", help="Marketplace name, kebab-case (marketplace surface)."
    )
    emit.add_argument("--owner-name", help="Marketplace maintainer name (marketplace surface).")
    emit.add_argument("--owner-email", help="Marketplace maintainer email (marketplace surface).")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command: str | None = args.command
    if command is None:
        parser.print_help(sys.stderr)
        return 2
    if command == "intake":
        return _cmd_intake(args.use_case)
    if command == "ground":
        return _cmd_ground(args.repo)
    if command == "catalog":
        return _cmd_catalog(args.action)
    if command == "dev-catalog":
        return _cmd_dev_catalog(args)
    if command == "discover":
        return _cmd_discover(args.profile, args.catalog, args.limit)
    if command == "select":
        return _cmd_select(args.candidates, args.choose)
    if command == "fetch":
        return _cmd_fetch(args.selection)
    if command == "scan":
        return _cmd_scan(args.skill, args.license, args.sources)
    if command == "merge":
        return _cmd_merge(
            args.bundles, args.profile, args.grouping, args.adjudication, args.sources
        )
    if command == "eval":
        return _cmd_eval(args)
    if command == "emit":
        return _cmd_emit(args)
    parts: list[str] = [command]
    for attr in ("action", "step", "surface"):
        value = getattr(args, attr, None)
        if value is not None:
            parts.append(str(value))
    return _not_implemented(" ".join(parts))
