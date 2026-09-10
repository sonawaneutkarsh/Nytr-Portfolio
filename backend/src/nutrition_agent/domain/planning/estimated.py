"""Policy-gated eligibility and scoring for estimated configurable meals.

This lane is intentionally separate from strict Stacks profile eligibility and
``score_candidate``. A source occurrence proves availability only; estimated
nutrition comes solely from the immutable configurable definition.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType

from nutrition_agent.domain.configurable_meals import ConfigurableMealDefinition
from nutrition_agent.domain.nutrition import ENGINE_POLICY_VERSION
from nutrition_agent.domain.nutrition.meal import ComposedMeal
from nutrition_agent.domain.nutrition.scoring import ScoreResult
from nutrition_agent.domain.nutrition.targets import (
    EvaluationStatus,
    GoalKind,
    TargetEvaluation,
    TargetSet,
    evaluate_against_targets,
)
from nutrition_agent.domain.planning.menu_view import OfferingView
from nutrition_agent.domain.planning.policy import EstimatedMealPolicy, SlotPolicy
from nutrition_agent.domain.stacks.entities import MealPeriod, NutrientKey, NutritionSourceState


@dataclass(frozen=True)
class EstimatedCandidateDetails:
    definition: ConfigurableMealDefinition
    availability: OfferingView
    service_date: date
    menu_period: MealPeriod
    menu_snapshot_sha256: str


@dataclass(frozen=True)
class EligibleEstimatedCandidate:
    details: EstimatedCandidateDetails
    meal: ComposedMeal
    evaluation: TargetEvaluation
    nutritional_score: ScoreResult


@dataclass(frozen=True)
class EstimatedBuildResult:
    candidates: tuple[EligibleEstimatedCandidate, ...]
    rejection_details: tuple[str, ...]


def build_estimated_candidates(
    *,
    definitions: Sequence[ConfigurableMealDefinition],
    offerings: Sequence[OfferingView],
    campus_id: int | None,
    service_date: date,
    menu_period: MealPeriod,
    menu_snapshot_sha256: str,
    slot_policy: SlotPolicy,
    targets: TargetSet,
    policy: EstimatedMealPolicy,
) -> EstimatedBuildResult:
    """Return standalone candidates that pass every explicit estimated-lane gate."""

    if campus_id is None:
        return EstimatedBuildResult((), ("estimated lane requires an authoritative campus",))
    ordered_offerings = sorted(
        offerings,
        key=lambda item: (
            item.name_normalized,
            item.source_mid,
            item.occurrence_ordinal,
            str(item.offering_id),
        ),
    )
    candidates: list[EligibleEstimatedCandidate] = []
    rejections: list[str] = []
    for definition in sorted(
        definitions, key=lambda item: (item.definition_id, item.definition_version)
    ):
        if definition.allowlist_identity not in policy.allowlisted_configuration_identities:
            rejections.append(f"{definition.definition_id}: configuration identity not allowlisted")
            continue
        if definition.campus_id != campus_id:
            rejections.append(f"{definition.definition_id}: campus does not match")
            continue
        availability = next(
            (
                offering
                for offering in ordered_offerings
                if offering.name_normalized == definition.source_name_normalized
                and offering.profile is None
                and offering.profile_sha256 is None
                and offering.nutrition_source_state
                in {
                    NutritionSourceState.SOURCE_PLACEHOLDER,
                    NutritionSourceState.SOURCE_INCOMPLETE,
                }
            ),
            None,
        )
        if availability is None:
            rejections.append(f"{definition.definition_id}: no accepted mapped source occurrence")
            continue

        estimate = definition.estimate
        if estimate.state not in policy.permitted_estimate_states or estimate.totals is None:
            rejections.append(f"{definition.definition_id}: estimate state is not permitted")
            continue
        if estimate.unresolved_components:
            rejections.append(f"{definition.definition_id}: selected components are unresolved")
            continue
        if any(component.portion is None for component in definition.selected_components):
            rejections.append(f"{definition.definition_id}: selected portion is unresolved")
            continue
        if any(key not in estimate.totals.quantities for key in policy.required_known_dimensions):
            rejections.append(
                f"{definition.definition_id}: required calories/protein are not both known"
            )
            continue
        if not _targets_are_supported(estimate.totals.quantities, targets, policy):
            rejections.append(
                f"{definition.definition_id}: active non-advisory target is unsupported or unknown"
            )
            continue

        calories = estimate.totals.quantities[NutrientKey.CALORIES_KCAL]
        if calories < slot_policy.calorie_min or calories > slot_policy.calorie_max:
            rejections.append(f"{definition.definition_id}: calories outside slot bounds")
            continue
        evaluation = evaluate_against_targets(estimate.totals, targets)
        nutritional_score = score_estimated_candidate(evaluation, targets, policy)
        meal = ComposedMeal(
            lines=(),
            totals=estimate.totals,
            confidence=estimate.totals.confidence,
            engine_policy_version=ENGINE_POLICY_VERSION,
        )
        candidates.append(
            EligibleEstimatedCandidate(
                details=EstimatedCandidateDetails(
                    definition=definition,
                    availability=availability,
                    service_date=service_date,
                    menu_period=menu_period,
                    menu_snapshot_sha256=menu_snapshot_sha256,
                ),
                meal=meal,
                evaluation=evaluation,
                nutritional_score=nutritional_score,
            )
        )
    return EstimatedBuildResult(tuple(candidates), tuple(rejections))


def _targets_are_supported(
    known: Mapping[NutrientKey, Decimal],
    targets: TargetSet,
    policy: EstimatedMealPolicy,
) -> bool:
    known_dimensions = set(known)
    for key, goal in targets.goals.items():
        if goal.kind is GoalKind.ADVISORY:
            continue
        if key not in policy.permitted_scored_dimensions or key not in known_dimensions:
            return False
    return True


def score_estimated_candidate(
    evaluation: TargetEvaluation,
    targets: TargetSet,
    policy: EstimatedMealPolicy,
) -> ScoreResult:
    """Score only policy-permitted known dimensions, then apply uncertainty."""

    breakdown: dict[str, Decimal] = {}
    for entry in evaluation.entries:
        goal = targets.goals[entry.key]
        if goal.kind is GoalKind.ADVISORY or goal.weight is None or goal.weight == 0:
            continue
        if entry.key not in policy.permitted_scored_dimensions:
            raise ValueError("unsupported non-advisory target reached estimated scorer")
        if entry.status is EvaluationStatus.UNKNOWN or entry.delta is None:
            raise ValueError("unknown non-advisory target reached estimated scorer")
        deviation = Decimal(0)
        if entry.status is EvaluationStatus.BELOW_FLOOR:
            deviation = max(Decimal(0), -entry.delta)
        elif entry.status is EvaluationStatus.ABOVE_CEILING:
            deviation = max(Decimal(0), entry.delta)
        elif entry.status is EvaluationStatus.OFF_TARGET:
            deviation = abs(entry.delta)
        contribution = Decimal(0)
        if deviation != 0:
            contribution = -goal.weight * deviation / goal.value
        breakdown[f"{entry.key.value}:{goal.kind.value}"] = contribution
    breakdown["uncertainty:estimated_nutrition"] = -policy.uncertainty_penalty
    return ScoreResult(
        total=sum(breakdown.values(), Decimal(0)),
        breakdown=MappingProxyType(breakdown),
        target_policy_version=targets.policy_version,
    )
