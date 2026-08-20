# SPDX-License-Identifier: Apache-2.0
"""Scanner adapters: bandit is a hard dependency; semgrep, gitleaks, and skillspector run
when installed.

Adapters only ever add findings — they can escalate a verdict, never relax one, and none of
them emits CRITICAL: BLOCK stays reserved for the core rules' high-precision hits, so an
adapter's worst case is REVIEW. Subprocesses run without a shell and under hard timeouts;
semgrep uses the pinned config shipped with the package (never the remote registry), and
every scanner's version lands in the report so hosted verdicts stay scanner-versioned.
Secret values found by gitleaks are never echoed. skillspector always runs ``--no-llm`` so
scanned content never leaves the machine; its SC4 check may still send dependency *names*
(never file contents) to OSV.dev, with a bundled fallback list when that host is unreachable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

from skillmeld.models import ScanFinding
from skillmeld.security.rules import (
    CREDENTIAL_HANDLING,
    MALICIOUS_CODE,
    META,
    PROMPT_INJECTION,
    SECRET_EXPOSURE,
    UNVERIFIABLE_DEPENDENCY,
    Severity,
)

_TIMEOUT = 120.0
_SEMGREP_CONFIG = Path(__file__).parent / "configs" / "semgrep.yml"

_BANDIT_SEVERITY = {
    "HIGH": Severity.HIGH,
    "MEDIUM": Severity.MEDIUM,
    "LOW": Severity.LOW,
}
_SEMGREP_SEVERITY = {
    "ERROR": Severity.HIGH,
    "WARNING": Severity.MEDIUM,
    "INFO": Severity.LOW,
}
# CRITICAL deliberately caps at HIGH: adapter findings are probabilistic, so their worst
# verdict is REVIEW — only the core rules can BLOCK.
_SKILLSPECTOR_SEVERITY = {
    "CRITICAL": Severity.HIGH,
    "HIGH": Severity.HIGH,
    "MEDIUM": Severity.MEDIUM,
    "LOW": Severity.LOW,
}
# skillspector's own 17 categories, folded onto the taxonomy the gate reports; anything
# unmapped (Behavioral AST, YARA Signatures, ...) lands in malicious-code.
_SKILLSPECTOR_CATEGORY = {
    "Prompt Injection": PROMPT_INJECTION,
    "Anti-Refusal": PROMPT_INJECTION,
    "System Prompt Leakage": PROMPT_INJECTION,
    "Memory Poisoning": PROMPT_INJECTION,
    "Trigger Abuse": PROMPT_INJECTION,
    "Data Exfiltration": CREDENTIAL_HANDLING,
    "Supply Chain": UNVERIFIABLE_DEPENDENCY,
}


def run_all(bundle: Path, py_files: list[Path]) -> tuple[list[ScanFinding], dict[str, str]]:
    """Run every available scanner over the bundle; report findings and scanner versions."""
    findings: list[ScanFinding] = []
    versions: dict[str, str] = {"bandit": _dist_version("bandit")}

    if py_files:
        findings.extend(_run_bandit(bundle))

    semgrep = shutil.which("semgrep")
    if semgrep:
        semgrep_findings, semgrep_version = _run_semgrep(semgrep, bundle)
        findings.extend(semgrep_findings)
        versions["semgrep"] = semgrep_version
    else:
        versions["semgrep"] = "absent"
        findings.append(_coverage_notice("semgrep"))

    gitleaks = shutil.which("gitleaks")
    if gitleaks:
        findings.extend(_run_gitleaks(gitleaks, bundle))
        versions["gitleaks"] = _gitleaks_version(gitleaks)
    else:
        versions["gitleaks"] = "absent"
        findings.append(_coverage_notice("gitleaks"))

    skillspector = shutil.which("skillspector")
    if skillspector:
        findings.extend(_run_skillspector(skillspector, bundle))
        versions["skillspector"] = _skillspector_version(skillspector)
    else:
        versions["skillspector"] = "absent"
        findings.append(_coverage_notice("skillspector"))

    return findings, versions


def _coverage_notice(name: str) -> ScanFinding:
    """A gate that runs with reduced coverage says so, not just a quiet version entry."""
    return _notice(
        f"{name} is not on PATH — this scan ran without its coverage; "
        f"install it to escalate-only widen the gate"
    )


def _run_bandit(bundle: Path) -> list[ScanFinding]:
    cmd = [sys.executable, "-m", "bandit", "-q", "-f", "json", "-r", str(bundle)]
    output = _run(cmd, "bandit", ok_codes={0, 1})
    if isinstance(output, ScanFinding):
        return [output]
    return parse_bandit(output, bundle)


def parse_bandit(output: str, bundle: Path) -> list[ScanFinding]:
    try:
        data = json.loads(output)
    except ValueError:
        return [_notice("bandit produced unparseable output")]
    findings: list[ScanFinding] = []
    for result in data.get("results", []):
        severity = _BANDIT_SEVERITY.get(str(result.get("issue_severity")), Severity.LOW)
        findings.append(
            ScanFinding(
                rule_id=f"bandit:{result.get('test_id', 'unknown')}",
                category=MALICIOUS_CODE,
                severity=severity,
                locus=f"{_rel(result.get('filename', ''), bundle)}:{result.get('line_number', 0)}",
                message=str(result.get("issue_text", "bandit finding")),
            )
        )
    return findings


def _run_semgrep(binary: str, bundle: Path) -> tuple[list[ScanFinding], str]:
    cmd = [
        binary,
        "scan",
        "--json",
        "--quiet",
        "--metrics=off",
        "--config",
        str(_SEMGREP_CONFIG),
        str(bundle),
    ]
    output = _run(cmd, "semgrep", ok_codes={0, 1})
    if isinstance(output, ScanFinding):
        return [output], "error"
    return parse_semgrep(output, bundle)


def parse_semgrep(output: str, bundle: Path) -> tuple[list[ScanFinding], str]:
    try:
        data = json.loads(output)
    except ValueError:
        return [_notice("semgrep produced unparseable output")], "unknown"
    findings: list[ScanFinding] = []
    for result in data.get("results", []):
        extra = result.get("extra", {})
        severity = _SEMGREP_SEVERITY.get(str(extra.get("severity")), Severity.LOW)
        findings.append(
            ScanFinding(
                rule_id=f"semgrep:{result.get('check_id', 'unknown')}",
                category=MALICIOUS_CODE,
                severity=severity,
                locus=f"{_rel(result.get('path', ''), bundle)}:"
                f"{result.get('start', {}).get('line', 0)}",
                message=str(extra.get("message", "semgrep finding")),
            )
        )
    return findings, str(data.get("version", "unknown"))


def _run_gitleaks(binary: str, bundle: Path) -> list[ScanFinding]:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        cmd = [
            binary,
            "detect",
            "--source",
            str(bundle),
            "--no-git",
            "--report-format",
            "json",
            "--report-path",
            str(report),
        ]
        output = _run(cmd, "gitleaks", ok_codes={0, 1})
        if isinstance(output, ScanFinding):
            return [output]
        try:
            report_text = report.read_text(encoding="utf-8")
        except OSError:
            return []
    return parse_gitleaks(report_text, bundle)


def parse_gitleaks(report_text: str, bundle: Path) -> list[ScanFinding]:
    try:
        data = json.loads(report_text)
    except ValueError:
        return [_notice("gitleaks produced an unparseable report")]
    findings: list[ScanFinding] = []
    for leak in data if isinstance(data, list) else []:
        findings.append(
            ScanFinding(
                rule_id=f"gitleaks:{leak.get('RuleID', 'unknown')}",
                category=SECRET_EXPOSURE,
                severity=Severity.HIGH,
                locus=f"{_rel(leak.get('File', ''), bundle)}:{leak.get('StartLine', 0)}",
                message=str(leak.get("Description", "secret detected")),
            )
        )
    return findings


def _run_skillspector(binary: str, bundle: Path) -> list[ScanFinding]:
    # --no-llm is non-negotiable: the default mode sends file contents to an LLM provider.
    cmd = [binary, "scan", str(bundle), "--format", "json", "--no-llm"]
    output = _run(cmd, "skillspector", ok_codes={0, 1})
    if isinstance(output, ScanFinding):
        return [output]
    return parse_skillspector(output, bundle)


def parse_skillspector(output: str, bundle: Path) -> list[ScanFinding]:
    try:
        data = json.loads(output)
    except ValueError:
        return [_notice("skillspector produced unparseable output")]
    findings: list[ScanFinding] = []
    issues = data.get("issues", []) if isinstance(data, dict) else []
    for issue in issues:
        category = str(issue.get("category", ""))
        severity = _SKILLSPECTOR_SEVERITY.get(str(issue.get("severity")), Severity.LOW)
        location = issue.get("location", {}) or {}
        confidence = issue.get("confidence")
        detail = f" ({float(confidence):.0%} confidence)" if confidence is not None else ""
        findings.append(
            ScanFinding(
                rule_id=f"skillspector:{issue.get('id', 'unknown')}",
                category=_SKILLSPECTOR_CATEGORY.get(category, MALICIOUS_CODE),
                severity=severity,
                locus=f"{_rel(str(location.get('file', '')), bundle)}:"
                f"{location.get('start_line', 0)}",
                message=str(issue.get("message") or f"{category or 'skillspector'}{detail}"),
            )
        )
    return findings


def _skillspector_version(binary: str) -> str:
    output = _run([binary, "--version"], "skillspector", ok_codes={0})
    if isinstance(output, ScanFinding):
        return "unknown"
    text = output.strip().splitlines()[0] if output.strip() else ""
    return text.removeprefix("SkillSpector v") or "unknown"


def _gitleaks_version(binary: str) -> str:
    output = _run([binary, "version"], "gitleaks", ok_codes={0})
    if isinstance(output, ScanFinding):
        return "unknown"
    return output.strip().splitlines()[0] if output.strip() else "unknown"


def _run(cmd: list[str], name: str, *, ok_codes: set[int]) -> str | ScanFinding:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _notice(f"{name} failed to run: {exc}")
    if proc.returncode not in ok_codes:
        return _notice(f"{name} exited with code {proc.returncode}")
    return proc.stdout


def _rel(filename: str, bundle: Path) -> str:
    try:
        return Path(filename).resolve().relative_to(bundle.resolve()).as_posix()
    except ValueError:
        return filename


def _dist_version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "unknown"


def _notice(message: str) -> ScanFinding:
    return ScanFinding(
        rule_id="core:scanner-notice",
        category=META,
        severity=Severity.INFO,
        locus="-",
        message=message,
    )
