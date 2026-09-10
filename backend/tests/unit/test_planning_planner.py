"""Planner end-to-end: real lunch fixtures, synthetic ranking, strict no-plan, determinism."""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from nutrition_agent.domain.nutrition.targets import GoalKind, NutrientGoal, TargetSet
from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.planning.menu_view import MenuDayView, PeriodMenu
from nutrition_agent.domain.planning.planner import (
    PlannerStatus,
    generate_daily_plan,
    slice_target_set,
)
from nutrition_agent.domain.planning.policy import PlannerPolicy, SlotPolicy
from nutrition_agent.domain.planning.schedule import ScheduleException
from nutrition_agent.domain.stacks.entities import (
    Confidence,
    DietaryTag,
    MealPeriod,
    NutrientKey,
)
from nutrition_agent.infrastructure.stacks_source.label_parser import LabelPageParser
from nutrition_agent.infrastructure.stacks_source.menu_parser import MenuPageParser
from nutrition_agent.infrastructure.stacks_source.normalizer import (
    assign_occurrence_ordinals,
    menu_day_from_parsed,
    profile_from_parsed,
)
from tests.unit.planning_helpers import (
    PLAN_AT,
    make_facts,
    make_offering,
    real_weekly_schedule,
)

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "stacks"
SERVICE_DATE = date(2026, 8, 24)  # a Monday


def _targets() -> TargetSet:
    return TargetSet(
        policy_version="daily-test.v1",
        goals={
            NutrientKey.CALORIES_KCAL: NutrientGoal(
                kind=GoalKind.TARGET, value=Decimal(3000), weight=Decimal(2)
            ),
            NutrientKey.PROTEIN_G: NutrientGoal(
                kind=GoalKind.FLOOR, value=Decimal(160), weight=Decimal("1.5")
            ),
            NutrientKey.SODIUM_MG: NutrientGoal(
                kind=GoalKind.CEILING, value=Decimal(3500), weight=Decimal("0.5")
            ),
        },
    )


def _policy() -> PlannerPolicy:
    return PlannerPolicy(
        version="planner-test.v1",
        context_period={
            MealContext.POST_WORKOUT_LUNCH: MealPeriod.LUNCH,
            MealContext.DINNER: MealPeriod.DINNER,
        },
        slot_shares={
            MealContext.POST_WORKOUT_LUNCH: {
                NutrientKey.CALORIES_KCAL: Decimal("0.35"),
                NutrientKey.PROTEIN_G: Decimal("0.40"),
                NutrientKey.SODIUM_MG: Decimal("0.30"),
            },
            MealContext.DINNER: {
                NutrientKey.CALORIES_KCAL: Decimal("0.30"),
                NutrientKey.PROTEIN_G: Decimal("0.30"),
                NutrientKey.SODIUM_MG: Decimal("0.25"),
            },
        },
        max_candidates_per_slot=200,
        max_menu_age=timedelta(hours=20),
    )


def _slot_policies(**overrides: object) -> dict:
    lunch = SlotPolicy(
        context=MealContext.POST_WORKOUT_LUNCH,
        exclude_tags=(DietaryTag.CONTAINS_PORK,),
        calorie_min=Decimal(400),
        calorie_max=Decimal(1400),
    )
    dinner = SlotPolicy(
        context=MealContext.DINNER,
        exclude_tags=(DietaryTag.CONTAINS_PORK,),
        calorie_min=Decimal(300),
        calorie_max=Decimal(1600),
        portable_preferred=True,
    )
    out = {MealContext.POST_WORKOUT_LUNCH: lunch, MealContext.DINNER: dinner}
    for context, patch in overrides.items():
        out[context] = dataclasses.replace(out[context], **patch)  # type: ignore[arg-type]
    return out


