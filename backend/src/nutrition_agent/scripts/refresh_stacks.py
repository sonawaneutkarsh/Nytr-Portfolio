"""Bounded source-window Stacks refresh command.

The live path is intentionally narrow: one configured institutional dining
source, source-advertised dates capped at seven, and Breakfast/Lunch/Dinner. It has no arbitrary
URL/date/location arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import date
from pathlib import Path

from nutrition_agent.application.ingest_stacks import IngestStacksUseCase
from nutrition_agent.application.ports import (
    AcceptedMenuPageObservation,
    Clock,
    IngestCommand,
    MenuPageCoverageRepository,
)
from nutrition_agent.application.stacks_refresh import (
    BOOTSTRAP_MAX_UNIQUE_LABEL_FETCHES,
    CANARY_MAX_PAGES,
    CANARY_MAX_UNIQUE_LABEL_FETCHES,
    MAX_DAILY_PAGES,
    RefreshAvailableStacksWindowUseCase,
    StacksPageIngester,
    StacksRefreshPlanner,
    StacksRefreshResult,
    StacksRefreshScope,
    StacksRefreshStatus,
)
from nutrition_agent.config import Settings
from nutrition_agent.db.sql_repos import (
    SqlMenuPageCoverageRepository,
    SqlStacksRefreshLease,
)
from nutrition_agent.domain.stacks.ingestion import IngestionRunReport, RunStatus
from nutrition_agent.infrastructure.compliance_gate import IngestionMode
from nutrition_agent.infrastructure.http_transport import HttpxTransport, TransportMetrics
from nutrition_agent.infrastructure.stacks_source.provider import StacksHttpSource
from nutrition_agent.scripts.ingest_stacks import (
    DEFAULT_FIXTURE_DIR,
    RequestedDateClock,
    SystemClock,
    _build_deps,
    _has_real_owner_contact,
)

OFFICIAL_STACKS_BASE_URL = "https://institutional-menu.example.invalid"
MAX_SCHEDULED_ATTEMPTS = 1950
MAX_CANARY_ATTEMPTS = 2 * (CANARY_MAX_PAGES + CANARY_MAX_UNIQUE_LABEL_FETCHES)
MAX_BOOTSTRAP_ATTEMPTS = 2 * (MAX_DAILY_PAGES + BOOTSTRAP_MAX_UNIQUE_LABEL_FETCHES)


class _EmptyCoverageRepository(MenuPageCoverageRepository):
    def accepted_pages(
        self,
        *,
        campus_id: int,
        start_date: date,
        end_date: date,
    ) -> tuple[AcceptedMenuPageObservation, ...]:
        return ()


class _LazyIngester(StacksPageIngester):
    """Build one ingestion stack and reuse it across the bounded page batch."""

    def __init__(
        self,
        *,
        settings: Settings,
        source_name: str,
        fixture_dir: Path,
        persist: bool,
        clock: Clock,
        max_unique_label_fetches: int | None = None,
    ) -> None:
        self._settings = settings
        self._source_name = source_name
        self._fixture_dir = fixture_dir
        self._persist = persist
        self._clock = clock
        self._max_unique_label_fetches = max_unique_label_fetches
        self._use_case: IngestStacksUseCase | None = None
        self._transport: HttpxTransport | None = None
        self.metrics: TransportMetrics | None = None

    def execute(self, command: IngestCommand) -> IngestionRunReport:
        if self._use_case is None:
            deps = _build_deps(
                self._settings,
                source_name=self._source_name,
                fixture_dir=self._fixture_dir,
                persist=self._persist,
                clock=self._clock,
            )
            if self._max_unique_label_fetches is not None:
                deps = replace(
                    deps,
                    max_unique_label_fetches=self._max_unique_label_fetches,
                )
            self._use_case = IngestStacksUseCase(deps)
            if isinstance(deps.source, StacksHttpSource) and isinstance(
                deps.source.transport, HttpxTransport
            ):
                self._transport = deps.source.transport
        report = self._use_case.execute(command)
        if self._transport is not None:
            self.metrics = self._transport.metrics
        return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh a bounded institutional dining source window"
    )
    parser.add_argument("--source", choices=("fixture", "live"), default="fixture")
    parser.add_argument(
        "--refresh-mode",
        choices=tuple(scope.value for scope in StacksRefreshScope),
        default=None,
        help=(
            "required for live source; canary is current-day only, bootstrap is a "
            "resumable bounded cold fill, and full uses the source window"
        ),
    )
    authority = parser.add_mutually_exclusive_group(required=True)
    authority.add_argument("--persist", action="store_true")
    authority.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=None,
        help="fixture directory override; valid only with --source fixture",
    )
    parser.add_argument(
        "--fixture-as-of-date",
        type=date.fromisoformat,
        default=None,
        help="offline fixture clock; rejected for live source",
    )
    return parser


def _validate_args(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    settings: Settings,
) -> None:
    live = args.source == "live"
    if live and args.refresh_mode is None:
        parser.error("live refresh requires explicit --refresh-mode canary|bootstrap|full")
    if live and not args.persist:
        parser.error("scheduled live refresh requires explicit --persist")
    if live and (args.fixtures is not None or args.fixture_as_of_date is not None):
        parser.error("live refresh cannot use fixture inputs or a caller-selected date")
    if live and settings.ingestion_mode is not IngestionMode.SCHEDULED:
        parser.error("scheduled live refresh requires STACKS_INGESTION_MODE=scheduled")
    if live and not _has_real_owner_contact(settings.user_agent):
        parser.error("scheduled live refresh requires a genuine owner-contact User-Agent")
    if args.persist and not settings.database_url:
        parser.error("--persist requires STACKS_DATABASE_URL before any source construction")
    if settings.campus_id != 50:
        parser.error("scheduled refresh is fixed to the configured institutional campus")
    if live and settings.base_url.rstrip("/") != OFFICIAL_STACKS_BASE_URL:
        parser.error("scheduled live refresh is fixed to the official Stacks source host")
    if live and settings.min_request_interval_seconds < 10:
        parser.error("scheduled live refresh requires at least 10 seconds between attempts")
    if live and not 1 <= settings.max_attempts <= 2:
        parser.error("scheduled live refresh allows at most 2 attempts per request")
    if live:
        attempt_limit = {
            StacksRefreshScope.CANARY.value: MAX_CANARY_ATTEMPTS,
            StacksRefreshScope.BOOTSTRAP.value: MAX_BOOTSTRAP_ATTEMPTS,
            StacksRefreshScope.FULL.value: MAX_SCHEDULED_ATTEMPTS,
        }[str(args.refresh_mode)]
        if not 1 <= settings.attempt_budget <= attempt_limit:
            parser.error(
                f"{args.refresh_mode} live refresh attempt budget must be between "
                f"1 and {attempt_limit}"
            )
    if live and not 0 < settings.request_timeout_seconds <= 20:
        parser.error("scheduled live refresh timeout must be at most 20 seconds")
    if live and not 1 <= settings.max_items_per_page <= 125:
        parser.error("scheduled live refresh item limit must be between 1 and 125")


def _execute(
    *,
    args: argparse.Namespace,
    settings: Settings,
    coverage: MenuPageCoverageRepository,
    clock: Clock,
) -> tuple[StacksRefreshResult, TransportMetrics | None]:
    scope = _refresh_scope(args)
    planner = StacksRefreshPlanner(coverage, clock)
    ingester = _LazyIngester(
        settings=settings,
        source_name=str(args.source),
        fixture_dir=args.fixtures or DEFAULT_FIXTURE_DIR,
        persist=bool(args.persist),
        clock=clock,
        max_unique_label_fetches=(
            {
                StacksRefreshScope.CANARY: CANARY_MAX_UNIQUE_LABEL_FETCHES,
                StacksRefreshScope.BOOTSTRAP: BOOTSTRAP_MAX_UNIQUE_LABEL_FETCHES,
            }.get(scope)
            if args.source == "live"
            else None
        ),
    )
    # Fixture mode intentionally exercises only the source-window probe;
    # committed fixtures do not fabricate uncaptured date/period pages. Live
    # bounds come from the explicitly selected refresh scope.
    max_pages = 1 if args.source == "fixture" else None
    result = RefreshAvailableStacksWindowUseCase(planner, ingester).execute(
        scope=scope,
        max_pages=max_pages,
    )
    return result, ingester.metrics


def _print_result(
    result: StacksRefreshResult,
    *,
    source: str,
    scope: StacksRefreshScope,
    authoritative: bool,
    metrics: TransportMetrics | None,
) -> None:
    stats = _sum_counts(page.report.stats for page in result.pages)
    quarantines = _sum_counts(page.report.quarantines_by_code for page in result.pages)
    print(
        json.dumps(
            {
                "status": result.status.value,
                "source": source,
                "refresh_mode": scope.value,
                "authoritative": authoritative,
                "advertised_dates": [value.isoformat() for value in result.advertised_dates],
                "failure_reason": result.failure_reason,
                "pages": [
                    {
                        "target": {
                            "service_date": page.target.service_date.isoformat(),
                            "meal_period": page.target.meal_period.value,
                            "campus_id": page.target.campus_id,
                        },
                        "run_id": str(page.report.run_id),
                        "status": page.report.status.value,
                        "stats": page.report.stats,
                        "quarantines": page.report.quarantines_by_code,
                    }
                    for page in result.pages
                ],
                "stats": stats,
                "quarantines": quarantines,
                "summary": {
                    "menu_requests": stats.get("menu_requests", 0),
                    "unique_label_requests": stats.get("unique_label_requests", 0),
                    "reused_labels": stats.get("labels_reused", 0),
                    "fetched_labels": stats.get("labels_fetched", 0),
                    "accepted_pages": sum(
                        page.report.status is RunStatus.PERSISTED for page in result.pages
                    ),
                    "quarantine_count": sum(quarantines.values()),
                    "stop_reason": result.failure_reason,
                },
                "transport": (
                    {
                        "attempts": metrics.attempts_made,
                        "retries": metrics.retries_made,
                        "retry_causes": list(metrics.retry_causes),
                        "observed_min_spacing_seconds": (metrics.observed_min_spacing_seconds),
                        "attempt_budget": metrics.attempt_budget,
                        "configured_min_interval_seconds": (
                            metrics.configured_min_interval_seconds
                        ),
                    }
                    if metrics is not None
                    else None
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )


def _sum_counts(values: Iterable[Mapping[str, int]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for value in values:
        for key, count in value.items():
            totals[key] = totals.get(key, 0) + count
    return totals


def _refresh_scope(args: argparse.Namespace) -> StacksRefreshScope:
    return StacksRefreshScope(args.refresh_mode or StacksRefreshScope.CANARY.value)


def _exit_code(status: StacksRefreshStatus) -> int:
    if status in {
        StacksRefreshStatus.UP_TO_DATE,
        StacksRefreshStatus.OVERLAP_SKIPPED,
        StacksRefreshStatus.PERSISTED,
    }:
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    _validate_args(parser, args, settings)
    scope = _refresh_scope(args)

    authoritative = bool(args.persist)
    if args.source == "fixture":
        clock: Clock = (
            RequestedDateClock(args.fixture_as_of_date)
            if args.fixture_as_of_date is not None
            else SystemClock()
        )
        result, transport_metrics = _execute(
            args=args,
            settings=settings,
            coverage=_EmptyCoverageRepository(),
            clock=clock,
        )
        _print_result(
            result,
            source="fixture",
            scope=scope,
            authoritative=authoritative,
            metrics=transport_metrics,
        )
        if not authoritative:
            print("DRY RUN: no authoritative SQL state was written.", file=sys.stderr)
        return _exit_code(result.status)

    assert settings.database_url is not None
    transport_metrics = None
    with SqlStacksRefreshLease(settings.database_url) as lease:
        if not lease.acquired:
            result = StacksRefreshResult(
                status=StacksRefreshStatus.OVERLAP_SKIPPED,
                pages=(),
            )
        else:
            result, transport_metrics = _execute(
                args=args,
                settings=settings,
                coverage=SqlMenuPageCoverageRepository(settings.database_url),
                clock=SystemClock(),
            )
    _print_result(
        result,
        source="live",
        scope=scope,
        authoritative=True,
        metrics=transport_metrics,
    )
    return _exit_code(result.status)


if __name__ == "__main__":
    raise SystemExit(main())
