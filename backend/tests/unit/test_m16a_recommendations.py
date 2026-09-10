from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest

from nutrition_agent.application.server_inputs import PRODUCTION_SERVER_CONFIGURATION
from nutrition_agent.db.in_memory_repos import InMemoryNextMealRecommendationRepository
from nutrition_agent.domain.health.trend import BodyMassObservation
from nutrition_agent.domain.next_meal import (
    NextMealStatus,
    allocate_remaining_targets,
    build_next_meal_artifact,
    recommendation_from_artifact,
)
from nutrition_agent.domain.nutrition.ledger import (
    DailyNutritionLedger,
    DailyNutritionTarget,
    NutritionCompleteness,
)
from nutrition_agent.domain.nutrition.targets import GoalKind
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion
from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.planning.menu_view import MenuDayView, OfferingView, PeriodMenu
from nutrition_agent.domain.planning.policy import PlannerPolicy, SlotPolicy
from nutrition_agent.domain.planning.schedule import (
    BlockKind,
    DaySchedule,
    ScheduleBlock,
    Weekday,
    WeeklySchedule,
)
from nutrition_agent.domain.protein_target import (
    OWNER_PROTEIN_TARGET_POLICY_V1,
    build_protein_target_proposal,
)
from nutrition_agent.domain.stacks.entities import MealPeriod, NutrientKey, NutritionSourceState
from tests.unit.planning_helpers import make_offering

DAY = date(2026, 9, 5)
NOW = datetime(2026, 9, 6, 3, tzinfo=UTC)  # 23:00 America/New_York
USER_ID = UUID(int=1)
TARGET_ID = UUID(int=2)


def test_owner_protein_policy_is_decimal_deterministic_and_evidence_pinned() -> None:
    body_mass = BodyMassObservation(
        sample_uuid=UUID(int=3),
        value_kg=Decimal("160.0") * Decimal("0.45359237"),
        measured_at=datetime(2026, 9, 5, 12, tzinfo=UTC),
    )
    target = TargetPolicyVersion(
        version_id=TARGET_ID,
        user_id=USER_ID,
        policy_version="prior.v1",
        goals_jsonb=[
            {"nutrient": "calories_kcal", "kind": "target", "value": "2200", "weight": "1"}
        ],
        payload_sha256="a" * 64,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )

    first = build_protein_target_proposal(
        proposal_id=UUID(int=4),
        user_id=USER_ID,
        prior_target=target,
        body_mass=body_mass,
        generated_at=NOW,
        policy=OWNER_PROTEIN_TARGET_POLICY_V1,
    )
    replay = build_protein_target_proposal(
        proposal_id=UUID(int=5),
        user_id=USER_ID,
        prior_target=target,
        body_mass=body_mass,
        generated_at=NOW,
        policy=OWNER_PROTEIN_TARGET_POLICY_V1,
    )

    assert first.proposed_protein_g == Decimal("128")
    assert first.target_kind is GoalKind.FLOOR
    assert first.evidence_digest == replay.evidence_digest
    assert first.calculation_payload["body_mass"] == {
        "measured_at": "2026-09-05T12:00:00+00:00",
        "sample_uuid": str(body_mass.sample_uuid),
        "value_kg": str(body_mass.value_kg),
    }
    assert first.calculation_payload["max_evidence_age_days"] == 7


def test_remaining_opportunity_policy_does_not_chase_full_day_at_lunch() -> None:
    allocated = allocate_remaining_targets(
        remaining_calories=Decimal("1201"),
        remaining_protein_g=Decimal("80"),
        protein_kind=GoalKind.FLOOR,
        opportunity_count=2,
    )
    assert allocated.goals[NutrientKey.CALORIES_KCAL].value == Decimal("600")
    assert allocated.goals[NutrientKey.PROTEIN_G].value == Decimal("40.0")
    assert allocated.goals[NutrientKey.PROTEIN_G].kind is GoalKind.FLOOR

    final = allocate_remaining_targets(
        remaining_calories=Decimal("1201"),
        remaining_protein_g=Decimal("80"),
        protein_kind=GoalKind.FLOOR,
        opportunity_count=1,
    )
    assert final.goals[NutrientKey.CALORIES_KCAL].value == Decimal("1201")

    satisfied_floor = allocate_remaining_targets(
        remaining_calories=Decimal("0.1"),
        remaining_protein_g=Decimal("0"),
        protein_kind=GoalKind.FLOOR,
        opportunity_count=2,
    )
    assert satisfied_floor.goals[NutrientKey.CALORIES_KCAL].value == Decimal("1")
    assert satisfied_floor.goals[NutrientKey.PROTEIN_G].kind is GoalKind.FLOOR
    assert satisfied_floor.goals[NutrientKey.PROTEIN_G].weight == Decimal("0")


