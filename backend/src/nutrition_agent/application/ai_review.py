"""Owner-triggered orchestration for an explanatory, non-authoritative AI review."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from nutrition_agent.application.daily_nutrition_ledger import GetDailyNutritionLedgerUseCase
from nutrition_agent.application.ports import NextMealRecommendationRepository
from nutrition_agent.application.progress import GetLongitudinalProgressUseCase
from nutrition_agent.domain.ai_review import (
    AI_REVIEW_SNAPSHOT_VERSION,
    AIReviewContent,
    AIReviewFailureCode,
    AIReviewResult,
    AIReviewSnapshot,
    AIReviewStatus,
)
from nutrition_agent.domain.next_meal import NextMealRecommendation
from nutrition_agent.domain.nutrition.ledger import DailyNutritionLedger
from nutrition_agent.domain.nutrition.quality import candidate_quality
from nutrition_agent.domain.progress import LongitudinalProgress


class AIReviewProviderFailure(StrEnum):
    PRIVACY_DISABLED = "privacy_disabled"
    NOT_CONFIGURED = "not_configured"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"
    REFUSED = "refused"


class AIReviewProviderError(RuntimeError):
    def __init__(self, failure: AIReviewProviderFailure) -> None:
        super().__init__(failure.value)
        self.failure = failure


class AIReviewProvider(Protocol):
    def generate(self, snapshot: AIReviewSnapshot) -> AIReviewContent: ...


def recorded_nutrition_evidence_category(snapshot: AIReviewSnapshot) -> str:
    """Describe recorded evidence explicitly for the minimized local-model input.

    Completeness alone is ambiguous to a language model: ``partial`` does not
    state that a real consumption record exists, while ``unavailable`` may
    still describe a recorded item whose supported nutrients are all unknown.
    The item count remains the authority for whether consumption was recorded.
    """

    if snapshot.recorded_item_count_today == 0:
        return "none"
    if snapshot.recorded_nutrition_completeness == "complete":
        return "recorded_complete"
    return "recorded_partial"


def build_ai_review_snapshot(
    *,
    as_of_date: date,
    timezone: str,
    ledger: DailyNutritionLedger,
    progress: LongitudinalProgress,
    latest_next_meal: NextMealRecommendation | None,
) -> AIReviewSnapshot:
    """Project only aggregate, allowlisted facts; raw evidence never crosses the boundary."""

    target = ledger.target
    current_next_meal = (
        latest_next_meal
        if latest_next_meal is not None and latest_next_meal.local_date == as_of_date
        else None
    )
    quality = (
        candidate_quality(current_next_meal.artifact_jsonb.get("selected"))
        if current_next_meal
        else None
    )
    findings = quality.get("findings") if quality else None
    quality_flags = (
        tuple(
            f"selected_{finding['nutrient']}_{finding['band']}"
            for finding in findings
            if isinstance(finding, dict)
        )
        if isinstance(findings, list)
        else ()
    )
    interpretation = progress.goal_interpretation
    return AIReviewSnapshot(
        snapshot_version=AI_REVIEW_SNAPSHOT_VERSION,
        as_of_date=as_of_date,
        timezone=timezone,
        goal_mode=(progress.current_goal.direction.value if progress.current_goal else None),
        goal_band_status=(interpretation.status.value if interpretation else "unavailable"),
        calorie_target_kcal=target.calories_kcal if target else None,
        calorie_target_kind=target.calories_goal_kind if target else None,
        protein_target_g=target.protein_g if target else None,
        protein_target_kind=target.protein_goal_kind if target else None,
        recorded_item_count_today=ledger.consumed_item_count,
        recorded_nutrition_completeness=ledger.nutrition_completeness.value,
        recorded_calories_today_kcal=ledger.known_calories_consumed,
        recorded_protein_today_g=ledger.known_protein_g_consumed,
        recorded_nutrition_reason_codes=ledger.reason_codes,
        recorded_nutrition_authorities=tuple(value.value for value in ledger.authorities),
        body_trend_status=progress.body_trend_28d.status.value,
        body_latest_measurement_age_days=progress.body_trend_28d.latest_measurement_age_days,
        body_represented_day_count=progress.body_trend_28d.represented_day_count,
        body_coverage_span_days=progress.body_trend_28d.coverage_span_days,
        body_trailing_7d_average_kg=getattr(
            progress.body_trend_28d, "trailing_7d_average_kg", None
        ),
        body_weekly_rate_kg=getattr(progress.body_trend_28d, "weekly_rate_kg", None),
        nutrition_days_recorded_7d=progress.nutrition_summary_7d.days_with_recorded_events,
        nutrition_days_recorded_28d=progress.nutrition_summary_28d.days_with_recorded_events,
        nutrition_calorie_quantified_days_7d=(
            progress.nutrition_summary_7d.calories.quantified_recorded_days
        ),
        nutrition_protein_quantified_days_7d=(
            progress.nutrition_summary_7d.protein.quantified_recorded_days
        ),
        nutrition_includes_estimates_7d=progress.nutrition_summary_7d.includes_estimates,
        nutrition_average_calories_7d=getattr(
            progress.nutrition_summary_7d.calories, "average_recorded_value", None
        ),
        nutrition_average_protein_g_7d=getattr(
            progress.nutrition_summary_7d.protein, "average_recorded_value", None
        ),
        next_meal_status=(current_next_meal.status.value if current_next_meal else "not_generated"),
        next_meal_reason_codes=(current_next_meal.reason_codes if current_next_meal else ()),
        limitation_codes=progress.limitation_codes,
        nutrition_quality_flags=quality_flags,
    )


@dataclass(frozen=True)
class GenerateAIReviewUseCase:
    ledger: GetDailyNutritionLedgerUseCase
    progress: GetLongitudinalProgressUseCase
    next_meals: NextMealRecommendationRepository
    provider: AIReviewProvider

    def snapshot(
        self,
        *,
        user_id: UUID,
        as_of_date: date,
        timezone: str,
    ) -> AIReviewSnapshot:
        ledger = self.ledger.execute(
            user_id=user_id,
            local_date=as_of_date,
            timezone=timezone,
        )
        progress = self.progress.execute(
            user_id=user_id,
            as_of_date=as_of_date,
            timezone=timezone,
        )
        return build_ai_review_snapshot(
            as_of_date=as_of_date,
            timezone=timezone,
            ledger=ledger,
            progress=progress,
            latest_next_meal=self.next_meals.latest(user_id),
        )

    def execute(self, *, user_id: UUID, as_of_date: date, timezone: str) -> AIReviewResult:
        snapshot = self.snapshot(user_id=user_id, as_of_date=as_of_date, timezone=timezone)
        try:
            review = self.provider.generate(snapshot)
        except AIReviewProviderError as exc:
            return AIReviewResult(
                status=AIReviewStatus.UNAVAILABLE,
                snapshot=snapshot,
                review=None,
                failure_code=_failure_code(exc.failure),
            )
        except Exception:  # noqa: BLE001 - provider detail must not escape this boundary
            return AIReviewResult(
                status=AIReviewStatus.UNAVAILABLE,
                snapshot=snapshot,
                review=None,
                failure_code=AIReviewFailureCode.PROVIDER_UNAVAILABLE,
            )
        return AIReviewResult(
            status=AIReviewStatus.AVAILABLE,
            snapshot=snapshot,
            review=review,
            failure_code=None,
        )


def _failure_code(failure: AIReviewProviderFailure) -> AIReviewFailureCode:
    return {
        AIReviewProviderFailure.PRIVACY_DISABLED: AIReviewFailureCode.PRIVACY_DISABLED,
        AIReviewProviderFailure.NOT_CONFIGURED: AIReviewFailureCode.NOT_CONFIGURED,
        AIReviewProviderFailure.RATE_LIMITED: AIReviewFailureCode.RATE_LIMITED,
        AIReviewProviderFailure.TIMEOUT: AIReviewFailureCode.TIMEOUT,
        AIReviewProviderFailure.UNAVAILABLE: AIReviewFailureCode.PROVIDER_UNAVAILABLE,
        AIReviewProviderFailure.INVALID_RESPONSE: AIReviewFailureCode.PROVIDER_INVALID,
        AIReviewProviderFailure.REFUSED: AIReviewFailureCode.PROVIDER_REFUSED,
    }[failure]


__all__ = [
    "AIReviewProvider",
    "AIReviewProviderError",
    "AIReviewProviderFailure",
    "GenerateAIReviewUseCase",
    "build_ai_review_snapshot",
    "recorded_nutrition_evidence_category",
]
