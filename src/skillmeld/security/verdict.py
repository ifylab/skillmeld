# SPDX-License-Identifier: Apache-2.0
"""Hosted-verdict reconciliation: scanner-versioned lookup, prefer-but-reverify.

The hosted index is advisory central intelligence. A hosted PASS is a fast-path cache and
never overrides a local BLOCK; a hosted BLOCK escalates a clean local scan; a record scanned
with different scanner or ruleset versions is ignored (the local rescan stands alone).
"""

from __future__ import annotations

from pathlib import Path

from skillmeld.grounding import IGNORE_DIRS
from skillmeld.models import ScanFinding, ScanReport, SkillFile, Verdict, VerdictRecord
from skillmeld.registries.catalog import bundle_hash, load_verdict_index
from skillmeld.registries.catalog_client import sha256_hex
from skillmeld.security.rules import META, Severity

_ORDER = {Verdict.PASS: 0, Verdict.REVIEW: 1, Verdict.BLOCK: 2}


def worse(a: Verdict, b: Verdict) -> Verdict:
    return a if _ORDER[a] >= _ORDER[b] else b


def dir_bundle_hash(path: Path) -> str:
    """Bundle hash of a local directory, using the pinned catalog canonicalization."""
    root = path.resolve()
    files = [
        SkillFile(path=p.relative_to(root).as_posix(), sha256=sha256_hex(p.read_bytes()))
        for p in sorted(root.rglob("*"))
        if p.is_file() and not any(part in IGNORE_DIRS for part in p.parts)
    ]
    return bundle_hash(files)


def lookup(bundle_hash_value: str, cache_dir: Path | None = None) -> VerdictRecord | None:
    """Find the hosted verdict record for a bundle hash, if the cached index has one."""
    index = load_verdict_index(cache_dir)
    if index is None:
        return None
    for record in index.records:
        if record.bundle_hash == bundle_hash_value:
            return record
    return None


def reconcile(local: ScanReport, hosted: VerdictRecord | None) -> ScanReport:
    """Merge a hosted advisory verdict into a local report. The worse verdict wins."""
    if hosted is None:
        return local
    versions_match = (
        hosted.scanner_version == local.scanner_version
        and hosted.ruleset_versions.get("core") == local.rulesets.get("core")
    )
    findings = list(local.findings)
    if not versions_match:
        findings.append(
            ScanFinding(
                rule_id="core:hosted-verdict-stale",
                category=META,
                severity=Severity.INFO,
                locus="-",
                message=(
                    f"hosted verdict ignored: scanned by {hosted.scanner_version!r}, "
                    f"local is {local.scanner_version!r}"
                ),
            )
        )
        return local.model_copy(update={"findings": findings})
    for finding in hosted.findings:
        if finding.rule_id.startswith("hosted:"):
            findings.append(finding)
        else:
            findings.append(finding.model_copy(update={"rule_id": f"hosted:{finding.rule_id}"}))
    return local.model_copy(
        update={
            "findings": findings,
            "verdict": worse(local.verdict, hosted.verdict),
            "hosted_verdict": hosted.verdict,
        }
    )
