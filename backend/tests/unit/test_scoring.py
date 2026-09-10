"""Scoring: TargetSet-driven only; no confidence term; strict-ineligible refusal."""

from __future__ import annotations

from decimal import Decimal

import pytest

from nutrition_agent.domain.nutrition.facts import NutritionFacts
from nutrition_agent.domain.nutrition.scoring import StrictIneligibleError, score_candidate
from nutrition_agent.domain.nutrition.targets import (
    GoalKind,
    NutrientGoal,
    TargetSet,
    evaluate_against_targets,
)
from nutrition_agent.domain.stacks.entities import Confidence, NutrientKey


def _strict_facts(values: dict[NutrientKey, str]) -> NutritionFacts:
    quantities = {key: Decimal("1") for key in NutrientKey}
    quantities.update({key: Decimal(v) for key, v in values.items()})
    return NutritionFacts(
        quantities=quantities,
        published_zero=frozenset(),
        declared_unavailable=frozenset(),
        confidence=Confidence.OFFICIAL_PUBLISHED,
    )


def _incomplete_facts() -> NutritionFacts:
    return NutritionFacts(
        quantities={NutrientKey.CALORIES_KCAL: Decimal(500)},
        published_zero=frozenset(),
        declared_unavailable=frozenset(),
        confidence=Confidence.OFFICIAL_PUBLISHED,
    )


def _partial_confidence_facts() -> NutritionFacts:
    facts = _strict_facts({})
    return NutritionFacts(
        quantities=facts.quantities,
        published_zero=frozenset(),
        declared_unavailable=frozenset(),
        confidence=Confidence.PARTIAL,
    )


def _targets(goals: dict[NutrientKey, NutrientGoal]) -> TargetSet:
    return TargetSet(policy_version="policy-x", goals=goals)


def test_floor_penalty_hand_computed() -> None:
    # actual 60 vs floor 90 -> shortfall 30 -> -1.0 * 30/90 = -1/3 exactly representable? no:
    # Decimal division gives 0.333... with 28-digit precision; assert exact expected string.
    facts = _strict_facts({NutrientKey.PROTEIN_G: "60"})
    targets = _targets(
        {NutrientKey.PROTEIN_G: NutrientGoal(GoalKind.FLOOR, Decimal(90), weight=Decimal(1))}
    )
    evaluation = evaluate_against_targets(facts, targets)
    result = score_candidate(evaluation, facts, targets)
    assert result.total == -(Decimal(30) / Decimal(90))
    assert result.breakdown["protein_g:floor"] == result.total


def test_floor_satisfied_contributes_zero() -> None:
    facts = _strict_facts({NutrientKey.PROTEIN_G: "90"})
    targets = _targets(
        {NutrientKey.PROTEIN_G: NutrientGoal(GoalKind.FLOOR, Decimal(90), weight=Decimal(2))}
    )
    result = score_candidate(evaluate_against_targets(facts, targets), facts, targets)
    assert result.total == 0


def test_ceiling_penalty_only_when_exceeded() -> None:
    facts = _strict_facts({NutrientKey.SODIUM_MG: "1500"})
    targets = _targets(
        {
            NutrientKey.SODIUM_MG: NutrientGoal(
                GoalKind.CEILING, Decimal(1000), weight=Decimal("0.5")
            )
        }
    )
    result = score_candidate(evaluate_against_targets(facts, targets), facts, targets)
    assert result.total == -(Decimal("0.5") * Decimal(500) / Decimal(1000))

    under = score_candidate(
        evaluate_against_targets(_strict_facts({NutrientKey.SODIUM_MG: "900"}), targets),
        _strict_facts({NutrientKey.SODIUM_MG: "900"}),
        targets,
    )
    assert under.total == 0


def test_target_absolute_deviation() -> None:
    facts = _strict_facts({NutrientKey.CALORIES_KCAL: "750"})
    targets = _targets(
        {NutrientKey.CALORIES_KCAL: NutrientGoal(GoalKind.TARGET, Decimal(800), weight=Decimal(2))}
    )
    result = score_candidate(evaluate_against_targets(facts, targets), facts, targets)
    assert result.total == -(Decimal(2) * Decimal(50) / Decimal(800))


