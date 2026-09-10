"""Bounded source-window Stacks refresh selection and orchestration.

The first current-day Breakfast page is both a required authoritative page and
the source-window probe. The remaining keys come only from the dates advertised
by that selection-validated response. Every advertised page is re-observed once
per configured institutional local day; a same-day rerun skips pages already refreshed today.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo

from nutrition_agent.application.ingest_stacks import WINDOW_DAYS
from nutrition_agent.application.ports import (
    AcceptedMenuPageKey,
    Clock,
    IngestCommand,
    MenuPageCoverageRepository,
)
from nutrition_agent.domain.stacks.entities import MealPeriod
from nutrition_agent.domain.stacks.ingestion import IngestionRunReport, RunStatus
from nutrition_agent.infrastructure.stacks_source.constants import STACKS_CAMPUS_ID

CAMPUS_TIME_ZONE = ZoneInfo("America/New_York")
REQUIRED_MEAL_PERIODS = tuple(MealPeriod)
MAX_DAILY_PAGES = WINDOW_DAYS * len(REQUIRED_MEAL_PERIODS)
CANARY_MAX_PAGES = len(REQUIRED_MEAL_PERIODS)
CANARY_MAX_UNIQUE_LABEL_FETCHES = 5
BOOTSTRAP_MAX_UNIQUE_LABEL_FETCHES = 125


class StacksRefreshScope(StrEnum):
    CANARY = "canary"
    BOOTSTRAP = "bootstrap"
    FULL = "full"


@dataclass(frozen=True, order=True)
class StacksRefreshTarget:
    service_date: date
    meal_period: MealPeriod
    campus_id: int = STACKS_CAMPUS_ID


class StacksRefreshStatus(StrEnum):
    UP_TO_DATE = "up_to_date"
    OVERLAP_SKIPPED = "overlap_skipped"
    PERSISTED = "persisted"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"
    BLOCKED_BY_POLICY = "blocked_by_policy"


@dataclass(frozen=True)
class StacksRefreshPageResult:
    target: StacksRefreshTarget
    report: IngestionRunReport


@dataclass(frozen=True)
class StacksRefreshResult:
    status: StacksRefreshStatus
    pages: tuple[StacksRefreshPageResult, ...]
    advertised_dates: tuple[date, ...] = ()
    failure_reason: str | None = None


class InvalidAdvertisedWindow(ValueError):
    """The selection-validated source page did not advertise a safe window."""


class StacksPageIngester(Protocol):
    def execute(self, command: IngestCommand) -> IngestionRunReport: ...


class StacksRefreshPlanner:
    """Enumerate source-advertised pages that have not been refreshed today."""

    def __init__(
        self,
        coverage: MenuPageCoverageRepository,
        clock: Clock,
        *,
        campus_id: int = STACKS_CAMPUS_ID,
        max_window_days: int = WINDOW_DAYS,
    ) -> None:
        if max_window_days < 1:
            raise ValueError("max_window_days must be positive")
        self._coverage = coverage
        self._clock = clock
        self._campus_id = campus_id
        self._max_window_days = max_window_days

    @property
    def today(self) -> date:
        return _campus_date(self._clock.now())

    def probe_target(self) -> StacksRefreshTarget:
        return StacksRefreshTarget(self.today, MealPeriod.BREAKFAST, self._campus_id)

    def remaining_targets(
        self,
        advertised_dates: tuple[date, ...],
    ) -> tuple[StacksRefreshTarget, ...]:
        dates = self._validated_window(advertised_dates)
        observations = self._coverage.accepted_pages(
            campus_id=self._campus_id,
            start_date=dates[0],
            end_date=dates[-1],
        )
        accepted = {observation.key: observation for observation in observations}
        targets: list[StacksRefreshTarget] = []

        # Breakfast for today was already fetched as the probe. Every other
        # advertised page receives one fresh observation per local day. This
        # catches future-menu edits without repeating work on a same-day rerun.
        for service_date in dates:
            for meal_period in REQUIRED_MEAL_PERIODS:
                if service_date == self.today and meal_period is MealPeriod.BREAKFAST:
                    continue
                key = AcceptedMenuPageKey(service_date, meal_period, self._campus_id)
                observation = accepted.get(key)
                if observation is None or _campus_date(observation.accepted_at) != self.today:
                    targets.append(StacksRefreshTarget(service_date, meal_period, self._campus_id))
        return tuple(targets)

    def canary_targets(
        self,
        advertised_dates: tuple[date, ...],
    ) -> tuple[StacksRefreshTarget, ...]:
        self._validated_window(advertised_dates)
        return tuple(
            StacksRefreshTarget(self.today, meal_period, self._campus_id)
            for meal_period in (MealPeriod.LUNCH, MealPeriod.DINNER)
        )

    def _validated_window(self, advertised_dates: tuple[date, ...]) -> tuple[date, ...]:
        dates = tuple(advertised_dates)
        if not dates:
            raise InvalidAdvertisedWindow("source advertised no menu dates")
        if len(dates) > self._max_window_days:
            raise InvalidAdvertisedWindow(
                f"source advertised more than {self._max_window_days} dates"
            )
        expected = tuple(self.today + timedelta(days=offset) for offset in range(len(dates)))
        if dates != expected:
            raise InvalidAdvertisedWindow(
                "source dates must be ordered, contiguous, and start on the campus date"
            )
        return dates


class RefreshAvailableStacksWindowUseCase:
    """Ingest the advertised window page-by-page under one shared transport budget."""

    def __init__(self, planner: StacksRefreshPlanner, ingester: StacksPageIngester) -> None:
        self._planner = planner
        self._ingester = ingester

    def execute(
        self,
        *,
        scope: StacksRefreshScope = StacksRefreshScope.FULL,
        max_pages: int | None = None,
    ) -> StacksRefreshResult:
        page_limit = CANARY_MAX_PAGES if scope is StacksRefreshScope.CANARY else MAX_DAILY_PAGES
        if max_pages is not None:
            if not 1 <= max_pages <= page_limit:
                raise ValueError(f"max_pages must be between 1 and {page_limit}")
            page_limit = max_pages

        probe = self._planner.probe_target()
        probe_report = self._execute_target(probe)
        pages = [StacksRefreshPageResult(probe, probe_report)]
        advertised_dates = probe_report.advertised_dates
        try:
            remaining = (
                self._planner.canary_targets(advertised_dates)
                if scope is StacksRefreshScope.CANARY
                else self._planner.remaining_targets(advertised_dates)
            )
        except InvalidAdvertisedWindow as exc:
            return StacksRefreshResult(
                status=_aggregate_status(pages, window_valid=False),
                pages=tuple(pages),
                advertised_dates=advertised_dates,
                failure_reason=str(exc),
            )

        for target in remaining[: page_limit - 1]:
            page = StacksRefreshPageResult(target, self._execute_target(target))
            pages.append(page)
            if (
                scope is StacksRefreshScope.BOOTSTRAP
                and page.report.stats.get("label_fetch_cap_reached", 0) > 0
            ):
                break

        cap_reached = any(page.report.stats.get("label_fetch_cap_reached", 0) for page in pages)
        cap_failure_reason = {
            StacksRefreshScope.CANARY: (
                "canary unique-label fetch cap reached; affected pages were not accepted"
            ),
            StacksRefreshScope.BOOTSTRAP: (
                "bootstrap unique-label fetch cap reached; affected page was not accepted"
            ),
        }.get(scope)
        return StacksRefreshResult(
            status=_aggregate_status(pages, window_valid=True),
            pages=tuple(pages),
            advertised_dates=advertised_dates,
            failure_reason=cap_failure_reason if cap_reached else None,
        )

    def _execute_target(self, target: StacksRefreshTarget) -> IngestionRunReport:
        return self._ingester.execute(
            IngestCommand(
                service_date=target.service_date,
                meal_periods=(target.meal_period,),
            )
        )


def _campus_date(value: datetime) -> date:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("refresh clock must return a timezone-aware datetime")
    return value.astimezone(CAMPUS_TIME_ZONE).date()


def _aggregate_status(
    pages: list[StacksRefreshPageResult], *, window_valid: bool
) -> StacksRefreshStatus:
    statuses = tuple(page.report.status for page in pages)
    if not window_valid:
        if statuses and statuses[0] is RunStatus.BLOCKED_BY_POLICY:
            return StacksRefreshStatus.BLOCKED_BY_POLICY
        return StacksRefreshStatus.FAILED
    if statuses and all(status is RunStatus.PERSISTED for status in statuses):
        return StacksRefreshStatus.PERSISTED
    if any(status is RunStatus.PERSISTED for status in statuses):
        return StacksRefreshStatus.PARTIAL_FAILURE
    if statuses and all(status is RunStatus.BLOCKED_BY_POLICY for status in statuses):
        return StacksRefreshStatus.BLOCKED_BY_POLICY
    if any(status is RunStatus.FAILED for status in statuses):
        return StacksRefreshStatus.FAILED
    return StacksRefreshStatus.PARTIAL_FAILURE
