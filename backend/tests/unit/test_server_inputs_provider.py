"""M7 Step 1 tests for deterministic server-side input assembly."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from nutrition_agent.application.daily_plan import (
    DailyPlanInputs,
    compute_inputs_fingerprint,
)
from nutrition_agent.application.ports import ResolvedMenuDay
from nutrition_agent.application.server_inputs import (
    CANONICAL_M6_DEMO_CONFIGURATION,
    CANONICAL_M6_DEMO_TARGETS,
    PRODUCTION_SERVER_CONFIGURATION,
    DefaultServerInputsProvider,
)
from nutrition_agent.db.in_memory_repos import InMemoryMenuDayReadRepository
from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.planning.menu_view import MenuDayView, OfferingView, PeriodMenu
from nutrition_agent.domain.planning.schedule import BlockKind, Weekday
from nutrition_agent.domain.stacks.entities import MealPeriod, NutrientKey
from tests.unit.planning_helpers import make_offering

PLAN_DATE = date(2026, 8, 21)
USER_ID = UUID(int=42)
TARGET_POLICY_VERSION_ID = UUID(int=777)


def _resolved_menu(
    *, snapshot_sha256: str = "provider-menu-sha", food_number: int = 1
) -> ResolvedMenuDay:
    offering = cast(OfferingView, make_offering(food_number).build())
    menu = MenuDayView(
        service_date=PLAN_DATE,
        periods={MealPeriod.LUNCH: PeriodMenu(offerings=(offering,))},
        fetched_at=datetime(2026, 8, 21, 6, 0, tzinfo=UTC),
        snapshot_sha256=snapshot_sha256,
    )
    return ResolvedMenuDay(
        menu=menu,
        offering_profile_ids={str(offering.offering_id): UUID(int=5000 + food_number)},
    )


def _provider(resolved: ResolvedMenuDay) -> DefaultServerInputsProvider:
    return DefaultServerInputsProvider(
        menu_days=InMemoryMenuDayReadRepository(days={PLAN_DATE: resolved}),
        configuration=CANONICAL_M6_DEMO_CONFIGURATION,
    )


def _build(provider: DefaultServerInputsProvider) -> DailyPlanInputs:
    return provider.build(
        user_id=USER_ID,
        requested_for_date=PLAN_DATE,
        timezone="America/New_York",
        targets=CANONICAL_M6_DEMO_TARGETS,
        target_policy_version_id=TARGET_POLICY_VERSION_ID,
    )


def test_same_resolved_values_produce_equivalent_inputs_and_fingerprint() -> None:
    resolved = _resolved_menu()
    actual = _build(_provider(resolved))
    expected = DailyPlanInputs(
        user_id=USER_ID,
        requested_for_date=PLAN_DATE,
        timezone="America/New_York",
        schedule=CANONICAL_M6_DEMO_CONFIGURATION.schedule,
        exceptions=CANONICAL_M6_DEMO_CONFIGURATION.schedule_exceptions,
        menu=resolved.menu,
        policy=CANONICAL_M6_DEMO_CONFIGURATION.planner_policy,
        slot_policies=CANONICAL_M6_DEMO_CONFIGURATION.slot_policies,
        targets=CANONICAL_M6_DEMO_TARGETS,
        target_policy_version_id=TARGET_POLICY_VERSION_ID,
        offering_profile_ids=resolved.offering_profile_ids,
    )

    assert actual == expected
    assert compute_inputs_fingerprint(actual) == compute_inputs_fingerprint(expected)


def test_canonical_m6_demo_configuration_is_pinned_and_not_weekly_test() -> None:
    config = CANONICAL_M6_DEMO_CONFIGURATION
    friday = config.schedule.days[Weekday.FRIDAY]
    assert config.schedule.version == "m6-demo.v1"
    assert config.schedule.version != "weekly-test.v1"
    assert config.schedule.timezone == "America/New_York"
    assert config.schedule_exceptions == ()
    assert config.planner_policy.version == "demo-planner.v1"
    assert config.planner_policy.context_period == {
        MealContext.POST_WORKOUT_LUNCH: MealPeriod.LUNCH
    }
    assert len(friday.blocks) == 1
    assert friday.blocks[0].kind is BlockKind.STACKS_MEAL
    assert friday.blocks[0].label == "stacks post-workout lunch"
    assert set(config.slot_policies) == {
        MealContext.POST_WORKOUT_LUNCH,
        MealContext.DINNER,
    }
    assert CANONICAL_M6_DEMO_TARGETS.policy_version == "m6-demo-targets.v1"
    assert CANONICAL_M6_DEMO_TARGETS.goals[NutrientKey.CALORIES_KCAL].value == Decimal("900")
    assert CANONICAL_M6_DEMO_TARGETS.goals[NutrientKey.PROTEIN_G].value == Decimal("50")


def test_default_provider_selects_versioned_production_configuration() -> None:
    default_provider = DefaultServerInputsProvider(
        menu_days=InMemoryMenuDayReadRepository(days={PLAN_DATE: _resolved_menu()})
    )
    assert default_provider.configuration is PRODUCTION_SERVER_CONFIGURATION
    assert default_provider.configuration is not CANONICAL_M6_DEMO_CONFIGURATION
    assert default_provider.configuration.schedule.version == "psh-fall-2026.v1"
    assert default_provider.configuration.planner_policy.version == ("psh-fall-2026-planner.v5")
    assert default_provider.configuration.configurable_meal_definitions


def test_provider_uses_menu_reader_values_without_hard_coded_menu_facts() -> None:
    resolved = _resolved_menu(snapshot_sha256="reader-selected-sha", food_number=9)
    inputs = _build(_provider(resolved))

    assert inputs.menu is resolved.menu
    assert inputs.menu.snapshot_sha256 == "reader-selected-sha"
    assert inputs.menu.periods[MealPeriod.LUNCH].offerings[0].name_normalized == "Food 9"
    assert inputs.offering_profile_ids == resolved.offering_profile_ids


def test_snapshot_provenance_reaches_existing_fingerprint_path_unchanged() -> None:
    first = _build(_provider(_resolved_menu(snapshot_sha256="snapshot-a")))
    second = _build(_provider(_resolved_menu(snapshot_sha256="snapshot-b")))

    assert first.menu.snapshot_sha256 == "snapshot-a"
    assert second.menu.snapshot_sha256 == "snapshot-b"
    assert compute_inputs_fingerprint(first) != compute_inputs_fingerprint(second)


def test_repeated_builds_are_deterministic_and_do_not_mutate_inputs() -> None:
    pins = {"00000000-0000-0000-0000-000000000064": UUID(int=5001)}
    resolved = _resolved_menu()
    resolved_from_mutable_pins = ResolvedMenuDay(menu=resolved.menu, offering_profile_ids=pins)
    repository = InMemoryMenuDayReadRepository(days={PLAN_DATE: resolved_from_mutable_pins})
    provider = DefaultServerInputsProvider(
        menu_days=repository,
        configuration=CANONICAL_M6_DEMO_CONFIGURATION,
    )
    repository_before = dict(repository.days)
    config_before = (
        provider.configuration.schedule,
        provider.configuration.schedule_exceptions,
        dict(provider.configuration.slot_policies),
    )
    pins["later"] = UUID(int=9999)

    first = _build(provider)
    second = _build(provider)

    assert first == second
    assert compute_inputs_fingerprint(first) == compute_inputs_fingerprint(second)
    assert repository.days == repository_before
    assert (
        provider.configuration.schedule,
        provider.configuration.schedule_exceptions,
        dict(provider.configuration.slot_policies),
    ) == config_before
    assert "later" not in (first.offering_profile_ids or {})


def test_decision_relevant_inputs_contain_no_floats() -> None:
    inputs = _build(_provider(_resolved_menu()))

    def assert_no_float(value: object, seen: set[int]) -> None:
        if isinstance(value, float):
            raise AssertionError(f"float found in decision inputs: {value!r}")
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        if is_dataclass(value) and not isinstance(value, type):
            for field_info in fields(value):
                assert_no_float(getattr(value, field_info.name), seen)
        elif isinstance(value, Mapping):
            for key, item in value.items():
                assert_no_float(key, seen)
                assert_no_float(item, seen)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                assert_no_float(item, seen)

    assert_no_float(inputs, set())
