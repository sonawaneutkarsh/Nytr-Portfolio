"""Pure, versioned selection of a factual Lunch planning context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.training.context import (
    TrainingDayContext,
    TrainingDayReasonCode,
)


class TrainingEvidenceState(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class MealContextSelectionReason(StrEnum):
    QUALIFYING_WORKOUT_BEFORE_LUNCH = "qualifying_workout_before_lunch"
    NO_QUALIFYING_WORKOUT = "no_qualifying_workout"
    QUALIFYING_WORKOUT_ONLY_AFTER_LUNCH = "qualifying_workout_only_after_lunch"
    TRAINING_EVIDENCE_UNAVAILABLE = "training_evidence_unavailable"


@dataclass(frozen=True)
class TrainingAwareMealContextPolicy:
    version: str
    post_workout_context: MealContext
    neutral_lunch_context: MealContext

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("meal-context policy version is required")
        if self.post_workout_context is not MealContext.POST_WORKOUT_LUNCH:
            raise ValueError("post-workout context must be explicit")
        if self.neutral_lunch_context is not MealContext.LUNCH:
            raise ValueError("neutral Lunch context must be explicit")


OWNER_TRAINING_AWARE_MEAL_CONTEXT_POLICY_V1 = TrainingAwareMealContextPolicy(
    version="owner-training-aware-meal-context.v1",
    post_workout_context=MealContext.POST_WORKOUT_LUNCH,
    neutral_lunch_context=MealContext.LUNCH,
)


@dataclass(frozen=True)
class MealContextSelection:
    local_date: date
    lunch_starts_at: datetime
    policy_version: str
    training_policy_version: str | None
    evidence_state: TrainingEvidenceState
    selected_context: MealContext
    reason_code: MealContextSelectionReason
    training_reason_codes: tuple[TrainingDayReasonCode, ...]
    qualifying_prior_session_ids: tuple[UUID, ...]


def select_lunch_meal_context(
    *,
    local_date: date,
    timezone: str,
    lunch_starts_at: datetime,
    training_context: TrainingDayContext | None,
    policy: TrainingAwareMealContextPolicy = OWNER_TRAINING_AWARE_MEAL_CONTEXT_POLICY_V1,
) -> MealContextSelection:
    """Select post-workout only from a completed qualifying workout before Lunch.

    The temporal boundary is deliberately strict: a workout ending exactly when
    Lunch begins is not described as completed *before* Lunch.
    """

    if lunch_starts_at.tzinfo is None or lunch_starts_at.utcoffset() is None:
        raise ValueError("lunch_starts_at must be timezone-aware")
    if training_context is None:
        return _unavailable_selection(local_date, lunch_starts_at, policy)
    if (
        training_context.local_date != local_date
        or training_context.timezone != timezone
        or training_context.is_training_day != bool(training_context.qualifying_sessions)
        or training_context.qualifying_session_count != len(training_context.qualifying_sessions)
    ):
        return _unavailable_selection(local_date, lunch_starts_at, policy)

    prior = tuple(
        evidence
        for evidence in training_context.qualifying_sessions
        if evidence.ended_at.astimezone(lunch_starts_at.tzinfo) < lunch_starts_at
    )
    if prior:
        return MealContextSelection(
            local_date=local_date,
            lunch_starts_at=lunch_starts_at,
            policy_version=policy.version,
            training_policy_version=training_context.policy_version,
            evidence_state=TrainingEvidenceState.AVAILABLE,
            selected_context=policy.post_workout_context,
            reason_code=MealContextSelectionReason.QUALIFYING_WORKOUT_BEFORE_LUNCH,
            training_reason_codes=training_context.reason_codes,
            qualifying_prior_session_ids=tuple(item.session_id for item in prior),
        )

    reason = (
        MealContextSelectionReason.QUALIFYING_WORKOUT_ONLY_AFTER_LUNCH
        if training_context.qualifying_sessions
        else MealContextSelectionReason.NO_QUALIFYING_WORKOUT
    )
    return MealContextSelection(
        local_date=local_date,
        lunch_starts_at=lunch_starts_at,
        policy_version=policy.version,
        training_policy_version=training_context.policy_version,
        evidence_state=TrainingEvidenceState.AVAILABLE,
        selected_context=policy.neutral_lunch_context,
        reason_code=reason,
        training_reason_codes=training_context.reason_codes,
        qualifying_prior_session_ids=(),
    )


def _unavailable_selection(
    local_date: date,
    lunch_starts_at: datetime,
    policy: TrainingAwareMealContextPolicy,
) -> MealContextSelection:
    return MealContextSelection(
        local_date=local_date,
        lunch_starts_at=lunch_starts_at,
        policy_version=policy.version,
        training_policy_version=None,
        evidence_state=TrainingEvidenceState.UNAVAILABLE,
        selected_context=policy.neutral_lunch_context,
        reason_code=MealContextSelectionReason.TRAINING_EVIDENCE_UNAVAILABLE,
        training_reason_codes=(),
        qualifying_prior_session_ids=(),
    )


__all__ = [
    "MealContextSelection",
    "MealContextSelectionReason",
    "OWNER_TRAINING_AWARE_MEAL_CONTEXT_POLICY_V1",
    "TrainingAwareMealContextPolicy",
    "TrainingEvidenceState",
    "select_lunch_meal_context",
]