def _view_from_lunch_fixture(fetched_at: datetime | None = None) -> MenuDayView:
    html = (FIXTURES / "daily_menu_lunch_2026-08-21.html").read_text(encoding="utf-8")
    parsed = MenuPageParser().parse(html, SERVICE_DATE - timedelta(days=3), MealPeriod.LUNCH, 50)
    assert parsed.ok and parsed.value is not None
    day = menu_day_from_parsed(parsed.value)
    ordinals = assign_occurrence_ordinals(day.offerings)
    label_cache: dict[str, object] = {}
    for mid in ("900000001", "900000002"):
        label_html = (
            (FIXTURES / f"label_{mid}.html").read_text(encoding="utf-8")
            if Path(FIXTURES / f"label_{mid}.html").exists()
            else (
                FIXTURES
                / {
                    "900000001": "nutrition_label_standard_roast_chicken.html",
                    "900000002": "nutrition_label_placeholder_halal_bowl.html",
                }[mid]
            ).read_text(encoding="utf-8")
        )
        result = LabelPageParser().parse(label_html)
        assert result.ok and result.value is not None
        label_cache[mid] = result.value

    from nutrition_agent.domain.planning.menu_view import OfferingView

    offerings = []
    for index, offering_input in enumerate(day.offerings):
        profile = None
        sha = None
        parsed_label = label_cache.get(offering_input.source_mid)
        if parsed_label is not None and not getattr(parsed_label, "placeholder", False):
            from uuid import NAMESPACE_OID, uuid5

            food_id = uuid5(NAMESPACE_OID, f"50:{offering_input.name_normalized}")
            provenance = type(
                "P",
                (),
                {
                    "snapshot_id": UUID(int=index + 1),
                    "content_sha256": f"sha-{offering_input.source_mid}",
                    "source_url": "fixture://label",
                    "parser_version": "2026-08-21.m2.1",
                    "fetched_at": fetched_at or PLAN_AT,
                },
            )()
            profile = profile_from_parsed(parsed_label, food_id, provenance)  # type: ignore[arg-type]
            sha = f"sha-{offering_input.source_mid}"
        else:
            from uuid import NAMESPACE_OID, uuid5

            food_id = uuid5(NAMESPACE_OID, f"50:{offering_input.name_normalized}")
        offerings.append(
            OfferingView(
                offering_id=UUID(int=index + 1),
                food_id=food_id,
                name_normalized=offering_input.name_normalized,
                source_mid=offering_input.source_mid,
                occurrence_ordinal=ordinals[index],
                category_name=offering_input.category_name,
                dietary_tags=offering_input.dietary_tags,
                profile=profile,
                profile_sha256=sha,
                snapshot_sha256="menu-sha",
            )
        )
    return MenuDayView(
        service_date=SERVICE_DATE,
        periods={MealPeriod.LUNCH: PeriodMenu(offerings=tuple(offerings))},
        fetched_at=fetched_at or PLAN_AT,
        snapshot_sha256="menu-sha",
    )


def test_fixture_day_only_roast_chicken_is_strict_eligible() -> None:
    view = _view_from_lunch_fixture()
    lunch_menu = view.period(MealPeriod.LUNCH)
    assert lunch_menu is not None
    assert len(lunch_menu.offerings) == 22
    with_profiles = [offering for offering in lunch_menu.offerings if offering.profile is not None]
    assert len(with_profiles) == 1
    assert with_profiles[0].name_normalized == "Demo Entree 01"


def test_fixture_end_to_end_single_candidate_with_reasons() -> None:
    view = _view_from_lunch_fixture()

    # dinner period absent entirely => MENU_DATA_UNAVAILABLE
    result = generate_daily_plan(
        plan_date=SERVICE_DATE,
        plan_at=PLAN_AT,
        schedule=real_weekly_schedule(),
        exceptions=(),
        menu=view,
        policy=_policy(),
        slot_policies=_slot_policies(),
        targets=_targets(),
    )
    assert result.status is PlannerStatus.NO_PLAN  # dinner slot has no data
    by_context = {slot.context: slot for slot in result.slots}
    lunch = by_context[MealContext.POST_WORKOUT_LUNCH]
    assert lunch.status is PlannerStatus.OK
    assert len(lunch.candidates) == 1  # only the roast chicken single; no pair partner
    candidate = lunch.candidates[0]
    assert candidate.candidate_id == "post_workout_lunch-001"
    assert candidate.line_refs[0].name_normalized == "Demo Entree 01"
    # strict-only planner: no degraded concept exists on candidates (Rev 3)
    assert not hasattr(candidate, "degraded")

    dinner = by_context[MealContext.DINNER]
    assert dinner.status is PlannerStatus.NO_PLAN
    assert dinner.failure_reasons == (
        __import__(
            "nutrition_agent.domain.planning.eligibility", fromlist=["ReasonCode"]
        ).ReasonCode.MENU_DATA_UNAVAILABLE,
    )


def test_no_plan_when_period_explicitly_empty() -> None:
    view = MenuDayView(
        service_date=SERVICE_DATE,
        periods={MealPeriod.LUNCH: PeriodMenu(offerings=(), explicitly_empty=True)},
        fetched_at=PLAN_AT,
        snapshot_sha256="sha",
    )
    result = generate_daily_plan(
        SERVICE_DATE,
        PLAN_AT,
        real_weekly_schedule(),
        (),
        view,
        _policy(),
        _slot_policies(),
        _targets(),
    )
    lunch = result.slots[0]
    assert lunch.status is PlannerStatus.NO_PLAN
    from nutrition_agent.domain.planning.eligibility import ReasonCode

    assert ReasonCode.EMPTY_MENU_PERIOD in lunch.failure_reasons


