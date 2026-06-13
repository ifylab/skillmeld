# SPDX-License-Identifier: Apache-2.0
"""The deterministic security gate: tri-state PASS / REVIEW / BLOCK over a skill bundle.

Pattern matching cannot catch every natural-language injection, so REVIEW plus host-Claude
judgment is load-bearing: critical findings BLOCK, high/medium findings REVIEW, low/info are
recorded without gating. Python files go through bandit (AST-based, a hard dependency);
semgrep and gitleaks add findings when installed — adapters only ever escalate, never relax.
"""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path

from skillmeld import __version__
from skillmeld.grounding import IGNORE_DIRS
from skillmeld.models import ScanFinding, ScanReport, Verdict
from skillmeld.security import adapters
from skillmeld.security.rules import (
    ALLOWED_DOMAINS,
    BIDI_CONTROLS,
    CREDENTIAL_HANDLING,
    CREDENTIAL_WINDOW,
    LINE_RULES,
    META,
    NETWORK_RE,
    POPULAR_PACKAGES,
    PROMPT_INJECTION,
    RULESET_VERSION,
    SECRET_PATH_RE,
    SUSPICIOUS_DOWNLOAD,
    TYPOSQUAT_EXCEPTIONS,
    UNVERIFIABLE_DEPENDENCY,
    URL_HOST_RE,
    ZERO_WIDTH,
    FileKind,
    Severity,
)

SCANNER_VERSION = f"skillmeld/{__version__}"

_MAX_FILE_BYTES = 2_000_000
_MAX_FILES = 2_000
_MAX_FINDINGS_PER_RULE_FILE = 5
_B64_BLOB = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")
_MAX_BLOBS_PER_FILE = 20
_MAX_DECODE_BYTES = 65_536

_SEVERITY_ORDER = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}

_MARKDOWN_EXTS = frozenset({".md", ".markdown", ".rst", ".txt"})
_SCRIPT_EXTS = frozenset(
    {".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".ps1", ".bat", ".cmd"}
)
_MANIFEST_NAMES = frozenset({"pyproject.toml", "package.json", "Pipfile"})

_VERSION_SPECIFIER = re.compile(r"[=<>!~]|@|\bgit\+")
# A word mixing Latin with Cyrillic look-alikes is the homoglyph-spoofing vector (a Cyrillic
# 'a' or 'o' hidden inside an otherwise-Latin word). Greek (U+0370-03FF) is deliberately
# excluded: it is overwhelmingly legitimate math and science notation in technical skills, not an
# attack, and flagging it only trains REVIEW fatigue. Cyrillic is U+0400-04FF.
_CYRILLIC = "Ѐ-ӿ"  # U+0400..U+04FF, the Cyrillic block
_MIXED_SCRIPT_WORD = re.compile(
    rf"\b\w*[a-zA-Z]\w*[{_CYRILLIC}]\w*\b|\b\w*[{_CYRILLIC}]\w*[a-zA-Z]\w*\b"
)


def verdict_from(findings: list[ScanFinding]) -> Verdict:
    """critical -> BLOCK; high or medium -> REVIEW; low/info never gate."""
    worst = Severity.INFO
    for finding in findings:
        severity = Severity(finding.severity)
        if _SEVERITY_ORDER[severity] > _SEVERITY_ORDER[worst]:
            worst = severity
    if worst is Severity.CRITICAL:
        return Verdict.BLOCK
    if worst in (Severity.HIGH, Severity.MEDIUM):
        return Verdict.REVIEW
    return Verdict.PASS


def scan_bundle(bundle: Path, *, deep: bool = True) -> ScanReport:
    """Scan every file in a bundle dir. ``deep`` also runs bandit plus optional scanners."""
    from skillmeld.security.verdict import dir_bundle_hash

    root = bundle.resolve()
    findings: list[ScanFinding] = []
    py_files: list[Path] = []
    scanned = 0

    for path in _walk(root):
        if scanned >= _MAX_FILES:
            findings.append(
                _meta(f"file cap reached ({_MAX_FILES}); remaining files not content-scanned")
            )
            break
        scanned += 1
        rel = path.relative_to(root).as_posix()
        if path.suffix == ".py":
            py_files.append(path)
        raw = path.read_bytes()
        if len(raw) > _MAX_FILE_BYTES:
            findings.append(_meta(f"{rel}: larger than {_MAX_FILE_BYTES} bytes; skipped"))
            continue
        if b"\x00" in raw[:8192]:
            continue
        text = raw.decode("utf-8", errors="replace")
        findings.extend(_scan_text(rel, _classify(path), text))

    rulesets = {"core": RULESET_VERSION}
    if deep:
        adapter_findings, adapter_versions = adapters.run_all(root, py_files)
        findings.extend(adapter_findings)
        rulesets.update(adapter_versions)

    return ScanReport(
        verdict=verdict_from(findings),
        findings=findings,
        scanned_files=scanned,
        bundle_hash=dir_bundle_hash(root),
        scanner_version=SCANNER_VERSION,
        rulesets=rulesets,
    )


def _walk(root: Path) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and not any(part in IGNORE_DIRS for part in path.parts)
    ]


