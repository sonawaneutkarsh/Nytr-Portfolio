"""Deterministic goal-policy and target-review domain (M10A).

The evaluator compares an already-derived M9 body-mass trend with explicit,
versioned policy.  It never reads storage, infers energy intake, or mutates an
approved target policy.  All authoritative numeric work is Decimal-only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from nutrition_agent.domain.health.trend import BodyMassTrendStatus, BodyMassTrendSummary
from nutrition_agent.domain.nutrition.targets import GoalKind, TargetSet
from nutrition_agent.domain.planning.artifacts import DecisionLogEntry, TargetPolicyVersion
from nutrition_agent.domain.stacks.entities import NutrientKey

TARGET_REVIEW_POLICY_VERSION = "target-review-v1"


class GoalDirection(StrEnum):
    MAINTAIN = "maintain"
    GAIN = "gain"
    LOSE = "lose"


class GoalBandStatus(StrEnum):
    UNAVAILABLE = "unavailable"
    BELOW_BAND = "below_band"
    WITHIN_BAND = "within_band"
    ABOVE_BAND = "above_band"


class TargetReviewStatus(StrEnum):
    EVIDENCE_UNAVAILABLE = "evidence_unavailable"
    COOLDOWN_HOLD = "cooldown_hold"
    WITHIN_BAND = "within_band"
    BOUND_HOLD = "bound_hold"
    RECOMMENDATION_READY = "recommendation_ready"


class TargetReviewReason(StrEnum):
    NO_DATA = "no_data"
    INSUFFICIENT = "insufficient"
    STALE = "stale"
    COOLDOWN_ACTIVE = "cooldown_active"
    WITHIN_BAND = "within_band"
    OBSERVED_BELOW_BAND = "observed_below_band"
    OBSERVED_ABOVE_BAND = "observed_above_band"
    LOWER_BOUND_APPLIED = "lower_bound_applied"
    UPPER_BOUND_APPLIED = "upper_bound_applied"
    LOWER_BOUND_HOLD = "lower_bound_hold"
    UPPER_BOUND_HOLD = "upper_bound_hold"
    POSITIVE_TARGET_HOLD = "positive_target_hold"


class TargetReviewDecisionValue(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


def _require_decimal(name: str, value: object) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    return value


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _datetime_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class GoalPolicyVersion:
    version_id: UUID
    user_id: UUID
    policy_version: str
    direction: GoalDirection
    desired_rate_kg_per_week: Decimal
    payload_sha256: str
    created_at: datetime

    def __post_init__(self) -> None:
        rate = _require_decimal("desired_rate_kg_per_week", self.desired_rate_kg_per_week)
        if not self.policy_version.strip():
            raise ValueError("goal policy version must not be empty")
        if len(self.payload_sha256) != 64:
            raise ValueError("goal policy payload_sha256 must be a SHA-256 hex digest")
        if self.payload_sha256 != goal_policy_payload_sha256(self.direction, rate):
            raise ValueError("goal policy payload_sha256 does not match its payload")
        _require_aware("goal policy created_at", self.created_at)
        if self.direction is GoalDirection.GAIN and rate <= 0:
            raise ValueError("gain goal requires desired rate > 0")
        if self.direction is GoalDirection.LOSE and rate >= 0:
            raise ValueError("lose goal requires desired rate < 0")
        if self.direction is GoalDirection.MAINTAIN and rate != 0:
            raise ValueError("maintain goal requires desired rate = 0")


def goal_policy_payload_sha256(direction: GoalDirection, desired_rate_kg_per_week: Decimal) -> str:
    rate = _require_decimal("desired_rate_kg_per_week", desired_rate_kg_per_week)
    payload = {
        "desired_rate_kg_per_week": _decimal_text(rate),
        "direction": direction.value,
        "rate_unit": "kg_per_week",
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class TargetReviewPolicy:
    policy_version: str
    deadband_kg_per_week: Decimal
    adjustment_step_kcal: Decimal
    cooldown_days: int
    lower_calorie_bound: Decimal | None = None
    upper_calorie_bound: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ValueError("review policy version must not be empty")
        deadband = _require_decimal("deadband_kg_per_week", self.deadband_kg_per_week)
        step = _require_decimal("adjustment_step_kcal", self.adjustment_step_kcal)
        if deadband < 0:
            raise ValueError("review deadband must be >= 0")
        if step <= 0:
            raise ValueError("review calorie step must be > 0")
        if not isinstance(self.cooldown_days, int) or isinstance(self.cooldown_days, bool):
            raise TypeError("review cooldown_days must be an integer")
        if self.cooldown_days < 0:
            raise ValueError("review cooldown_days must be >= 0")
        lower = self.lower_calorie_bound
        upper = self.upper_calorie_bound
        if lower is not None and _require_decimal("lower_calorie_bound", lower) <= 0:
            raise ValueError("lower calorie bound must be > 0")
        if upper is not None and _require_decimal("upper_calorie_bound", upper) <= 0:
            raise ValueError("upper calorie bound must be > 0")
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("lower calorie bound must be <= upper calorie bound")


M10A_TARGET_REVIEW_POLICY = TargetReviewPolicy(
    policy_version=TARGET_REVIEW_POLICY_VERSION,
    deadband_kg_per_week=Decimal("0.10"),
    adjustment_step_kcal=Decimal("100"),
    cooldown_days=14,
)


@dataclass(frozen=True)
class GoalBandInterpretation:
    status: GoalBandStatus
    goal_direction: GoalDirection
    desired_rate_kg_per_week: Decimal
    acceptable_rate_lower_kg_per_week: Decimal
    acceptable_rate_upper_kg_per_week: Decimal
    observed_rate_kg_per_week: Decimal | None


def interpret_goal_band(
    *,
    trend: BodyMassTrendSummary,
    goal_policy: GoalPolicyVersion,
    review_policy: TargetReviewPolicy = M10A_TARGET_REVIEW_POLICY,
) -> GoalBandInterpretation:
    """Reuse M10's exact observed-rate band without creating a review or proposal."""

    lower = goal_policy.desired_rate_kg_per_week - review_policy.deadband_kg_per_week
    upper = goal_policy.desired_rate_kg_per_week + review_policy.deadband_kg_per_week
    observed = trend.weekly_rate_kg
    if trend.status is not BodyMassTrendStatus.READY or observed is None:
        status = GoalBandStatus.UNAVAILABLE
    elif observed < lower:
        status = GoalBandStatus.BELOW_BAND
    elif observed > upper:
        status = GoalBandStatus.ABOVE_BAND
    else:
        status = GoalBandStatus.WITHIN_BAND
    return GoalBandInterpretation(
        status=status,
        goal_direction=goal_policy.direction,
        desired_rate_kg_per_week=goal_policy.desired_rate_kg_per_week,
        acceptable_rate_lower_kg_per_week=lower,
        acceptable_rate_upper_kg_per_week=upper,
        observed_rate_kg_per_week=observed,
    )


