"""Minimal transparent candidate scoring driven ONLY by a supplied TargetSet.

No confidence term, no built-in nutrient dimensions; contributions come solely
from weighted goals in the TargetSet. Strict-ineligible facts are refused.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from nutrition_agent.domain.nutrition.facts import NutritionFacts, is_strict_eligible
from nutrition_agent.domain.nutrition.targets import (
    EvaluationStatus,
    TargetEvaluation,
    TargetSet,
)


class StrictIneligibleError(ValueError):
    pass


@dataclass(frozen=True)
class ScoreResult:
    total: Decimal
    breakdown: Mapping[str, Decimal]
    target_policy_version: str


def score_candidate(
    evaluation: TargetEvaluation, facts: NutritionFacts, targets: TargetSet
) -> ScoreResult:
    if not is_strict_eligible(facts):
        raise StrictIneligibleError("candidate is not strict-eligible; refusing to score")
    breakdown: dict[str, Decimal] = {}
    for entry in evaluation.entries:
        goal = targets.goals[entry.key]
        if goal.weight is None or goal.weight == 0:
            continue
        if entry.status is EvaluationStatus.UNKNOWN:  # pragma: no cover - guarded above
            raise AssertionError("strict-eligible facts cannot yield UNKNOWN evaluations")
        contribution = Decimal(0)
        deviation = _deviation(entry.status, entry.delta)
        if deviation != 0:
            contribution = -goal.weight * deviation / goal.value
        breakdown[f"{entry.key.value}:{goal.kind.value}"] = contribution
    total = sum(breakdown.values(), Decimal(0))
    return ScoreResult(
        total=total,
        breakdown=MappingProxyType(breakdown),
        target_policy_version=targets.policy_version,
    )


def _deviation(status: EvaluationStatus, delta: Decimal | None) -> Decimal:
    if delta is None:
        return Decimal(0)
    if status is EvaluationStatus.BELOW_FLOOR:
        return max(Decimal(0), -delta)
    if status is EvaluationStatus.ABOVE_CEILING:
        return max(Decimal(0), delta)
    if status is EvaluationStatus.OFF_TARGET:
        return abs(delta)
    return Decimal(0)