def _classify(path: Path) -> FileKind:
    name = path.name
    if name in _MANIFEST_NAMES or (name.startswith("requirements") and path.suffix == ".txt"):
        return FileKind.MANIFEST
    if path.suffix.lower() in _SCRIPT_EXTS:
        return FileKind.SCRIPT
    if path.suffix.lower() in _MARKDOWN_EXTS:
        return FileKind.MARKDOWN
    return FileKind.OTHER


def _scan_text(rel: str, kind: FileKind, text: str) -> list[ScanFinding]:
    lines = text.splitlines()
    findings = _apply_line_rules(rel, kind, lines)
    findings.extend(_credential_cooccurrence(rel, kind, lines))
    findings.extend(_unicode_findings(rel, lines))
    findings.extend(_domain_findings(rel, kind, lines))
    findings.extend(_decode_one_level(rel, kind, lines))
    if kind is FileKind.MANIFEST:
        findings.extend(_dependency_findings(rel, lines))
    return findings


def _apply_line_rules(rel: str, kind: FileKind, lines: list[str]) -> list[ScanFinding]:
    findings: list[ScanFinding] = []
    for rule in LINE_RULES:
        if kind not in rule.kinds:
            continue
        hits = 0
        for number, line in enumerate(lines, start=1):
            if rule.pattern.search(line):
                hits += 1
                if hits <= _MAX_FINDINGS_PER_RULE_FILE:
                    findings.append(
                        ScanFinding(
                            rule_id=rule.id,
                            category=rule.category,
                            severity=rule.severity,
                            locus=f"{rel}:{number}",
                            message=rule.message,
                        )
                    )
        if hits > _MAX_FINDINGS_PER_RULE_FILE:
            findings.append(
                _meta(
                    f"{rel}: {rule.id} capped ({hits} matches, kept {_MAX_FINDINGS_PER_RULE_FILE})"
                )
            )
    return findings


def _credential_cooccurrence(rel: str, kind: FileKind, lines: list[str]) -> list[ScanFinding]:
    """Secret-path access near network use: same line is exfiltration, nearby is suspect."""
    secret_lines = [n for n, line in enumerate(lines, start=1) if SECRET_PATH_RE.search(line)]
    network_lines = {n for n, line in enumerate(lines, start=1) if NETWORK_RE.search(line)}
    findings: list[ScanFinding] = []
    near_reported = False
    for number in secret_lines:
        if number in network_lines:
            findings.append(
                ScanFinding(
                    rule_id="core:credential-exfil",
                    category=CREDENTIAL_HANDLING,
                    severity=Severity.CRITICAL,
                    locus=f"{rel}:{number}",
                    message="Credential or secret path used together with a network call.",
                )
            )
        elif not near_reported and any(
            abs(number - other) <= CREDENTIAL_WINDOW for other in network_lines
        ):
            near_reported = True
            findings.append(
                ScanFinding(
                    rule_id="core:credential-near-network",
                    category=CREDENTIAL_HANDLING,
                    severity=Severity.HIGH,
                    locus=f"{rel}:{number}",
                    message=(
                        f"Secret path accessed within {CREDENTIAL_WINDOW} lines of network use."
                    ),
                )
            )
    if not findings and secret_lines and kind in (FileKind.SCRIPT, FileKind.MANIFEST):
        findings.append(
            ScanFinding(
                rule_id="core:secret-path-access",
                category=CREDENTIAL_HANDLING,
                severity=Severity.MEDIUM,
                locus=f"{rel}:{secret_lines[0]}",
                message="Executable file references credential or secret paths.",
            )
        )
    return findings


def _unicode_findings(rel: str, lines: list[str]) -> list[ScanFinding]:
    findings: list[ScanFinding] = []
    bidi_line = zero_line = mixed_line = 0
    for number, line in enumerate(lines, start=1):
        if not bidi_line and any(ch in BIDI_CONTROLS for ch in line):
            bidi_line = number
        if (
            not zero_line
            and any(ch in ZERO_WIDTH for ch in line)
            and not (number == 1 and line.startswith("﻿") and "﻿" not in line[1:])
        ):
            zero_line = number
        if not mixed_line and _MIXED_SCRIPT_WORD.search(line):
            mixed_line = number
    if bidi_line:
        findings.append(
            ScanFinding(
                rule_id="core:bidi-control",
                category=PROMPT_INJECTION,
                severity=Severity.HIGH,
                locus=f"{rel}:{bidi_line}",
                message="Bidirectional control characters can reorder visible text.",
            )
        )
    if zero_line:
        findings.append(
            ScanFinding(
                rule_id="core:zero-width",
                category=PROMPT_INJECTION,
                severity=Severity.HIGH,
                locus=f"{rel}:{zero_line}",
                message="Zero-width characters can hide instructions from review.",
            )
        )
    if mixed_line:
        findings.append(
            ScanFinding(
                rule_id="core:homoglyph-mix",
                category=PROMPT_INJECTION,
                severity=Severity.MEDIUM,
                locus=f"{rel}:{mixed_line}",
                message="Word mixes Latin with Cyrillic look-alike characters.",
            )
        )
    return findings


