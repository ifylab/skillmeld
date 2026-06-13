# SPDX-License-Identifier: Apache-2.0
"""License detection and combination tests: fingerprints, precedence, most-restrictive-wins."""

from __future__ import annotations

from pathlib import Path

from skillmeld.models import LicenseInfo
from skillmeld.security.license import combine, detect_bundle, detect_text

MIT_TEXT = (
    "MIT License\n\nCopyright (c) 2026 Example\n\n"
    "Permission is hereby granted, free of charge, to any person obtaining a copy "
    "of this software and associated documentation files...\n"
)
APACHE_TEXT = "Apache License\nVersion 2.0, January 2004\nhttp://www.apache.org/licenses/\n"
GPL3_TEXT = "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n"


def test_detect_text_fingerprints() -> None:
    assert detect_text(MIT_TEXT) == "MIT"
    assert detect_text(APACHE_TEXT) == "Apache-2.0"
    assert detect_text(GPL3_TEXT) == "GPL-3.0-only"
    assert detect_text("All rights reserved, proprietary.") is None


def test_license_file_wins(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "LICENSE").write_text(MIT_TEXT)
    (root / "SKILL.md").write_text("---\nname: x\nlicense: GPL-3.0-only\n---\nbody\n")
    info, findings = detect_bundle(root)
    assert info.spdx_id == "MIT"
    assert info.source == "license-file"
    assert not info.copyleft
    assert any(f.rule_id == "core:license-conflict" for f in findings)


def test_spdx_tag_detection(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "tool.py").write_text("# SPDX-License-Identifier: Apache-2.0\nprint('x')\n")
    info, _ = detect_bundle(root)
    assert info.spdx_id == "Apache-2.0"
    assert info.source == "spdx-tag"


def test_frontmatter_detection(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: x\nlicense: MIT\n---\nbody\n")
    info, _ = detect_bundle(root)
    assert info.spdx_id == "MIT"
    assert info.source == "frontmatter"


def test_unknown_license_is_flagged(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: x\n---\nbody\n")
    info, findings = detect_bundle(root)
    assert info.spdx_id is None
    assert any(f.rule_id == "core:license-unknown" for f in findings)


def test_copyleft_is_flagged(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "LICENSE").write_text(GPL3_TEXT)
    info, findings = detect_bundle(root)
    assert info.spdx_id == "GPL-3.0-only"
    assert info.copyleft
    assert any(f.rule_id == "core:license-copyleft" for f in findings)


def _info(spdx_id: str | None) -> LicenseInfo:
    return LicenseInfo(spdx_id=spdx_id)


def test_combine_permissive_takes_most_restrictive() -> None:
    combined, findings = combine([_info("MIT"), _info("Apache-2.0")])
    assert combined.spdx_id == "Apache-2.0"
    assert not combined.copyleft
    assert not [f for f in findings if f.severity in ("high", "critical")]


def test_combine_with_copyleft_surfaces_obligations() -> None:
    combined, findings = combine([_info("MIT"), _info("GPL-3.0-only")])
    assert combined.spdx_id == "GPL-3.0-only"
    assert combined.copyleft
    assert any(f.rule_id == "core:license-copyleft-mix" for f in findings)


def test_combine_incompatible_pair_is_high() -> None:
    _, findings = combine([_info("Apache-2.0"), _info("GPL-2.0-only")])
    incompatible = [f for f in findings if f.rule_id == "core:license-incompatible"]
    assert incompatible and incompatible[0].severity == "high"


def test_combine_with_unknown_input_is_flagged() -> None:
    combined, findings = combine([_info("MIT"), _info(None)])
    assert any(f.rule_id == "core:license-unknown" for f in findings)
    # An unlicensed input dominates: the set cannot be asserted as MIT just because one part is.
    assert combined.spdx_id is None


def test_combine_of_only_known_licenses_does_not_flag_unknown() -> None:
    _, findings = combine([_info("MIT"), _info("MIT")])
    assert not any(f.rule_id == "core:license-unknown" for f in findings)
