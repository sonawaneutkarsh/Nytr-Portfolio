"""HTTP transport with bounded retries, rate limiting, and mandatory compliance gate.

The gate is checked inside the transport itself so no caller can bypass it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx

from nutrition_agent.infrastructure.compliance_gate import ComplianceGate


class TransportError(Exception):
    pass


@dataclass(frozen=True)
class HttpRequest:
    url: str
    method: str = "GET"
    form_fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FetchResult:
    status: int
    body: bytes
    fetched_at: datetime
    final_url: str


@dataclass(frozen=True)
class TransportMetrics:
    attempts_made: int
    retry_causes: tuple[str, ...]
    observed_min_spacing_seconds: float | None
    attempt_budget: int
    configured_min_interval_seconds: float

    @property
    def retries_made(self) -> int:
        return len(self.retry_causes)


class HttpTransport(Protocol):
    def fetch(self, request: HttpRequest) -> FetchResult: ...


class RateLimiter:
    def __init__(
        self,
        min_interval_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._min_interval = min_interval_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._last: float | None = None

    def wait_turn(self) -> None:
        if self._min_interval <= 0:
            return
        now = self._clock()
        if self._last is not None:
            elapsed = now - self._last
            remaining = self._min_interval - elapsed
            if remaining > 0:
                self._sleeper(remaining)
                now = self._clock()
        self._last = now


class HttpxTransport:
    """Production transport. Sequential, rate-limited, retry-bounded, gate-checked."""

    def __init__(
        self,
        gate: ComplianceGate,
        source_system: str,
        *,
        user_agent: str,
        max_attempts: int = 2,
        backoff_base_seconds: float = 1.0,
        min_interval_seconds: float = 10.0,
        attempt_budget: int = 150,
        timeout_seconds: float = 20.0,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_attempts not in (1, 2):
            raise ValueError("max_attempts must be 1 or 2")
        if attempt_budget < 1:
            raise ValueError("attempt_budget must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._gate = gate
        self._source_system = source_system
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base_seconds
        self._attempt_budget = attempt_budget
        self._min_interval_seconds = min_interval_seconds
        self._clock = clock
        self._client = httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": user_agent},
        )
        self._rate_limiter = RateLimiter(min_interval_seconds, clock, sleeper)
        self._sleep = sleeper
        self.attempts_made = 0
        self._attempt_started_at: list[float] = []
        self._retry_causes: list[str] = []

    @property
    def requests_made(self) -> int:
        """Backward-compatible observability alias; every attempt is counted."""
        return self.attempts_made

    @property
    def metrics(self) -> TransportMetrics:
        spacings = [
            current - previous
            for previous, current in zip(
                self._attempt_started_at,
                self._attempt_started_at[1:],
                strict=False,
            )
        ]
        return TransportMetrics(
            attempts_made=self.attempts_made,
            retry_causes=tuple(self._retry_causes),
            observed_min_spacing_seconds=min(spacings) if spacings else None,
            attempt_budget=self._attempt_budget,
            configured_min_interval_seconds=self._min_interval_seconds,
        )

    def fetch(self, request: HttpRequest) -> FetchResult:
        self._gate.ensure_fetch_allowed(self._source_system)

        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            retry_cause: str | None = None
            if self.attempts_made >= self._attempt_budget:
                raise TransportError(f"invocation attempt budget reached ({self._attempt_budget})")
            self._rate_limiter.wait_turn()
            self.attempts_made += 1
            self._attempt_started_at.append(self._clock())
            try:
                response = self._client.request(
                    request.method,
                    request.url,
                    data=request.form_fields or None,
                )
            except httpx.TransportError as exc:
                last_error = exc
                retry_cause = "network_error"
            else:
                result = FetchResult(
                    status=response.status_code,
                    body=response.content,
                    fetched_at=datetime.now(UTC),
                    final_url=str(response.url),
                )
                if response.status_code not in {408, 429, 500, 502, 503, 504}:
                    return result
                if attempt == self._max_attempts:
                    return result
                last_error = TransportError(f"retryable HTTP {response.status_code}")
                retry_cause = f"http_{response.status_code}"
            if attempt < self._max_attempts:
                assert retry_cause is not None
                self._retry_causes.append(retry_cause)
                self._sleep(self._backoff_base * (2 ** (attempt - 1)))
        raise TransportError(f"transport failed after {self._max_attempts} attempts: {last_error}")
