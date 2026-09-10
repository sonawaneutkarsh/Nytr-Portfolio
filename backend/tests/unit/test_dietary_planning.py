"""M11K explicit no-beef/no-pork planning regressions."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import cast

import pytest

from nutrition_agent.application.server_inputs import (
    PRODUCTION_SERVER_CONFIGURATION,
    PRODUCTION_SERVER_CONFIGURATION_V3,
    PRODUCTION_SERVER_CONFIGURATION_V4,
)
from nutrition_agent.domain.nutrition.targets import GoalKind, NutrientGoal, TargetSet
from nutrition_agent.domain.planning.artifacts import plan_document
from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.planning.dietary import (
    AnimalSource,
    AnimalSourceEvidence,
    DietaryClassification,
    assess_animal_source,
)
from nutrition_agent.domain.planning.eligibility import ReasonCode, dietary_gate
from nutrition_agent.domain.planning.menu_view import MenuDayView, OfferingView, PeriodMenu
from nutrition_agent.domain.planning.planner import generate_daily_plan
from nutrition_agent.domain.planning.policy import OfferingPreference, PlannerPolicy, SlotPolicy
from nutrition_agent.domain.planning.schedule import (
    BlockKind,
    DaySchedule,
    ScheduleBlock,
    Weekday,
    WeeklySchedule,
)
from nutrition_agent.domain.stacks.entities import (
    ComponentStatement,
    DietaryTag,
    MealPeriod,
    NutrientKey,
)
from tests.unit.planning_helpers import make_offering

PLAN_DATE = date(2026, 8, 31)
PLAN_AT = datetime(2026, 8, 31, 12, tzinfo=UTC)


def _offering(
    number: int,
    name: str,
    calories: str = "450",
    *,
    tags: tuple[DietaryTag, ...] = (),
) -> OfferingView:
    return cast(
        OfferingView,
        make_offering(number, name=name, calories=calories, tags=tags).build(),
    )


def _schedule() -> WeeklySchedule:
    slot = ScheduleBlock(
        kind=BlockKind.STACKS_MEAL,
        start=time(12),
        end=time(13),
        label="post-workout lunch",
        meal_context=MealContext.POST_WORKOUT_LUNCH,
    )
    return WeeklySchedule(
        version="m11k-dietary-test.v1",
        timezone="America/New_York",
        days={
            weekday: DaySchedule(
                weekday=weekday,
                blocks=(slot,) if weekday is Weekday.MONDAY else (),
            )
            for weekday in Weekday
        },
    )


def _with_source_text(
    offering: OfferingView,
    *,
    ingredients: str,
    recipe_text: str | None = None,
) -> OfferingView:
    assert offering.profile is not None
    components = (
        (ComponentStatement(component_name="source recipe", text=recipe_text),)
        if recipe_text is not None
        else None
    )
    return replace(
        offering,
        profile=replace(
            offering.profile,
            ingredients_raw=ingredients,
            ingredient_components=components,
        ),
    )


def _targets() -> TargetSet:
    return TargetSet(
        policy_version="m11k-targets.v1",
        goals={
            NutrientKey.CALORIES_KCAL: NutrientGoal(
                kind=GoalKind.TARGET,
                value=Decimal("2000"),
                weight=Decimal("1"),
            )
        },
    )


def _result(
    offerings: tuple[OfferingView, ...],
    *,
    policy: PlannerPolicy | None = None,
):
    selected = policy or PRODUCTION_SERVER_CONFIGURATION.planner_policy
    return generate_daily_plan(
        plan_date=PLAN_DATE,
        plan_at=PLAN_AT,
        schedule=_schedule(),
        exceptions=(),
        menu=MenuDayView(
            service_date=PLAN_DATE,
            periods={MealPeriod.LUNCH: PeriodMenu(offerings=offerings)},
            fetched_at=PLAN_AT,
            snapshot_sha256="m11k-accepted-menu",
            campus_id=50,
        ),
        policy=selected,
        slot_policies={
            MealContext.POST_WORKOUT_LUNCH: SlotPolicy(context=MealContext.POST_WORKOUT_LUNCH)
        },
        targets=_targets(),
    )


def _candidate_names(result) -> tuple[tuple[str, ...], ...]:  # type: ignore[no-untyped-def]
    return tuple(
        tuple(ref.name_normalized for ref in candidate.line_refs)
        for candidate in result.slots[0].candidates
    )


def test_explicit_beef_and_structured_pork_are_hard_excluded() -> None:
    policy = PRODUCTION_SERVER_CONFIGURATION.planner_policy.dietary_policy
    assert policy is not None
    steak = _offering(1, "Southwestern Flank Steak")
    pork = _offering(2, "Italian Panini", tags=(DietaryTag.CONTAINS_PORK,))

    assert dietary_gate(steak, policy).reason is ReasonCode.DIETARY_FILTER  # type: ignore[union-attr]
    assert assess_animal_source(steak, policy).source is AnimalSource.BEEF
    assert dietary_gate(pork, policy).reason is ReasonCode.DIETARY_FILTER  # type: ignore[union-attr]
    assert assess_animal_source(pork, policy).source is AnimalSource.PORK


def test_structured_pork_precedes_conflicting_ingredient_evidence() -> None:
    policy = PRODUCTION_SERVER_CONFIGURATION.planner_policy.dietary_policy
    assert policy is not None
    offering = _with_source_text(
        _offering(3, "Daily Special", tags=(DietaryTag.CONTAINS_PORK,)),
        ingredients="ground beef, seasoning",
    )

    assessment = assess_animal_source(offering, policy)

    assert assessment.source is AnimalSource.PORK
    assert assessment.evidence is AnimalSourceEvidence.STRUCTURED_DIETARY_TAG


def test_explicit_ingredient_beef_and_recipe_pork_are_hard_excluded() -> None:
    policy = PRODUCTION_SERVER_CONFIGURATION.planner_policy.dietary_policy
    assert policy is not None
    beef = _with_source_text(
        _offering(4, "Chef Special"),
        ingredients="rice, ground beef, spices",
    )
    pork = _with_source_text(
        _offering(5, "Roasted Entrée"),
        ingredients="seasoning",
        recipe_text="slow-roasted pork shoulder with vegetables",
    )

    beef_assessment = assess_animal_source(beef, policy)
    pork_assessment = assess_animal_source(pork, policy)

    assert beef_assessment.source is AnimalSource.BEEF
    assert beef_assessment.evidence is AnimalSourceEvidence.EXPLICIT_INGREDIENT_TEXT
    assert dietary_gate(beef, policy) is not None
    assert pork_assessment.source is AnimalSource.PORK
    assert pork_assessment.evidence is AnimalSourceEvidence.EXPLICIT_RECIPE_TEXT
    assert dietary_gate(pork, policy) is not None


def test_cows_milk_is_safe_but_turkey_bacon_is_disallowed_without_being_pork() -> None:
    policy = PRODUCTION_SERVER_CONFIGURATION.planner_policy.dietary_policy
    assert policy is not None
    cows_milk = _with_source_text(
        _offering(6, "Cheese Plate"),
        ingredients="cow's milk, cultures, salt",
    )
    turkey_bacon = _with_source_text(
        _offering(7, "Breakfast Plate"),
        ingredients="turkey bacon, egg, potato",
    )

    assert dietary_gate(cows_milk, policy) is None
    assessment = assess_animal_source(turkey_bacon, policy)
    assert assessment.source is AnimalSource.TURKEY
    assert assessment.classification is DietaryClassification.DISALLOWED_ANIMAL
    assert dietary_gate(turkey_bacon, policy) is not None


@pytest.mark.parametrize(
    ("name", "source"),
    (
        ("Chicken Tender and Ranch Snack Wrap", AnimalSource.CHICKEN),
        ("Buffalo Chicken Wrap", AnimalSource.CHICKEN),
        ("Grilled Salmon", AnimalSource.FISH),
        ("Baked Fish", AnimalSource.FISH),
        ("Cajun Seafood Pasta", AnimalSource.SEAFOOD),
        ("Roasted Corn & Shrimp Chowder", AnimalSource.SHRIMP),
        ("#6 Tuna Salad & American Cheese", AnimalSource.TUNA),
        ("Roast Chicken & Provolone Panini", AnimalSource.CHICKEN),
    ),
)
def test_explicit_allowed_animal_sources_remain_eligible(
    name: str,
    source: AnimalSource,
) -> None:
    policy = PRODUCTION_SERVER_CONFIGURATION.planner_policy.dietary_policy
    assert policy is not None
    offering = _offering(10, name)

    assessment = assess_animal_source(offering, policy)

    assert assessment.source is source
    assert assessment.classification is DietaryClassification.SAFE_ALLOWED_ANIMAL
    assert dietary_gate(offering, policy) is None


def test_buffalo_chicken_source_text_is_chicken_not_buffalo_meat() -> None:
    policy = PRODUCTION_SERVER_CONFIGURATION.planner_policy.dietary_policy
    assert policy is not None
    offering = _with_source_text(
        _offering(18, "Weekly Wrap"),
        ingredients="buffalo chicken, lettuce, ranch",
    )

    assessment = assess_animal_source(offering, policy)

    assert assessment.source is AnimalSource.CHICKEN
    assert assessment.evidence is AnimalSourceEvidence.EXPLICIT_INGREDIENT_TEXT
    assert assessment.classification is DietaryClassification.SAFE_ALLOWED_ANIMAL
    assert dietary_gate(offering, policy) is None


@pytest.mark.parametrize(
    "offering",
    (
        _offering(19, "#8 Veggie", tags=(DietaryTag.MEATLESS,)),
        _offering(20, "Pimento Macaroni and Cheese"),
        _offering(21, "Three Cheese Egg Bite"),
        _offering(22, "Roasted Garlic Mashed Potatoes"),
        _offering(23, "Brown Rice"),
        _offering(24, "Fresh Fruit Cup"),
        _offering(25, "Roasted Vegetables"),
        _offering(26, "Goat Cheese Salad"),
    ),
)
def test_nonmeat_and_neutral_foods_remain_eligible(offering: OfferingView) -> None:
    policy = PRODUCTION_SERVER_CONFIGURATION.planner_policy.dietary_policy
    assert policy is not None

    assessment = assess_animal_source(offering, policy)

    assert assessment.classification is DietaryClassification.SAFE_NONMEAT
    assert dietary_gate(offering, policy) is None


@pytest.mark.parametrize(
    ("name", "source"),
    (
        ("Southwestern Flank Steak", AnimalSource.BEEF),
        ("Turkey Burger", AnimalSource.TURKEY),
        ("Lamb Curry", AnimalSource.LAMB),
        ("Mutton Stew", AnimalSource.MUTTON),
        ("Goat Curry", AnimalSource.GOAT),
        ("Bison Roast", AnimalSource.OTHER_MAMMAL),
    ),
)
def test_disallowed_animal_sources_are_rejected(name: str, source: AnimalSource) -> None:
    policy = PRODUCTION_SERVER_CONFIGURATION.planner_policy.dietary_policy
    assert policy is not None
    offering = _offering(26, name)

    assessment = assess_animal_source(offering, policy)

    assert assessment.source is source
    assert assessment.classification is DietaryClassification.DISALLOWED_ANIMAL
    assert dietary_gate(offering, policy) is not None


def test_v5_policy_has_exact_allowed_and_disallowed_animal_sets() -> None:
    policy = PRODUCTION_SERVER_CONFIGURATION.planner_policy.dietary_policy
    assert policy is not None

    assert policy.allowed_animal_sources == frozenset(
        {
            AnimalSource.CHICKEN,
            AnimalSource.FISH,
            AnimalSource.SEAFOOD,
            AnimalSource.SHRIMP,
            AnimalSource.TUNA,
        }
    )
    assert policy.disallowed_animal_sources == frozenset(
        {
            AnimalSource.BEEF,
            AnimalSource.PORK,
            AnimalSource.TURKEY,
            AnimalSource.LAMB,
            AnimalSource.MUTTON,
            AnimalSource.GOAT,
            AnimalSource.OTHER_MAMMAL,
        }
    )


@pytest.mark.parametrize(
    "name",
    (
        "Double Cheese Burger",
        "Double Cheeseburger",
        "Mystery Burger",
        "Bacon Pizza",
        "Ham Sandwich",
        "Sausage Pizza",
        "Meatball Sub",
    ),
)
def test_unresolved_meat_source_fails_closed(name: str) -> None:
    policy = PRODUCTION_SERVER_CONFIGURATION.planner_policy.dietary_policy
    assert policy is not None
    offering = _offering(27, name)

    assessment = assess_animal_source(offering, policy)

    assert assessment.source is AnimalSource.UNKNOWN
    assert assessment.classification is DietaryClassification.UNKNOWN_MEAT_SOURCE
    assert dietary_gate(offering, policy) is not None


def test_preference_cannot_bypass_dietary_gate_and_unsafe_pairs_never_exist() -> None:
    base = PRODUCTION_SERVER_CONFIGURATION.planner_policy
    policy = replace(
        base,
        offering_preferences=(
            OfferingPreference(
                "unsafe.steak",
                frozenset({"Southwestern Flank Steak"}),
                Decimal("100"),
            ),
        ),
    )
    steak = _offering(30, "Southwestern Flank Steak", "800")
    chicken = _offering(31, "Fried Chicken Mashed Potato Bowl", "500")
    macaroni = _offering(32, "Pimento Macaroni and Cheese", "300")
    result = _result((steak, chicken, macaroni), policy=policy)

    names = _candidate_names(result)
    assert names
    assert all("Southwestern Flank Steak" not in candidate for candidate in names)
    assert any(len(candidate) == 2 for candidate in names)
    assert "preference:unsafe.steak" not in {
        key for candidate in result.slots[0].candidates for key in candidate.score_breakdown
    }


def test_physical_lunch_choices_keep_allowed_foods_and_remove_disallowed_sources() -> None:
    offerings = (
        _offering(33, "Chicken Tender and Ranch Snack Wrap", "800"),
        _offering(34, "Buffalo Chicken Wrap", "790"),
        _offering(35, "#8 Veggie", "780", tags=(DietaryTag.MEATLESS,)),
        _offering(36, "Roast Chicken & Provolone Panini", "810"),
        _offering(37, "Turkey Burger", "800"),
        _offering(38, "Southwestern Flank Steak", "800"),
    )

    names = _candidate_names(_result(offerings))
    singles = {candidate[0] for candidate in names if len(candidate) == 1}

    assert {
        "Chicken Tender and Ranch Snack Wrap",
        "Buffalo Chicken Wrap",
        "#8 Veggie",
        "Roast Chicken & Provolone Panini",
    } <= singles
    assert all("Turkey Burger" not in candidate for candidate in names)
    assert all("Southwestern Flank Steak" not in candidate for candidate in names)


def test_physical_dinner_regression_removes_steak_before_deterministic_ranking() -> None:
    offerings = (
        _offering(40, "Fried Chicken Mashed Potato Bowl", "530"),
        _offering(41, "Pimento Macaroni and Cheese", "390"),
        _offering(42, "Southwestern Flank Steak", "430"),
        _offering(43, "Roasted Garlic Mashed Potatoes", "300"),
    )
    first = _result(offerings)
    second = _result(tuple(reversed(offerings)))

    assert _candidate_names(first) == _candidate_names(second)
    assert all("Southwestern Flank Steak" not in candidate for candidate in _candidate_names(first))
    assert any(
        set(candidate) == {"Fried Chicken Mashed Potato Bowl", "Pimento Macaroni and Cheese"}
        for candidate in _candidate_names(first)
    )


def test_historical_v3_has_no_dietary_policy_and_retains_historical_behavior() -> None:
    v3 = PRODUCTION_SERVER_CONFIGURATION_V3.planner_policy
    steak = _offering(50, "Southwestern Flank Steak", "800")

    assert v3.version == "psh-fall-2026-planner.v3"
    assert v3.dietary_policy is None
    assert _candidate_names(_result((steak,), policy=v3)) == (("Southwestern Flank Steak",),)


def test_historical_v4_policy_remains_the_original_beef_pork_denylist() -> None:
    v4 = PRODUCTION_SERVER_CONFIGURATION_V4.planner_policy
    assert v4.version == "psh-fall-2026-planner.v4"
    assert v4.dietary_policy is not None
    assert v4.dietary_policy.version == "owner-no-beef-pork.v1"
    assert v4.dietary_policy.allowed_animal_sources == frozenset()
    assert dietary_gate(_offering(51, "Turkey Burger"), v4.dietary_policy) is None


def test_owner_cyo_availability_is_explicit_allowed_chicken() -> None:
    policy = PRODUCTION_SERVER_CONFIGURATION.planner_policy.dietary_policy
    assert policy is not None
    cyo = replace(_offering(52, "CYO Halal Bowl"), profile=None, profile_sha256=None)

    assessment = assess_animal_source(cyo, policy)

    assert assessment.source is AnimalSource.CHICKEN
    assert assessment.evidence is AnimalSourceEvidence.OWNER_CONFIGURATION
    assert assessment.classification is DietaryClassification.SAFE_ALLOWED_ANIMAL
    assert dietary_gate(cyo, policy) is None


def test_artifact_result_carries_exact_dietary_policy_version() -> None:
    result = _result((_offering(60, "Chicken Tender and Ranch Snack Wrap", "800"),))
    document = plan_document(result)

    assert result.dietary_policy_version == "owner-chicken-seafood-veg.v1"
    assert document["policy_versions"]["dietary"] == "owner-chicken-seafood-veg.v1"  # type: ignore[index]
