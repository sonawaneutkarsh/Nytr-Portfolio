from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from nutrition_agent.domain.body_goals import (
    OwnerBodyProfile,
    PhaseProgressStatus,
    WaistMeasurement,
    estimate_starting_calories,
    evaluate_phase_progress,
)
from nutrition_agent.domain.health.trend import (
    BodyMassTrendStatus,
    BodyMassTrendSummary,
)
from nutrition_agent.domain.target_review import (
    GoalDirection,
    GoalPolicyVersion,
    goal_policy_payload_sha256,
)

USER = UUID("00000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 1, 29, 12, tzinfo=UTC)


def _goal(direction: GoalDirection, rate: str) -> GoalPolicyVersion:
    value = Decimal(rate)
    return GoalPolicyVersion(
        version_id=UUID(int=2),
        user_id=USER,
        policy_version="synthetic-goal",
        direction=direction,
        desired_rate_kg_per_week=value,
        payload_sha256=goal_policy_payload_sha256(direction, value),
        created_at=NOW,
    )


def _trend(
    rate: str | None, status: BodyMassTrendStatus = BodyMassTrendStatus.READY
) -> BodyMassTrendSummary:
    return BodyMassTrendSummary(
        as_of_date=date(2026, 1, 29),
        timezone="UTC",
        algorithm_version="test",
        latest_measurement_date=date(2026, 1, 29),
        latest_measurement_age_days=0,
        first_measurement_date=date(2026, 1, 1),
        last_measurement_date=date(2026, 1, 29),
        represented_day_count=10,
        coverage_span_days=28,
        trailing_7d_average_kg=Decimal("70"),
        weekly_rate_kg=Decimal(rate) if rate is not None else None,
        status=status,
        input_digest="0" * 64,
    )


def _waist(value: str, days: int) -> WaistMeasurement:
    return WaistMeasurement(
        measurement_id=UUID(int=days + 10),
        user_id=USER,
        waist_cm=Decimal(value),
        measured_at=NOW - timedelta(days=days),
        recorded_at=NOW,
    )


def test_starting_estimate_is_bounded_and_distinct_from_approval() -> None:
    estimate = estimate_starting_calories(Decimal("70"))
    assert estimate.estimate_kcal == 2400
    assert estimate.confidence == "estimated"
    assert "not measured maintenance" in estimate.rationale


def test_estimate_fails_closed_for_missing_or_invalid_weight() -> None:
    with pytest.raises((TypeError, ValueError)):
        estimate_starting_calories(Decimal("0"))


def test_profile_rejects_implausible_height_and_waist_is_owner_attributed() -> None:
    with pytest.raises(ValueError):
        OwnerBodyProfile(USER, Decimal("90"), None, NOW)
    measurement = _waist("82.5", 0)
    assert measurement.source == "owner_manual"


def test_insufficient_weight_evidence_fails_closed_and_no_phase_switch_exists() -> None:
    result = evaluate_phase_progress(
        goal=_goal(GoalDirection.GAIN, "0.20"),
        trend=_trend(None, BodyMassTrendStatus.INSUFFICIENT),
        waist_measurements=(),
    )
    assert result.status is PhaseProgressStatus.UNAVAILABLE


def test_gain_review_uses_waist_as_supplementary_evidence() -> None:
    result = evaluate_phase_progress(
        goal=_goal(GoalDirection.GAIN, "0.20"),
        trend=_trend("0.40"),
        waist_measurements=(_waist("84", 14), _waist("85", 0)),
    )
    assert result.status is PhaseProgressStatus.REVIEW
    assert result.waist_rate_cm_per_week is not None


def test_lose_and_maintain_semantics_are_deterministic() -> None:
    lose = evaluate_phase_progress(
        goal=_goal(GoalDirection.LOSE, "-0.20"), trend=_trend("-0.20"), waist_measurements=()
    )
    maintain = evaluate_phase_progress(
        goal=_goal(GoalDirection.MAINTAIN, "0"), trend=_trend("0.01"), waist_measurements=()
    )
    assert lose.status is PhaseProgressStatus.ON_TRACK
    assert maintain.status is PhaseProgressStatus.ON_TRACK
