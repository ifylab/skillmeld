# SPDX-License-Identifier: Apache-2.0
"""License detection and merge-time combination, lightweight by design.

The curated corpus is almost entirely MIT/CC-BY, so detection is fingerprint plus SPDX-tag
matching with ``license-expression`` for parsing — not scancode. Combination is
most-restrictive-wins; an unknown license or a known-incompatible pair surfaces a REVIEW-tier
finding, and copyleft is always flagged explicitly.
"""

from __future__ import annotations

import re
from pathlib import Path

from license_expression import ExpressionError, get_spdx_licensing

from skillmeld.models import LicenseInfo, ScanFinding
from skillmeld.security.rules import LICENSE, Severity

_LICENSING = get_spdx_licensing()

# Ordered, first match wins: every phrase must appear in the normalized license text.
_FINGERPRINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("AGPL-3.0-only", ("gnu affero general public license",)),
    ("LGPL-3.0-only", ("gnu lesser general public license", "version 3")),
    ("LGPL-2.1-only", ("lesser general public license", "version 2.1")),
    ("GPL-3.0-only", ("gnu general public license", "version 3")),
    ("GPL-2.0-only", ("gnu general public license", "version 2")),
    ("MPL-2.0", ("mozilla public license version 2.0",)),
    ("Apache-2.0", ("apache license", "version 2.0")),
    ("MIT", ("permission is hereby granted free of charge",)),
    ("BSD-3-Clause", ("redistribution and use in source and binary forms", "neither the name")),
    ("BSD-2-Clause", ("redistribution and use in source and binary forms",)),
    ("ISC", ("permission to use copy modify and/or distribute this software",)),
    ("Unlicense", ("free and unencumbered software",)),
    ("CC-BY-4.0", ("creative commons attribution 4.0",)),
    ("CC-BY-SA-4.0", ("creative commons attribution-sharealike 4.0",)),
)

COPYLEFT = frozenset(
    {
        "GPL-2.0-only",
        "GPL-3.0-only",
        "AGPL-3.0-only",
        "LGPL-2.1-only",
        "LGPL-3.0-only",
        "MPL-2.0",
        "CC-BY-SA-4.0",
    }
)

_RESTRICTIVENESS: dict[str, int] = {
    "Unlicense": 0,
    "MIT": 1,
    "ISC": 1,
    "BSD-2-Clause": 1,
    "BSD-3-Clause": 2,
    "CC-BY-4.0": 2,
    "Apache-2.0": 3,
    "MPL-2.0": 4,
    "LGPL-2.1-only": 5,
    "LGPL-3.0-only": 5,
    "GPL-2.0-only": 6,
    "GPL-3.0-only": 7,
    "AGPL-3.0-only": 8,
}

_INCOMPATIBLE: frozenset[frozenset[str]] = frozenset({frozenset({"GPL-2.0-only", "Apache-2.0"})})

_SPDX_TAG = re.compile(r"SPDX-License-Identifier:\s*([A-Za-z0-9.+-]+)")
_FRONTMATTER_LICENSE = re.compile(r"^license:\s*[\"']?([A-Za-z0-9.+-]+)", re.MULTILINE)
_LICENSE_FILE = re.compile(r"^(license|licence|copying)([._-].*)?$", re.IGNORECASE)
_MAX_TAG_FILES = 200


