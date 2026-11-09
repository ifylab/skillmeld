# SPDX-License-Identifier: Apache-2.0
"""Shared HTTP plumbing: the runtime client, its User-Agent, and GET with retry.

The catalog builder, the bundle fetcher and ``catalog sync`` all read from CDNs that now and
then answer one request with a spurious error, so every GET goes through ``get_with_retry``.
Retries log at INFO on this module's logger; only ``build-catalog`` attaches a handler, so
runtime commands stay quiet.
"""

from __future__ import annotations

import logging
from time import sleep

import httpx

from skillmeld import __version__

USER_AGENT = f"skillmeld/{__version__}"
_TIMEOUT = 30.0
_RETRY_DELAYS = (1.0, 2.0, 4.0)
_MAX_RETRY_AFTER = 60.0

log = logging.getLogger(__name__)


def new_client() -> httpx.Client:
    """The runtime client: a fixed timeout and an explicit ``skillmeld/<version>`` agent."""
    return httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": USER_AGENT})


def get_with_retry(http: httpx.Client, url: str) -> httpx.Response:
    """GET ``url``, retrying any 5xx, a 429, or a transport error with backoff.

    The weekly catalog build makes thousands of GitHub requests, and GitHub's raw CDN
    occasionally answers one with a spurious 5xx (even a 501); without a retry that single
    response aborts the whole build. Any other status is returned for the caller to judge, and
    the final attempt's response or transport error goes back unchanged.
    """
    retries = len(_RETRY_DELAYS)
    for attempt, delay in enumerate(_RETRY_DELAYS, start=1):
        try:
            response = http.get(url)
        except httpx.TransportError as exc:
            _note_retry(url, type(exc).__name__, attempt, retries, delay)
            sleep(delay)
            continue
        if response.status_code != 429 and response.status_code < 500:
            return response
        retry_after = _retry_after(response)
        wait = delay if retry_after is None else retry_after
        _note_retry(url, str(response.status_code), attempt, retries, wait)
        sleep(wait)
    return http.get(url)


def _note_retry(url: str, reason: str, attempt: int, retries: int, wait: float) -> None:
    log.info("GET %s failed (%s); retry %d of %d in %gs", url, reason, attempt, retries, wait)


def _retry_after(response: httpx.Response) -> float | None:
    """A ``Retry-After`` in whole seconds, capped; the HTTP-date form falls back to backoff."""
    try:
        seconds = int(response.headers.get("Retry-After", ""))
    except ValueError:
        return None
    return min(max(float(seconds), 0.0), _MAX_RETRY_AFTER)