def test_next_meal_selects_and_allocates_across_only_eligible_opportunities() -> None:
    decision_at = datetime(2026, 9, 5, 15, tzinfo=UTC)  # 11:00 America/New_York
    schedule = WeeklySchedule(
        version="schedule.v1",
        timezone="America/New_York",
        days={
            Weekday.SATURDAY: DaySchedule(
                weekday=Weekday.SATURDAY,
                blocks=(
                    ScheduleBlock(
                        kind=BlockKind.STACKS_MEAL,
                        start=time(12),
                        end=time(13),
                        label="lunch",
                        meal_context=MealContext.LUNCH,
                    ),
                    ScheduleBlock(
                        kind=BlockKind.STACKS_MEAL,
                        start=time(18),
                        end=time(19),
                        label="dinner",
                        meal_context=MealContext.DINNER,
                    ),
                ),
            )
        },
    )
    offering = cast(OfferingView, make_offering(1, calories="600", protein="40").build())
    menu = MenuDayView(
        service_date=DAY,
        periods={
            MealPeriod.LUNCH: PeriodMenu(offerings=(offering,), explicitly_empty=False),
            MealPeriod.DINNER: PeriodMenu(offerings=(offering,), explicitly_empty=False),
        },
        fetched_at=decision_at,
        snapshot_sha256="b" * 64,
    )
    target = DailyNutritionTarget(
        policy_version_id=TARGET_ID,
        policy_version="target.v1",
        calories_kcal=Decimal("2200"),
        calories_goal_kind="target",
        protein_g=Decimal("119"),
        protein_goal_kind="floor",
    )
    ledger = DailyNutritionLedger(
        local_date=DAY,
        timezone="America/New_York",
        target=target,
        consumed_item_count=1,
        known_calories_consumed=Decimal("1000"),
        known_protein_g_consumed=Decimal("39"),
        remaining_known_calories=Decimal("1200"),
        remaining_known_protein_g=Decimal("80"),
        nutrition_completeness=NutritionCompleteness.COMPLETE,
        authorities=(),
        unknown_nutrients=(),
        consumed_items=(),
        reason_codes=(),
    )
    planner = PlannerPolicy(
        version="planner.v1",
        context_period={
            MealContext.LUNCH: MealPeriod.LUNCH,
            MealContext.DINNER: MealPeriod.DINNER,
        },
        slot_shares={},
    )

    status, reasons, artifact = build_next_meal_artifact(
        local_date=DAY,
        timezone="America/New_York",
        decision_at=decision_at,
        ledger=ledger,
        schedule=schedule,
        exceptions=(),
        menu=menu,
        planner_policy=planner,
        slot_policies={},
        configurable_meal_definitions=(),
        target_policy_version_id=TARGET_ID,
        target_policy_version="target.v1",
    )

    assert status is NextMealStatus.RECOMMENDED
    assert reasons == ()
    assert artifact["selected_opportunity"] == {
        "context": "lunch",
        "menu_period": "Lunch",
        "window": ["12:00:00", "13:00:00"],
    }
    assert artifact["allocated_targets"] == {
        "calories_kcal": "600",
        "calories_goal_kind": "target",
        "protein_g": "40.0",
        "protein_goal_kind": "floor",
        "protein_scoring_active": True,
        "opportunity_count": 2,
        "rule": "equal_share_across_remaining_opportunities_final_takes_remainder",
    }
    assert cast(dict[str, object], artifact["selected"])["candidate_id"] == "lunch-001"

    lunch_only_status, _, lunch_only = build_next_meal_artifact(
        local_date=DAY,
        timezone="America/New_York",
        decision_at=decision_at,
        ledger=ledger,
        schedule=schedule,
        exceptions=(),
        menu=replace(
            menu,
            periods={
                MealPeriod.LUNCH: PeriodMenu(offerings=(offering,), explicitly_empty=False),
                MealPeriod.DINNER: PeriodMenu(offerings=(), explicitly_empty=True),
            },
        ),
        planner_policy=planner,
        slot_policies={},
        configurable_meal_definitions=(),
        target_policy_version_id=TARGET_ID,
        target_policy_version="target.v1",
    )
    assert lunch_only_status is NextMealStatus.RECOMMENDED
    assert cast(dict[str, object], lunch_only["allocated_targets"])["opportunity_count"] == 1
    assert cast(dict[str, object], lunch_only["allocated_targets"])["calories_kcal"] == "1200"

    dinner_only_status, _, dinner_only = build_next_meal_artifact(
        local_date=DAY,
        timezone="America/New_York",
        decision_at=decision_at,
        ledger=ledger,
        schedule=schedule,
        exceptions=(),
        menu=replace(
            menu,
            periods={
                MealPeriod.LUNCH: PeriodMenu(offerings=(), explicitly_empty=True),
                MealPeriod.DINNER: PeriodMenu(offerings=(offering,), explicitly_empty=False),
            },
        ),
        planner_policy=planner,
        slot_policies={},
        configurable_meal_definitions=(),
        target_policy_version_id=TARGET_ID,
        target_policy_version="target.v1",
    )
    assert dinner_only_status is NextMealStatus.RECOMMENDED
    assert cast(dict[str, object], dinner_only["selected_opportunity"])["context"] == ("dinner")
    assert cast(dict[str, object], dinner_only["allocated_targets"])["opportunity_count"] == 1

    none_status, none_reasons, none = build_next_meal_artifact(
        local_date=DAY,
        timezone="America/New_York",
        decision_at=decision_at,
        ledger=ledger,
        schedule=schedule,
        exceptions=(),
        menu=replace(
            menu,
            periods={
                MealPeriod.LUNCH: PeriodMenu(offerings=(), explicitly_empty=True),
                MealPeriod.DINNER: PeriodMenu(offerings=(), explicitly_empty=True),
            },
        ),
        planner_policy=planner,
        slot_policies={},
        configurable_meal_definitions=(),
        target_policy_version_id=TARGET_ID,
        target_policy_version="target.v1",
    )
    assert none_status is NextMealStatus.NO_ELIGIBLE_CANDIDATE
    assert none_reasons == ("empty_menu_period",)
    assert none["status"] == "no_eligible_candidate"

    inside_status, _, inside = build_next_meal_artifact(
        local_date=DAY,
        timezone="America/New_York",
        decision_at=datetime(2026, 9, 5, 16, 30, tzinfo=UTC),
        ledger=ledger,
        schedule=schedule,
        exceptions=(),
        menu=menu,
        planner_policy=planner,
        slot_policies={},
        configurable_meal_definitions=(),
        target_policy_version_id=TARGET_ID,
        target_policy_version="target.v1",
    )
    assert inside_status is NextMealStatus.RECOMMENDED
    assert cast(dict[str, object], inside["allocated_targets"])["opportunity_count"] == 2

    dinner_status, _, dinner = build_next_meal_artifact(
        local_date=DAY,
        timezone="America/New_York",
        decision_at=datetime(2026, 9, 5, 17, 30, tzinfo=UTC),
        ledger=ledger,
        schedule=schedule,
        exceptions=(),
        menu=menu,
        planner_policy=planner,
        slot_policies={},
        configurable_meal_definitions=(),
        target_policy_version_id=TARGET_ID,
        target_policy_version="target.v1",
    )
    assert dinner_status is NextMealStatus.RECOMMENDED
    assert cast(dict[str, object], dinner["selected_opportunity"])["context"] == "dinner"
    assert cast(dict[str, object], dinner["allocated_targets"])["calories_kcal"] == "1200"

    stale_status, stale_reasons, _ = build_next_meal_artifact(
        local_date=DAY,
        timezone="America/New_York",
        decision_at=decision_at,
        ledger=ledger,
        schedule=schedule,
        exceptions=(),
        menu=replace(menu, fetched_at=decision_at - timedelta(days=2)),
        planner_policy=planner,
        slot_policies={},
        configurable_meal_definitions=(),
        target_policy_version_id=TARGET_ID,
        target_policy_version="target.v1",
    )
    assert stale_status is NextMealStatus.STALE_MENU_DATA
    assert stale_reasons == ("menu_snapshot_stale",)

    mismatch_status, mismatch_reasons, _ = build_next_meal_artifact(
        local_date=DAY,
        timezone="America/New_York",
        decision_at=decision_at,
        ledger=ledger,
        schedule=schedule,
        exceptions=(),
        menu=replace(menu, service_date=DAY - timedelta(days=1)),
        planner_policy=planner,
        slot_policies={},
        configurable_meal_definitions=(),
        target_policy_version_id=TARGET_ID,
        target_policy_version="target.v1",
    )
    assert mismatch_status is NextMealStatus.MENU_DATA_UNAVAILABLE
    assert mismatch_reasons == ("menu_service_date_mismatch",)