@dataclass(frozen=True)
class TargetGoalSnapshot:
    nutrient: NutrientKey
    kind: GoalKind
    value: Decimal
    weight: Decimal | None

    def __post_init__(self) -> None:
        _require_decimal("target goal value", self.value)
        if self.weight is not None:
            _require_decimal("target goal weight", self.weight)


@dataclass(frozen=True)
class TargetReviewEvaluation:
    user_id: UUID
    as_of_date: date
    trend: BodyMassTrendSummary
    goal_policy: GoalPolicyVersion
    review_policy: TargetReviewPolicy
    prior_target_policy_version_id: UUID
    prior_target_policy_version: str
    prior_target_payload_sha256: str
    prior_target_approved_at: datetime
    current_goals: tuple[TargetGoalSnapshot, ...]
    status: TargetReviewStatus
    reason_codes: tuple[TargetReviewReason, ...]
    current_calorie_target: Decimal
    proposed_calorie_target: Decimal | None
    calorie_delta: Decimal | None
    proposed_goals: tuple[TargetGoalSnapshot, ...] | None
    recommendation_digest: str

    @property
    def acceptable_rate_lower(self) -> Decimal:
        return self.goal_policy.desired_rate_kg_per_week - self.review_policy.deadband_kg_per_week

    @property
    def acceptable_rate_upper(self) -> Decimal:
        return self.goal_policy.desired_rate_kg_per_week + self.review_policy.deadband_kg_per_week

    def __post_init__(self) -> None:
        _require_aware("prior target approved_at", self.prior_target_approved_at)
        current = _require_decimal("current_calorie_target", self.current_calorie_target)
        if current <= 0:
            raise ValueError("current calorie target must be > 0")
        if len(self.recommendation_digest) != 64:
            raise ValueError("recommendation_digest must be a SHA-256 hex digest")
        has_proposal = self.status is TargetReviewStatus.RECOMMENDATION_READY
        proposal_parts = (
            self.proposed_calorie_target,
            self.calorie_delta,
            self.proposed_goals,
        )
        if has_proposal and any(part is None for part in proposal_parts):
            raise ValueError("recommendation_ready requires complete proposed target data")
        if not has_proposal and any(part is not None for part in proposal_parts):
            raise ValueError("non-recommendation result cannot carry proposed target data")
        if self.proposed_calorie_target is not None:
            proposed = _require_decimal("proposed_calorie_target", self.proposed_calorie_target)
            delta = _require_decimal("calorie_delta", self.calorie_delta)
            if proposed <= 0 or proposed - current != delta or delta == 0:
                raise ValueError("proposed calorie target/delta is inconsistent")