def test_advisory_and_unweighted_goals_never_scored() -> None:
    facts = _strict_facts({NutrientKey.FIBER_G: "3", NutrientKey.IRON_MG: "1"})
    targets = _targets(
        {
            NutrientKey.FIBER_G: NutrientGoal(GoalKind.ADVISORY, Decimal(10), weight=Decimal(5)),
            NutrientKey.IRON_MG: NutrientGoal(GoalKind.TARGET, Decimal(8)),  # weight None
        }
    )
    evaluation = evaluate_against_targets(facts, targets)
    result = score_candidate(evaluation, facts, targets)
    # advisory/unweighted goals contribute exactly zero to the total
    assert result.total == 0
    assert all(contribution == 0 for contribution in result.breakdown.values())


def test_zero_weight_goals_yield_exact_zero_total() -> None:
    facts = _strict_facts({NutrientKey.PROTEIN_G: "60"})
    targets = _targets(
        {NutrientKey.PROTEIN_G: NutrientGoal(GoalKind.FLOOR, Decimal(90), weight=Decimal(0))}
    )
    result = score_candidate(evaluate_against_targets(facts, targets), facts, targets)
    assert result.total == 0
    assert all(contribution == 0 for contribution in result.breakdown.values())


def test_breakdown_sums_to_total_multiple_goals() -> None:
    facts = _strict_facts(
        {NutrientKey.PROTEIN_G: "70", NutrientKey.SODIUM_MG: "1200", NutrientKey.FIBER_G: "4"}
    )
    targets = _targets(
        {
            NutrientKey.PROTEIN_G: NutrientGoal(GoalKind.FLOOR, Decimal(90), weight=Decimal(3)),
            NutrientKey.SODIUM_MG: NutrientGoal(
                GoalKind.CEILING, Decimal(1000), weight=Decimal("0.25")
            ),
            NutrientKey.CALORIES_KCAL: NutrientGoal(
                GoalKind.TARGET, Decimal(800), weight=Decimal(1)
            ),
        }
    )
    result = score_candidate(evaluate_against_targets(facts, targets), facts, targets)
    assert sum(result.breakdown.values(), Decimal(0)) == result.total


def test_determinism_same_inputs_same_output() -> None:
    facts = _strict_facts({NutrientKey.PROTEIN_G: "70"})
    targets = _targets(
        {NutrientKey.PROTEIN_G: NutrientGoal(GoalKind.FLOOR, Decimal(90), weight=Decimal(1))}
    )
    evaluation = evaluate_against_targets(facts, targets)
    first = score_candidate(evaluation, facts, targets)
    second = score_candidate(evaluation, facts, targets)
    assert first.total == second.total
    assert dict(first.breakdown) == dict(second.breakdown)


def test_strict_ineligible_refusals() -> None:
    facts = _strict_facts({NutrientKey.PROTEIN_G: "70"})
    targets = _targets(
        {NutrientKey.PROTEIN_G: NutrientGoal(GoalKind.FLOOR, Decimal(90), weight=Decimal(1))}
    )
    evaluation = evaluate_against_targets(facts, targets)

    with pytest.raises(StrictIneligibleError):
        score_candidate(evaluation, _incomplete_facts(), targets)
    with pytest.raises(StrictIneligibleError):
        score_candidate(evaluation, _partial_confidence_facts(), targets)


def test_result_carries_target_policy_version() -> None:
    facts = _strict_facts({NutrientKey.PROTEIN_G: "90"})
    targets = _targets(
        {NutrientKey.PROTEIN_G: NutrientGoal(GoalKind.FLOOR, Decimal(90), weight=Decimal(1))}
    )
    result = score_candidate(evaluate_against_targets(facts, targets), facts, targets)
    assert result.target_policy_version == "policy-x"