def detect_bundle(bundle: Path) -> tuple[LicenseInfo, list[ScanFinding]]:
    """Detect a bundle's license: LICENSE file wins, then SPDX tags, then frontmatter."""
    root = bundle.resolve()
    findings: list[ScanFinding] = []

    file_license: str | None = None
    for path in sorted(root.iterdir()) if root.is_dir() else []:
        if path.is_file() and _LICENSE_FILE.match(path.name):
            file_license = detect_text(path.read_text(encoding="utf-8", errors="replace"))
            break

    tag_licenses: set[str] = set()
    for index, path in enumerate(sorted(root.rglob("*"))):
        if index >= _MAX_TAG_FILES:
            break
        if path.is_file() and path.suffix.lower() in {".py", ".sh", ".js", ".ts", ".md"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            tag_licenses.update(match.group(1) for match in _SPDX_TAG.finditer(text))

    frontmatter_license: str | None = None
    skill_md = root / "SKILL.md"
    if skill_md.is_file():
        match = _FRONTMATTER_LICENSE.search(
            skill_md.read_text(encoding="utf-8", errors="replace")[:4096]
        )
        if match:
            frontmatter_license = match.group(1)

    spdx_id, source = _resolve_precedence(file_license, tag_licenses, frontmatter_license)
    detected = {value for value in (file_license, frontmatter_license, *tag_licenses) if value}
    if len(detected) > 1:
        findings.append(
            _finding(
                "license-conflict",
                Severity.MEDIUM,
                f"Conflicting license signals: {', '.join(sorted(detected))} "
                f"(kept {spdx_id} from {source}).",
            )
        )
    if spdx_id is None:
        findings.append(
            _finding("license-unknown", Severity.MEDIUM, "No license could be determined.")
        )
        return LicenseInfo(spdx_id=None, copyleft=False, source=None), findings

    if not _is_known_spdx(spdx_id):
        findings.append(
            _finding(
                "license-unrecognized",
                Severity.MEDIUM,
                f"License id {spdx_id!r} is not a recognized SPDX identifier.",
            )
        )
    info = LicenseInfo(spdx_id=spdx_id, copyleft=spdx_id in COPYLEFT, source=source)
    if info.copyleft:
        findings.append(
            _finding(
                "license-copyleft",
                Severity.LOW,
                f"{spdx_id} is copyleft: derived works carry share-alike obligations.",
            )
        )
    return info, findings


def detect_text(text: str) -> str | None:
    """Identify a license text by its distinctive phrases."""
    normalized = re.sub(r"[^a-z0-9./\s-]", "", re.sub(r"\s+", " ", text.lower()))
    for spdx_id, phrases in _FINGERPRINTS:
        if all(phrase in normalized for phrase in phrases):
            return spdx_id
    return None


def combine(licenses: list[LicenseInfo]) -> tuple[LicenseInfo, list[ScanFinding]]:
    """Combine per-skill licenses for a merged set: most restrictive wins, mixes surfaced."""
    findings: list[ScanFinding] = []
    ids = sorted({info.spdx_id for info in licenses if info.spdx_id})
    has_unknown = any(info.spdx_id is None for info in licenses)
    if has_unknown:
        findings.append(
            _finding(
                "license-unknown",
                Severity.MEDIUM,
                "At least one input has no determined license; the combination is unsafe "
                "to assert.",
            )
        )
    for pair in _INCOMPATIBLE:
        if pair <= set(ids):
            findings.append(
                _finding(
                    "license-incompatible",
                    Severity.HIGH,
                    f"Licenses {' and '.join(sorted(pair))} are mutually incompatible; "
                    "this merge cannot satisfy both.",
                )
            )
    # An unlicensed input is all-rights-reserved, which is more restrictive than any SPDX. Most-
    # restrictive-wins therefore makes the whole set unassertable, not the permissive known license.
    if not ids or has_unknown:
        return LicenseInfo(spdx_id=None, copyleft=False, source="combined"), findings

    combined_id = max(ids, key=lambda spdx: _RESTRICTIVENESS.get(spdx, 9))
    combined = LicenseInfo(spdx_id=combined_id, copyleft=combined_id in COPYLEFT, source="combined")
    if combined.copyleft and any(
        _RESTRICTIVENESS.get(other, 9) < _RESTRICTIVENESS.get(combined_id, 9) for other in ids
    ):
        findings.append(
            _finding(
                "license-copyleft-mix",
                Severity.MEDIUM,
                f"Most-restrictive-wins: the merged set carries {combined_id} obligations "
                f"(inputs: {', '.join(ids)}).",
            )
        )
    return combined, findings


def _resolve_precedence(
    file_license: str | None, tag_licenses: set[str], frontmatter_license: str | None
) -> tuple[str | None, str | None]:
    if file_license:
        return file_license, "license-file"
    if len(tag_licenses) == 1:
        return next(iter(tag_licenses)), "spdx-tag"
    if frontmatter_license:
        return frontmatter_license, "frontmatter"
    if tag_licenses:
        return sorted(tag_licenses)[0], "spdx-tag"
    return None, None


def _is_known_spdx(spdx_id: str) -> bool:
    try:
        parsed = _LICENSING.parse(spdx_id, validate=True)
    except (ExpressionError, ValueError):
        return False
    return parsed is not None


def _finding(slug: str, severity: Severity, message: str) -> ScanFinding:
    return ScanFinding(
        rule_id=f"core:{slug}",
        category=LICENSE,
        severity=severity,
        locus="-",
        message=message,
    )