@dataclass(frozen=True)
class TargetReview:
    review_id: UUID
    evaluation: TargetReviewEvaluation
    created_at: datetime

    def __post_init__(self) -> None:
        _require_aware("target review created_at", self.created_at)

    @property
    def user_id(self) -> UUID:
        return self.evaluation.user_id

    @property
    def recommendation_digest(self) -> str:
        return self.evaluation.recommendation_digest


@dataclass(frozen=True)
class PersistTargetReviewOutcome:
    review: TargetReview
    created: bool


@dataclass(frozen=True)
class TargetReviewDecision:
    decision_id: UUID
    user_id: UUID
    review_id: UUID
    decision: TargetReviewDecisionValue
    rationale: str
    resulting_target_policy_version_id: UUID | None
    client_event_id: UUID
    decided_at: datetime

    def __post_init__(self) -> None:
        if not self.rationale.strip():
            raise ValueError("target review decision rationale must not be empty")
        _require_aware("target review decided_at", self.decided_at)
        if self.decision is TargetReviewDecisionValue.APPROVED:
            if self.resulting_target_policy_version_id is None:
                raise ValueError("approved review decision requires a resulting target policy")
        elif self.resulting_target_policy_version_id is not None:
            raise ValueError("rejected review decision cannot reference a target policy")


@dataclass(frozen=True)
class DecideTargetReviewOutcome:
    review_decision: TargetReviewDecision
    resulting_policy: TargetPolicyVersion | None
    target_decision_log: DecisionLogEntry | None
    created: bool

    def __post_init__(self) -> None:
        approved = self.review_decision.decision is TargetReviewDecisionValue.APPROVED
        if approved and (self.resulting_policy is None or self.target_decision_log is None):
            raise ValueError("approved outcome requires target policy and decision log")
        if not approved and (
            self.resulting_policy is not None or self.target_decision_log is not None
        ):
            raise ValueError("rejected outcome cannot create target-policy records")
        if self.resulting_policy is not None:
            if (
                self.resulting_policy.version_id
                != self.review_decision.resulting_target_policy_version_id
            ):
                raise ValueError("review decision and resulting target policy disagree")
            assert self.target_decision_log is not None
            if self.target_decision_log.policy_version_id != self.resulting_policy.version_id:
                raise ValueError("target decision log references the wrong policy")


def _goal_snapshot_document(goal: TargetGoalSnapshot) -> dict[str, str | None]:
    return {
        "kind": goal.kind.value,
        "nutrient": goal.nutrient.value,
        "value": _decimal_text(goal.value),
        "weight": _decimal_text(goal.weight) if goal.weight is not None else None,
    }


