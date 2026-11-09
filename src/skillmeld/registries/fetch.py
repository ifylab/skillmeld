# SPDX-License-Identifier: Apache-2.0
"""Fetch selected skill bundles, verify every pinned hash, cache content-addressed.

Only selected entries are ever fetched (at most three). Every file must match the sha256 the
signed catalog pinned for it, and the assembled bundle must re-hash to the entry's bundle
hash; anything else is refused and nothing partial is left behind.
"""

from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from time import sleep
from urllib.parse import quote

import httpx

from skillmeld.models import CatalogEntry, SkillFile
from skillmeld.registries.catalog import bundle_hash
from skillmeld.registries.catalog_client import cache_root, sha256_hex

_TIMEOUT = 30.0
_RETRY_DELAYS = (1.0, 2.0, 4.0)
_MAX_RETRY_AFTER = 60.0


class FetchError(Exception):
    """A bundle could not be fetched or failed verification."""


def get_with_retry(http: httpx.Client, url: str) -> httpx.Response:
    """GET ``url``, retrying any 5xx, a 429, or a transport error with backoff.

    The weekly catalog build makes thousands of GitHub requests, and GitHub's raw CDN
    occasionally answers one with a spurious 5xx (even a 501); without a retry that single
    response aborts the whole build. Any other status is returned for the caller to judge, and
    the final attempt's response or transport error goes back unchanged.
    """
    for delay in _RETRY_DELAYS:
        try:
            response = http.get(url)
        except httpx.TransportError:
            sleep(delay)
            continue
        if response.status_code != 429 and response.status_code < 500:
            return response
        wait = _retry_after(response)
        sleep(delay if wait is None else wait)
    return http.get(url)


def _retry_after(response: httpx.Response) -> float | None:
    """A ``Retry-After`` in whole seconds, capped; the HTTP-date form falls back to backoff."""
    try:
        seconds = int(response.headers.get("Retry-After", ""))
    except ValueError:
        return None
    return min(max(float(seconds), 0.0), _MAX_RETRY_AFTER)


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
    response = get_with_retry(http, url)
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
