from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from nutrition_agent.application.ports import (
    AcceptedMenuPageKey,
    AcceptedMenuPageObservation,
    IngestCommand,
)
from nutrition_agent.application.stacks_refresh import (
    CANARY_MAX_PAGES,
    MAX_DAILY_PAGES,
    RefreshAvailableStacksWindowUseCase,
    StacksRefreshPlanner,
    StacksRefreshResult,
    StacksRefreshScope,
    StacksRefreshStatus,
)
from nutrition_agent.domain.stacks.entities import MealPeriod
from nutrition_agent.domain.stacks.ingestion import IngestionRunReport, RunStatus


@dataclass
class FixedClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


@dataclass
class Coverage:
    pages: tuple[AcceptedMenuPageObservation, ...] = ()
    calls: list[tuple[int, date, date]] = field(default_factory=list)

    def accepted_pages(
        self, *, campus_id: int, start_date: date, end_date: date
    ) -> tuple[AcceptedMenuPageObservation, ...]:
        self.calls.append((campus_id, start_date, end_date))
        return self.pages


@dataclass
class Ingester:
    advertised_dates: tuple[date, ...]
    status_by_target: dict[tuple[date, MealPeriod], RunStatus] = field(default_factory=dict)
    stats_by_target: dict[tuple[date, MealPeriod], dict[str, int]] = field(default_factory=dict)
    commands: list[IngestCommand] = field(default_factory=list)

    def execute(self, command: IngestCommand) -> IngestionRunReport:
        self.commands.append(command)
        period = command.meal_periods[0]
        status = self.status_by_target.get((command.service_date, period), RunStatus.PERSISTED)
        return IngestionRunReport(
            run_id=UUID(int=len(self.commands)),
            status=status,
            stats={
                "pages_fetched": 1,
                **self.stats_by_target.get((command.service_date, period), {}),
            },
            quarantines_by_code={},
            advertised_dates=self.advertised_dates,
        )


NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)
TODAY = date(2026, 8, 30)
SEVEN_DATES = tuple(TODAY + timedelta(days=offset) for offset in range(7))


def _key(service_date: date, period: MealPeriod) -> AcceptedMenuPageKey:
    return AcceptedMenuPageKey(service_date, period, 50)


def _observation(
    service_date: date,
    period: MealPeriod,
    *,
    accepted_at: datetime = NOW,
) -> AcceptedMenuPageObservation:
    return AcceptedMenuPageObservation(_key(service_date, period), accepted_at)


def _run(
    coverage: Coverage,
    ingester: Ingester,
    *,
    clock: FixedClock | None = None,
    max_pages: int | None = None,
    scope: StacksRefreshScope = StacksRefreshScope.FULL,
) -> StacksRefreshResult:
    return RefreshAvailableStacksWindowUseCase(
        StacksRefreshPlanner(coverage, clock or FixedClock(NOW)),
        ingester,
    ).execute(max_pages=max_pages, scope=scope)


def test_cold_start_ingests_every_source_date_and_period_in_order() -> None:
    coverage = Coverage()
    ingester = Ingester(SEVEN_DATES)

    result = _run(coverage, ingester)

    assert result.status is StacksRefreshStatus.PERSISTED
    assert result.advertised_dates == SEVEN_DATES
    assert len(result.pages) == 21
    assert [(command.service_date, command.meal_periods[0]) for command in ingester.commands] == [
        (service_date, period) for service_date in SEVEN_DATES for period in MealPeriod
    ]
    assert coverage.calls == [(50, SEVEN_DATES[0], SEVEN_DATES[-1])]


def test_source_window_length_is_derived_instead_of_assumed() -> None:
    six_dates = SEVEN_DATES[:6]
    ingester = Ingester(six_dates)

    result = _run(Coverage(), ingester)

    assert result.status is StacksRefreshStatus.PERSISTED
    assert result.advertised_dates == six_dates
    assert len(result.pages) == 18
    assert {page.target.service_date for page in result.pages} == set(six_dates)


