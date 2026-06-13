# SPDX-License-Identifier: Apache-2.0
"""Read hosted artifacts out of the verified local cache: discovery catalog, verdict index.

The cache is written by ``catalog_client.sync`` after signature and hash verification; reads
re-check each blob against its pinned hash to catch corruption. ``load_catalog_file`` is the
explicit unsigned escape hatch for development fixtures — the CLI labels its output as such.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from skillmeld.models import CatalogDocument, SkillFile, Verdict, VerdictIndex
from skillmeld.registries.catalog_client import (
    CatalogError,
    cache_root,
    cached_manifest,
    sha256_hex,
)

ARTIFACT_CATALOG = "catalog"
ARTIFACT_VERDICTS = "verdicts"


def bundle_hash(files: list[SkillFile]) -> str:
    """Hash a skill bundle: sha256 over sorted ``path NUL sha256`` lines joined by newlines.

    This canonicalization is pinned: the hosted build, the fetcher, and the verdict index
    must all compute bundle hashes exactly this way.
    """
    lines = [f"{file.path}\x00{file.sha256}" for file in sorted(files, key=lambda f: f.path)]
    return sha256_hex("\n".join(lines).encode())


def load_catalog(cache_dir: Path | None = None) -> CatalogDocument:
    """Load the discovery catalog from the synced cache."""
    target = cache_dir if cache_dir is not None else cache_root()
    data = _read_artifact(target, ARTIFACT_CATALOG)
    if data is None:
        raise CatalogError("no synced catalog found; run `skillmeld catalog sync` first")
    try:
        return CatalogDocument.model_validate_json(data)
    except ValidationError as exc:
        raise CatalogError(f"cached catalog artifact is invalid: {exc}") from exc


def load_catalog_file(path: Path) -> CatalogDocument:
    """Load a catalog from a plain local JSON file. Unsigned: development use only."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CatalogError(f"catalog file not readable: {exc}") from exc
    try:
        return CatalogDocument.model_validate_json(raw)
    except ValidationError as exc:
        raise CatalogError(f"catalog file {path} is invalid: {exc}") from exc


def load_verdict_index(cache_dir: Path | None = None) -> VerdictIndex | None:
    """Load the cached verdict index, or None when the cache has no index yet."""
    target = cache_dir if cache_dir is not None else cache_root()
    data = _read_artifact(target, ARTIFACT_VERDICTS)
    if data is None:
        return None
    try:
        return VerdictIndex.model_validate_json(data)
    except ValidationError as exc:
        raise CatalogError(f"cached verdict index is invalid: {exc}") from exc


def load_blocked_hashes(cache_dir: Path | None = None) -> set[str]:
    """Bundle hashes carrying a BLOCK verdict in the cached index. Empty when no index exists.

    Discovery uses this to drop known-bad skills before the user ever sees them; the security
    gate later re-checks authoritatively with full scanner-version semantics.
    """
    index = load_verdict_index(cache_dir)
    if index is None:
        return set()
    return {record.bundle_hash for record in index.records if record.verdict is Verdict.BLOCK}


def _read_artifact(cache_dir: Path, name: str) -> bytes | None:
    """Read a named artifact blob from the cache. None when unsynced; error on corruption."""
    manifest = cached_manifest(cache_dir)
    if manifest is None:
        return None
    artifact = next((a for a in manifest.artifacts if a.name == name), None)
    if artifact is None:
        return None
    blob = cache_dir / "blobs" / artifact.sha256
    if not blob.is_file():
        raise CatalogError(f"cached artifact {name!r} is missing its blob; re-run catalog sync")
    data = blob.read_bytes()
    if sha256_hex(data) != artifact.sha256:
        raise CatalogError(f"cached artifact {name!r} failed its hash check; re-run catalog sync")
    return data
