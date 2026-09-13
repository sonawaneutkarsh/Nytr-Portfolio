"""M11I policy-gated estimated configurable-meal planning regressions."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
from hashlib import sha256
from typing import cast
from uuid import UUID

import pytest

from nutrition_agent.application.consumption import RecordConsumptionUseCase
from nutrition_agent.application.daily_plan import (
    DailyPlanInputs,
    GenerateDailyPlanUseCase,
    compute_inputs_fingerprint,
)
from nutrition_agent.application.server_inputs import (
    PRODUCTION_SERVER_CONFIGURATION,
    PRODUCTION_SERVER_CONFIGURATION_V2,
    PRODUCTION_SERVER_CONFIGURATION_V3,
    PRODUCTION_SERVER_CONFIGURATION_V4,
)
from nutrition_agent.db.in_memory_repos import (
    InMemoryConsumptionRepository,
    InMemoryPlanRunRepository,
)
from nutrition_agent.domain.consumption import ConsumptionState
from nutrition_agent.domain.nutrition.facts import ALL_NUTRIENT_KEYS, NutritionFacts
from nutrition_agent.domain.nutrition.serialization import to_json_bytes
from nutrition_agent.domain.nutrition.targets import GoalKind, NutrientGoal, TargetSet
from nutrition_agent.domain.owner_meal_config import (
    OWNER_CRISPY_CHICKPEA_HALAL_BOWL_DEFINITION,
    OWNER_USUAL_HALAL_BOWL_DEFINITION,
)
from nutrition_agent.domain.planning.artifacts import candidate_document, plan_document
from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.planning.menu_view import MenuDayView, OfferingView, PeriodMenu
from nutrition_agent.domain.planning.planner import (
    CandidateKind,
    PlannerStatus,
    generate_daily_plan,
)
from nutrition_agent.domain.planning.policy import PlannerPolicy, SlotPolicy
from nutrition_agent.domain.planning.schedule import (
    BlockKind,
    DaySchedule,
    ScheduleBlock,
    Weekday,
    WeeklySchedule,
)
from nutrition_agent.domain.stacks.entities import (
    Confidence,
    MealPeriod,
    NutrientKey,
    NutritionSourceState,
)
from tests.unit.planning_helpers import make_offering

PLAN_DATE = date(2026, 8, 31)
PLAN_AT = datetime(2026, 8, 31, 12, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return PLAN_AT


class _Ids:
    def __init__(self) -> None:
        self.value = 10_000

    def new_id(self) -> UUID:
        self.value += 1
        return UUID(int=self.value)


def _schedule() -> WeeklySchedule:
    block = ScheduleBlock(
        kind=BlockKind.STACKS_MEAL,
        start=time(12),
        end=time(13),
        label="post-workout lunch",
        meal_context=MealContext.POST_WORKOUT_LUNCH,
    )
    return WeeklySchedule(
        version="estimated-test-schedule.v1",
        timezone="America/New_York",
        days={
            weekday: DaySchedule(
                weekday=weekday,
                blocks=(block,) if weekday is Weekday.MONDAY else (),
            )
            for weekday in Weekday
        },
    )


def _cyo(number: int = 1) -> OfferingView:
    base = cast(
        OfferingView,
        make_offering(number, name="CYO Halal Bowl", with_profile=False).build(),
    )
    return replace(
        base,
        nutrition_source_state=NutritionSourceState.SOURCE_PLACEHOLDER,
        nutrition_snapshot_sha256=f"placeholder-sha-{number}",
    )


def _strict(number: int, calories: str, name: str = "Strict Plate") -> OfferingView:
    return cast(
        OfferingView,
        make_offering(number, name=name, calories=calories, protein="46").build(),
    )


def _targets(
    calories: str = "1500",
    *,
    extra: tuple[NutrientKey, GoalKind] | None = None,
) -> TargetSet:
    goals = {
        NutrientKey.CALORIES_KCAL: NutrientGoal(
            kind=GoalKind.TARGET,
            value=Decimal(calories),
            weight=Decimal("1"),
        )
    }
    if extra is not None:
        goals[extra[0]] = NutrientGoal(
            kind=extra[1],
            value=Decimal("100"),
            weight=Decimal("1"),
        )
    return TargetSet(policy_version="estimated-targets.v1", goals=goals)


def _result(
    offerings: tuple[OfferingView, ...],
    *,
    policy: PlannerPolicy | None = None,
    targets: TargetSet | None = None,
    definitions=(OWNER_USUAL_HALAL_BOWL_DEFINITION,),  # type: ignore[no-untyped-def]
    campus_id: int | None = 50,
    service_date: date = PLAN_DATE,
):
    selected = policy or PRODUCTION_SERVER_CONFIGURATION_V3.planner_policy
    menu = MenuDayView(
        service_date=service_date,
        periods={MealPeriod.LUNCH: PeriodMenu(offerings=offerings)},
        fetched_at=PLAN_AT,
        snapshot_sha256="accepted-menu-sha",
        campus_id=campus_id,
    )
    return generate_daily_plan(
        plan_date=PLAN_DATE,
        plan_at=PLAN_AT,
        schedule=_schedule(),
        exceptions=(),
        menu=menu,
        policy=selected,
        slot_policies={
            MealContext.POST_WORKOUT_LUNCH: SlotPolicy(context=MealContext.POST_WORKOUT_LUNCH)
        },
        targets=targets or _targets(),
        configurable_meal_definitions=definitions,
    )


def _policy_for(*definitions):  # type: ignore[no-untyped-def]
    base = PRODUCTION_SERVER_CONFIGURATION_V3.planner_policy
    assert base.estimated_meal_policy is not None
    return replace(
        base,
        estimated_meal_policy=replace(
            base.estimated_meal_policy,
            allowlisted_configuration_identities=frozenset(
                definition.allowlist_identity for definition in definitions
            ),
        ),
    )


def _estimated(result):  # type: ignore[no-untyped-def]
    return next(
        candidate
        for candidate in result.slots[0].candidates
        if candidate.candidate_kind is CandidateKind.CONFIGURABLE_ESTIMATE
    )


def test_v3_exact_allowlisted_partial_estimate_is_standalone_and_availability_only() -> None:
    result = _result((_cyo(),))

    assert result.status is PlannerStatus.OK
    candidate = _estimated(result)
    assert candidate.line_refs == ()
    assert candidate.meal.lines == ()
    assert candidate.estimated_details is not None
    assert candidate.estimated_details.availability.profile is None
    assert candidate.meal.totals.confidence is Confidence.ESTIMATED
    assert candidate.meal.totals.quantities[NutrientKey.CALORIES_KCAL] == Decimal("604.27781682500")
    assert candidate.meal.totals.quantities[NutrientKey.PROTEIN_G] == Decimal("45.7386516982500")


def test_v5_keeps_variant_a_eligible_and_variant_b_unresolved() -> None:
    config = PRODUCTION_SERVER_CONFIGURATION
    result = _result(
        (_cyo(),),
        policy=config.planner_policy,
        definitions=config.configurable_meal_definitions,
    )

    estimated = [
        candidate
        for candidate in result.slots[0].candidates
        if candidate.candidate_kind is CandidateKind.CONFIGURABLE_ESTIMATE
    ]
    assert len(estimated) == 1
    assert estimated[0].estimated_details is not None
    assert (
        estimated[0].estimated_details.definition.allowlist_identity
        == OWNER_USUAL_HALAL_BOWL_DEFINITION.allowlist_identity
    )
    assert any(
        OWNER_CRISPY_CHICKPEA_HALAL_BOWL_DEFINITION.definition_id in detail
        and "selected components are unresolved" in detail
        for detail in result.slots[0].rejection_details
    )
    assert sha256(to_json_bytes(plan_document(result))).hexdigest() == (
        "da306c1668c886989d408a36cb4f6d944181c92783a7013c851473981ff0287c"
    )


def test_historical_v3_and_v4_artifact_hashes_remain_frozen() -> None:
    v3 = _result((_cyo(),))
    v4_config = PRODUCTION_SERVER_CONFIGURATION_V4
    v4 = _result(
        (_cyo(),),
        policy=v4_config.planner_policy,
        definitions=v4_config.configurable_meal_definitions,
    )

    assert sha256(to_json_bytes(plan_document(v3))).hexdigest() == (
        "58323f9950695423f8d0ca5578933b8609f0c4f727ca854a53ffa2def4f07c3b"
    )
    assert sha256(to_json_bytes(plan_document(v4))).hexdigest() == (
        "bc84bb0b762d9f82cd890a24b14e90e9f933428fec9f4e3b3f42dbd50a696351"
    )


def test_cyo_can_be_eligible_but_rank_below_the_visible_top_four() -> None:
    result = _result(
        (
            _cyo(),
            _strict(101, "800", "Chicken Tender and Ranch Snack Wrap"),
            _strict(102, "790", "Buffalo Chicken Wrap"),
            _strict(103, "810", "Chicken Tender Club"),
            _strict(104, "780", "Pimento Macaroni and Cheese"),
        ),
        policy=PRODUCTION_SERVER_CONFIGURATION.planner_policy,
        targets=_targets("2000"),
        definitions=PRODUCTION_SERVER_CONFIGURATION.configurable_meal_definitions,
    )
    ranks = {
        candidate.candidate_kind: index
        for index, candidate in enumerate(result.slots[0].candidates, start=1)
        if candidate.candidate_kind is CandidateKind.CONFIGURABLE_ESTIMATE
    }

    assert ranks[CandidateKind.CONFIGURABLE_ESTIMATE] > 4


def test_policy_absent_or_definition_not_allowlisted_keeps_estimated_lane_disabled() -> None:
    old = _result((_cyo(),), policy=PRODUCTION_SERVER_CONFIGURATION_V2.planner_policy)
    changed = replace(
        OWNER_USUAL_HALAL_BOWL_DEFINITION,
        definition_version="owner-cyo-halal-bowl.v999",
    )
    changed_evidence = replace(
        OWNER_USUAL_HALAL_BOWL_DEFINITION,
        configuration_summary="different configuration under an unchanged version",
    )
    unlisted = _result((_cyo(),), definitions=(changed,))
    unlisted_evidence = _result((_cyo(),), definitions=(changed_evidence,))

    assert old.status is PlannerStatus.NO_PLAN
    assert unlisted.status is PlannerStatus.NO_PLAN
    assert unlisted_evidence.status is PlannerStatus.NO_PLAN


def test_exact_date_period_campus_and_source_classification_are_required() -> None:
    wrong_name = replace(_cyo(), name_normalized="CYO Halal Bowl Special")
    official_profile = _strict(2, "604", name="CYO Halal Bowl")
    source_unavailable = replace(
        _cyo(3), nutrition_source_state=NutritionSourceState.SOURCE_UNAVAILABLE
    )

    assert _result((wrong_name,)).status is PlannerStatus.NO_PLAN
    assert _result((_cyo(),), campus_id=99).status is PlannerStatus.NO_PLAN
    assert _result((_cyo(),), service_date=date(2026, 9, 1)).status is PlannerStatus.NO_PLAN
    assert _result((source_unavailable,)).status is PlannerStatus.NO_PLAN
    strict_result = _result((official_profile,))
    assert all(
        candidate.candidate_kind is CandidateKind.STRICT
        for candidate in strict_result.slots[0].candidates
    )


def test_unresolved_component_or_portion_is_rejected() -> None:
    components = list(OWNER_USUAL_HALAL_BOWL_DEFINITION.selected_components)
    components[0] = replace(components[0], portion=None)
    definition = replace(
        OWNER_USUAL_HALAL_BOWL_DEFINITION,
        selected_components=tuple(components),
    )

    assert (
        _result((_cyo(),), policy=_policy_for(definition), definitions=(definition,)).status
        is PlannerStatus.NO_PLAN
    )


def test_missing_required_protein_is_rejected_and_missing_is_not_zero() -> None:
    references = []
    for reference in OWNER_USUAL_HALAL_BOWL_DEFINITION.nutrition_references:
        quantities = dict(reference.facts.quantities)
        quantities.pop(NutrientKey.PROTEIN_G)
        references.append(
            replace(
                reference,
                facts=NutritionFacts(
                    quantities=quantities,
                    published_zero=frozenset(
                        key
                        for key in reference.facts.published_zero
                        if key is not NutrientKey.PROTEIN_G
                    ),
                    declared_unavailable=reference.facts.declared_unavailable,
                    confidence=reference.facts.confidence,
                ),
            )
        )
    definition = replace(
        OWNER_USUAL_HALAL_BOWL_DEFINITION,
        nutrition_references=tuple(references),
    )

    result = _result((_cyo(),), policy=_policy_for(definition), definitions=(definition,))
    assert result.status is PlannerStatus.NO_PLAN
    assert NutrientKey.PROTEIN_G not in definition.estimate.totals.quantities  # type: ignore[union-attr]


def test_missing_required_calories_is_rejected() -> None:
    references = []
    for reference in OWNER_USUAL_HALAL_BOWL_DEFINITION.nutrition_references:
        quantities = dict(reference.facts.quantities)
        quantities.pop(NutrientKey.CALORIES_KCAL)
        references.append(
            replace(
                reference,
                facts=NutritionFacts(
                    quantities=quantities,
                    published_zero=frozenset(
                        key
                        for key in reference.facts.published_zero
                        if key is not NutrientKey.CALORIES_KCAL
                    ),
                    declared_unavailable=reference.facts.declared_unavailable,
                    confidence=reference.facts.confidence,
                ),
            )
        )
    definition = replace(
        OWNER_USUAL_HALAL_BOWL_DEFINITION,
        nutrition_references=tuple(references),
    )

    assert (
        _result((_cyo(),), policy=_policy_for(definition), definitions=(definition,)).status
        is PlannerStatus.NO_PLAN
    )


def test_complete_estimate_is_allowed_by_v3() -> None:
    complete_facts = NutritionFacts(
        quantities={key: Decimal("1") for key in ALL_NUTRIENT_KEYS},
        published_zero=frozenset(),
        declared_unavailable=frozenset(),
        confidence=Confidence.ESTIMATED,
    )
    definition = replace(
        OWNER_USUAL_HALAL_BOWL_DEFINITION,
        nutrition_references=tuple(
            replace(reference, facts=complete_facts, caveats=())
            for reference in OWNER_USUAL_HALAL_BOWL_DEFINITION.nutrition_references
        ),
    )

    assert definition.estimate.state.value == "complete_estimate"
    assert (
        _result((_cyo(),), policy=_policy_for(definition), definitions=(definition,)).status
        is PlannerStatus.OK
    )


@pytest.mark.parametrize(
    "unsupported",
    (
        NutrientKey.SODIUM_MG,
        NutrientKey.CARBOHYDRATE_G,
        NutrientKey.ADDED_SUGARS_G,
        NutrientKey.TRANS_FAT_G,
    ),
)
def test_unsupported_nonadvisory_target_rejects(unsupported: NutrientKey) -> None:
    required = _result((_cyo(),), targets=_targets(extra=(unsupported, GoalKind.CEILING)))

    assert required.status is PlannerStatus.NO_PLAN
    assert "estimated_meal_ineligible" in {
        reason.value for reason in required.slots[0].failure_reasons
    }


def test_advisory_unknown_target_is_allowed_and_remains_unknown() -> None:
    advisory = _result(
        (_cyo(),), targets=_targets(extra=(NutrientKey.ADDED_SUGARS_G, GoalKind.ADVISORY))
    )

    assert advisory.status is PlannerStatus.OK
    assert _estimated(advisory).evaluation.entries[0].key is NutrientKey.ADDED_SUGARS_G


def test_estimated_score_applies_exact_uncertainty_then_one_cyo_preference() -> None:
    candidate = _estimated(_result((_cyo(),)))
    nutritional = candidate.score_breakdown["calories_kcal:target"]

    assert candidate.score_breakdown["uncertainty:estimated_nutrition"] == Decimal("-0.10")
    assert candidate.score_breakdown["preference:owner.cyo_halal_bowl"] == Decimal("0.05")
    assert candidate.score_total == nutritional - Decimal("0.10") + Decimal("0.05")


def test_ranking_examples_preserve_fit_and_allow_useful_estimate() -> None:
    poor_strict = _result((_cyo(), _strict(10, "900")))
    good_strict = _result((_cyo(), _strict(11, "650")), targets=_targets("1625"))

    assert poor_strict.slots[0].candidates[0].candidate_kind is (
        CandidateKind.CONFIGURABLE_ESTIMATE
    )
    assert good_strict.slots[0].candidates[0].candidate_kind is CandidateKind.STRICT


def test_estimated_candidate_is_never_paired_with_strict_or_another_estimate() -> None:
    second = replace(
        OWNER_USUAL_HALAL_BOWL_DEFINITION,
        definition_id="owner.cyo_halal_bowl.second",
        definition_version="owner-cyo-halal-bowl-second.v1",
    )
    policy = replace(
        PRODUCTION_SERVER_CONFIGURATION_V3.planner_policy,
        estimated_meal_policy=replace(
            PRODUCTION_SERVER_CONFIGURATION_V3.planner_policy.estimated_meal_policy,
            allowlisted_configuration_identities=frozenset(
                {
                    OWNER_USUAL_HALAL_BOWL_DEFINITION.allowlist_identity,
                    second.allowlist_identity,
                }
            ),
        ),
    )
    result = _result(
        (_cyo(), _strict(20, "700")),
        policy=policy,
        definitions=(OWNER_USUAL_HALAL_BOWL_DEFINITION, second),
    )

    estimated = [
        candidate
        for candidate in result.slots[0].candidates
        if candidate.candidate_kind is CandidateKind.CONFIGURABLE_ESTIMATE
    ]
    assert len(estimated) == 2
    assert all(candidate.line_refs == () and candidate.meal.lines == () for candidate in estimated)
    assert all(len(candidate.line_refs) <= 2 for candidate in result.slots[0].candidates)


def test_estimated_artifact_freezes_configuration_evidence_and_source_without_profile() -> None:
    candidate = _estimated(_result((_cyo(),)))
    document = candidate_document(candidate)
    estimate = document["configurable_estimate"]
    assert isinstance(estimate, dict)
    definition = estimate["definition"]
    assert isinstance(definition, dict)

    assert document["candidate_kind"] == "configurable_estimate"
    assert document["lines"] == []
    assert document["provenance"]["profile_content_sha256s"] == []  # type: ignore[index]
    assert definition["configuration_summary"].endswith("no sauces")
    assert len(definition["selected_components"]) == 8  # type: ignore[arg-type]
    assert definition["estimate"]["state"] == "partial_estimate"  # type: ignore[index]
    assert definition["estimate"]["unknown_nutrients"] == [  # type: ignore[index]
        "added_sugars_g",
        "trans_fat_g",
    ]
    assert estimate["availability"]["nutrition_source_state"] == "source_placeholder"  # type: ignore[index]
    assert estimate["evidence_digest"] == OWNER_USUAL_HALAL_BOWL_DEFINITION.evidence_digest


def test_estimated_plan_persistence_uses_source_identity_and_empty_official_profile_pins() -> None:
    offering = _cyo()
    menu = MenuDayView(
        service_date=PLAN_DATE,
        periods={MealPeriod.LUNCH: PeriodMenu(offerings=(offering,))},
        fetched_at=PLAN_AT,
        snapshot_sha256="accepted-menu-sha",
        campus_id=50,
    )
    config = PRODUCTION_SERVER_CONFIGURATION_V3
    inputs = DailyPlanInputs(
        user_id=UUID(int=42),
        requested_for_date=PLAN_DATE,
        timezone="America/New_York",
        schedule=_schedule(),
        exceptions=(),
        menu=menu,
        policy=config.planner_policy,
        slot_policies={
            MealContext.POST_WORKOUT_LUNCH: SlotPolicy(context=MealContext.POST_WORKOUT_LUNCH)
        },
        targets=_targets(),
        target_policy_version_id=UUID(int=77),
        offering_profile_ids={},
        configurable_meal_definitions=config.configurable_meal_definitions,
    )
    repository = InMemoryPlanRunRepository()
    outcome = GenerateDailyPlanUseCase(repository, _Clock(), _Ids()).execute(inputs, PLAN_AT)

    assert outcome.run is not None and outcome.run.status.value == "completed"
    assert outcome.version is not None
    assert len(outcome.items) == 1
    item = outcome.items[0]
    assert item.offering_ids == (offering.offering_id,)
    assert item.food_ids == (offering.food_id,)
    assert item.profile_row_ids == ()
    assert item.profile_content_sha256s == ()
    candidate = outcome.version.plan_jsonb["slots"][0]["candidates"][0]  # type: ignore[index]
    assert candidate["candidate_kind"] == "configurable_estimate"

    original_canonical = outcome.version.plan_canonical
    consumption = RecordConsumptionUseCase(
        InMemoryConsumptionRepository(), _Clock(), _Ids()
    ).execute(
        user_id=inputs.user_id,
        plan_run_id=outcome.run.run_id,
        plan_version_id=outcome.version.version_id,
        item_id=item.item_id,
        state=ConsumptionState.EATEN,
        client_event_id=UUID(int=88),
    )
    later_definition = replace(
        OWNER_USUAL_HALAL_BOWL_DEFINITION,
        definition_version="owner-cyo-halal-bowl.v2",
    )
    assert consumption.entry.item_id == item.item_id
    assert consumption.entry.state is ConsumptionState.EATEN
    assert later_definition.evidence_digest != OWNER_USUAL_HALAL_BOWL_DEFINITION.evidence_digest
    assert outcome.version.plan_canonical == original_canonical


def test_definition_or_evidence_revision_changes_fingerprint_without_mutating_history() -> None:
    config = PRODUCTION_SERVER_CONFIGURATION_V3
    offering = _cyo()
    menu = MenuDayView(
        service_date=PLAN_DATE,
        periods={MealPeriod.LUNCH: PeriodMenu(offerings=(offering,))},
        fetched_at=PLAN_AT,
        snapshot_sha256="accepted-menu-sha",
        campus_id=50,
    )
    base = DailyPlanInputs(
        user_id=UUID(int=42),
        requested_for_date=PLAN_DATE,
        timezone="America/New_York",
        schedule=_schedule(),
        exceptions=(),
        menu=menu,
        policy=config.planner_policy,
        slot_policies={
            MealContext.POST_WORKOUT_LUNCH: SlotPolicy(context=MealContext.POST_WORKOUT_LUNCH)
        },
        targets=_targets(),
        configurable_meal_definitions=config.configurable_meal_definitions,
    )
    revision = replace(
        OWNER_USUAL_HALAL_BOWL_DEFINITION,
        definition_version="owner-cyo-halal-bowl.v2",
    )

    assert compute_inputs_fingerprint(base) != compute_inputs_fingerprint(
        replace(base, configurable_meal_definitions=(revision,))
    )
    assert compute_inputs_fingerprint(base) != compute_inputs_fingerprint(
        replace(base, menu=replace(menu, campus_id=99))
    )