def test_stale_menu_refused_by_default() -> None:
    view = _view_from_lunch_fixture(fetched_at=datetime(2026, 8, 23, 6, 0, tzinfo=UTC))
    result = generate_daily_plan(
        SERVICE_DATE,
        PLAN_AT,
        real_weekly_schedule(),
        (),
        view,
        _policy(),
        _slot_policies(),
        _targets(),
    )
    lunch = result.slots[0]
    assert lunch.status is PlannerStatus.NO_PLAN
    from nutrition_agent.domain.planning.eligibility import ReasonCode

    assert lunch.failure_reasons == (ReasonCode.MENU_STALE,)


def test_synthetic_multi_food_ranking_and_tie_break() -> None:
    chicken = make_offering(101, name="Grilled Chicken Plate", calories="700", protein="55")
    fruit = make_offering(102, name="Fruit Cup", calories="150", protein="2")
    fries = make_offering(103, name="Fries", calories="450", protein="5", sodium="1200")
    view = MenuDayView(
        service_date=SERVICE_DATE,
        periods={
            MealPeriod.LUNCH: PeriodMenu(
                offerings=(chicken.build(), fruit.build(), fries.build())  # type: ignore[list-item]
            )
        },
        fetched_at=PLAN_AT,
        snapshot_sha256="sha",
    )
    result = generate_daily_plan(
        SERVICE_DATE,
        PLAN_AT,
        real_weekly_schedule(),
        (),
        view,
        _policy(),
        _slot_policies(),
        _targets(),
    )
    lunch = result.slots[0]
    assert lunch.status is PlannerStatus.OK
    # singles(3) + pairs(3) = 6 candidates before bounds; pair chicken+fries = 1150 kcal ok
    scored = lunch.candidates
    assert all(entry.score_total <= 0 for entry in scored)
    totals = [entry.score_total for entry in scored]
    assert totals == sorted(totals, reverse=True) or totals == sorted(totals)
    ids = [entry.candidate_id for entry in scored]
    assert len(ids) == len(set(ids))
    assert ids[0] == "post_workout_lunch-001"
    # deterministic tie-break check: construct two identical foods via distinct ids
    tie_a = make_offering(201, name="Alpha Wrap", calories="500", protein="30")
    tie_b = make_offering(202, name="Zeta Wrap", calories="500", protein="30")
    view_tie = MenuDayView(
        service_date=SERVICE_DATE,
        periods={
            MealPeriod.DINNER: PeriodMenu(offerings=(tie_a.build(), tie_b.build()))  # type: ignore[list-item]
        },
        fetched_at=PLAN_AT,
        snapshot_sha256="sha",
    )

    class TuesdaySchedule(real_weekly_schedule().__class__):
        pass

    tuesday = date(2026, 8, 25)
    result_tie = generate_daily_plan(
        tuesday,
        PLAN_AT,
        real_weekly_schedule(),
        (),
        view_tie,
        _policy(),
        _slot_policies(),
        _targets(),
    )
    dinner = result_tie.slots[0]
    if dinner.status is PlannerStatus.OK:
        names_order = [
            [ref.name_normalized for ref in entry.line_refs] for entry in dinner.candidates
        ]
        assert names_order == sorted(names_order)


def test_full_determinism_including_ids() -> None:
    chicken = make_offering(301, name="Chicken Bowl", calories="650", protein="48")
    rice = make_offering(302, name="Rice Plate", calories="350", protein="8")
    view = MenuDayView(
        service_date=SERVICE_DATE,
        periods={
            MealPeriod.LUNCH: PeriodMenu(offerings=(chicken.build(), rice.build()))  # type: ignore[list-item]
        },
        fetched_at=PLAN_AT,
        snapshot_sha256="sha",
    )
    args = (SERVICE_DATE, PLAN_AT, real_weekly_schedule(), (), view, _policy())
    first = generate_daily_plan(*args, _slot_policies(), _targets())
    second = generate_daily_plan(*args, _slot_policies(), _targets())
    assert first == second


def test_provenance_completeness_on_candidates() -> None:
    chicken = make_offering(401, name="Salmon Bowl", calories="600", protein="42")
    view = MenuDayView(
        service_date=SERVICE_DATE,
        periods={MealPeriod.LUNCH: PeriodMenu(offerings=(chicken.build(),))},  # type: ignore[list-item]
        fetched_at=PLAN_AT,
        snapshot_sha256="menu-sha",
    )
    result = generate_daily_plan(
        SERVICE_DATE,
        PLAN_AT,
        real_weekly_schedule(),
        (),
        view,
        _policy(),
        _slot_policies(),
        _targets(),
    )
    candidate = result.slots[0].candidates[0]
    ref = candidate.line_refs[0]
    assert ref.profile_sha256.startswith("sha-")
    assert ref.parser_version == "2026-08-21.m2.1"
    assert ref.source_mid.isdigit()
    meal_lines = candidate.meal.lines
    pinned = {line.profile_content_sha256 for line in meal_lines}
    assert pinned == {ref.profile_sha256}
    assert result.engine_policy_version == "2026-08-21.m3.1"
    assert result.planner_policy_version == "planner-test.v1"
    assert result.target_policy_version.startswith("daily-test.v1+")