@pytest.mark.parametrize(
    ("target", "expected_status", "expected_reason"),
    (
        (
            DailyNutritionTarget(
                policy_version_id=TARGET_ID,
                policy_version="target.v1",
                calories_kcal=None,
                calories_goal_kind=None,
                protein_g=Decimal("119"),
                protein_goal_kind="floor",
            ),
            NextMealStatus.NO_APPROVED_CALORIE_TARGET,
            "approved_calorie_target_required",
        ),
        (
            DailyNutritionTarget(
                policy_version_id=TARGET_ID,
                policy_version="target.v1",
                calories_kcal=Decimal("2200"),
                calories_goal_kind="target",
                protein_g=None,
                protein_goal_kind=None,
            ),
            NextMealStatus.NO_APPROVED_PROTEIN_TARGET,
            "approved_protein_target_required",
        ),
        (
            DailyNutritionTarget(
                policy_version_id=TARGET_ID,
                policy_version="target.v1",
                calories_kcal=Decimal("2200"),
                calories_goal_kind="target",
                protein_g=Decimal("119"),
                protein_goal_kind="advisory",
            ),
            NextMealStatus.UNSUPPORTED_TARGET_SEMANTICS,
            "calorie_target_and_protein_target_or_floor_required",
        ),
    ),
)
def test_missing_required_target_is_a_specific_typed_failure(
    target: DailyNutritionTarget,
    expected_status: NextMealStatus,
    expected_reason: str,
) -> None:
    ledger = DailyNutritionLedger(
        local_date=DAY,
        timezone="America/New_York",
        target=target,
        consumed_item_count=0,
        known_calories_consumed=Decimal(0),
        known_protein_g_consumed=Decimal(0),
        remaining_known_calories=(
            target.calories_kcal if target.calories_kcal is not None else None
        ),
        remaining_known_protein_g=(target.protein_g if target.protein_g is not None else None),
        nutrition_completeness=NutritionCompleteness.COMPLETE,
        authorities=(),
        unknown_nutrients=(),
        consumed_items=(),
        reason_codes=(),
    )
    status, reasons, _ = build_next_meal_artifact(
        local_date=DAY,
        timezone="America/New_York",
        decision_at=NOW,
        ledger=ledger,
        schedule=WeeklySchedule(version="schedule.v1", timezone="America/New_York", days={}),
        exceptions=(),
        menu=MenuDayView(
            service_date=DAY,
            periods={},
            fetched_at=NOW,
            snapshot_sha256="b" * 64,
        ),
        planner_policy=PlannerPolicy(version="planner.v1", context_period={}, slot_shares={}),
        slot_policies={},
        configurable_meal_definitions=(),
        target_policy_version_id=TARGET_ID,
        target_policy_version="target.v1",
    )
    assert status is expected_status
    assert reasons == (expected_reason,)


