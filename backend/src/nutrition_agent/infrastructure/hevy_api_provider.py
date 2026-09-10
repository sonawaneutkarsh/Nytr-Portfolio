"""Bounded official Hevy REST provider; credentials remain process-local."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from nutrition_agent.application.ports import (
    DetailedTrainingSourceError,
    DetailedTrainingSourceFailureKind,
    DetailedTrainingSyncSource,
)
from nutrition_agent.domain.training.detail import (
    DetailedTrainingDeletion,
    DetailedTrainingImportBatch,
    DetailedTrainingSession,
    DetailedTrainingSourceFetch,
)
from nutrition_agent.infrastructure.hevy_detail_source import (
    HEVY_PUBLIC_API_PARSER_VERSION,
    HevySourceFailure,
    HevySourceFailureKind,
    ParsedHevyEventPage,
    ParsedHevyWorkoutPage,
    parse_hevy_event_page,
    parse_hevy_workout_page,
)

HEVY_API_BASE_URL = "https://api.hevyapp.com"
HEVY_API_PROVIDER_VERSION = "hevy-api-provider.v1"
HEVY_PAGE_SIZE = 10
HEVY_MAX_PAGES = 20
HEVY_MAX_WORKOUTS = 200
HEVY_MAX_ATTEMPTS = 25
HEVY_MAX_ATTEMPTS_PER_REQUEST = 2
HEVY_MAX_RUNTIME_SECONDS = 180.0
HEVY_MIN_REQUEST_INTERVAL_SECONDS = 1.0
HEVY_TIMEOUT_SECONDS = 10.0
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}


@dataclass
class _RequestBudget:
    started_at: float
    attempts_made: int = 0
    logical_requests: int = 0
    retry_causes: list[str] = field(default_factory=list)
    last_attempt_at: float | None = None

    @property
    def retries(self) -> int:
        return len(self.retry_causes)


class HevyApiProvider(DetailedTrainingSyncSource):
    """Synchronous, serial, bounded adapter for the official public API."""

    def __init__(
        self,
        api_key: str | None,
        *,
        client: httpx.Client | None = None,
        page_size: int = HEVY_PAGE_SIZE,
        max_pages: int = HEVY_MAX_PAGES,
        max_workouts: int = HEVY_MAX_WORKOUTS,
        attempt_budget: int = HEVY_MAX_ATTEMPTS,
        max_attempts_per_request: int = HEVY_MAX_ATTEMPTS_PER_REQUEST,
        max_runtime_seconds: float = HEVY_MAX_RUNTIME_SECONDS,
        request_timeout_seconds: float = HEVY_TIMEOUT_SECONDS,
        min_request_interval_seconds: float = HEVY_MIN_REQUEST_INTERVAL_SECONDS,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if page_size < 1 or page_size > 10:
            raise ValueError("Hevy page_size must be between 1 and 10")
        if max_pages < 1 or max_workouts < 1 or attempt_budget < 1:
            raise ValueError("Hevy bounds must be positive")
        if max_attempts_per_request not in (1, 2):
            raise ValueError("Hevy max_attempts_per_request must be 1 or 2")
        if (
            max_runtime_seconds <= 0
            or request_timeout_seconds <= 0
            or request_timeout_seconds > HEVY_TIMEOUT_SECONDS
            or min_request_interval_seconds < 0
        ):
            raise ValueError("Hevy timing bounds are invalid")
        self._api_key = api_key.strip() if api_key and api_key.strip() else None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(HEVY_TIMEOUT_SECONDS, connect=HEVY_TIMEOUT_SECONDS),
            follow_redirects=False,
        )
        self._page_size = page_size
        self._max_pages = max_pages
        self._max_workouts = max_workouts
        self._attempt_budget = attempt_budget
        self._max_attempts_per_request = max_attempts_per_request
        self._max_runtime_seconds = max_runtime_seconds
        self._request_timeout_seconds = request_timeout_seconds
        self._min_request_interval_seconds = min_request_interval_seconds
        self._sleep = sleeper
        self._monotonic = monotonic
        self._wall_clock = wall_clock

    @property
    def is_configured(self) -> bool:
        return self._api_key is not None

    def load_initial(self) -> DetailedTrainingSourceFetch:
        budget = self._new_budget()
        pages: list[ParsedHevyWorkoutPage] = []
        first = self._parse_workout_page(
            self._request(
                "/v1/workouts",
                {"page": "1", "pageSize": str(self._page_size)},
                budget,
            ),
            budget,
        )
        pages.append(first)
        if first.page != 1:
            self._fail_incomplete("Hevy bootstrap did not begin at page 1", budget)
        if first.page_count > self._max_pages or len(first.sessions) > self._max_workouts:
            return self._incomplete_bootstrap(pages, budget)
        for page_number in range(2, first.page_count + 1):
            page = self._parse_workout_page(
                self._request(
                    "/v1/workouts",
                    {"page": str(page_number), "pageSize": str(self._page_size)},
                    budget,
                ),
                budget,
            )
            pages.append(page)
            self._validate_page(page.page, page.page_count, page_number, first.page_count, budget)
            if sum(len(item.sessions) for item in pages) > self._max_workouts:
                return self._incomplete_bootstrap(pages, budget)
        sessions = tuple(session for page in pages for session in page.sessions)
        batch = self._batch(sessions, (), complete=True, budget=budget)
        watermark = max(
            (item.source_updated_at for item in sessions if item.source_updated_at is not None),
            default=_EPOCH,
        )
        return self._source_fetch(batch, len(pages), False, watermark, budget)

    def load_changes(self, since: datetime) -> DetailedTrainingSourceFetch:
        if since.tzinfo is None or since.utcoffset() is None:
            raise ValueError("Hevy since timestamp must be timezone-aware")
        budget = self._new_budget()
        pages: list[ParsedHevyEventPage] = []
        params = {
            "page": "1",
            "pageSize": str(self._page_size),
            "since": _rfc3339(since),
        }
        first = self._parse_event_page(self._request("/v1/workouts/events", params, budget), budget)
        pages.append(first)
        if first.page != 1:
            self._fail_incomplete("Hevy event sync did not begin at page 1", budget)
        if first.page_count > self._max_pages:
            return self._incomplete_events(pages, since, budget)
        for page_number in range(2, first.page_count + 1):
            page = self._parse_event_page(
                self._request(
                    "/v1/workouts/events",
                    {
                        "page": str(page_number),
                        "pageSize": str(self._page_size),
                        "since": _rfc3339(since),
                    },
                    budget,
                ),
                budget,
            )
            pages.append(page)
            self._validate_page(page.page, page.page_count, page_number, first.page_count, budget)
        event_times = tuple(value for page in pages for value in page.event_times)
        adjacent_times = zip(event_times, event_times[1:], strict=False)
        if any(first_time < second_time for first_time, second_time in adjacent_times):
            self._fail_incomplete("Hevy events were not ordered newest to oldest", budget)
        sessions = tuple(session for page in pages for session in page.sessions)
        deletions = tuple(deletion for page in pages for deletion in page.deletions)
        batch = self._batch(sessions, deletions, complete=True, budget=budget)
        watermark = max(event_times, default=since)
        return self._source_fetch(batch, len(pages), False, watermark, budget)

    def _new_budget(self) -> _RequestBudget:
        if not self.is_configured:
            raise DetailedTrainingSourceError(
                DetailedTrainingSourceFailureKind.NOT_CONFIGURED,
                "Hevy API access is not configured",
            )
        return _RequestBudget(started_at=self._monotonic())

    def _parse_workout_page(
        self,
        payload: bytes,
        budget: _RequestBudget,
    ) -> ParsedHevyWorkoutPage:
        try:
            return parse_hevy_workout_page(payload)
        except HevySourceFailure as exc:
            self._raise_parser_failure(exc, budget)
            raise AssertionError("unreachable") from exc

    def _parse_event_page(
        self,
        payload: bytes,
        budget: _RequestBudget,
    ) -> ParsedHevyEventPage:
        try:
            return parse_hevy_event_page(payload)
        except HevySourceFailure as exc:
            self._raise_parser_failure(exc, budget)
            raise AssertionError("unreachable") from exc

    def _raise_parser_failure(
        self,
        error: HevySourceFailure,
        budget: _RequestBudget,
    ) -> None:
        kind = (
            DetailedTrainingSourceFailureKind.INCOMPLETE_PAGINATION
            if error.kind is HevySourceFailureKind.INCOMPLETE_PAGINATION
            else DetailedTrainingSourceFailureKind.MALFORMED_RESPONSE
        )
        self._raise(kind, "Hevy returned an invalid source response", budget)

    def _request(self, path: str, params: dict[str, str], budget: _RequestBudget) -> bytes:
        assert self._api_key is not None
        budget.logical_requests += 1
        last_kind = DetailedTrainingSourceFailureKind.PROVIDER_UNAVAILABLE
        last_detail = "Hevy provider request failed"
        for attempt in range(1, self._max_attempts_per_request + 1):
            response: httpx.Response | None = None
            self._wait_for_attempt(budget)
            budget.attempts_made += 1
            budget.last_attempt_at = self._monotonic()
            try:
                remaining_runtime = self._max_runtime_seconds - (
                    self._monotonic() - budget.started_at
                )
                response = self._client.get(
                    f"{HEVY_API_BASE_URL}{path}",
                    params=params,
                    headers={"api-key": self._api_key, "Accept": "application/json"},
                    timeout=min(self._request_timeout_seconds, remaining_runtime),
                )
            except httpx.TimeoutException:
                last_kind = DetailedTrainingSourceFailureKind.TIMEOUT
                last_detail = "Hevy provider request timed out"
                retry_cause = "timeout"
            except httpx.TransportError:
                last_kind = DetailedTrainingSourceFailureKind.PROVIDER_UNAVAILABLE
                last_detail = "Hevy provider transport was unavailable"
                retry_cause = "transport"
            else:
                if response.status_code == 200:
                    return response.content
                if response.status_code == 401:
                    self._raise(
                        DetailedTrainingSourceFailureKind.UNAUTHORIZED,
                        "Hevy rejected the configured API credential",
                        budget,
                    )
                if response.status_code == 403:
                    self._raise(
                        DetailedTrainingSourceFailureKind.FORBIDDEN,
                        "Hevy denied access for the configured account",
                        budget,
                    )
                if response.status_code == 429:
                    last_kind = DetailedTrainingSourceFailureKind.RATE_LIMITED
                    last_detail = "Hevy rate limited the bounded sync"
                    retry_cause = "http_429"
                elif response.status_code in _RETRYABLE_STATUSES:
                    last_kind = (
                        DetailedTrainingSourceFailureKind.TIMEOUT
                        if response.status_code == 408
                        else DetailedTrainingSourceFailureKind.PROVIDER_UNAVAILABLE
                    )
                    last_detail = "Hevy returned a retryable provider error"
                    retry_cause = f"http_{response.status_code}"
                else:
                    self._raise(
                        DetailedTrainingSourceFailureKind.PROVIDER_UNAVAILABLE,
                        "Hevy returned an unexpected HTTP status",
                        budget,
                    )
            if attempt == self._max_attempts_per_request:
                self._raise(last_kind, last_detail, budget)
            budget.retry_causes.append(retry_cause)
            delay = 1.0
            if response is not None and response.status_code == 429:
                delay = max(delay, self._retry_after_seconds(response.headers.get("Retry-After")))
            self._sleep_within_runtime(delay, budget)
        raise AssertionError("unreachable Hevy retry loop")

    def _wait_for_attempt(self, budget: _RequestBudget) -> None:
        if budget.attempts_made >= self._attempt_budget:
            self._raise(
                DetailedTrainingSourceFailureKind.INCOMPLETE_PAGINATION,
                "Hevy sync attempt budget was exhausted",
                budget,
            )
        elapsed = self._monotonic() - budget.started_at
        if elapsed >= self._max_runtime_seconds:
            self._raise(
                DetailedTrainingSourceFailureKind.INCOMPLETE_PAGINATION,
                "Hevy sync runtime bound was reached",
                budget,
            )
        if budget.last_attempt_at is not None:
            remaining = self._min_request_interval_seconds - (
                self._monotonic() - budget.last_attempt_at
            )
            if remaining > 0:
                self._sleep_within_runtime(remaining, budget)

    def _sleep_within_runtime(self, seconds: float, budget: _RequestBudget) -> None:
        remaining = self._max_runtime_seconds - (self._monotonic() - budget.started_at)
        if seconds > remaining:
            self._raise(
                DetailedTrainingSourceFailureKind.INCOMPLETE_PAGINATION,
                "Hevy sync runtime bound was reached",
                budget,
            )
        self._sleep(seconds)

    def _retry_after_seconds(self, raw: str | None) -> float:
        if raw is None:
            return 1.0
        try:
            return max(0.0, float(raw))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(raw)
            except (TypeError, ValueError, OverflowError):
                return 1.0
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            return max(0.0, (retry_at - self._wall_clock()).total_seconds())

    @staticmethod
    def _validate_page(
        actual_page: int,
        actual_count: int,
        expected_page: int,
        expected_count: int,
        budget: _RequestBudget,
    ) -> None:
        if actual_page != expected_page or actual_count != expected_count:
            raise DetailedTrainingSourceError(
                DetailedTrainingSourceFailureKind.INCOMPLETE_PAGINATION,
                "Hevy pagination metadata changed during the bounded sync",
                attempts_made=budget.attempts_made,
                retries=budget.retries,
                retry_causes=tuple(budget.retry_causes),
            )

    def _batch(
        self,
        sessions: tuple[DetailedTrainingSession, ...],
        deletions: tuple[DetailedTrainingDeletion, ...],
        *,
        complete: bool,
        budget: _RequestBudget,
    ) -> DetailedTrainingImportBatch:
        try:
            return DetailedTrainingImportBatch(sessions, deletions, complete)
        except ValueError as exc:
            self._raise(
                DetailedTrainingSourceFailureKind.MALFORMED_RESPONSE,
                "Hevy response repeated one source revision or deletion identity",
                budget,
            )
            raise AssertionError("unreachable") from exc

    def _incomplete_bootstrap(
        self,
        pages: list[ParsedHevyWorkoutPage],
        budget: _RequestBudget,
    ) -> DetailedTrainingSourceFetch:
        sessions = tuple(session for page in pages for session in page.sessions)
        batch = self._batch(sessions, (), complete=False, budget=budget)
        watermark = max(
            (item.source_updated_at for item in sessions if item.source_updated_at is not None),
            default=_EPOCH,
        )
        return self._source_fetch(batch, len(pages), True, watermark, budget)

    def _incomplete_events(
        self,
        pages: list[ParsedHevyEventPage],
        since: datetime,
        budget: _RequestBudget,
    ) -> DetailedTrainingSourceFetch:
        sessions = tuple(session for page in pages for session in page.sessions)
        deletions = tuple(deletion for page in pages for deletion in page.deletions)
        times = tuple(value for page in pages for value in page.event_times)
        batch = self._batch(sessions, deletions, complete=False, budget=budget)
        return self._source_fetch(batch, len(pages), True, max(times, default=since), budget)

    @staticmethod
    def _source_fetch(
        batch: DetailedTrainingImportBatch,
        pages_fetched: int,
        has_more: bool,
        watermark: datetime,
        budget: _RequestBudget,
    ) -> DetailedTrainingSourceFetch:
        return DetailedTrainingSourceFetch(
            batch=batch,
            pages_fetched=pages_fetched,
            has_more=has_more,
            source_event_watermark=watermark,
            logical_requests=budget.logical_requests,
            attempts_made=budget.attempts_made,
            retries=budget.retries,
            parser_version=HEVY_PUBLIC_API_PARSER_VERSION,
            provider_version=HEVY_API_PROVIDER_VERSION,
        )

    def _fail_incomplete(self, detail: str, budget: _RequestBudget) -> None:
        self._raise(DetailedTrainingSourceFailureKind.INCOMPLETE_PAGINATION, detail, budget)

    @staticmethod
    def _raise(
        kind: DetailedTrainingSourceFailureKind,
        detail: str,
        budget: _RequestBudget,
    ) -> None:
        raise DetailedTrainingSourceError(
            kind,
            detail,
            attempts_made=budget.attempts_made,
            retries=budget.retries,
            retry_causes=tuple(budget.retry_causes),
        )


def _rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "HEVY_API_BASE_URL",
    "HEVY_API_PROVIDER_VERSION",
    "HEVY_MAX_ATTEMPTS",
    "HEVY_MAX_PAGES",
    "HEVY_MAX_RUNTIME_SECONDS",
    "HEVY_MAX_WORKOUTS",
    "HEVY_PAGE_SIZE",
    "HevyApiProvider",
]