def _trend_document(trend: BodyMassTrendSummary) -> dict[str, object]:
    return {
        "algorithm_version": trend.algorithm_version,
        "as_of_date": trend.as_of_date.isoformat(),
        "coverage_span_days": trend.coverage_span_days,
        "first_measurement_date": (
            trend.first_measurement_date.isoformat() if trend.first_measurement_date else None
        ),
        "input_digest": trend.input_digest,
        "last_measurement_date": (
            trend.last_measurement_date.isoformat() if trend.last_measurement_date else None
        ),
        "latest_measurement_age_days": trend.latest_measurement_age_days,
        "latest_measurement_date": (
            trend.latest_measurement_date.isoformat() if trend.latest_measurement_date else None
        ),
        "represented_day_count": trend.represented_day_count,
        "status": trend.status.value,
        "timezone": trend.timezone,
        "trailing_7d_average_kg": (
            _decimal_text(trend.trailing_7d_average_kg)
            if trend.trailing_7d_average_kg is not None
            else None
        ),
        "weekly_rate_kg": (
            _decimal_text(trend.weekly_rate_kg) if trend.weekly_rate_kg is not None else None
        ),
    }


def _goal_policy_document(goal: GoalPolicyVersion) -> dict[str, object]:
    return {
        "created_at": _datetime_text(goal.created_at),
        "desired_rate_kg_per_week": _decimal_text(goal.desired_rate_kg_per_week),
        "direction": goal.direction.value,
        "payload_sha256": goal.payload_sha256,
        "policy_version": goal.policy_version,
        "user_id": str(goal.user_id),
        "version_id": str(goal.version_id),
    }


def _review_policy_document(policy: TargetReviewPolicy) -> dict[str, object]:
    return {
        "adjustment_step_kcal": _decimal_text(policy.adjustment_step_kcal),
        "cooldown_days": policy.cooldown_days,
        "deadband_kg_per_week": _decimal_text(policy.deadband_kg_per_week),
        "lower_calorie_bound": (
            _decimal_text(policy.lower_calorie_bound)
            if policy.lower_calorie_bound is not None
            else None
        ),
        "policy_version": policy.policy_version,
        "upper_calorie_bound": (
            _decimal_text(policy.upper_calorie_bound)
            if policy.upper_calorie_bound is not None
            else None
        ),
    }


def target_review_evaluation_document(evaluation: TargetReviewEvaluation) -> dict[str, object]:
    """Return the canonical persisted evidence document for one evaluation."""

    return {
        "as_of_date": evaluation.as_of_date.isoformat(),
        "current_calorie_target": _decimal_text(evaluation.current_calorie_target),
        "current_goals": [_goal_snapshot_document(goal) for goal in evaluation.current_goals],
        "goal_policy": _goal_policy_document(evaluation.goal_policy),
        "prior_target_policy": {
            "approved_at": _datetime_text(evaluation.prior_target_approved_at),
            "payload_sha256": evaluation.prior_target_payload_sha256,
            "policy_version": evaluation.prior_target_policy_version,
            "version_id": str(evaluation.prior_target_policy_version_id),
        },
        "proposal": {
            "calorie_delta": (
                _decimal_text(evaluation.calorie_delta)
                if evaluation.calorie_delta is not None
                else None
            ),
            "goals": (
                [_goal_snapshot_document(goal) for goal in evaluation.proposed_goals]
                if evaluation.proposed_goals is not None
                else None
            ),
            "proposed_calorie_target": (
                _decimal_text(evaluation.proposed_calorie_target)
                if evaluation.proposed_calorie_target is not None
                else None
            ),
        },
        "reason_codes": [reason.value for reason in evaluation.reason_codes],
        "review_policy": _review_policy_document(evaluation.review_policy),
        "status": evaluation.status.value,
        "trend": _trend_document(evaluation.trend),
        "user_id": str(evaluation.user_id),
    }


def _digest_document(document: dict[str, object]) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _target_goal_snapshots(targets: TargetSet) -> tuple[TargetGoalSnapshot, ...]:
    return tuple(
        TargetGoalSnapshot(
            nutrient=key,
            kind=goal.kind,
            value=goal.value,
            weight=goal.weight,
        )
        for key, goal in sorted(targets.goals.items(), key=lambda item: item[0].value)
    )


def _replace_calories(
    goals: tuple[TargetGoalSnapshot, ...], proposed: Decimal
) -> tuple[TargetGoalSnapshot, ...]:
    return tuple(
        TargetGoalSnapshot(goal.nutrient, goal.kind, proposed, goal.weight)
        if goal.nutrient is NutrientKey.CALORIES_KCAL
        else goal
        for goal in goals
    )


