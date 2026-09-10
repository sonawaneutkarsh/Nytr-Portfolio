"""Shared fixtures/helpers for the ingestion test suite."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest

from nutrition_agent.application.ingest_stacks import IngestionDeps, IngestStacksUseCase
from nutrition_agent.db.in_memory_repos import (
    InMemoryFoodRepository,
    InMemoryMenuPageVersionRepository,
    InMemoryOfferingRepository,
    InMemoryProfileRepository,
    InMemoryQuarantineRepository,
    InMemoryRunRepository,
    InMemorySnapshotRepository,
)
from nutrition_agent.domain.stacks.entities import MealPeriod
from nutrition_agent.infrastructure.compliance_gate import ComplianceGate, IngestionMode
from nutrition_agent.infrastructure.snapshot_store import SnapshotStore
from nutrition_agent.infrastructure.stacks_source.fixture_source import FixtureStacksSource

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "stacks"
SERVICE_DATE = date(2026, 8, 21)


class FixedClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class SequentialIds:
    def __init__(self) -> None:
        self._n = 0

    def new_id(self) -> UUID:
        self._n += 1
        return UUID(int=self._n)


def make_use_case(
    mode: IngestionMode = IngestionMode.MANUAL,
    fixture_dir: Path | None = None,
    snapshot_root: Path | None = None,
    source=None,
) -> tuple[IngestStacksUseCase, IngestionDeps]:
    gate = ComplianceGate(mode)
    if source is None:
        source = FixtureStacksSource(fixture_dir or FIXTURE_DIR)
    foods = InMemoryFoodRepository()
    offerings = InMemoryOfferingRepository()
    profiles = InMemoryProfileRepository()
    deps = IngestionDeps(
        gate=gate,
        source=source,
        snapshots=SnapshotStore(snapshot_root or Path("/tmp/na-test-snapshots")),
        runs=InMemoryRunRepository(),
        snapshot_repo=InMemorySnapshotRepository(),
        pages=InMemoryMenuPageVersionRepository(
            foods=foods,
            offerings=offerings,
            profiles=profiles,
        ),
        foods=foods,
        offerings=offerings,
        profiles=profiles,
        quarantine=InMemoryQuarantineRepository(),
        clock=FixedClock(),
        ids=SequentialIds(),
    )
    return IngestStacksUseCase(deps), deps


@pytest.fixture
def lunch_command():
    from nutrition_agent.application.ports import IngestCommand

    return IngestCommand(service_date=SERVICE_DATE, meal_periods=[MealPeriod.LUNCH])
