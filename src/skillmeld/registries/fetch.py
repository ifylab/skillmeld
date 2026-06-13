# SPDX-License-Identifier: Apache-2.0
"""Fetch selected skill bundles, verify every pinned hash, cache content-addressed.

Only selected entries are ever fetched (at most three). Every file must match the sha256 the
signed catalog pinned for it, and the assembled bundle must re-hash to the entry's bundle
hash; anything else is refused and nothing partial is left behind.
"""

from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx

from skillmeld.models import CatalogEntry, SkillFile
from skillmeld.registries.catalog import bundle_hash
from skillmeld.registries.catalog_client import cache_root, sha256_hex

_TIMEOUT = 30.0


class FetchError(Exception):
    """A bundle could not be fetched or failed verification."""


def fetch_bundle(
    entry: CatalogEntry,
    *,
    cache_dir: Path | None = None,
    client: httpx.Client | None = None,
) -> Path:
    """Download one entry's files into ``cache/bundles/<bundle_hash>/`` and return that path.

    Idempotent: a cached bundle that still verifies is returned without any network use.
    """
    if not entry.files:
        raise FetchError(f"catalog entry {entry.id!r} lists no files")
    if not entry.fetch_base:
        raise FetchError(f"catalog entry {entry.id!r} has no fetch_base")
    if entry.bundle_hash != bundle_hash(entry.files):
        raise FetchError(
            f"catalog entry {entry.id!r} is inconsistent: bundle_hash does not match its files"
        )
    for file in entry.files:
        _check_relpath(entry.id, file.path)

    bundles = (cache_dir if cache_dir is not None else cache_root()) / "bundles"
    dest = bundles / entry.bundle_hash
    if dest.is_dir() and _dir_matches(dest, entry.files):
        return dest

    staging = bundles / f".tmp-{entry.bundle_hash}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    base = entry.fetch_base.rstrip("/")
    owns_client = client is None
    http = client if client is not None else httpx.Client(timeout=_TIMEOUT)
    try:
        for file in entry.files:
            data = _fetch_file(http, base, file)
            target = staging / PurePosixPath(file.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if dest.exists():
            shutil.rmtree(dest)
        staging.replace(dest)
        return dest
    except FetchError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except (httpx.HTTPError, OSError) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise FetchError(f"fetch of {entry.id!r} failed: {exc}") from exc
    finally:
        if owns_client:
            http.close()


def _fetch_file(http: httpx.Client, base: str, file: SkillFile) -> bytes:
    url = f"{base}/{quote(file.path)}"
    response = http.get(url)
    response.raise_for_status()
    data = response.content
    actual = sha256_hex(data)
    if actual != file.sha256:
        raise FetchError(
            f"{file.path} failed its hash check (expected {file.sha256[:12]}, got {actual[:12]})"
        )
    return data


def _check_relpath(entry_id: str, path: str) -> None:
    """Refuse path traversal and absolute or otherwise unsafe paths from a catalog entry."""
    if not path or "\x00" in path or "\\" in path or ":" in path:
        raise FetchError(f"unsafe file path in catalog entry {entry_id!r}: {path!r}")
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise FetchError(f"unsafe file path in catalog entry {entry_id!r}: {path!r}")


def _dir_matches(dest: Path, files: list[SkillFile]) -> bool:
    """True when the cached dir holds exactly the manifest's files, every hash intact."""
    expected = {file.path: file.sha256 for file in files}
    found: set[str] = set()
    for path in dest.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(dest).as_posix()
        digest = expected.get(rel)
        if digest is None or sha256_hex(path.read_bytes()) != digest:
            return False
        found.add(rel)
    return found == set(expected)