def _evidence_reason(status: BodyMassTrendStatus) -> TargetReviewReason:
    mapping = {
        BodyMassTrendStatus.NO_DATA: TargetReviewReason.NO_DATA,
        BodyMassTrendStatus.INSUFFICIENT: TargetReviewReason.INSUFFICIENT,
        BodyMassTrendStatus.STALE: TargetReviewReason.STALE,
    }
    return mapping[status]


def evaluate_target_review(
    *,
    trend: BodyMassTrendSummary,
    goal_policy: GoalPolicyVersion,
    review_policy: TargetReviewPolicy,
    current_target_policy: TargetPolicyVersion,
    current_targets: TargetSet,
    current_target_approved_at: datetime,
    as_of_date: date,
) -> TargetReviewEvaluation:
    """Evaluate one immutable review without persistence or target mutation."""

    if trend.as_of_date != as_of_date:
        raise ValueError("review as_of_date must match the trend summary")
    if goal_policy.user_id != current_target_policy.user_id:
        raise ValueError("goal and target policies must belong to the same user")
    _require_aware("current target approved_at", current_target_approved_at)
    if (
        current_target_policy.created_at is not None
        and current_target_policy.created_at != current_target_approved_at
    ):
        raise ValueError("current target approval must match the persisted target policy")
    if current_target_policy.version_id.int == 0:
        raise ValueError("current target policy identity must not be nil")

    current_goals = _target_goal_snapshots(current_targets)
    calorie_goals = [goal for goal in current_goals if goal.nutrient is NutrientKey.CALORIES_KCAL]
    if len(calorie_goals) != 1 or calorie_goals[0].kind is not GoalKind.TARGET:
        raise ValueError("current target policy must contain one calorie target goal")
    current_calories = calorie_goals[0].value
    if current_calories <= 0:
        raise ValueError("current calorie target must be positive")

    status: TargetReviewStatus
    reasons: tuple[TargetReviewReason, ...]
    proposed: Decimal | None = None
    delta: Decimal | None = None
    proposed_goals: tuple[TargetGoalSnapshot, ...] | None = None

    if trend.status is not BodyMassTrendStatus.READY:
        status = TargetReviewStatus.EVIDENCE_UNAVAILABLE
        reasons = (_evidence_reason(trend.status),)
    else:
        alignment = interpret_goal_band(
            trend=trend,
            goal_policy=goal_policy,
            review_policy=review_policy,
        )
        observed = alignment.observed_rate_kg_per_week
        if observed is None:
            raise ValueError("ready trend must carry a weekly rate")
        approval_date = current_target_approved_at.astimezone(ZoneInfo(trend.timezone)).date()
        target_age_days = (as_of_date - approval_date).days
        if target_age_days < 0:
            raise ValueError("current target approval cannot be after review date")
        if target_age_days < review_policy.cooldown_days:
            status = TargetReviewStatus.COOLDOWN_HOLD
            reasons = (TargetReviewReason.COOLDOWN_ACTIVE,)
        else:
            if alignment.status is GoalBandStatus.WITHIN_BAND:
                status = TargetReviewStatus.WITHIN_BAND
                reasons = (TargetReviewReason.WITHIN_BAND,)
            elif alignment.status is GoalBandStatus.BELOW_BAND:
                raw = current_calories + review_policy.adjustment_step_kcal
                upper = review_policy.upper_calorie_bound
                proposed = min(raw, upper) if upper is not None else raw
                if proposed <= current_calories:
                    status = TargetReviewStatus.BOUND_HOLD
                    reasons = (
                        TargetReviewReason.OBSERVED_BELOW_BAND,
                        TargetReviewReason.UPPER_BOUND_HOLD,
                    )
                    proposed = None
                else:
                    status = TargetReviewStatus.RECOMMENDATION_READY
                    reasons = (TargetReviewReason.OBSERVED_BELOW_BAND,)
                    if upper is not None and proposed == upper and proposed != raw:
                        reasons += (TargetReviewReason.UPPER_BOUND_APPLIED,)
            else:
                raw = current_calories - review_policy.adjustment_step_kcal
                lower = review_policy.lower_calorie_bound
                if lower is None and raw <= 0:
                    status = TargetReviewStatus.BOUND_HOLD
                    reasons = (
                        TargetReviewReason.OBSERVED_ABOVE_BAND,
                        TargetReviewReason.POSITIVE_TARGET_HOLD,
                    )
                    proposed = None
                else:
                    proposed = max(raw, lower) if lower is not None else raw
                    if proposed >= current_calories:
                        status = TargetReviewStatus.BOUND_HOLD
                        reasons = (
                            TargetReviewReason.OBSERVED_ABOVE_BAND,
                            TargetReviewReason.LOWER_BOUND_HOLD,
                        )
                        proposed = None
                    else:
                        status = TargetReviewStatus.RECOMMENDATION_READY
                        reasons = (TargetReviewReason.OBSERVED_ABOVE_BAND,)
                        if lower is not None and proposed == lower and proposed != raw:
                            reasons += (TargetReviewReason.LOWER_BOUND_APPLIED,)

    if proposed is not None:
        delta = proposed - current_calories
        proposed_goals = _replace_calories(current_goals, proposed)

    placeholder = TargetReviewEvaluation(
        user_id=goal_policy.user_id,
        as_of_date=as_of_date,
        trend=trend,
        goal_policy=goal_policy,
        review_policy=review_policy,
        prior_target_policy_version_id=current_target_policy.version_id,
        prior_target_policy_version=current_target_policy.policy_version,
        prior_target_payload_sha256=current_target_policy.payload_sha256,
        prior_target_approved_at=current_target_approved_at,
        current_goals=current_goals,
        status=status,
        reason_codes=reasons,
        current_calorie_target=current_calories,
        proposed_calorie_target=proposed,
        calorie_delta=delta,
        proposed_goals=proposed_goals,
        recommendation_digest="0" * 64,
    )
    digest = _digest_document(target_review_evaluation_document(placeholder))
    return replace(placeholder, recommendation_digest=digest)


