# SPDX-License-Identifier: Apache-2.0
"""Adapter parsing tests with canned scanner output; one real bandit run lives in scan tests."""

from __future__ import annotations

import json
from pathlib import Path

from skillmeld.security.adapters import parse_bandit, parse_gitleaks, parse_semgrep, run_all

BUNDLE = Path("/tmp/bundle")


def test_parse_bandit_maps_findings() -> None:
    output = json.dumps(
        {
            "results": [
                {
                    "filename": "/tmp/bundle/tool.py",
                    "line_number": 3,
                    "issue_severity": "HIGH",
                    "issue_text": "subprocess call with shell=True",
                    "test_id": "B602",
                }
            ]
        }
    )
    findings = parse_bandit(output, BUNDLE)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "bandit:B602"
    assert finding.severity == "high"
    assert finding.locus == "tool.py:3"


def test_parse_bandit_garbage_is_a_notice() -> None:
    findings = parse_bandit("not json", BUNDLE)
    assert findings[0].rule_id == "core:scanner-notice"
    assert findings[0].severity == "info"


def test_parse_semgrep_maps_findings_and_version() -> None:
    output = json.dumps(
        {
            "version": "1.165.0",
            "results": [
                {
                    "check_id": "skillmeld-python-eval",
                    "path": "/tmp/bundle/tool.py",
                    "start": {"line": 7},
                    "extra": {"severity": "ERROR", "message": "eval() on dynamic input"},
                }
            ],
        }
    )
    findings, version = parse_semgrep(output, BUNDLE)
    assert version == "1.165.0"
    assert findings[0].rule_id == "semgrep:skillmeld-python-eval"
    assert findings[0].severity == "high"
    assert findings[0].locus == "tool.py:7"


def test_parse_gitleaks_never_echoes_the_secret() -> None:
    report = json.dumps(
        [
            {
                "RuleID": "aws-access-key-id",
                "Description": "AWS access key id detected",
                "File": "/tmp/bundle/scripts/env.sh",
                "StartLine": 2,
                "Secret": "AKIA-SHOULD-NOT-APPEAR",
                "Match": "AKIA-SHOULD-NOT-APPEAR",
            }
        ]
    )
    findings = parse_gitleaks(report, BUNDLE)
    assert findings[0].rule_id == "gitleaks:aws-access-key-id"
    assert findings[0].severity == "high"
    assert findings[0].locus == "scripts/env.sh:2"
    assert "SHOULD-NOT-APPEAR" not in findings[0].message


def test_run_all_without_python_files_skips_bandit(tmp_path: Path) -> None:
    findings, versions = run_all(tmp_path, py_files=[])
    assert "bandit" in versions and versions["bandit"] != "absent"
    assert "semgrep" in versions
    assert "gitleaks" in versions
    assert not [f for f in findings if f.rule_id.startswith("bandit:")]
