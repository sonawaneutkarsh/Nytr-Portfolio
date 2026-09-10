"""Explicit one-page Stacks ingestion CLI. No scheduler exists anywhere.

Fixture mode is the safe default and uses only in-memory repositories unless
``--persist`` is supplied. Live networking requires every authority switch:
manual compliance mode, ``--source live``, and an explicit dry-run/persist
choice. PostgreSQL authority additionally requires ``--persist`` plus a DSN.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4

from nutrition_agent.application.ingest_stacks import (
    IngestionDeps,
    IngestStacksUseCase,
    StacksSource,
)
from nutrition_agent.application.ports import (
    Clock,
    FoodRepository,
    IdGenerator,
    IngestCommand,
    MenuPageVersionRepository,
    OfferingRepository,
    ProfileRepository,
    QuarantineRepository,
    RunRepository,
    SnapshotRepository,
)
from nutrition_agent.config import Settings
from nutrition_agent.db.in_memory_repos import (
    InMemoryFoodRepository,
    InMemoryMenuPageVersionRepository,
    InMemoryOfferingRepository,
    InMemoryProfileRepository,
    InMemoryQuarantineRepository,
    InMemoryRunRepository,
    InMemorySnapshotRepository,
)
from nutrition_agent.db.sql_repos import (
    SqlFoodRepository,
    SqlMenuPageVersionRepository,
    SqlOfferingRepository,
    SqlProfileRepository,
    SqlQuarantineRepository,
    SqlRunRepository,
    SqlSnapshotRepository,
)
from nutrition_agent.domain.stacks.entities import MealPeriod
from nutrition_agent.domain.stacks.ingestion import RunStatus
from nutrition_agent.infrastructure.compliance_gate import ComplianceGate, IngestionMode
from nutrition_agent.infrastructure.http_transport import HttpxTransport
from nutrition_agent.infrastructure.snapshot_store import SnapshotStore
from nutrition_agent.infrastructure.stacks_source.constants import SOURCE_SYSTEM_INSTITUTIONAL
from nutrition_agent.infrastructure.stacks_source.fixture_source import FixtureStacksSource
from nutrition_agent.infrastructure.stacks_source.provider import StacksHttpSource

DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "stacks"
_CONTACT_PATTERN = re.compile(r"contact:\s*([^\s;)]+@[^\s;)]+)", re.IGNORECASE)
_PLACEHOLDER_CONTACTS = {"owner", "placeholder", "demo@example.invalid", "example@example.invalid"}


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(UTC)


class RequestedDateClock(Clock):
    """Keeps historical fixture replays inside their recorded source window."""

    def __init__(self, service_date: date) -> None:
        self._now = datetime(
            service_date.year,
            service_date.month,
            service_date.day,
            12,
            tzinfo=UTC,
        )

    def now(self) -> datetime:
        return self._now


class Uuid4Generator(IdGenerator):
    def new_id(self) -> UUID:
        return uuid4()


def _has_real_owner_contact(user_agent: str) -> bool:
    match = _CONTACT_PATTERN.search(user_agent)
    if match is None:
        return False
    contact = match.group(1).lower()
    _, domain = contact.rsplit("@", 1)
    return (
        contact not in _PLACEHOLDER_CONTACTS
        and "." in domain
        and not domain.endswith((".invalid", ".test", ".example"))
        and domain != "example.com"
    )


def _build_deps(
    settings: Settings,
    *,
    source_name: str,
    fixture_dir: Path,
    persist: bool,
    clock: Clock | None = None,
) -> IngestionDeps:
    fixture_mode = source_name == "fixture"
    gate = ComplianceGate(IngestionMode.MANUAL if fixture_mode else settings.ingestion_mode)
    source: StacksSource
    if fixture_mode:
        source = FixtureStacksSource(fixture_dir)
    else:
        transport = HttpxTransport(
            gate,
            SOURCE_SYSTEM_INSTITUTIONAL,
            user_agent=settings.user_agent,
            max_attempts=settings.max_attempts,
            min_interval_seconds=settings.min_request_interval_seconds,
            attempt_budget=settings.attempt_budget,
            timeout_seconds=settings.request_timeout_seconds,
        )
        source = StacksHttpSource(transport, base_url=settings.base_url)

    if persist:
        assert settings.database_url is not None
        dsn = settings.database_url
        foods: FoodRepository = SqlFoodRepository(dsn)
        offerings: OfferingRepository = SqlOfferingRepository(dsn)
        profiles: ProfileRepository = SqlProfileRepository(dsn)
        runs: RunRepository = SqlRunRepository(dsn)
        snapshots: SnapshotRepository = SqlSnapshotRepository(dsn)
        pages: MenuPageVersionRepository = SqlMenuPageVersionRepository(dsn)
        quarantine: QuarantineRepository = SqlQuarantineRepository(dsn)
    else:
        memory_foods = InMemoryFoodRepository()
        memory_offerings = InMemoryOfferingRepository()
        memory_profiles = InMemoryProfileRepository()
        foods = memory_foods
        offerings = memory_offerings
        profiles = memory_profiles
        runs = InMemoryRunRepository()
        snapshots = InMemorySnapshotRepository()
        pages = InMemoryMenuPageVersionRepository(
            foods=memory_foods,
            offerings=memory_offerings,
            profiles=memory_profiles,
        )
        quarantine = InMemoryQuarantineRepository()

    return IngestionDeps(
        gate=gate,
        source=source,
        snapshots=SnapshotStore(settings.snapshot_root),
        runs=runs,
        snapshot_repo=snapshots,
        pages=pages,
        foods=foods,
        offerings=offerings,
        profiles=profiles,
        quarantine=quarantine,
        clock=clock or SystemClock(),
        ids=Uuid4Generator(),
        max_items_per_page=settings.max_items_per_page,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Explicit one-page Stacks ingestion runner")
    parser.add_argument("--source", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--campus", choices=("demo",), default="demo")
    parser.add_argument("--location", choices=("stacks",), default="stacks")
    parser.add_argument(
        "--service-date",
        type=date.fromisoformat,
        required=True,
        help="one YYYY-MM-DD date",
    )
    parser.add_argument(
        "--meal-period",
        choices=tuple(period.value for period in MealPeriod),
        required=True,
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=None,
        help="fixture directory override; valid only with --source fixture",
    )
    authority = parser.add_mutually_exclusive_group()
    authority.add_argument("--persist", action="store_true", help="write authoritative SQL state")
    authority.add_argument("--dry-run", action="store_true", help="use in-memory repositories")
    return parser


def _validate_args(
    parser: argparse.ArgumentParser, args: argparse.Namespace, settings: Settings
) -> None:
    if args.source == "live" and not (args.persist or args.dry_run):
        parser.error("live mode requires an explicit --persist or --dry-run")
    if args.source == "live" and args.fixtures is not None:
        parser.error("--fixtures cannot be combined with --source live")
    if args.source == "live" and settings.ingestion_mode is not IngestionMode.MANUAL:
        parser.error("live mode requires STACKS_INGESTION_MODE=manual")
    if args.source == "live" and not _has_real_owner_contact(settings.user_agent):
        parser.error("live mode requires STACKS_USER_AGENT with a real owner email in contact:...")
    if args.persist and not settings.database_url:
        parser.error("--persist requires STACKS_DATABASE_URL before any source access")
    if settings.campus_id != 50:
        parser.error("the public fixture mode supports only the demo dining campus")
    if args.source == "live" and settings.min_request_interval_seconds < 10:
        parser.error("live mode requires at least 10 seconds between network attempts")
    if args.source == "live" and not 1 <= settings.max_attempts <= 2:
        parser.error("live mode allows at most 2 attempts per request")
    if args.source == "live" and not 1 <= settings.attempt_budget <= 150:
        parser.error("live mode attempt budget must be between 1 and 150")
    if args.source == "live" and not 0 < settings.request_timeout_seconds <= 20:
        parser.error("live mode request timeout must be at most 20 seconds")
    if args.source == "live" and not 1 <= settings.max_items_per_page <= 125:
        parser.error("live mode item limit must be between 1 and 125")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    _validate_args(parser, args, settings)

    fixture_dir = args.fixtures or DEFAULT_FIXTURE_DIR
    deps = _build_deps(
        settings,
        source_name=args.source,
        fixture_dir=fixture_dir,
        persist=bool(args.persist),
        clock=(RequestedDateClock(args.service_date) if args.source == "fixture" else None),
    )
    report = IngestStacksUseCase(deps).execute(
        IngestCommand(
            service_date=args.service_date,
            meal_periods=(MealPeriod(args.meal_period),),
        )
    )

    authoritative = bool(args.persist)
    print(
        json.dumps(
            {
                "status": report.status.value,
                "source": args.source,
                "authoritative": authoritative,
                "stats": report.stats,
                "quarantines": report.quarantines_by_code,
            },
            indent=2,
        )
    )
    if not authoritative:
        print(
            "DRY RUN: in-memory repositories were used; no authoritative SQL state was written.",
            file=sys.stderr,
        )
    return 0 if report.status is RunStatus.PERSISTED else 1


if __name__ == "__main__":
    raise SystemExit(main())