def _required(document: dict[str, Any], key: str, expected: type[Any]) -> Any:
    value = document.get(key)
    if not isinstance(value, expected):
        raise ValueError(f"invalid target review document field: {key}")
    return value


def _optional_date(value: object) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid optional date in target review document")
    return date.fromisoformat(value)


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid optional decimal in target review document")
    return Decimal(value)


def _goal_snapshot_from_document(raw: object) -> TargetGoalSnapshot:
    if not isinstance(raw, dict):
        raise ValueError("invalid goal snapshot")
    weight = raw.get("weight")
    if weight is not None and not isinstance(weight, str):
        raise ValueError("invalid goal weight")
    return TargetGoalSnapshot(
        nutrient=NutrientKey(_required(raw, "nutrient", str)),
        kind=GoalKind(_required(raw, "kind", str)),
        value=Decimal(_required(raw, "value", str)),
        weight=Decimal(weight) if weight is not None else None,
    )


def target_review_evaluation_from_document(
    document: dict[str, Any], recommendation_digest: str
) -> TargetReviewEvaluation:
    """Reconstruct and verify a stored canonical evaluation document."""

    if _digest_document(document) != recommendation_digest:
        raise ValueError("stored target review digest does not match its evidence")
    trend_raw = _required(document, "trend", dict)
    goal_raw = _required(document, "goal_policy", dict)
    review_raw = _required(document, "review_policy", dict)
    prior_raw = _required(document, "prior_target_policy", dict)
    proposal_raw = _required(document, "proposal", dict)
    current_raw = _required(document, "current_goals", list)
    proposed_raw = proposal_raw.get("goals")
    if proposed_raw is not None and not isinstance(proposed_raw, list):
        raise ValueError("invalid proposed goals")
    reasons_raw = _required(document, "reason_codes", list)
    if not all(isinstance(reason, str) for reason in reasons_raw):
        raise ValueError("invalid target review reason codes")

    trend = BodyMassTrendSummary(
        as_of_date=date.fromisoformat(_required(trend_raw, "as_of_date", str)),
        timezone=_required(trend_raw, "timezone", str),
        algorithm_version=_required(trend_raw, "algorithm_version", str),
        latest_measurement_date=_optional_date(trend_raw.get("latest_measurement_date")),
        latest_measurement_age_days=trend_raw.get("latest_measurement_age_days"),
        first_measurement_date=_optional_date(trend_raw.get("first_measurement_date")),
        last_measurement_date=_optional_date(trend_raw.get("last_measurement_date")),
        represented_day_count=_required(trend_raw, "represented_day_count", int),
        coverage_span_days=_required(trend_raw, "coverage_span_days", int),
        trailing_7d_average_kg=_optional_decimal(trend_raw.get("trailing_7d_average_kg")),
        weekly_rate_kg=_optional_decimal(trend_raw.get("weekly_rate_kg")),
        status=BodyMassTrendStatus(_required(trend_raw, "status", str)),
        input_digest=_required(trend_raw, "input_digest", str),
    )
    goal = GoalPolicyVersion(
        version_id=UUID(_required(goal_raw, "version_id", str)),
        user_id=UUID(_required(goal_raw, "user_id", str)),
        policy_version=_required(goal_raw, "policy_version", str),
        direction=GoalDirection(_required(goal_raw, "direction", str)),
        desired_rate_kg_per_week=Decimal(_required(goal_raw, "desired_rate_kg_per_week", str)),
        payload_sha256=_required(goal_raw, "payload_sha256", str),
        created_at=datetime.fromisoformat(
            _required(goal_raw, "created_at", str).replace("Z", "+00:00")
        ),
    )
    review = TargetReviewPolicy(
        policy_version=_required(review_raw, "policy_version", str),
        deadband_kg_per_week=Decimal(_required(review_raw, "deadband_kg_per_week", str)),
        adjustment_step_kcal=Decimal(_required(review_raw, "adjustment_step_kcal", str)),
        cooldown_days=_required(review_raw, "cooldown_days", int),
        lower_calorie_bound=_optional_decimal(review_raw.get("lower_calorie_bound")),
        upper_calorie_bound=_optional_decimal(review_raw.get("upper_calorie_bound")),
    )
    evaluation = TargetReviewEvaluation(
        user_id=UUID(_required(document, "user_id", str)),
        as_of_date=date.fromisoformat(_required(document, "as_of_date", str)),
        trend=trend,
        goal_policy=goal,
        review_policy=review,
        prior_target_policy_version_id=UUID(_required(prior_raw, "version_id", str)),
        prior_target_policy_version=_required(prior_raw, "policy_version", str),
        prior_target_payload_sha256=_required(prior_raw, "payload_sha256", str),
        prior_target_approved_at=datetime.fromisoformat(
            _required(prior_raw, "approved_at", str).replace("Z", "+00:00")
        ),
        current_goals=tuple(_goal_snapshot_from_document(item) for item in current_raw),
        status=TargetReviewStatus(_required(document, "status", str)),
        reason_codes=tuple(TargetReviewReason(reason) for reason in reasons_raw),
        current_calorie_target=Decimal(_required(document, "current_calorie_target", str)),
        proposed_calorie_target=_optional_decimal(proposal_raw.get("proposed_calorie_target")),
        calorie_delta=_optional_decimal(proposal_raw.get("calorie_delta")),
        proposed_goals=(
            tuple(_goal_snapshot_from_document(item) for item in proposed_raw)
            if proposed_raw is not None
            else None
        ),
        recommendation_digest=recommendation_digest,
    )
    if evaluation.user_id != evaluation.goal_policy.user_id:
        raise ValueError("stored target review owner is inconsistent")
    return evaluation


__all__ = [
    "DecideTargetReviewOutcome",
    "GoalBandInterpretation",
    "GoalBandStatus",
    "GoalDirection",
    "GoalPolicyVersion",
    "M10A_TARGET_REVIEW_POLICY",
    "PersistTargetReviewOutcome",
    "TARGET_REVIEW_POLICY_VERSION",
    "TargetGoalSnapshot",
    "TargetReview",
    "TargetReviewDecision",
    "TargetReviewDecisionValue",
    "TargetReviewEvaluation",
    "TargetReviewPolicy",
    "TargetReviewReason",
    "TargetReviewStatus",
    "evaluate_target_review",
    "goal_policy_payload_sha256",
    "interpret_goal_band",
    "target_review_evaluation_document",
    "target_review_evaluation_from_document",
]