def test_next_meal_reuses_policy_gated_estimated_lane() -> None:
    decision_at = datetime(2026, 9, 5, 15, tzinfo=UTC)
    schedule = WeeklySchedule(
        version="training-context.v1+meal-context.v1+training-day.v1+qualifying_workout",
        timezone="America/New_York",
        days={
            Weekday.SATURDAY: DaySchedule(
                weekday=Weekday.SATURDAY,
                blocks=(
                    ScheduleBlock(
                        kind=BlockKind.STACKS_MEAL,
                        start=time(12),
                        end=time(13),
                        label="post-workout lunch",
                        meal_context=MealContext.POST_WORKOUT_LUNCH,
                    ),
                ),
            )
        },
    )
    cyo = cast(
        OfferingView,
        make_offering(91, name="CYO Halal Bowl", with_profile=False).build(),
    )
    cyo = replace(
        cyo,
        nutrition_source_state=NutritionSourceState.SOURCE_PLACEHOLDER,
        nutrition_snapshot_sha256="placeholder-sha",
    )
    target = DailyNutritionTarget(
        policy_version_id=TARGET_ID,
        policy_version="target.v1",
        calories_kcal=Decimal("2200"),
        calories_goal_kind="target",
        protein_g=Decimal("119"),
        protein_goal_kind="floor",
    )
    ledger = DailyNutritionLedger(
        local_date=DAY,
        timezone="America/New_York",
        target=target,
        consumed_item_count=0,
        known_calories_consumed=Decimal(0),
        known_protein_g_consumed=Decimal(0),
        remaining_known_calories=Decimal("2200"),
        remaining_known_protein_g=Decimal("119"),
        nutrition_completeness=NutritionCompleteness.COMPLETE,
        authorities=(),
        unknown_nutrients=(),
        consumed_items=(),
        reason_codes=("no_consumption",),
    )
    config = PRODUCTION_SERVER_CONFIGURATION
    status, _, artifact = build_next_meal_artifact(
        local_date=DAY,
        timezone="America/New_York",
        decision_at=decision_at,
        ledger=ledger,
        schedule=schedule,
        exceptions=(),
        menu=MenuDayView(
            service_date=DAY,
            periods={MealPeriod.LUNCH: PeriodMenu(offerings=(cyo,), explicitly_empty=False)},
            fetched_at=decision_at,
            snapshot_sha256="d" * 64,
            campus_id=50,
        ),
        planner_policy=config.planner_policy,
        slot_policies=dict(config.slot_policies),
        configurable_meal_definitions=tuple(config.configurable_meal_definitions),
        target_policy_version_id=TARGET_ID,
        target_policy_version="target.v1",
    )
    assert status is NextMealStatus.RECOMMENDED
    selected = cast(dict[str, object], artifact["selected"])
    assert selected["candidate_kind"] == "configurable_estimate"
    assert cast(dict[str, object], artifact["selected_opportunity"])["context"] == (
        "post_workout_lunch"
    )
    assert "training-day.v1" in str(artifact["schedule_version"])


