"""Target policies and deterministic evaluation against them.

TargetSets are caller-supplied versioned data; nothing here computes TDEE,
maintenance calories, phase targets, or bodyweight-derived goals.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from nutrition_agent.domain.nutrition.facts import (
    NutrientPresence,
    NutritionFacts,
    presence_of,
)
from nutrition_agent.domain.stacks.entities import NutrientKey


class GoalKind(StrEnum):
    TARGET = "target"
    FLOOR = "floor"
    CEILING = "ceiling"
    ADVISORY = "advisory"


class EvaluationStatus(StrEnum):
    MET = "met"
    OFF_TARGET = "off_target"
    SATISFIED = "satisfied"
    BELOW_FLOOR = "below_floor"
    ABOVE_CEILING = "above_ceiling"
    ADVISORY_DELTA = "advisory_delta"
    UNKNOWN = "unknown"


_MISSING_STATES: frozenset[NutrientPresence] = frozenset(
    {NutrientPresence.DECLARED_UNAVAILABLE, NutrientPresence.UNKNOWN_ABSENT}
)


@dataclass(frozen=True)
class NutrientGoal:
    kind: GoalKind
    value: Decimal
    weight: Decimal | None = None

    def __post_init__(self) -> None:
        if self.kind in {GoalKind.TARGET, GoalKind.FLOOR, GoalKind.CEILING} and self.value <= 0:
            raise ValueError(f"{self.kind.value} goal requires a positive value")
        if self.weight is not None and self.weight < 0:
            raise ValueError("goal weight must be >= 0")


@dataclass(frozen=True)
class TargetSet:
    policy_version: str
    goals: Mapping[NutrientKey, NutrientGoal]

    def __post_init__(self) -> None:
        object.__setattr__(self, "goals", MappingProxyType(dict(self.goals)))


@dataclass(frozen=True)
class GoalEvaluation:
    key: NutrientKey
    status: EvaluationStatus
    delta: Decimal | None = None
    presence: str | None = None


@dataclass(frozen=True)
class TargetEvaluation:
    entries: tuple[GoalEvaluation, ...]
    policy_version: str


def evaluate_against_targets(facts: NutritionFacts, targets: TargetSet) -> TargetEvaluation:
    entries: list[GoalEvaluation] = []
    for key in sorted(targets.goals.keys(), key=lambda nutrient: nutrient.value):
        goal = targets.goals[key]
        if goal.kind is GoalKind.FLOOR:
            entries.append(_floor_entry(facts, key, goal))
        elif goal.kind is GoalKind.CEILING:
            entries.append(_ceiling_entry(facts, key, goal))
        elif goal.kind is GoalKind.ADVISORY:
            entries.append(_advisory_entry(facts, key, goal))
        else:
            entries.append(_target_entry(facts, key, goal))
    return TargetEvaluation(entries=tuple(entries), policy_version=targets.policy_version)


def _missing_entry(facts: NutritionFacts, key: NutrientKey) -> GoalEvaluation:
    return GoalEvaluation(
        key=key,
        status=EvaluationStatus.UNKNOWN,
        delta=None,
        presence=presence_of(facts, key).value,
    )


def _is_missing(facts: NutritionFacts, key: NutrientKey) -> bool:
    return presence_of(facts, key) in _MISSING_STATES


def _floor_entry(facts: NutritionFacts, key: NutrientKey, goal: NutrientGoal) -> GoalEvaluation:
    if _is_missing(facts, key):
        return _missing_entry(facts, key)
    actual = facts.quantities[key]
    delta = actual - goal.value
    if actual >= goal.value:
        return GoalEvaluation(key=key, status=EvaluationStatus.SATISFIED, delta=delta)
    return GoalEvaluation(key=key, status=EvaluationStatus.BELOW_FLOOR, delta=delta)


def _ceiling_entry(facts: NutritionFacts, key: NutrientKey, goal: NutrientGoal) -> GoalEvaluation:
    if _is_missing(facts, key):
        return _missing_entry(facts, key)
    actual = facts.quantities[key]
    delta = actual - goal.value
    if actual <= goal.value:
        return GoalEvaluation(key=key, status=EvaluationStatus.SATISFIED, delta=delta)
    return GoalEvaluation(key=key, status=EvaluationStatus.ABOVE_CEILING, delta=delta)


def _advisory_entry(facts: NutritionFacts, key: NutrientKey, goal: NutrientGoal) -> GoalEvaluation:
    del goal
    if _is_missing(facts, key):
        return _missing_entry(facts, key)
    return GoalEvaluation(
        key=key,
        status=EvaluationStatus.ADVISORY_DELTA,
        delta=facts.quantities[key],
    )


def _target_entry(facts: NutritionFacts, key: NutrientKey, goal: NutrientGoal) -> GoalEvaluation:
    if _is_missing(facts, key):
        return _missing_entry(facts, key)
    delta = facts.quantities[key] - goal.value
    status = EvaluationStatus.MET if delta == 0 else EvaluationStatus.OFF_TARGET
    return GoalEvaluation(key=key, status=status, delta=delta)