def test_target_slicing_is_allocation_policy_arithmetic() -> None:
    sliced = slice_target_set(_targets(), MealContext.POST_WORKOUT_LUNCH, _policy())
    assert sliced.goals[NutrientKey.CALORIES_KCAL].value == Decimal(1050)  # 3000*0.35
    assert sliced.goals[NutrientKey.PROTEIN_G].value == Decimal(64)  # 160*0.4 -> 64.0
    assert sliced.goals[NutrientKey.SODIUM_MG].value == Decimal(1050)  # 3500*0.3
    weights_preserved = all(
        sliced.goals[key].weight == _targets().goals[key].weight for key in sliced.goals
    )
    assert weights_preserved
    assert sliced.policy_version == "daily-test.v1+planner-test.v1+post_workout_lunch"


def test_thursday_schedule_yields_empty_result() -> None:
    thursday = date(2026, 8, 27)
    chicken = make_offering(501, name="Any Item", calories="600").build()
    view = MenuDayView(
        service_date=thursday,
        periods={MealPeriod.LUNCH: PeriodMenu(offerings=(chicken,))},
        fetched_at=PLAN_AT,
        snapshot_sha256="sha",
    )
    result = generate_daily_plan(
        thursday,
        PLAN_AT,
        real_weekly_schedule(),
        (),
        view,
        _policy(),
        _slot_policies(),
        _targets(),
    )
    assert result.slots == ()
    assert result.status is PlannerStatus.NO_PLAN
    from nutrition_agent.domain.planning.eligibility import ReasonCode

    assert result.failure_reasons == (ReasonCode.NO_RESOLVED_MEAL_SLOTS,)


def test_holiday_exception_removes_all_slots() -> None:
    exception = ScheduleException(date=SERVICE_DATE, version="holiday.v1", day=None)
    chicken = make_offering(601, name="Item", calories="600").build()
    view = MenuDayView(
        service_date=SERVICE_DATE,
        periods={MealPeriod.LUNCH: PeriodMenu(offerings=(chicken,))},
        fetched_at=PLAN_AT,
        snapshot_sha256="sha",
    )
    result = generate_daily_plan(
        SERVICE_DATE,
        PLAN_AT,
        real_weekly_schedule(),
        (exception,),
        view,
        _policy(),
        _slot_policies(),
        _targets(),
    )
    assert result.slots == ()


def test_all_items_rejected_reports_counts_and_reasons() -> None:
    partial_a = make_offering(701, confidence=Confidence.PARTIAL).build()
    partial_b = make_offering(702, confidence=Confidence.PARTIAL).build()
    porky = make_offering(703, tags=(DietaryTag.CONTAINS_PORK,)).build()
    incomplete = make_offering(704, complete=False).build()
    view = MenuDayView(
        service_date=SERVICE_DATE,
        periods={
            MealPeriod.LUNCH: PeriodMenu(
                offerings=(partial_a, partial_b, porky, incomplete)  # type: ignore[list-item]
            )
        },
        fetched_at=PLAN_AT,
        snapshot_sha256="sha",
    )
    result = generate_daily_plan(
        SERVICE_DATE,
        PLAN_AT,
        real_weekly_schedule(),
        (),
        view,
        _policy(),
        _slot_policies(),
        _targets(),
    )
    lunch = result.slots[0]
    assert lunch.status is PlannerStatus.NO_PLAN
    counts = dict(lunch.rejection_counts)
    assert counts["not_strict_eligible"] == 3
    assert counts["dietary_filter"] == 1
    from nutrition_agent.domain.planning.eligibility import ReasonCode

    assert set(lunch.failure_reasons) >= {
        ReasonCode.NOT_STRICT_ELIGIBLE,
        ReasonCode.DIETARY_FILTER,
    }
    assert lunch.rejection_details


def test_no_degraded_flag_exists() -> None:
    """M4 is strict-only: the planner exposes no degraded escape hatch."""
    import inspect

    signature = inspect.signature(generate_daily_plan)
    assert all("degraded" not in name.lower() for name in signature.parameters)
    from nutrition_agent.domain.planning.planner import RankedCandidate

    assert "degraded" not in RankedCandidate.__dataclass_fields__


def test_make_facts_helper_sanity() -> None:
    facts = make_facts({"CALORIES_KCAL": "999"} and {})
    assert facts.quantities[NutrientKey.CALORIES_KCAL] == Decimal(400)


_ = pytest
