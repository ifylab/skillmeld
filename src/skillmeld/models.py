# SPDX-License-Identifier: Apache-2.0
"""Core data models: pure data contracts shared across the pipeline."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

# Routing-surface character budgets for a skill's description (verified 2026-06-23):
#   - Claude API /v1/skills: `description` max 1024 chars (authoring cap; longer is rejected).
#   - Claude Code skill listing: `description` + `when_to_use` combined, truncated past 1536
#     (`maxSkillDescriptionChars`, default since Claude Code v2.1.105).
# skillmeld emits no `when_to_use`, so the description alone is what gets budgeted on each surface.
# These track an evolving spec; re-verify against the Claude docs before relying on them.
API_DESCRIPTION_LIMIT = 1024
CLAUDE_CODE_ROUTING_LIMIT = 1536


class Verdict(StrEnum):
    """Tri-state security outcome. BLOCK is refused; REVIEW is surfaced for a human decision."""

    PASS = "pass"
    REVIEW = "review"
    BLOCK = "block"


class AtomKind(StrEnum):
    heading = "heading"
    directive = "directive"
    example = "example"
    trigger = "trigger"
    context = "context"
    dependency_ref = "dependency-ref"


class LicenseInfo(BaseModel):
    spdx_id: str | None = None
    copyleft: bool = False
    source: str | None = None


class ScanFinding(BaseModel):
    rule_id: str
    category: str
    severity: str
    locus: str
    message: str


class SkillSource(BaseModel):
    name: str
    repo: str | None = None
    url: str | None = None
    stars: int | None = None
    license: LicenseInfo = Field(default_factory=LicenseInfo)


class SkillDoc(BaseModel):
    source: SkillSource
    frontmatter: dict[str, object] = Field(default_factory=dict)
    body: str = ""
    resources: list[str] = Field(default_factory=list)
    content_hash: str | None = None


class SkillFile(BaseModel):
    """One file of a skill bundle, pinned by hash in the catalog."""

    path: str
    sha256: str


class CatalogEntry(BaseModel):
    """One skill in the discovery catalog: metadata plus pinned hashes, never body content.

    ``id`` is stable across catalog builds: ``{owner}/{repo}:{skill_dir}``. ``fetch_base`` is a
    raw-content URL prefix pinned to a commit, so fetched bytes match ``files`` hashes.
    """

    id: str
    source: SkillSource
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    files: list[SkillFile] = Field(default_factory=list)
    fetch_base: str | None = None
    bundle_hash: str = ""


class CatalogDocument(BaseModel):
    """The discovery-catalog artifact: what the hosted pipeline builds and the client loads."""

    schema_version: int = 1
    generated_at: str = ""
    entries: list[CatalogEntry] = Field(default_factory=list)


class Atom(BaseModel):
    """Smallest addressable unit of a skill body: a byte-exact slice of the source.

    Id is ``{skill}:{path}:{sha256(text)[:16]}`` over **raw** bytes (tamper-evident). ``path``
    is a structural block index (``s2/l1/i0``), stable under edits elsewhere. The invariant
    ``source_body_bytes[start:end] == text.encode()`` makes byte-traceability mechanical.
    """

    id: str
    skill: str
    path: str
    text: str
    start: int = 0
    end: int = 0
    kind: AtomKind | None = None
    detected_kind: AtomKind | None = None
    max_severity: str | None = None
    source_order: int = 0
    norm_key: str = ""


class UseCaseProfile(BaseModel):
    summary: str = ""
    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    conventions: list[str] = Field(default_factory=list)
    tasks: list[str] = Field(default_factory=list)


class RepoEvidence(BaseModel):
    """Deterministic facts from a repo scan. The host Claude derives the summary + tasks."""

    root: str
    file_counts: dict[str, int] = Field(default_factory=dict)
    manifests: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    config_files: list[str] = Field(default_factory=list)
    top_dirs: list[str] = Field(default_factory=list)
    readme_excerpt: str = ""
    has_tests: bool = False


class Candidate(BaseModel):
    """A discovery candidate: catalog entry plus the deterministic match evidence.

    ``matched`` records why the prefilter kept it (``language:python``, ``tag:ifc``); the host
    Claude ranks candidates and the security gate fills ``verdict``/``findings`` later.
    """

    entry: CatalogEntry
    score: float = 0.0
    matched: list[str] = Field(default_factory=list)
    verdict: Verdict | None = None
    findings: list[ScanFinding] = Field(default_factory=list)


class DiscoveryResult(BaseModel):
    candidates: list[Candidate] = Field(default_factory=list)
    considered: int = 0
    excluded_blocked: int = 0


class Selection(BaseModel):
    """The validated final pick (at most three), in the host Claude's ranked order."""

    chosen: list[Candidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Conflict(BaseModel):
    """A clash between two atoms. Structural conflicts (Python-flagged) are non-dismissible."""

    atom_a: str
    atom_b: str
    type: str
    source: Literal["structural", "semantic"] = "structural"
    severity: str | None = None
    winner: str | None = None


class DependencyEdge(BaseModel):
    """A resolved dependency reference. Built before closure so cycles terminate."""

    src_atom_id: str
    target_ref: str
    resolved_atom_id: str | None = None
    kind: Literal["atom", "resource", "unresolved"] = "unresolved"


class AssembledAtom(BaseModel):
    """One slot in an emitted skill body. ``source`` atoms are verbatim; ``scaffold`` are templates.

    The verifier reconstructs the body from the layout and asserts byte-equality, so every
    output byte is either a verbatim source atom or a closed-vocabulary scaffold token.
    """

    atom_id: str
    kind: AtomKind | None = None
    role: Literal["source", "scaffold"] = "source"
    template_id: str | None = None


class Provenance(BaseModel):
    atom_id: str
    source_skill: str
    source_path: str
    license: LicenseInfo = Field(default_factory=LicenseInfo)


class RouteEntry(BaseModel):
    """One orchestrator routing line, rendered from a frozen template (never free prose)."""

    template_id: str
    label: str
    skill_name: str


class MergePlan(BaseModel):
    kept: list[str] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)
    drop_reasons: dict[str, str] = Field(default_factory=dict)
    deduped: list[str] = Field(default_factory=list)
    conflicts_resolved: list[Conflict] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)
    license_resolution: LicenseInfo = Field(default_factory=LicenseInfo)
    frontmatter_findings: list[ScanFinding] = Field(default_factory=list)
    frontmatter_verdict: Verdict = Verdict.PASS
    warnings: list[str] = Field(default_factory=list)
    confidence: str | None = None


