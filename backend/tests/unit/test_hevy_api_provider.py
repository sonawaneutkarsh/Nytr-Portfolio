"""Network-isolated contract tests for the official Hevy REST provider."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from nutrition_agent.application.ports import (
    DetailedTrainingSourceError,
    DetailedTrainingSourceFailureKind,
)
from nutrition_agent.infrastructure.hevy_api_provider import (
    HEVY_API_BASE_URL,
    HEVY_API_PROVIDER_VERSION,
    HevyApiProvider,
)

TEST_KEY = "00000000-0000-0000-0000-000000000014"
SINCE = datetime(2026, 9, 1, 12, tzinfo=UTC)


def _workout(
    source_id: str,
    updated_at: str,
    *,
    title: str = "Push Day",
) -> dict[str, Any]:
    return {
        "id": source_id,
        "title": title,
        "routine_id": None,
        "description": None,
        "start_time": "2026-09-01T10:00:00Z",
        "end_time": "2026-09-01T11:00:00Z",
        "created_at": "2026-09-01T11:00:01Z",
        "updated_at": updated_at,
        "exercises": [
            {
                "index": 0,
                "title": "Bench Press (Barbell)",
                "notes": None,
                "exercise_template_id": "bench-template",
                "superset_id": None,
                "sets": [
                    {
                        "index": 0,
                        "type": "normal",
                        "weight_kg": 65.25,
                        "reps": 8,
                        "distance_meters": None,
                        "duration_seconds": None,
                        "rpe": 8.5,
                        "custom_metric": None,
                    }
                ],
            }
        ],
    }


def _client(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler))


def _response(request: httpx.Request, payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload, request=request)


def test_missing_key_fails_before_constructing_any_http_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _response(request, {})

    provider = HevyApiProvider(None, client=_client(handler), min_request_interval_seconds=0)
    with pytest.raises(DetailedTrainingSourceError) as error:
        provider.load_initial()
    assert error.value.kind is DetailedTrainingSourceFailureKind.NOT_CONFIGURED
    assert error.value.attempts_made == 0
    assert requests == []


def test_bootstrap_uses_official_endpoint_header_and_bounded_pagination() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = int(request.url.params["page"])
        workouts = [
            _workout(
                f"workout-{page}",
                f"2026-09-0{page}T12:00:00Z",
            )
        ]
        return _response(request, {"page": page, "page_count": 2, "workouts": workouts})

    provider = HevyApiProvider(
        TEST_KEY,
        client=_client(handler),
        min_request_interval_seconds=0,
    )
    fetched = provider.load_initial()

    assert fetched.batch.complete is True
    assert fetched.has_more is False
    assert fetched.pages_fetched == 2
    assert fetched.logical_requests == fetched.attempts_made == 2
    assert fetched.provider_version == HEVY_API_PROVIDER_VERSION
    assert tuple(item.source_session_id for item in fetched.batch.sessions) == (
        "workout-1",
        "workout-2",
    )
    assert fetched.batch.sessions[0].exercises[0].sets[0].load.value == Decimal("65.25")  # type: ignore[union-attr]
    assert all(request.url.host == "api.hevyapp.com" for request in requests)
    assert all(request.url.path == "/v1/workouts" for request in requests)
    assert all(request.url.params["pageSize"] == "10" for request in requests)
    assert all(request.headers["api-key"] == TEST_KEY for request in requests)
    assert all(set(request.extensions["timeout"].values()) == {10.0} for request in requests)
    assert str(requests[0].url).startswith(HEVY_API_BASE_URL)


def test_incremental_uses_since_and_parses_updates_and_deletions_newest_first() -> None:
    requests: list[httpx.Request] = []
    workout = _workout("updated-1", "2026-09-04T12:00:00Z")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _response(
            request,
            {
                "page": 1,
                "page_count": 1,
                "events": [
                    {"type": "deleted", "id": "deleted-1", "deleted_at": "2026-09-04T13:00:00Z"},
                    {"type": "updated", "workout": workout},
                ],
            },
        )

    provider = HevyApiProvider(
        TEST_KEY,
        client=_client(handler),
        min_request_interval_seconds=0,
    )
    fetched = provider.load_changes(SINCE)

    assert tuple(item.source_session_id for item in fetched.batch.sessions) == ("updated-1",)
    assert tuple(item.source_session_id for item in fetched.batch.deletions) == ("deleted-1",)
    assert fetched.source_event_watermark == datetime(2026, 9, 4, 13, tzinfo=UTC)
    assert requests[0].url.path == "/v1/workouts/events"
    assert requests[0].url.params["since"] == "2026-09-01T12:00:00Z"


def test_bootstrap_and_update_event_share_canonical_workout_revision_digest() -> None:
    workout = _workout("same-workout", "2026-09-04T12:00:00Z")
    responses = iter(
        (
            {"page": 1, "page_count": 1, "workouts": [workout]},
            {
                "page": 1,
                "page_count": 1,
                "events": [{"type": "updated", "workout": workout}],
            },
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, next(responses))

    provider = HevyApiProvider(
        TEST_KEY,
        client=_client(handler),
        min_request_interval_seconds=0,
    )
    initial = provider.load_initial().batch.sessions[0]
    replay = provider.load_changes(SINCE).batch.sessions[0]
    assert initial.source_revision == replay.source_revision
    assert initial.source_payload_sha256 == replay.source_payload_sha256


def test_page_cap_is_explicit_incomplete_and_does_not_fetch_next_page() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _response(
            request,
            {"page": 1, "page_count": 2, "workouts": [_workout("one", "2026-09-01T12:00:00Z")]},
        )

    provider = HevyApiProvider(
        TEST_KEY,
        client=_client(handler),
        max_pages=1,
        min_request_interval_seconds=0,
    )
    fetched = provider.load_initial()
    assert fetched.batch.complete is False
    assert fetched.has_more is True
    assert fetched.pages_fetched == 1
    assert len(requests) == 1


def test_workout_cap_is_explicit_incomplete_without_silent_truncation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(
            request,
            {
                "page": 1,
                "page_count": 1,
                "workouts": [
                    _workout("one", "2026-09-01T12:00:00Z"),
                    _workout("two", "2026-09-02T12:00:00Z"),
                ],
            },
        )

    fetched = HevyApiProvider(
        TEST_KEY,
        client=_client(handler),
        max_workouts=1,
        min_request_interval_seconds=0,
    ).load_initial()

    assert fetched.batch.complete is False
    assert fetched.has_more is True
    assert len(fetched.batch.sessions) == 2


def test_changing_pagination_metadata_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        page_count = 2 if page == 1 else 3
        return _response(
            request,
            {
                "page": page,
                "page_count": page_count,
                "workouts": [_workout(str(page), f"2026-09-0{page}T12:00:00Z")],
            },
        )

    provider = HevyApiProvider(
        TEST_KEY,
        client=_client(handler),
        min_request_interval_seconds=0,
    )
    with pytest.raises(DetailedTrainingSourceError) as error:
        provider.load_initial()
    assert error.value.kind is DetailedTrainingSourceFailureKind.INCOMPLETE_PAGINATION


@pytest.mark.parametrize("bound", ("attempts", "runtime"))
def test_global_attempt_and_runtime_bounds_stop_before_another_request(bound: str) -> None:
    timer = _Timer()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if bound == "runtime":
            timer.now += 2
        return _response(request, {"page": 1, "page_count": 2, "workouts": []})

    provider = HevyApiProvider(
        TEST_KEY,
        client=_client(handler),
        attempt_budget=1 if bound == "attempts" else 25,
        max_runtime_seconds=1 if bound == "runtime" else 180,
        min_request_interval_seconds=0,
        sleeper=timer.sleep,
        monotonic=timer.monotonic,
    )
    with pytest.raises(DetailedTrainingSourceError) as error:
        provider.load_initial()
    assert error.value.kind is DetailedTrainingSourceFailureKind.INCOMPLETE_PAGINATION
    assert error.value.attempts_made == 1
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("status", "kind"),
    (
        (401, DetailedTrainingSourceFailureKind.UNAUTHORIZED),
        (403, DetailedTrainingSourceFailureKind.FORBIDDEN),
    ),
)
def test_auth_failures_are_typed_and_never_retried(
    status: int,
    kind: DetailedTrainingSourceFailureKind,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, request=request)

    provider = HevyApiProvider(
        TEST_KEY,
        client=_client(handler),
        min_request_interval_seconds=0,
    )
    with pytest.raises(DetailedTrainingSourceError) as error:
        provider.load_initial()
    assert error.value.kind is kind
    assert error.value.attempts_made == 1
    assert calls == 1


class _Timer:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_requests_are_serial_and_observe_minimum_start_spacing() -> None:
    timer = _Timer()
    starts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        starts.append(timer.monotonic())
        page = int(request.url.params["page"])
        return _response(request, {"page": page, "page_count": 2, "workouts": []})

    HevyApiProvider(
        TEST_KEY,
        client=_client(handler),
        min_request_interval_seconds=1,
        sleeper=timer.sleep,
        monotonic=timer.monotonic,
    ).load_initial()

    assert starts == [0.0, 1.0]
    assert timer.sleeps == [1.0]


def test_429_honors_retry_after_then_succeeds_with_bounded_metrics() -> None:
    timer = _Timer()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "3"}, request=request)
        return _response(request, {"page": 1, "page_count": 1, "workouts": []})

    fetched = HevyApiProvider(
        TEST_KEY,
        client=_client(handler),
        min_request_interval_seconds=0,
        sleeper=timer.sleep,
        monotonic=timer.monotonic,
    ).load_initial()
    assert (fetched.attempts_made, fetched.retries) == (2, 1)
    assert timer.sleeps == [3.0]


@pytest.mark.parametrize(
    ("failure", "kind"),
    (
        ("timeout", DetailedTrainingSourceFailureKind.TIMEOUT),
        ("503", DetailedTrainingSourceFailureKind.PROVIDER_UNAVAILABLE),
    ),
)
def test_transient_failure_retries_once_then_fails_typed(
    failure: str,
    kind: DetailedTrainingSourceFailureKind,
) -> None:
    timer = _Timer()

    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(503, request=request)

    provider = HevyApiProvider(
        TEST_KEY,
        client=_client(handler),
        min_request_interval_seconds=0,
        sleeper=timer.sleep,
        monotonic=timer.monotonic,
    )
    with pytest.raises(DetailedTrainingSourceError) as error:
        provider.load_initial()
    assert error.value.kind is kind
    assert (error.value.attempts_made, error.value.retries) == (2, 1)


def test_malformed_json_and_newest_first_drift_fail_closed() -> None:
    responses: list[object] = [b"not-json"]

    def malformed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=responses[0], request=request)

    malformed = HevyApiProvider(
        TEST_KEY,
        client=_client(malformed_handler),
        min_request_interval_seconds=0,
    )
    with pytest.raises(DetailedTrainingSourceError) as error:
        malformed.load_initial()
    assert error.value.kind is DetailedTrainingSourceFailureKind.MALFORMED_RESPONSE

    def unordered_handler(request: httpx.Request) -> httpx.Response:
        return _response(
            request,
            {
                "page": 1,
                "page_count": 1,
                "events": [
                    {"type": "updated", "workout": _workout("old", "2026-09-02T12:00:00Z")},
                    {"type": "updated", "workout": _workout("new", "2026-09-03T12:00:00Z")},
                ],
            },
        )

    unordered = HevyApiProvider(
        TEST_KEY,
        client=_client(unordered_handler),
        min_request_interval_seconds=0,
    )
    with pytest.raises(DetailedTrainingSourceError) as ordering:
        unordered.load_changes(SINCE)
    assert ordering.value.kind is DetailedTrainingSourceFailureKind.INCOMPLETE_PAGINATION


def test_malformed_source_object_is_not_coerced() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        malformed = _workout("bad", "2026-09-04T12:00:00Z")
        malformed["exercises"][0]["sets"][0]["reps"] = "8"
        return _response(
            request,
            {"page": 1, "page_count": 1, "workouts": [malformed]},
        )

    provider = HevyApiProvider(
        TEST_KEY,
        client=_client(handler),
        min_request_interval_seconds=0,
    )
    with pytest.raises(DetailedTrainingSourceError) as error:
        provider.load_initial()
    assert error.value.kind is DetailedTrainingSourceFailureKind.MALFORMED_RESPONSE