def test_elapsed_schedule_is_typed_fail_closed_and_persistable() -> None:
    schedule = WeeklySchedule(
        version="schedule.v1",
        timezone="America/New_York",
        days={
            Weekday.SATURDAY: DaySchedule(
                weekday=Weekday.SATURDAY,
                blocks=(
                    ScheduleBlock(
                        kind=BlockKind.STACKS_MEAL,
                        start=time(17),
                        end=time(18),
                        label="dinner",
                        meal_context=MealContext.DINNER,
                    ),
                ),
            )
        },
    )
    planner = PlannerPolicy(
        version="planner.v1",
        context_period={MealContext.DINNER: MealPeriod.DINNER},
        slot_shares={},
    )
    target = DailyNutritionTarget(
        policy_version_id=TARGET_ID,
        policy_version="target.v1",
        calories_kcal=Decimal("2200"),
        calories_goal_kind="target",
        protein_g=Decimal("119"),
        protein_goal_kind="floor",
    )
    ledger = DailyNutritionLedger(
        local_date=DAY,
        timezone="America/New_York",
        target=target,
        consumed_item_count=0,
        known_calories_consumed=Decimal(0),
        known_protein_g_consumed=Decimal(0),
        remaining_known_calories=Decimal("2200"),
        remaining_known_protein_g=Decimal("119"),
        nutrition_completeness=NutritionCompleteness.COMPLETE,
        authorities=(),
        unknown_nutrients=(),
        consumed_items=(),
        reason_codes=("no_consumption",),
    )
    incomplete_status, incomplete_reasons, incomplete_artifact = build_next_meal_artifact(
        local_date=DAY,
        timezone="America/New_York",
        decision_at=NOW,
        ledger=replace(ledger, nutrition_completeness=NutritionCompleteness.PARTIAL),
        schedule=schedule,
        exceptions=(),
        menu=MenuDayView(
            service_date=DAY,
            periods={MealPeriod.DINNER: PeriodMenu(offerings=(), explicitly_empty=True)},
            fetched_at=NOW,
            snapshot_sha256="b" * 64,
        ),
        planner_policy=planner,
        slot_policies={MealContext.DINNER: SlotPolicy(MealContext.DINNER)},
        configurable_meal_definitions=(),
        target_policy_version_id=TARGET_ID,
        target_policy_version="target.v1",
    )
    assert incomplete_status is NextMealStatus.INCOMPLETE_LEDGER_NUTRITION
    assert incomplete_reasons == ("consumed_nutrition_not_complete",)
    incomplete_ledger = cast(dict[str, object], incomplete_artifact["ledger"])
    assert incomplete_ledger["nutrition_completeness"] == "partial"
    assert incomplete_ledger["remaining_calories"] == "2200"

    no_target_status, no_target_reasons, _ = build_next_meal_artifact(
        local_date=DAY,
        timezone="America/New_York",
        decision_at=NOW,
        ledger=replace(ledger, target=None),
        schedule=schedule,
        exceptions=(),
        menu=MenuDayView(
            service_date=DAY,
            periods={MealPeriod.DINNER: PeriodMenu(offerings=(), explicitly_empty=True)},
            fetched_at=NOW,
            snapshot_sha256="b" * 64,
        ),
        planner_policy=planner,
        slot_policies={MealContext.DINNER: SlotPolicy(MealContext.DINNER)},
        configurable_meal_definitions=(),
        target_policy_version_id=TARGET_ID,
        target_policy_version="target.v1",
    )
    assert no_target_status is NextMealStatus.NO_APPROVED_TARGET_POLICY
    assert no_target_reasons == ("approved_target_required",)

    status, reasons, artifact = build_next_meal_artifact(
        local_date=DAY,
        timezone="America/New_York",
        decision_at=NOW,
        ledger=ledger,
        schedule=schedule,
        exceptions=(),
        menu=MenuDayView(
            service_date=DAY,
            periods={MealPeriod.DINNER: PeriodMenu(offerings=(), explicitly_empty=True)},
            fetched_at=NOW,
            snapshot_sha256="b" * 64,
        ),
        planner_policy=planner,
        slot_policies={MealContext.DINNER: SlotPolicy(MealContext.DINNER)},
        configurable_meal_definitions=(),
        target_policy_version_id=TARGET_ID,
        target_policy_version="target.v1",
    )
    assert status is NextMealStatus.NO_REMAINING_MEAL_OPPORTUNITY
    assert reasons == ("all_stacks_windows_elapsed",)

    value = recommendation_from_artifact(
        recommendation_id=UUID(int=10),
        user_id=USER_ID,
        client_request_id=UUID(int=11),
        local_date=DAY,
        timezone="America/New_York",
        decision_at=NOW,
        target_policy_version_id=TARGET_ID,
        status=status,
        reason_codes=reasons,
        artifact=artifact,
    )
    repository = InMemoryNextMealRecommendationRepository()
    assert repository.save(value).created is True
    assert repository.save(value).created is False
    later_recalculation = replace(
        value,
        recommendation_id=UUID(int=12),
        decision_at=NOW.replace(hour=4),
        artifact_sha256="c" * 64,
    )
    replay = repository.save(later_recalculation)
    assert replay.created is False
    assert replay.recommendation == value
    assert repository.find_by_client_request_id(USER_ID, UUID(int=11)) == value
    assert repository.latest(USER_ID) == value
