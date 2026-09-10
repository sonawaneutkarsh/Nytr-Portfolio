"""Target evaluation: FLOOR/CEILING/TARGET/ADVISORY edges + UNKNOWN propagation."""

from __future__ import annotations

from decimal import Decimal

import pytest

from nutrition_agent.domain.nutrition.facts import NutritionFacts
from nutrition_agent.domain.nutrition.targets import (
    EvaluationStatus,
    GoalKind,
    NutrientGoal,
    TargetSet,
    evaluate_against_targets,
)
from nutrition_agent.domain.stacks.entities import Confidence, NutrientKey


def _facts(
    values: dict[NutrientKey, str],
    declared_unavailable: tuple[NutrientKey, ...] = (),
) -> NutritionFacts:
    return NutritionFacts(
        quantities={key: Decimal(v) for key, v in values.items()},
        published_zero=frozenset(),
        declared_unavailable=frozenset(declared_unavailable),
        confidence=Confidence.OFFICIAL_PUBLISHED,
    )


def _targets(goals: dict[NutrientKey, NutrientGoal]) -> TargetSet:
    return TargetSet(policy_version="test-policy-1", goals=goals)


def test_floor_satisfied_at_boundary_and_below_detected() -> None:
    facts = _facts({NutrientKey.PROTEIN_G: "60"})
    targets = _targets({NutrientKey.PROTEIN_G: NutrientGoal(GoalKind.FLOOR, Decimal(60))})
    entries = evaluate_against_targets(facts, targets).entries
    assert entries[0].status is EvaluationStatus.SATISFIED
    assert entries[0].delta == 0

    low = evaluate_against_targets(
        _facts({NutrientKey.PROTEIN_G: "45"}),
        _targets({NutrientKey.PROTEIN_G: NutrientGoal(GoalKind.FLOOR, Decimal(60))}),
    ).entries[0]
    assert low.status is EvaluationStatus.BELOW_FLOOR
    assert low.delta == Decimal("-15")


def test_ceiling_satisfied_at_boundary_and_above_detected() -> None:
    targets = _targets({NutrientKey.SODIUM_MG: NutrientGoal(GoalKind.CEILING, Decimal("1000"))})
    at = evaluate_against_targets(_facts({NutrientKey.SODIUM_MG: "1000"}), targets).entries[0]
    assert at.status is EvaluationStatus.SATISFIED
    over = evaluate_against_targets(_facts({NutrientKey.SODIUM_MG: "1500"}), targets).entries[0]
    assert over.status is EvaluationStatus.ABOVE_CEILING
    assert over.delta == Decimal("500")


def test_target_met_and_off_target_signed_delta() -> None:
    targets = _targets({NutrientKey.CALORIES_KCAL: NutrientGoal(GoalKind.TARGET, Decimal(800))})
    met = evaluate_against_targets(_facts({NutrientKey.CALORIES_KCAL: "800"}), targets).entries[0]
    assert met.status is EvaluationStatus.MET
    off = evaluate_against_targets(_facts({NutrientKey.CALORIES_KCAL: "750"}), targets).entries[0]
    assert off.status is EvaluationStatus.OFF_TARGET
    assert off.delta == Decimal("-50")


def test_advisory_never_pass_fail() -> None:
    targets = _targets(
        {NutrientKey.FIBER_G: NutrientGoal(GoalKind.ADVISORY, Decimal(10), weight=Decimal("0.5"))}
    )
    entry = evaluate_against_targets(_facts({NutrientKey.FIBER_G: "3"}), targets).entries[0]
    assert entry.status is EvaluationStatus.ADVISORY_DELTA
    assert entry.delta == Decimal("3")


def test_unknown_preserves_missing_reason() -> None:
    targets = _targets(
        {
            NutrientKey.FIBER_G: NutrientGoal(GoalKind.FLOOR, Decimal(5)),
            NutrientKey.IRON_MG: NutrientGoal(GoalKind.TARGET, Decimal(8)),
        }
    )
    facts = _facts({}, declared_unavailable=(NutrientKey.FIBER_G,))
    by_key = {entry.key: entry for entry in evaluate_against_targets(facts, targets).entries}
    assert by_key[NutrientKey.FIBER_G].status is EvaluationStatus.UNKNOWN
    assert by_key[NutrientKey.FIBER_G].presence == "declared_unavailable"
    assert by_key[NutrientKey.IRON_MG].status is EvaluationStatus.UNKNOWN
    assert by_key[NutrientKey.IRON_MG].presence == "unknown_absent"


def test_goal_validation_rejects_bad_values() -> None:
    with pytest.raises(ValueError, match="positive value"):
        NutrientGoal(GoalKind.TARGET, Decimal(0))
    with pytest.raises(ValueError, match="positive value"):
        NutrientGoal(GoalKind.FLOOR, Decimal("-1"))
    with pytest.raises(ValueError, match="weight"):
        NutrientGoal(GoalKind.CEILING, Decimal(10), weight=Decimal("-0.1"))
    NutrientGoal(GoalKind.CEILING, Decimal(10), weight=Decimal(0))  # zero OK
