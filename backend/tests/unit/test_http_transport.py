"""M8 bounded transport retry, spacing, and request-shape tests."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

import httpx
import pytest

from nutrition_agent.domain.stacks.entities import MealPeriod
from nutrition_agent.infrastructure.compliance_gate import ComplianceGate, IngestionMode
from nutrition_agent.infrastructure.http_transport import (
    HttpRequest,
    HttpxTransport,
    TransportError,
)
from nutrition_agent.infrastructure.stacks_source.provider import StacksHttpSource


class _StubClient:
    def __init__(self, outcomes: Iterable[httpx.Response | Exception]) -> None:
        self._outcomes = iter(outcomes)
        self.calls: list[tuple[str, str, object]] = []

    def request(self, method: str, url: str, *, data: object) -> httpx.Response:
        self.calls.append((method, url, data))
        outcome = next(self._outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _response(status: int, *, body: bytes = b"body") -> httpx.Response:
    return httpx.Response(
        status,
        content=body,
        request=httpx.Request("GET", "https://source.test/page"),
    )


def _transport(
    outcomes: Iterable[httpx.Response | Exception],
    **kwargs: object,
) -> tuple[HttpxTransport, _StubClient]:
    transport = HttpxTransport(
        ComplianceGate(IngestionMode.MANUAL),
        "institutional_menu",
        user_agent="test (contact: demo@example.invalid)",
        min_interval_seconds=0,
        backoff_base_seconds=0,
        **kwargs,
    )
    client = _StubClient(outcomes)
    transport._client = client  # type: ignore[assignment]  # test seam
    return transport, client


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_retryable_statuses_receive_one_bounded_retry(status: int) -> None:
    transport, client = _transport([_response(status), _response(200, body=b"ok")])

    result = transport.fetch(HttpRequest("https://source.test/page"))

    assert result.status == 200
    assert result.body == b"ok"
    assert len(client.calls) == 2
    assert transport.attempts_made == 2
    assert transport.metrics.retries_made == 1
    assert transport.metrics.retry_causes == (f"http_{status}",)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
def test_ordinary_client_errors_are_not_retried(status: int) -> None:
    transport, client = _transport([_response(status)], max_attempts=2)

    result = transport.fetch(HttpRequest("https://source.test/page"))

    assert result.status == status
    assert len(client.calls) == 1
    assert transport.attempts_made == 1
    assert transport.metrics.retries_made == 0


def test_network_failure_is_retried_and_failed_attempt_counts() -> None:
    request = httpx.Request("GET", "https://source.test/page")
    transport, client = _transport([httpx.ConnectError("reset", request=request), _response(200)])

    assert transport.fetch(HttpRequest(str(request.url))).status == 200
    assert len(client.calls) == 2
    assert transport.attempts_made == 2
    assert transport.metrics.retry_causes == ("network_error",)


def test_attempt_budget_counts_network_failures_before_request_returns() -> None:
    request = httpx.Request("GET", "https://source.test/page")
    transport, client = _transport(
        [httpx.ConnectError("reset", request=request)],
        attempt_budget=1,
    )

    with pytest.raises(TransportError, match="attempt budget"):
        transport.fetch(HttpRequest(str(request.url)))
    assert len(client.calls) == 1
    assert transport.attempts_made == 1


def test_attempts_are_spaced_by_at_least_ten_seconds() -> None:
    monotonic = [0.0]
    call_times: list[float] = []

    class TimedClient(_StubClient):
        def request(self, method: str, url: str, *, data: object) -> httpx.Response:
            call_times.append(monotonic[0])
            return super().request(method, url, data=data)

    def sleep(seconds: float) -> None:
        monotonic[0] += seconds

    request = httpx.Request("GET", "https://source.test/page")
    transport = HttpxTransport(
        ComplianceGate(IngestionMode.MANUAL),
        "institutional_menu",
        user_agent="test (contact: demo@example.invalid)",
        max_attempts=2,
        min_interval_seconds=10,
        backoff_base_seconds=0,
        clock=lambda: monotonic[0],
        sleeper=sleep,
    )
    client = TimedClient([httpx.ConnectError("reset", request=request), _response(200)])
    transport._client = client  # type: ignore[assignment]  # test seam

    transport.fetch(HttpRequest(str(request.url)))

    assert call_times == [0.0, 10.0]
    assert transport.metrics.observed_min_spacing_seconds == 10
    assert transport.metrics.configured_min_interval_seconds == 10
    assert transport.metrics.attempt_budget == 150


@pytest.mark.parametrize("meal_period", tuple(MealPeriod))
def test_menu_source_posts_exact_verified_form_for_each_period_and_preserves_error_body(
    meal_period: MealPeriod,
) -> None:
    class CaptureTransport:
        def __init__(self) -> None:
            self.request: HttpRequest | None = None

        def fetch(self, request: HttpRequest):
            from datetime import UTC, datetime

            from nutrition_agent.infrastructure.http_transport import FetchResult

            self.request = request
            return FetchResult(
                status=503,
                body=b"server unavailable",
                fetched_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
                final_url=request.url,
            )

    transport = CaptureTransport()
    raw = StacksHttpSource(transport, base_url="https://source.test").fetch_menu_page(
        date(2026, 8, 27), meal_period
    )

    assert transport.request == HttpRequest(
        url="https://source.test/menus/user-pages/daily-menu.cfm",
        method="POST",
        form_fields={
            "selMenuDate": "8/27/26",
            "selMeal": meal_period.value,
            "selCampus": "50",
        },
    )
    assert raw.http_status == 503
    assert raw.body == b"server unavailable"
