"""Offline fixture-mode daily-plan demonstration (M6).

Runs the complete M6 chain against COMMITTED TEST FIXTURES ONLY:

  fixture StacksSource -> ingest use case (manual gate, zero network)
    -> in-memory repositories -> MenuDayView adapter
    -> frozen M4 planner -> M3 byte-stable artifact -> sha256
    -> persisted into in-memory plan repositories.

The output is FIXTURE-BACKED. It does NOT authorize or exercise live Stacks
ingestion, which remains compliance-gated under ADR-010.
"""

from __future__ import annotations

import argparse
import tempfile
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from nutrition_agent.application.daily_plan import (
    DailyPlanInputs,
    GenerateDailyPlanUseCase,
)
from nutrition_agent.application.menu_view_adapter import (
    build_menu_day_view,
    offering_profile_ids,
)
from nutrition_agent.application.ports import ResolvedMenuDay
from nutrition_agent.application.server_inputs import (
    CANONICAL_M6_DEMO_CONFIGURATION,
    CANONICAL_M6_DEMO_TARGETS,
    DefaultServerInputsProvider,
)
from nutrition_agent.db.in_memory_repos import (
    InMemoryFoodRepository,
    InMemoryMenuDayReadRepository,
    InMemoryMenuPageVersionRepository,
    InMemoryOfferingRepository,
    InMemoryPlanRunRepository,
    InMemoryProfileRepository,
    InMemoryQuarantineRepository,
    InMemoryRunRepository,
    InMemorySnapshotRepository,
)
from nutrition_agent.domain.stacks.entities import MealPeriod
from nutrition_agent.infrastructure.compliance_gate import ComplianceGate, IngestionMode
from nutrition_agent.infrastructure.stacks_source.constants import (
    LEGACY_M6_DEMO_PARSER_VERSION,
)

FIXED_NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
SERVICE_DATE = date(2026, 8, 21)


class FixedClock:
    def now(self) -> datetime:
        return FIXED_NOW


class SequentialIds:
    _n = 0

    def new_id(self) -> UUID:
        SequentialIds._n += 1
        return UUID(int=SequentialIds._n)


def seed_repositories(
    fixture_dir: Path,
) -> tuple[object, InMemoryFoodRepository, InMemoryOfferingRepository, InMemoryProfileRepository]:
    from nutrition_agent.application.ingest_stacks import IngestionDeps, IngestStacksUseCase
    from nutrition_agent.application.ports import IngestCommand
    from nutrition_agent.infrastructure.snapshot_store import SnapshotStore
    from nutrition_agent.infrastructure.stacks_source.fixture_source import FixtureStacksSource

    offerings = InMemoryOfferingRepository()
    profiles = InMemoryProfileRepository()
    foods = InMemoryFoodRepository()

    deps = IngestionDeps(
        gate=ComplianceGate(IngestionMode.MANUAL),
        source=FixtureStacksSource(fixture_dir),
        snapshots=SnapshotStore(Path(tempfile.mkdtemp(prefix="m6-demo-snapshots-"))),
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
        parser_version=LEGACY_M6_DEMO_PARSER_VERSION,
    )
    report = IngestStacksUseCase(deps).execute(
        IngestCommand(service_date=SERVICE_DATE, meal_periods=[MealPeriod.LUNCH])
    )
    return report, foods, offerings, profiles


def build_demo_inputs(fixture_dir: Path) -> tuple[DailyPlanInputs, GenerateDailyPlanUseCase]:
    report, foods, offerings, profiles = seed_repositories(fixture_dir)

    from nutrition_agent.domain.stacks.entities import MenuOffering, NutritionProfile

    rows: list[tuple[MenuOffering, NutritionProfile | None]] = []
    food_names: dict[UUID, str] = {}
    for offering_id in list(offerings.offerings):
        offering = offerings.offerings[offering_id]
        profile = profiles.profiles.get(offering.profile_id) if offering.profile_id else None
        name = foods.foods.get(offering.food_id, {}).get("name_normalized", "")
        food_names[offering.food_id] = str(name)
        # The frozen M6 fixture artifact predates accepted non-profile source
        # states. Its sequential test IDs included one quarantine-record ID
        # after each profile-less fixture label. Reconstruct that demo-only
        # sequence so M8 source-state semantics cannot rewrite the M6 golden.
        legacy_offering_id = (
            2 + len(rows) + sum(existing_profile is None for _, existing_profile in rows)
        )
        rows.append((replace(offering, offering_id=UUID(int=legacy_offering_id)), profile))
    del report

    view = build_menu_day_view(
        service_date=SERVICE_DATE,
        fetched_at=FIXED_NOW,
        snapshot_sha256="fixture-menu-sha-2026-08-21-lunch",
        offerings_with_profiles=rows,
        food_names=food_names,
        explicitly_empty_periods=[MealPeriod.DINNER],
    )

    runs_repo = InMemoryPlanRunRepository()
    use_case = GenerateDailyPlanUseCase(runs=runs_repo, clock=FixedClock(), ids=SequentialIds())
    menu_days = InMemoryMenuDayReadRepository(
        days={
            SERVICE_DATE: ResolvedMenuDay(
                menu=view,
                offering_profile_ids=offering_profile_ids([row[0] for row in rows]),
            )
        }
    )
    provider = DefaultServerInputsProvider(
        menu_days=menu_days,
        configuration=CANONICAL_M6_DEMO_CONFIGURATION,
    )
    inputs = provider.build(
        user_id=UUID(int=42),
        requested_for_date=SERVICE_DATE,
        timezone="America/New_York",
        targets=CANONICAL_M6_DEMO_TARGETS,
        target_policy_version_id=None,
    )
    return inputs, use_case


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_fixtures = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "stacks"
    parser.add_argument("--fixtures-dir", type=Path, default=default_fixtures)
    args = parser.parse_args()

    inputs, use_case = build_demo_inputs(args.fixtures_dir)
    outcome = use_case.execute(inputs, plan_at=FIXED_NOW)

    print("=" * 64)
    print("M6 OFFLINE DEMO — FIXTURE DATA ONLY (NO live Stacks traffic)")
    if outcome.replayed and outcome.replay_view is not None:
        print(f"REPLAYED existing plan sha={outcome.replay_view.plan_sha256}")
        return 0
    assert outcome.version is not None and outcome.run is not None
    print(f"plan status          : {outcome.run.status.value}")
    print(f"inputs fingerprint   : {outcome.run.inputs_fingerprint[:16]}...")
    print(f"plan sha256          : {outcome.version.plan_sha256}")
    print(f"items persisted      : {len(outcome.items)}")
    replay = use_case.execute(inputs, plan_at=FIXED_NOW)
    print(f"deterministic replay : {'YES' if replay.replayed else 'NO'}")
    print("=" * 64)
    return 0 if outcome.run.status.value == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
