"""Deterministic daily meal planner with frozen strict and opt-in estimated lanes.

Pipeline per slot: period/freshness gates -> offering eligibility (A) ->
candidate generation -> candidate-level eligibility (B) -> scoring via M3
TargetSet-driven functions -> deterministic ordering + IDs. The M4 strict
pipeline remains unchanged; a versioned policy may add separately eligible,
standalone configurable estimates before final ordering.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from types import MappingProxyType

from nutrition_agent.domain.configurable_meals import ConfigurableMealDefinition
from nutrition_agent.domain.nutrition import ENGINE_POLICY_VERSION
from nutrition_agent.domain.nutrition.meal import ComposedMeal
from nutrition_agent.domain.nutrition.scoring import score_candidate
from nutrition_agent.domain.nutrition.targets import (
    NutrientGoal,
    TargetEvaluation,
    TargetSet,
    evaluate_against_targets,
)
from nutrition_agent.domain.planning.candidates import Candidate, generate_candidates
from nutrition_agent.domain.planning.context import MealContext, MealSlot
from nutrition_agent.domain.planning.eligibility import (
    ReasonCode,
    Rejection,
    candidate_gate,
    dietary_gate,
    freshness_gate,
    offering_gate,
    period_gate,
)
from nutrition_agent.domain.planning.estimated import (
    EstimatedCandidateDetails,
    build_estimated_candidates,
)
from nutrition_agent.domain.planning.menu_view import CandidateLineRef, MenuDayView
from nutrition_agent.domain.planning.policy import OfferingPreference, PlannerPolicy, SlotPolicy
from nutrition_agent.domain.planning.schedule import (
    ScheduleException,
    WeeklySchedule,
    resolve_day,
)
from nutrition_agent.domain.stacks.entities import MealPeriod, NutrientKey

_PLANNER_ENGINE_POLICY_VERSION = ENGINE_POLICY_VERSION

_CALORIE_QUANTUM = Decimal("1")
_MASS_QUANTUM = Decimal("0.1")


def _quantum_for(key: NutrientKey) -> Decimal:
    return _CALORIE_QUANTUM if key is NutrientKey.CALORIES_KCAL else _MASS_QUANTUM


def slice_target_set(daily: TargetSet, context: MealContext, policy: PlannerPolicy) -> TargetSet:
    """Deterministic ALLOCATION-POLICY slicing of an approved daily TargetSet.

    Shares are versioned bookkeeping, not scientific claims. Weights carry over
    unchanged; values are share * daily quantized (1 kcal / 0.1 g, HALF_EVEN).
    Nutrients without a configured share pass through unsliced.
    """
    shares = policy.slot_shares.get(context, {})
    goals: dict[NutrientKey, NutrientGoal] = {}
    for key, goal in daily.goals.items():
        share = shares.get(key)
        if share is None:
            goals[key] = goal
            continue
        sliced_value = (goal.value * share).quantize(_quantum_for(key), rounding=ROUND_HALF_EVEN)
        goals[key] = NutrientGoal(kind=goal.kind, value=sliced_value, weight=goal.weight)
    return TargetSet(
        policy_version=f"{daily.policy_version}+{policy.version}+{context.value}",
        goals=goals,
    )


class PlannerStatus(StrEnum):
    OK = "ok"
    NO_PLAN = "no_plan"


class CandidateKind(StrEnum):
    STRICT = "strict"
    CONFIGURABLE_ESTIMATE = "configurable_estimate"


@dataclass(frozen=True)
class RankedCandidate:
    candidate_id: str
    slot_context: MealContext
    meal: ComposedMeal
    score_total: Decimal
    score_breakdown: Mapping[str, Decimal]
    evaluation: TargetEvaluation
    line_refs: tuple[CandidateLineRef, ...]
    dietary_tags: tuple[str, ...]
    category_names: tuple[str, ...]
    candidate_kind: CandidateKind = CandidateKind.STRICT
    estimated_details: EstimatedCandidateDetails | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "score_breakdown", MappingProxyType(dict(self.score_breakdown)))
        if self.candidate_kind is CandidateKind.STRICT and self.estimated_details is not None:
            raise ValueError("strict candidates cannot carry estimated details")
        if (
            self.candidate_kind is CandidateKind.CONFIGURABLE_ESTIMATE
            and self.estimated_details is None
        ):
            raise ValueError("configurable estimates require estimated details")
        if self.estimated_details is not None and (self.line_refs or self.meal.lines):
            raise ValueError("configurable estimates must remain standalone non-strict meals")


@dataclass(frozen=True)
class SlotResult:
    context: MealContext
    menu_period: MealPeriod
    window: tuple[time, time]
    status: PlannerStatus
    candidates: tuple[RankedCandidate, ...] = ()
    failure_reasons: tuple[ReasonCode, ...] = ()
    rejection_counts: Mapping[str, int] = MappingProxyType({})
    rejection_details: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlannerResult:
    plan_date: date
    status: PlannerStatus
    slots: tuple[SlotResult, ...]
    planner_policy_version: str
    schedule_version: str
    target_policy_version: str
    engine_policy_version: str
    menu_snapshot_sha256: str | None
    menu_fetched_at: datetime | None
    dietary_policy_version: str | None = None
    failure_reasons: tuple[ReasonCode, ...] = ()


def generate_daily_plan(
    plan_date: date,
    plan_at: datetime,
    schedule: WeeklySchedule,
    exceptions: Sequence[ScheduleException],
    menu: MenuDayView,
    policy: PlannerPolicy,
    slot_policies: Mapping[MealContext, SlotPolicy],
    targets: TargetSet,
    configurable_meal_definitions: Sequence[ConfigurableMealDefinition] = (),
) -> PlannerResult:
    """Daily planning with an opt-in estimated lane beside frozen strict behavior."""
    slots = resolve_day(schedule, exceptions, plan_date, policy.context_period)
    failure_reasons = (ReasonCode.NO_RESOLVED_MEAL_SLOTS,) if not slots else ()
    slot_results = tuple(
        _plan_slot(
            slot,
            plan_date,
            plan_at,
            menu,
            policy,
            slot_policies,
            targets,
            configurable_meal_definitions,
        )
        for slot in slots
    )
    overall = (
        PlannerStatus.OK
        if slot_results and all(result.status is PlannerStatus.OK for result in slot_results)
        else PlannerStatus.NO_PLAN
    )
    used_target_versions = sorted(
        {f"{targets.policy_version}+{policy.version}+{slot.context.value}" for slot in slots}
    )
    target_provenance = (
        "+".join(used_target_versions) if used_target_versions else targets.policy_version
    )
    return PlannerResult(
        plan_date=plan_date,
        status=overall,
        slots=slot_results,
        planner_policy_version=policy.version,
        schedule_version=schedule.version,
        target_policy_version=target_provenance,
        engine_policy_version=_PLANNER_ENGINE_POLICY_VERSION,
        menu_snapshot_sha256=menu.snapshot_sha256,
        menu_fetched_at=menu.fetched_at,
        dietary_policy_version=(
            policy.dietary_policy.version if policy.dietary_policy is not None else None
        ),
        failure_reasons=failure_reasons,
    )


def _plan_slot(
    slot: MealSlot,
    plan_date: date,
    plan_at: datetime,
    menu: MenuDayView,
    policy: PlannerPolicy,
    slot_policies: Mapping[MealContext, SlotPolicy],
    targets: TargetSet,
    configurable_meal_definitions: Sequence[ConfigurableMealDefinition],
) -> SlotResult:
    failures: list[Rejection] = []

    period_menu = menu.period(slot.menu_period)
    period_failure = period_gate(period_menu)
    if period_failure is not None:
        failures.append(period_failure)

    if not failures:
        assert period_menu is not None
        stale = freshness_gate(menu.fetched_at, plan_at, policy.max_menu_age.total_seconds())
        if stale is not None:
            failures.append(stale)

    if failures:
        return _no_plan(slot, failures)

    assert period_menu is not None
    slot_policy = slot_policies.get(slot.context, SlotPolicy(context=slot.context))

    dietary_eligible = []
    for offering in period_menu.offerings:
        rejection = dietary_gate(offering, policy.dietary_policy)
        if rejection is None:
            dietary_eligible.append(offering)
        else:
            failures.append(rejection)

    eligible = []
    for offering in dietary_eligible:
        rejection = offering_gate(offering, slot_policy)
        if rejection is None:
            eligible.append(offering)
        else:
            failures.append(rejection)
    candidates = generate_candidates(tuple(eligible), policy.max_candidates_per_slot)

    surviving: list[tuple[Candidate, ComposedMeal]] = []
    for candidate in candidates:
        rejection = candidate_gate(candidate.meal, slot_policy)
        if rejection is None:
            surviving.append((candidate, candidate.meal))
        else:
            failures.append(rejection)
    slot_targets = slice_target_set(targets, slot.context, policy)
    ranked: list[RankedCandidate] = []
    for candidate, meal in surviving:
        evaluation = evaluate_against_targets(meal.totals, slot_targets)
        score = score_candidate(evaluation, meal.totals, slot_targets)
        preference = _preference_for(candidate, policy.offering_preferences)
        score_breakdown = dict(score.breakdown)
        score_total = score.total
        if preference is not None:
            score_breakdown[f"preference:{preference.preference_id}"] = preference.bonus
            score_total += preference.bonus
        ranked.append(
            RankedCandidate(
                candidate_id="",
                slot_context=slot.context,
                meal=meal,
                score_total=score_total,
                score_breakdown=score_breakdown,
                evaluation=evaluation,
                line_refs=candidate.lines,
                dietary_tags=tuple(tag.value for tag in candidate.dietary_tags),
                category_names=candidate.category_names,
            )
        )

    estimated_policy = policy.estimated_meal_policy
    if (
        estimated_policy is not None
        and configurable_meal_definitions
        and menu.service_date == plan_date
    ):
        estimated = build_estimated_candidates(
            definitions=configurable_meal_definitions,
            offerings=dietary_eligible,
            campus_id=menu.campus_id,
            service_date=menu.service_date,
            menu_period=slot.menu_period,
            menu_snapshot_sha256=menu.snapshot_sha256,
            slot_policy=slot_policy,
            targets=slot_targets,
            policy=estimated_policy,
        )
        failures.extend(
            Rejection(ReasonCode.ESTIMATED_MEAL_INELIGIBLE, detail)
            for detail in estimated.rejection_details
        )
        for entry in estimated.candidates:
            score_breakdown = dict(entry.nutritional_score.breakdown)
            score_total = entry.nutritional_score.total
            preference = _preference_for_names(
                {entry.details.definition.source_name_normalized},
                policy.offering_preferences,
            )
            if preference is not None:
                score_breakdown[f"preference:{preference.preference_id}"] = preference.bonus
                score_total += preference.bonus
            ranked.append(
                RankedCandidate(
                    candidate_id="",
                    slot_context=slot.context,
                    meal=entry.meal,
                    score_total=score_total,
                    score_breakdown=score_breakdown,
                    evaluation=entry.evaluation,
                    line_refs=(),
                    dietary_tags=tuple(
                        tag.value for tag in entry.details.availability.dietary_tags
                    ),
                    category_names=(entry.details.availability.category_name,),
                    candidate_kind=CandidateKind.CONFIGURABLE_ESTIMATE,
                    estimated_details=entry.details,
                )
            )

    if not ranked:
        return _no_plan(slot, failures)

    ranked.sort(key=lambda entry: _rank_key(entry, slot.context))
    ranked = ranked[: policy.max_candidates_per_slot]
    width = max(3, len(str(len(ranked))))
    ordered = tuple(
        RankedCandidate(
            candidate_id=f"{slot.context.value}-{index + 1:0{width}d}",
            slot_context=entry.slot_context,
            meal=entry.meal,
            score_total=entry.score_total,
            score_breakdown=entry.score_breakdown,
            evaluation=entry.evaluation,
            line_refs=entry.line_refs,
            dietary_tags=entry.dietary_tags,
            category_names=entry.category_names,
            candidate_kind=entry.candidate_kind,
            estimated_details=entry.estimated_details,
        )
        for index, entry in enumerate(ranked)
    )
    return SlotResult(
        context=slot.context,
        menu_period=slot.menu_period,
        window=slot.window,
        status=PlannerStatus.OK,
        candidates=ordered,
        failure_reasons=_unique_reasons(failures),
        rejection_counts=_counts(failures),
        rejection_details=tuple(rejection.detail for rejection in failures),
    )


def rank_candidates_for_slot(
    *,
    slot: MealSlot,
    plan_date: date,
    plan_at: datetime,
    menu: MenuDayView,
    policy: PlannerPolicy,
    slot_policies: Mapping[MealContext, SlotPolicy],
    targets: TargetSet,
    configurable_meal_definitions: Sequence[ConfigurableMealDefinition] = (),
) -> SlotResult:
    """Rank one caller-resolved opportunity with explicit targets.

    M16A uses this narrow public seam to reuse the frozen gates, candidate
    construction, dietary policy, estimated lane, and scoring. Daily planning
    still calls ``_plan_slot`` exactly as before. Callers that already allocated
    daily needs must provide a policy with no slot shares so targets are not
    sliced twice.
    """

    return _plan_slot(
        slot,
        plan_date,
        plan_at,
        menu,
        policy,
        slot_policies,
        targets,
        configurable_meal_definitions,
    )


def _preference_for(
    candidate: Candidate,
    preferences: tuple[OfferingPreference, ...],
) -> OfferingPreference | None:
    """Return at most one bounded preference; pair bonuses never accumulate."""

    names = {ref.name_normalized for ref in candidate.lines}
    return _preference_for_names(names, preferences)


def _preference_for_names(
    names: set[str],
    preferences: tuple[OfferingPreference, ...],
) -> OfferingPreference | None:
    matches = [
        preference for preference in preferences if names & preference.normalized_name_aliases
    ]
    if not matches:
        return None
    return min(matches, key=lambda preference: (-preference.bonus, preference.preference_id))


def _rank_key(
    entry: RankedCandidate, context: MealContext
) -> tuple[Decimal, Decimal, tuple[str, ...], tuple[str, ...]]:
    del context  # slots never mix contexts; key is per-slot deterministic
    names = tuple(ref.name_normalized for ref in entry.line_refs)
    mids = tuple(ref.source_mid for ref in entry.line_refs)
    if entry.estimated_details is not None:
        names = (entry.estimated_details.definition.source_name_normalized,)
        mids = (entry.estimated_details.availability.source_mid,)
    return (
        -entry.score_total,
        -_calories_of(entry.meal),
        names,
        mids,
    )


def _calories_of(meal: ComposedMeal) -> Decimal:
    return meal.totals.quantities[NutrientKey.CALORIES_KCAL]


def _no_plan(slot: MealSlot, failures: list[Rejection]) -> SlotResult:
    return SlotResult(
        context=slot.context,
        menu_period=slot.menu_period,
        window=slot.window,
        status=PlannerStatus.NO_PLAN,
        failure_reasons=_unique_reasons(failures),
        rejection_counts=_counts(failures),
        rejection_details=tuple(rejection.detail for rejection in failures),
    )


def _unique_reasons(failures: list[Rejection]) -> tuple[ReasonCode, ...]:
    seen: dict[ReasonCode, None] = {}
    for rejection in failures:
        seen.setdefault(rejection.reason)
    return tuple(seen)


def _counts(failures: list[Rejection]) -> Mapping[str, int]:
    counts: dict[str, int] = {}
    for rejection in failures:
        counts[rejection.reason.value] = counts.get(rejection.reason.value, 0) + 1
    return MappingProxyType(counts)