def _domain_findings(rel: str, kind: FileKind, lines: list[str]) -> list[ScanFinding]:
    """Inventory egress hosts. Unlisted hosts gate only in executable contexts."""
    findings: list[ScanFinding] = []
    seen: set[str] = set()
    for number, line in enumerate(lines, start=1):
        for host in URL_HOST_RE.findall(line):
            host = host.lower().rstrip(".")
            if host in seen or _is_allowed(host) or re.fullmatch(r"[\d.]+", host):
                continue
            seen.add(host)
            executable = kind in (FileKind.SCRIPT, FileKind.MANIFEST)
            findings.append(
                ScanFinding(
                    rule_id="core:unlisted-domain" if executable else "core:external-url",
                    category=SUSPICIOUS_DOWNLOAD,
                    severity=Severity.MEDIUM if executable else Severity.INFO,
                    locus=f"{rel}:{number}",
                    message=f"References host outside the allowlist: {host}",
                )
            )
    return findings


def _is_allowed(host: str) -> bool:
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in ALLOWED_DOMAINS)


def _decode_one_level(rel: str, kind: FileKind, lines: list[str]) -> list[ScanFinding]:
    """Decode base64 blobs once and re-run the line rules: hidden hostility is critical."""
    findings: list[ScanFinding] = []
    blobs = 0
    for number, line in enumerate(lines, start=1):
        for blob in _B64_BLOB.findall(line):
            if blobs >= _MAX_BLOBS_PER_FILE:
                return findings
            blobs += 1
            decoded = _decode_b64(blob)
            if decoded is None:
                continue
            inner = _apply_line_rules(rel, kind, decoded.splitlines())
            triggered = sorted({f.rule_id for f in inner if f.category != META})
            if triggered:
                findings.append(
                    ScanFinding(
                        rule_id="core:encoded-payload",
                        category=PROMPT_INJECTION,
                        severity=Severity.CRITICAL,
                        locus=f"{rel}:{number}",
                        message=(
                            "Base64 content decodes to material that itself triggers rules: "
                            + ", ".join(triggered)
                        ),
                    )
                )
    return findings


def _decode_b64(blob: str) -> str | None:
    padded = blob + "=" * (-len(blob) % 4)
    try:
        raw = base64.b64decode(padded, validate=True)[:_MAX_DECODE_BYTES]
    except (binascii.Error, ValueError):
        return None
    text = raw.decode("utf-8", errors="replace")
    printable = sum(ch.isprintable() or ch.isspace() for ch in text)
    if not text or printable / len(text) < 0.8:
        return None
    return text


def _dependency_findings(rel: str, lines: list[str]) -> list[ScanFinding]:
    findings: list[ScanFinding] = []
    unpinned = 0
    for number, line in enumerate(lines, start=1):
        name = _dependency_name(line)
        if not name:
            continue
        squatted = _typosquat_target(name)
        if squatted:
            findings.append(
                ScanFinding(
                    rule_id="core:typosquat-dependency",
                    category=UNVERIFIABLE_DEPENDENCY,
                    severity=Severity.HIGH,
                    locus=f"{rel}:{number}",
                    message=f"Dependency {name!r} is one edit away from {squatted!r}.",
                )
            )
        if rel.rpartition("/")[2].startswith("requirements") and not _VERSION_SPECIFIER.search(
            line
        ):
            unpinned += 1
    if unpinned:
        findings.append(
            ScanFinding(
                rule_id="core:unpinned-dependency",
                category=UNVERIFIABLE_DEPENDENCY,
                severity=Severity.LOW,
                locus=f"{rel}:1",
                message=f"{unpinned} dependency(ies) without a version constraint.",
            )
        )
    return findings


_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _dependency_name(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", "-", "[", "{", "}")):
        return None
    quoted = re.match(r"^\s*\"([A-Za-z0-9][A-Za-z0-9._-]*)\"\s*:", line)
    if quoted:
        return quoted.group(1).lower()
    match = _REQ_LINE.match(stripped)
    return match.group(1).lower() if match else None


def _typosquat_target(name: str) -> str | None:
    if name in POPULAR_PACKAGES or name in TYPOSQUAT_EXCEPTIONS:
        return None
    for popular in POPULAR_PACKAGES:
        if abs(len(name) - len(popular)) <= 1 and _edit_distance_le_one(name, popular):
            return popular
    return None


def _edit_distance_le_one(a: str, b: str) -> bool:
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = edits = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(a) == len(b):
            i += 1
        j += 1
    return edits + (len(b) - j) <= 1


def _meta(message: str) -> ScanFinding:
    return ScanFinding(
        rule_id="core:scan-notice",
        category=META,
        severity=Severity.INFO,
        locus="-",
        message=message,
    )
