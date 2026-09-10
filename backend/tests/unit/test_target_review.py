"""Pure Decimal-only M10A target-review policy tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from nutrition_agent.domain.health.trend import (
    BODY_MASS_TREND_ALGORITHM_VERSION,
    BodyMassTrendStatus,
    BodyMassTrendSummary,
)
from nutrition_agent.domain.nutrition.targets import GoalKind, NutrientGoal, TargetSet
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion
from nutrition_agent.domain.stacks.entities import NutrientKey
from nutrition_agent.domain.target_review import (
    M10A_TARGET_REVIEW_POLICY,
    GoalDirection,
    GoalPolicyVersion,
    TargetReviewPolicy,
    TargetReviewReason,
    TargetReviewStatus,
    evaluate_target_review,
    goal_policy_payload_sha256,
    target_review_evaluation_document,
    target_review_evaluation_from_document,
)

USER = UUID("00000000-0000-0000-0000-0000000000a1")
GOAL_ID = UUID(int=101)
TARGET_ID = UUID(int=201)
AS_OF = date(2026, 8, 28)
APPROVED_AT = datetime(2026, 8, 14, 12, tzinfo=UTC)


def _goal(
    direction: GoalDirection = GoalDirection.GAIN,
    desired: str = "0.25",
    *,
    version_id: UUID = GOAL_ID,
    label: str = "lean-bulk-v1",
) -> GoalPolicyVersion:
    rate = Decimal(desired)
    return GoalPolicyVersion(
        version_id=version_id,
        user_id=USER,
        policy_version=label,
        direction=direction,
        desired_rate_kg_per_week=rate,
        payload_sha256=goal_policy_payload_sha256(direction, rate),
        created_at=datetime(2026, 8, 1, 12, tzinfo=UTC),
    )


def _trend(
    status: BodyMassTrendStatus = BodyMassTrendStatus.READY,
    rate: str | None = "0.25",
    *,
    as_of_date: date = AS_OF,
    digest: str = "a" * 64,
) -> BodyMassTrendSummary:
    has_data = status is not BodyMassTrendStatus.NO_DATA
    return BodyMassTrendSummary(
        as_of_date=as_of_date,
        timezone="UTC",
        algorithm_version=BODY_MASS_TREND_ALGORITHM_VERSION,
        latest_measurement_date=as_of_date if has_data else None,
        latest_measurement_age_days=0 if has_data else None,
        first_measurement_date=as_of_date - timedelta(days=20) if has_data else None,
        last_measurement_date=as_of_date if has_data else None,
        represented_day_count=7 if has_data else 0,
        coverage_span_days=20 if has_data else 0,
        trailing_7d_average_kg=Decimal("70") if has_data else None,
        weekly_rate_kg=Decimal(rate) if rate is not None else None,
        status=status,
        input_digest=digest,
    )


def _target(
    calories: str = "2500",
    *,
    version_id: UUID = TARGET_ID,
    payload_hash: str = "b" * 64,
    reversed_order: bool = False,
    created_at: datetime = APPROVED_AT,
) -> tuple[TargetPolicyVersion, TargetSet]:
    entries = [
        (
            NutrientKey.CALORIES_KCAL,
            NutrientGoal(GoalKind.TARGET, Decimal(calories), Decimal("1")),
        ),
        (
            NutrientKey.PROTEIN_G,
            NutrientGoal(GoalKind.FLOOR, Decimal("120"), Decimal("2")),
        ),
    ]
    if reversed_order:
        entries.reverse()
    targets = TargetSet(policy_version="target-v1", goals=dict(entries))
    policy = TargetPolicyVersion(
        version_id=version_id,
        user_id=USER,
        policy_version="target-v1",
        goals_jsonb=[],
        payload_sha256=payload_hash,
        created_at=created_at,
    )
    return policy, targets


def _evaluate(
    *,
    direction: GoalDirection = GoalDirection.GAIN,
    desired: str = "0.25",
    observed: str = "0.25",
    status: BodyMassTrendStatus = BodyMassTrendStatus.READY,
    calories: str = "2500",
    approved_at: datetime = APPROVED_AT,
    policy: TargetReviewPolicy = M10A_TARGET_REVIEW_POLICY,
):
    target_policy, targets = _target(calories, created_at=approved_at)
    return evaluate_target_review(
        trend=_trend(status=status, rate=observed if status is BodyMassTrendStatus.READY else None),
        goal_policy=_goal(direction, desired),
        review_policy=policy,
        current_target_policy=target_policy,
        current_targets=targets,
        current_target_approved_at=approved_at,
        as_of_date=AS_OF,
    )


@pytest.mark.parametrize(
    ("direction", "rate"),
    [
        (GoalDirection.GAIN, "0.25"),
        (GoalDirection.LOSE, "-0.25"),
        (GoalDirection.MAINTAIN, "0"),
    ],
)
def test_goal_direction_accepts_only_consistent_rate(direction: GoalDirection, rate: str) -> None:
    assert _goal(direction, rate).desired_rate_kg_per_week == Decimal(rate)


@pytest.mark.parametrize(
    ("direction", "rate"),
    [
        (GoalDirection.GAIN, "0"),
        (GoalDirection.GAIN, "-0.25"),
        (GoalDirection.LOSE, "0"),
        (GoalDirection.LOSE, "0.25"),
        (GoalDirection.MAINTAIN, "0.10"),
        (GoalDirection.MAINTAIN, "-0.10"),
    ],
)
def test_goal_direction_rejects_inconsistent_rate(direction: GoalDirection, rate: str) -> None:
    with pytest.raises(ValueError):
        _goal(direction, rate)


def test_goal_policy_rejects_payload_hash_that_does_not_match() -> None:
    with pytest.raises(ValueError, match="does not match"):
        replace(_goal(), payload_sha256="f" * 64)


def test_initial_review_policy_values_are_explicit_and_versioned() -> None:
    assert M10A_TARGET_REVIEW_POLICY.policy_version == "target-review-v1"
    assert M10A_TARGET_REVIEW_POLICY.deadband_kg_per_week == Decimal("0.10")
    assert M10A_TARGET_REVIEW_POLICY.adjustment_step_kcal == Decimal("100")
    assert M10A_TARGET_REVIEW_POLICY.cooldown_days == 14
    assert M10A_TARGET_REVIEW_POLICY.lower_calorie_bound is None
    assert M10A_TARGET_REVIEW_POLICY.upper_calorie_bound is None


@pytest.mark.parametrize(
    ("evidence", "reason"),
    [
        (BodyMassTrendStatus.NO_DATA, TargetReviewReason.NO_DATA),
        (BodyMassTrendStatus.INSUFFICIENT, TargetReviewReason.INSUFFICIENT),
        (BodyMassTrendStatus.STALE, TargetReviewReason.STALE),
    ],
)
def test_non_ready_evidence_never_recommends(
    evidence: BodyMassTrendStatus, reason: TargetReviewReason
) -> None:
    result = _evaluate(status=evidence)
    assert result.status is TargetReviewStatus.EVIDENCE_UNAVAILABLE
    assert result.reason_codes == (reason,)
    assert result.proposed_calorie_target is None


@pytest.mark.parametrize("observed", ["0.15", "0.35"])
def test_deadband_boundaries_are_inclusive(observed: str) -> None:
    result = _evaluate(observed=observed)
    assert result.status is TargetReviewStatus.WITHIN_BAND
    assert result.calorie_delta is None


def test_below_and_above_band_recommend_exact_bounded_step() -> None:
    below = _evaluate(observed="0.149")
    above = _evaluate(observed="0.351")
    assert (below.proposed_calorie_target, below.calorie_delta) == (
        Decimal("2600"),
        Decimal("100"),
    )
    assert (above.proposed_calorie_target, above.calorie_delta) == (
        Decimal("2400"),
        Decimal("-100"),
    )


@pytest.mark.parametrize(
    ("direction", "desired", "observed", "delta"),
    [
        (GoalDirection.GAIN, "0.25", "0.05", "100"),
        (GoalDirection.GAIN, "0.25", "0.50", "-100"),
        (GoalDirection.MAINTAIN, "0", "-0.30", "100"),
        (GoalDirection.MAINTAIN, "0", "0.30", "-100"),
        (GoalDirection.LOSE, "-0.25", "-0.50", "100"),
        (GoalDirection.LOSE, "-0.25", "0", "-100"),
    ],
)
def test_direction_never_shortcuts_observed_vs_desired_comparison(
    direction: GoalDirection, desired: str, observed: str, delta: str
) -> None:
    result = _evaluate(direction=direction, desired=desired, observed=observed)
    assert result.status is TargetReviewStatus.RECOMMENDATION_READY
    assert result.calorie_delta == Decimal(delta)


def test_cooldown_allows_exactly_14_days_and_holds_at_13() -> None:
    allowed = _evaluate(approved_at=datetime(2026, 8, 14, 12, tzinfo=UTC), observed="0")
    held = _evaluate(approved_at=datetime(2026, 8, 15, 12, tzinfo=UTC), observed="0")
    assert allowed.status is TargetReviewStatus.RECOMMENDATION_READY
    assert held.status is TargetReviewStatus.COOLDOWN_HOLD
    assert held.reason_codes == (TargetReviewReason.COOLDOWN_ACTIVE,)


def test_optional_bounds_clamp_step_and_make_bound_hold_explicit() -> None:
    upper = replace(M10A_TARGET_REVIEW_POLICY, upper_calorie_bound=Decimal("2550"))
    lower = replace(M10A_TARGET_REVIEW_POLICY, lower_calorie_bound=Decimal("2450"))
    increase = _evaluate(observed="0", policy=upper)
    decrease = _evaluate(observed="0.5", policy=lower)
    assert (increase.proposed_calorie_target, increase.calorie_delta) == (
        Decimal("2550"),
        Decimal("50"),
    )
    assert TargetReviewReason.UPPER_BOUND_APPLIED in increase.reason_codes
    assert (decrease.proposed_calorie_target, decrease.calorie_delta) == (
        Decimal("2450"),
        Decimal("-50"),
    )
    assert TargetReviewReason.LOWER_BOUND_APPLIED in decrease.reason_codes

    held = _evaluate(
        observed="0",
        policy=replace(M10A_TARGET_REVIEW_POLICY, upper_calorie_bound=Decimal("2500")),
    )
    assert held.status is TargetReviewStatus.BOUND_HOLD
    assert held.proposed_calorie_target is None


def test_no_configured_lower_bound_never_proposes_non_positive_target() -> None:
    result = _evaluate(observed="0.5", calories="50")
    assert result.status is TargetReviewStatus.BOUND_HOLD
    assert result.reason_codes[-1] is TargetReviewReason.POSITIVE_TARGET_HOLD


def test_only_calorie_goal_changes_and_non_calorie_goals_are_identical() -> None:
    result = _evaluate(observed="0")
    assert result.proposed_goals is not None
    before = {goal.nutrient: goal for goal in result.current_goals}
    after = {goal.nutrient: goal for goal in result.proposed_goals}
    assert after[NutrientKey.CALORIES_KCAL].value == Decimal("2600")
    assert after[NutrientKey.PROTEIN_G] == before[NutrientKey.PROTEIN_G]


def test_digest_is_order_independent_and_round_trips_exact_evidence() -> None:
    policy, forward = _target(reversed_order=False)
    _, reversed_targets = _target(reversed_order=True)
    kwargs = dict(
        trend=_trend(rate="0"),
        goal_policy=_goal(),
        review_policy=M10A_TARGET_REVIEW_POLICY,
        current_target_policy=policy,
        current_target_approved_at=APPROVED_AT,
        as_of_date=AS_OF,
    )
    first = evaluate_target_review(current_targets=forward, **kwargs)
    second = evaluate_target_review(current_targets=reversed_targets, **kwargs)
    assert first.recommendation_digest == second.recommendation_digest
    document = target_review_evaluation_document(first)
    assert target_review_evaluation_from_document(document, first.recommendation_digest) == first


def test_digest_changes_for_each_decision_relevant_policy_or_evidence_change() -> None:
    base = _evaluate(observed="0")
    target_policy, targets = _target()

    def evaluate_with(**changes: object):
        return evaluate_target_review(
            trend=changes.get("trend", _trend(rate="0")),  # type: ignore[arg-type]
            goal_policy=changes.get("goal", _goal()),  # type: ignore[arg-type]
            review_policy=changes.get("policy", M10A_TARGET_REVIEW_POLICY),  # type: ignore[arg-type]
            current_target_policy=changes.get("target", target_policy),  # type: ignore[arg-type]
            current_targets=changes.get("targets", targets),  # type: ignore[arg-type]
            current_target_approved_at=APPROVED_AT,
            as_of_date=changes.get("as_of", AS_OF),  # type: ignore[arg-type]
        )

    changed = {
        evaluate_with(trend=_trend(rate="0", digest="c" * 64)).recommendation_digest,
        evaluate_with(goal=_goal(version_id=UUID(int=102), label="goal-v2")).recommendation_digest,
        evaluate_with(
            target=replace(target_policy, version_id=UUID(int=202), payload_sha256="d" * 64)
        ).recommendation_digest,
        evaluate_with(
            policy=replace(M10A_TARGET_REVIEW_POLICY, policy_version="target-review-v2")
        ).recommendation_digest,
        evaluate_with(
            trend=_trend(rate="0", as_of_date=AS_OF + timedelta(days=1)),
            as_of=AS_OF + timedelta(days=1),
        ).recommendation_digest,
        evaluate_with(
            policy=replace(M10A_TARGET_REVIEW_POLICY, adjustment_step_kcal=Decimal("50"))
        ).recommendation_digest,
    }
    assert base.recommendation_digest not in changed
    assert len(changed) == 6


def test_ready_without_rate_and_non_target_calorie_goal_fail_closed() -> None:
    target_policy, targets = _target()
    with pytest.raises(ValueError, match="weekly rate"):
        evaluate_target_review(
            trend=_trend(rate=None),
            goal_policy=_goal(),
            review_policy=M10A_TARGET_REVIEW_POLICY,
            current_target_policy=target_policy,
            current_targets=targets,
            current_target_approved_at=APPROVED_AT,
            as_of_date=AS_OF,
        )
    bad_targets = TargetSet(
        policy_version="bad",
        goals={
            NutrientKey.CALORIES_KCAL: NutrientGoal(GoalKind.CEILING, Decimal("2500"), Decimal("1"))
        },
    )
    with pytest.raises(ValueError, match="calorie target"):
        evaluate_target_review(
            trend=_trend(rate="0"),
            goal_policy=_goal(),
            review_policy=M10A_TARGET_REVIEW_POLICY,
            current_target_policy=target_policy,
            current_targets=bad_targets,
            current_target_approved_at=APPROVED_AT,
            as_of_date=AS_OF,
        )


def test_target_approval_timestamp_must_match_persisted_policy() -> None:
    target_policy, targets = _target()
    with pytest.raises(ValueError, match="must match"):
        evaluate_target_review(
            trend=_trend(rate="0"),
            goal_policy=_goal(),
            review_policy=M10A_TARGET_REVIEW_POLICY,
            current_target_policy=target_policy,
            current_targets=targets,
            current_target_approved_at=APPROVED_AT + timedelta(hours=1),
            as_of_date=AS_OF,
        )
