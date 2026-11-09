# SPDX-License-Identifier: Apache-2.0
"""Tests for the shared HTTP plumbing: retry policy, Retry-After, logging, User-Agent."""

from __future__ import annotations

import logging

import httpx
import pytest

from skillmeld import __version__
from skillmeld.registries.net import USER_AGENT, get_with_retry, new_client


def _scripted(script: list[httpx.Response | Exception], calls: list[str]) -> httpx.Client:
    """Answer each request with the next scripted response, or raise it if it is an error."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_retry_recovers_from_transient_5xx(sleeps: list[float]) -> None:
    calls: list[str] = []
    script: list[httpx.Response | Exception] = [
        httpx.Response(501),
        httpx.Response(502),
        httpx.Response(200, content=b"ok"),
    ]
    response = get_with_retry(_scripted(script, calls), "https://host/raw/f")
    assert response.status_code == 200 and response.content == b"ok"
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]


def test_retry_recovers_from_a_transport_error(sleeps: list[float]) -> None:
    calls: list[str] = []
    script: list[httpx.Response | Exception] = [
        httpx.ConnectError("reset"),
        httpx.Response(200, content=b"ok"),
    ]
    assert get_with_retry(_scripted(script, calls), "https://host/raw/f").status_code == 200
    assert sleeps == [1.0]


def test_retry_gives_up_after_three_retries(sleeps: list[float]) -> None:
    calls: list[str] = []
    script: list[httpx.Response | Exception] = [httpx.Response(503) for _ in range(4)]
    assert get_with_retry(_scripted(script, calls), "https://host/raw/f").status_code == 503
    assert len(calls) == 4
    assert sleeps == [1.0, 2.0, 4.0]


def test_retry_reraises_a_persistent_transport_error(sleeps: list[float]) -> None:
    calls: list[str] = []
    script: list[httpx.Response | Exception] = [httpx.ReadTimeout("slow") for _ in range(4)]
    with pytest.raises(httpx.ReadTimeout):
        get_with_retry(_scripted(script, calls), "https://host/raw/f")
    assert len(calls) == 4


@pytest.mark.parametrize("status", [200, 400, 403, 404])
def test_retry_leaves_non_transient_statuses_alone(sleeps: list[float], status: int) -> None:
    calls: list[str] = []
    script: list[httpx.Response | Exception] = [httpx.Response(status)]
    assert get_with_retry(_scripted(script, calls), "https://host/raw/f").status_code == status
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [
        ("7", 7.0),
        ("9999", 60.0),
        ("0", 0.0),
        ("-5", 0.0),
        ("nan", 1.0),
        ("Wed, 21 Oct 2026 07:28:00 GMT", 1.0),
    ],
)
def test_retry_honors_a_capped_retry_after(
    sleeps: list[float], retry_after: str, expected: float
) -> None:
    calls: list[str] = []
    script: list[httpx.Response | Exception] = [
        httpx.Response(429, headers={"Retry-After": retry_after}),
        httpx.Response(200),
    ]
    assert get_with_retry(_scripted(script, calls), "https://host/raw/f").status_code == 200
    assert sleeps == [expected]


def test_a_retry_is_logged_once_at_info(
    sleeps: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="skillmeld.registries.net")
    calls: list[str] = []
    script: list[httpx.Response | Exception] = [httpx.Response(502), httpx.Response(200)]
    get_with_retry(_scripted(script, calls), "https://host/raw/f")
    assert [record.levelno for record in caplog.records] == [logging.INFO]
    message = caplog.records[0].getMessage()
    assert "https://host/raw/f" in message and "502" in message and "retry 1 of 3" in message


def test_a_clean_get_logs_nothing(sleeps: list[float], caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="skillmeld.registries.net")
    calls: list[str] = []
    get_with_retry(_scripted([httpx.Response(200)], calls), "https://host/raw/f")
    assert caplog.records == []


def test_new_client_identifies_as_skillmeld() -> None:
    client = new_client()
    assert client.headers["User-Agent"] == USER_AGENT
    assert USER_AGENT.removeprefix("skillmeld/") == __version__
    assert client.timeout.read == 30.0
