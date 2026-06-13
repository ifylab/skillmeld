# SPDX-License-Identifier: Apache-2.0
"""Verdict reconciliation tests: bundle-hash parity, lookup, prefer-but-reverify."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from skillmeld.models import (
    ScanFinding,
    ScanReport,
    SkillFile,
    Verdict,
    VerdictIndex,
    VerdictRecord,
)
from skillmeld.registries.catalog import bundle_hash
from skillmeld.security.rules import RULESET_VERSION, SECRET_EXPOSURE, Severity
from skillmeld.security.scan import SCANNER_VERSION
from skillmeld.security.verdict import dir_bundle_hash, lookup, reconcile, worse


def test_dir_bundle_hash_matches_catalog_canonicalization(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    (root / "scripts").mkdir(parents=True)
    (root / "SKILL.md").write_bytes(b"# s\n")
    (root / "scripts" / "run.sh").write_bytes(b"echo hi\n")
    files = [
        SkillFile(path="SKILL.md", sha256=hashlib.sha256(b"# s\n").hexdigest()),
        SkillFile(path="scripts/run.sh", sha256=hashlib.sha256(b"echo hi\n").hexdigest()),
    ]
    assert dir_bundle_hash(root) == bundle_hash(files)


def test_dir_bundle_hash_ignores_vendored_dirs(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "SKILL.md").write_bytes(b"# s\n")
    before = dir_bundle_hash(root)
    (root / ".git").mkdir()
    (root / ".git" / "config").write_bytes(b"noise")
    assert dir_bundle_hash(root) == before


def _cache_with_verdicts(tmp_path: Path, records: list[VerdictRecord]) -> Path:
    cache = tmp_path / "cache"
    blobs = cache / "blobs"
    blobs.mkdir(parents=True)
    data = VerdictIndex(generated_at="2026-06-09T00:00:00Z", records=records)
    payload = data.model_dump_json().encode()
    digest = hashlib.sha256(payload).hexdigest()
    (blobs / digest).write_bytes(payload)
    manifest = {
        "schema_version": 1,
        "generated_at": "2026-06-09T00:00:00Z",
        "key_id": "k",
        "artifacts": [
            {
                "name": "verdicts",
                "version": "1",
                "url": "https://host/verdicts.json",
                "sha256": digest,
                "size": len(payload),
            }
        ],
    }
    (cache / "manifest.json").write_text(json.dumps(manifest))
    return cache


def _record(verdict: Verdict, scanner_version: str = SCANNER_VERSION) -> VerdictRecord:
    return VerdictRecord(
        bundle_hash="abc",
        scanner_version=scanner_version,
        ruleset_versions={"core": RULESET_VERSION},
        verdict=verdict,
        findings=[
            ScanFinding(
                rule_id="core:aws-access-key",
                category=SECRET_EXPOSURE,
                severity=Severity.HIGH,
                locus="x:1",
                message="central finding",
            )
        ],
        scanned_at="2026-06-09T00:00:00Z",
    )


def _local(verdict: Verdict) -> ScanReport:
    return ScanReport(
        verdict=verdict,
        scanner_version=SCANNER_VERSION,
        rulesets={"core": RULESET_VERSION},
    )


def test_lookup_finds_record(tmp_path: Path) -> None:
    cache = _cache_with_verdicts(tmp_path, [_record(Verdict.PASS)])
    found = lookup("abc", cache)
    assert found is not None and found.verdict is Verdict.PASS
    assert lookup("missing", cache) is None
    assert lookup("abc", tmp_path / "unsynced") is None


def test_reconcile_without_hosted_is_identity() -> None:
    local = _local(Verdict.PASS)
    assert reconcile(local, None) == local


def test_hosted_block_escalates_local_pass() -> None:
    merged = reconcile(_local(Verdict.PASS), _record(Verdict.BLOCK))
    assert merged.verdict is Verdict.BLOCK
    assert merged.hosted_verdict is Verdict.BLOCK
    assert any(f.rule_id == "hosted:core:aws-access-key" for f in merged.findings)


def test_hosted_pass_never_overrides_local_block() -> None:
    merged = reconcile(_local(Verdict.BLOCK), _record(Verdict.PASS))
    assert merged.verdict is Verdict.BLOCK


def test_version_mismatch_ignores_hosted_verdict() -> None:
    merged = reconcile(_local(Verdict.PASS), _record(Verdict.BLOCK, "skillmeld/9.9.9"))
    assert merged.verdict is Verdict.PASS
    assert merged.hosted_verdict is None
    assert any(f.rule_id == "core:hosted-verdict-stale" for f in merged.findings)


def test_worse_ordering() -> None:
    assert worse(Verdict.PASS, Verdict.REVIEW) is Verdict.REVIEW
    assert worse(Verdict.BLOCK, Verdict.REVIEW) is Verdict.BLOCK
    assert worse(Verdict.PASS, Verdict.PASS) is Verdict.PASS