class AssembledSkill(BaseModel):
    """An emitted skill plus the declared layout the verifier reconstructs it from."""

    doc: SkillDoc
    layout: list[AssembledAtom] = Field(default_factory=list)


class MergeResult(BaseModel):
    skills: list[AssembledSkill] = Field(default_factory=list)
    orchestrator: AssembledSkill | None = None
    routing_table: list[RouteEntry] = Field(default_factory=list)
    plan: MergePlan = Field(default_factory=MergePlan)


class VerdictRecord(BaseModel):
    """A signed, advisory verdict, valid only for its scanner and ruleset versions."""

    bundle_hash: str
    hash_algo: str = "sha256"
    scanner_version: str
    ruleset_versions: dict[str, str] = Field(default_factory=dict)
    verdict: Verdict
    findings: list[ScanFinding] = Field(default_factory=list)
    scanned_at: str
    advisory: bool = True
    license: LicenseInfo = Field(default_factory=LicenseInfo)


class VerdictIndex(BaseModel):
    """The verdict-index artifact: advisory scan verdicts keyed by bundle hash."""

    schema_version: int = 1
    generated_at: str = ""
    records: list[VerdictRecord] = Field(default_factory=list)


class ScanReport(BaseModel):
    """Local security-gate output for one skill bundle. Findings only ever escalate."""

    verdict: Verdict = Verdict.PASS
    findings: list[ScanFinding] = Field(default_factory=list)
    scanned_files: int = 0
    bundle_hash: str = ""
    scanner_version: str = ""
    rulesets: dict[str, str] = Field(default_factory=dict)
    hosted_verdict: Verdict | None = None
    license: LicenseInfo | None = None


class Artifact(BaseModel):
    name: str
    version: str
    url: str
    sha256: str
    size: int = 0


class CatalogManifest(BaseModel):
    schema_version: int = 1
    generated_at: str
    key_id: str
    artifacts: list[Artifact] = Field(default_factory=list)