def test_canary_uses_only_current_breakfast_lunch_and_dinner() -> None:
    coverage = Coverage(
        tuple(
            _observation(service_date, period)
            for service_date in SEVEN_DATES
            for period in MealPeriod
        )
    )
    ingester = Ingester(SEVEN_DATES)

    result = _run(
        coverage,
        ingester,
        max_pages=CANARY_MAX_PAGES,
        scope=StacksRefreshScope.CANARY,
    )

    assert result.status is StacksRefreshStatus.PERSISTED
    assert [(page.target.service_date, page.target.meal_period) for page in result.pages] == [
        (TODAY, MealPeriod.BREAKFAST),
        (TODAY, MealPeriod.LUNCH),
        (TODAY, MealPeriod.DINNER),
    ]
    assert coverage.calls == []


def test_canary_period_failure_does_not_prevent_later_period_probe() -> None:
    ingester = Ingester(
        SEVEN_DATES,
        status_by_target={(TODAY, MealPeriod.LUNCH): RunStatus.PARTIAL_FAILURE},
    )

    result = _run(
        Coverage(),
        ingester,
        max_pages=CANARY_MAX_PAGES,
        scope=StacksRefreshScope.CANARY,
    )

    assert result.status is StacksRefreshStatus.PARTIAL_FAILURE
    assert [command.meal_periods for command in ingester.commands] == [
        (MealPeriod.BREAKFAST,),
        (MealPeriod.LUNCH,),
        (MealPeriod.DINNER,),
    ]


def test_bootstrap_prioritizes_current_day_before_future_dates() -> None:
    ingester = Ingester(SEVEN_DATES)

    result = _run(Coverage(), ingester, scope=StacksRefreshScope.BOOTSTRAP)

    assert result.status is StacksRefreshStatus.PERSISTED
    assert [(page.target.service_date, page.target.meal_period) for page in result.pages[:4]] == [
        (TODAY, MealPeriod.BREAKFAST),
        (TODAY, MealPeriod.LUNCH),
        (TODAY, MealPeriod.DINNER),
        (TODAY + timedelta(days=1), MealPeriod.BREAKFAST),
    ]
    assert len(result.pages) == MAX_DAILY_PAGES


def test_bootstrap_stops_after_first_cap_truncated_page() -> None:
    capped = (TODAY + timedelta(days=1), MealPeriod.BREAKFAST)
    ingester = Ingester(
        SEVEN_DATES,
        status_by_target={capped: RunStatus.PARTIAL_FAILURE},
        stats_by_target={capped: {"label_fetch_cap_reached": 1}},
    )

    result = _run(Coverage(), ingester, scope=StacksRefreshScope.BOOTSTRAP)

    assert result.status is StacksRefreshStatus.PARTIAL_FAILURE
    assert result.failure_reason == (
        "bootstrap unique-label fetch cap reached; affected page was not accepted"
    )
    assert [(page.target.service_date, page.target.meal_period) for page in result.pages] == [
        (TODAY, MealPeriod.BREAKFAST),
        (TODAY, MealPeriod.LUNCH),
        (TODAY, MealPeriod.DINNER),
        capped,
    ]


def test_bootstrap_rerun_skips_pages_already_accepted_today() -> None:
    next_day = TODAY + timedelta(days=1)
    accepted = (
        _observation(TODAY, MealPeriod.LUNCH),
        _observation(TODAY, MealPeriod.DINNER),
        _observation(next_day, MealPeriod.BREAKFAST),
    )
    ingester = Ingester(SEVEN_DATES)

    result = _run(Coverage(accepted), ingester, scope=StacksRefreshScope.BOOTSTRAP)

    assert [(page.target.service_date, page.target.meal_period) for page in result.pages[:3]] == [
        (TODAY, MealPeriod.BREAKFAST),
        (next_day, MealPeriod.LUNCH),
        (next_day, MealPeriod.DINNER),
    ]


def test_same_day_future_pages_are_skipped_but_yesterday_current_pages_refresh() -> None:
    yesterday = NOW - timedelta(days=1)
    accepted = tuple(
        _observation(TODAY, period, accepted_at=yesterday) for period in MealPeriod
    ) + tuple(
        _observation(service_date, period)
        for service_date in SEVEN_DATES[1:]
        for period in MealPeriod
    )
    ingester = Ingester(SEVEN_DATES)

    result = _run(Coverage(accepted), ingester)

    assert result.status is StacksRefreshStatus.PERSISTED
    assert [(page.target.service_date, page.target.meal_period) for page in result.pages] == [
        (TODAY, MealPeriod.BREAKFAST),
        (TODAY, MealPeriod.LUNCH),
        (TODAY, MealPeriod.DINNER),
    ]


def test_previous_day_observations_are_refreshed_for_every_advertised_page() -> None:
    yesterday = NOW - timedelta(days=1)
    accepted = tuple(
        _observation(service_date, period, accepted_at=yesterday)
        for service_date in SEVEN_DATES
        for period in MealPeriod
    )
    ingester = Ingester(SEVEN_DATES)

    result = _run(Coverage(accepted), ingester)

    assert result.status is StacksRefreshStatus.PERSISTED
    assert len(result.pages) == 21
    assert [(page.target.service_date, page.target.meal_period) for page in result.pages] == [
        (service_date, period) for service_date in SEVEN_DATES for period in MealPeriod
    ]


def test_current_period_already_accepted_today_is_not_fetched_twice() -> None:
    accepted = (
        _observation(TODAY, MealPeriod.LUNCH),
        _observation(TODAY, MealPeriod.DINNER),
    ) + tuple(
        _observation(service_date, period)
        for service_date in SEVEN_DATES[1:]
        for period in MealPeriod
    )
    ingester = Ingester(SEVEN_DATES)

    result = _run(Coverage(accepted), ingester)

    assert result.status is StacksRefreshStatus.PERSISTED
    assert [page.target.meal_period for page in result.pages] == [MealPeriod.BREAKFAST]


@pytest.mark.parametrize(
    "advertised_dates",
    [
        (),
        (TODAY - timedelta(days=1), TODAY),
        (TODAY, TODAY + timedelta(days=2)),
        tuple(TODAY + timedelta(days=offset) for offset in range(8)),
    ],
)
def test_invalid_source_window_fails_closed_after_probe(
    advertised_dates: tuple[date, ...],
) -> None:
    ingester = Ingester(advertised_dates)

    result = _run(Coverage(), ingester)

    assert result.status is StacksRefreshStatus.FAILED
    assert result.failure_reason is not None
    assert len(result.pages) == 1
    assert len(ingester.commands) == 1


def test_one_page_failure_does_not_prevent_other_pages_and_prior_successes_remain() -> None:
    failing = (TODAY, MealPeriod.LUNCH)
    ingester = Ingester(SEVEN_DATES, status_by_target={failing: RunStatus.FAILED})

    result = _run(Coverage(), ingester)

    assert result.status is StacksRefreshStatus.PARTIAL_FAILURE
    assert len(result.pages) == 21
    assert result.pages[0].report.status is RunStatus.PERSISTED
    assert result.pages[1].report.status is RunStatus.FAILED
    assert result.pages[2].target.meal_period is MealPeriod.DINNER
    assert result.pages[2].report.status is RunStatus.PERSISTED


def test_institutional_local_date_drives_probe() -> None:
    before_local_midnight = datetime(2026, 8, 30, 3, 30, tzinfo=UTC)
    local_today = date(2026, 8, 29)
    dates = tuple(local_today + timedelta(days=offset) for offset in range(7))
    ingester = Ingester(dates)

    result = _run(Coverage(), ingester, clock=FixedClock(before_local_midnight), max_pages=1)

    assert result.pages[0].target.service_date == local_today


def test_fixture_page_cap_stops_after_source_window_probe() -> None:
    ingester = Ingester(SEVEN_DATES)

    result = _run(Coverage(), ingester, max_pages=1)

    assert result.status is StacksRefreshStatus.PERSISTED
    assert len(result.pages) == 1
    assert ingester.commands == [IngestCommand(TODAY, (MealPeriod.BREAKFAST,))]


def test_naive_refresh_clock_fails_closed_before_ingestion() -> None:
    ingester = Ingester(SEVEN_DATES)

    with pytest.raises(ValueError, match="timezone-aware"):
        _run(Coverage(), ingester, clock=FixedClock(datetime(2026, 8, 30, 12)))

    assert ingester.commands == []


@pytest.mark.parametrize("max_pages", (0, MAX_DAILY_PAGES + 1))
def test_invalid_page_cap_is_rejected(max_pages: int) -> None:
    with pytest.raises(ValueError, match="max_pages"):
        _run(Coverage(), Ingester(SEVEN_DATES), max_pages=max_pages)
