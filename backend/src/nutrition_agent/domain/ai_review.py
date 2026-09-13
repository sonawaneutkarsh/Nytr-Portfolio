"""Bounded, non-authoritative AI review contracts (M21)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

AI_REVIEW_SNAPSHOT_VERSION = "owner-ai-review-snapshot.v2"
AI_REVIEW_PROMPT_VERSION = "owner-ai-review-prompt.v2"


class AIReviewStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class AIReviewFailureCode(StrEnum):
    PRIVACY_DISABLED = "ai_disabled_for_privacy"
    NOT_CONFIGURED = "ai_not_configured"
    RATE_LIMITED = "ai_rate_limited"
    TIMEOUT = "ai_timeout"
    PROVIDER_UNAVAILABLE = "ai_provider_unavailable"
    PROVIDER_INVALID = "ai_provider_invalid"
    PROVIDER_REFUSED = "ai_provider_refused"


@dataclass(frozen=True)
class AIReviewSnapshot:
    """Minimal aggregate state that an external provider may inspect."""

    snapshot_version: str
    as_of_date: date
    timezone: str
    goal_mode: str | None
    goal_band_status: str
    calorie_target_kcal: Decimal | None
    calorie_target_kind: str | None
    protein_target_g: Decimal | None
    protein_target_kind: str | None
    recorded_item_count_today: int
    recorded_nutrition_completeness: str
    recorded_calories_today_kcal: Decimal | None
    recorded_protein_today_g: Decimal | None
    recorded_nutrition_reason_codes: tuple[str, ...]
    recorded_nutrition_authorities: tuple[str, ...]
    body_trend_status: str
    body_latest_measurement_age_days: int | None
    body_represented_day_count: int
    body_coverage_span_days: int
    nutrition_days_recorded_7d: int
    nutrition_days_recorded_28d: int
    nutrition_calorie_quantified_days_7d: int
    nutrition_protein_quantified_days_7d: int
    nutrition_includes_estimates_7d: bool
    next_meal_status: str
    next_meal_reason_codes: tuple[str, ...]
    limitation_codes: tuple[str, ...]
    body_trailing_7d_average_kg: Decimal | None = None
    body_weekly_rate_kg: Decimal | None = None
    nutrition_average_calories_7d: Decimal | None = None
    nutrition_average_protein_g_7d: Decimal | None = None
    nutrition_quality_flags: tuple[str, ...] = ()

    def provider_document(self) -> dict[str, object]:
        """Return the categorical third-party allowlist, without personal numeric evidence."""

        return {
            "snapshot_version": self.snapshot_version,
            "goal": {"mode": self.goal_mode, "band_status": self.goal_band_status},
            "targets": {
                "calorie_target_available": self.calorie_target_kcal is not None,
                "calories_kind": self.calorie_target_kind,
                "protein_target_available": self.protein_target_g is not None,
                "protein_kind": self.protein_target_kind,
            },
            "today_recorded": {
                "has_records": self.recorded_item_count_today > 0,
                "completeness": self.recorded_nutrition_completeness,
                "calories_available": self.recorded_calories_today_kcal is not None,
                "protein_available": self.recorded_protein_today_g is not None,
                "reason_codes": list(self.recorded_nutrition_reason_codes),
                "authorities": list(self.recorded_nutrition_authorities),
            },
            "body_trend": {"status": self.body_trend_status},
            "recorded_nutrition_progress": {
                "has_recent_records": self.nutrition_days_recorded_7d > 0,
                "has_longer_window_records": self.nutrition_days_recorded_28d > 0,
                "calorie_evidence_available": self.nutrition_calorie_quantified_days_7d > 0,
                "protein_evidence_available": self.nutrition_protein_quantified_days_7d > 0,
                "includes_estimates": self.nutrition_includes_estimates_7d,
            },
            "next_meal": {
                "status": self.next_meal_status,
                "reason_codes": list(self.next_meal_reason_codes),
            },
            "limitations": list(self.limitation_codes),
        }

    def client_document(self) -> dict[str, object]:
        """Return deterministic owner evidence to the authenticated first-party client."""

        return {
            "snapshot_version": self.snapshot_version,
            "as_of_date": self.as_of_date.isoformat(),
            "timezone": self.timezone,
            "goal": {
                "mode": self.goal_mode,
                "band_status": self.goal_band_status,
            },
            "targets": {
                "calories_kcal": _decimal(self.calorie_target_kcal),
                "calories_kind": self.calorie_target_kind,
                "protein_g": _decimal(self.protein_target_g),
                "protein_kind": self.protein_target_kind,
            },
            "today_recorded": {
                "item_count": self.recorded_item_count_today,
                "completeness": self.recorded_nutrition_completeness,
                "calories_kcal": _decimal(self.recorded_calories_today_kcal),
                "protein_g": _decimal(self.recorded_protein_today_g),
                "reason_codes": list(self.recorded_nutrition_reason_codes),
                "authorities": list(self.recorded_nutrition_authorities),
            },
            "body_trend": {
                "status": self.body_trend_status,
                "latest_measurement_age_days": self.body_latest_measurement_age_days,
                "represented_day_count": self.body_represented_day_count,
                "coverage_span_days": self.body_coverage_span_days,
                "trailing_7d_average_kg": _decimal(self.body_trailing_7d_average_kg),
                "weekly_rate_kg": _decimal(self.body_weekly_rate_kg),
            },
            "recorded_nutrition_progress": {
                "days_with_records_7d": self.nutrition_days_recorded_7d,
                "days_with_records_28d": self.nutrition_days_recorded_28d,
                "calorie_quantified_days_7d": self.nutrition_calorie_quantified_days_7d,
                "protein_quantified_days_7d": self.nutrition_protein_quantified_days_7d,
                "includes_estimates_7d": self.nutrition_includes_estimates_7d,
                "average_recorded_calories_7d": _decimal(self.nutrition_average_calories_7d),
                "average_recorded_protein_g_7d": _decimal(self.nutrition_average_protein_g_7d),
            },
            "next_meal": {
                "status": self.next_meal_status,
                "reason_codes": list(self.next_meal_reason_codes),
            },
            "limitations": list(self.limitation_codes),
        }

    def on_device_document(self) -> dict[str, object]:
        """Exact, bounded facts permitted only for first-party on-device inference."""

        return {
            "goal": self.goal_mode or "unavailable",
            "goal_band_status": self.goal_band_status,
            "weight_evidence": self.body_trend_status,
            "calorie_target_kcal": _decimal(self.calorie_target_kcal),
            "calorie_target_kind": self.calorie_target_kind,
            "protein_target_g": _decimal(self.protein_target_g),
            "protein_target_kind": self.protein_target_kind,
            "calorie_target_available": self.calorie_target_kcal is not None,
            "protein_target_available": self.protein_target_g is not None,
            "recorded_item_count": self.recorded_item_count_today,
            "nutrition_evidence": (
                "none"
                if self.recorded_item_count_today == 0
                else (
                    "recorded_complete"
                    if self.recorded_nutrition_completeness == "complete"
                    else "recorded_partial"
                )
            ),
            "recorded_calories_kcal": _decimal(self.recorded_calories_today_kcal),
            "recorded_protein_g": _decimal(self.recorded_protein_today_g),
            "recorded_nutrition_reason_codes": list(self.recorded_nutrition_reason_codes),
            "recorded_nutrition_authorities": list(self.recorded_nutrition_authorities),
            "weight_trend_status": self.body_trend_status,
            "weight_trailing_7d_average_kg": _decimal(self.body_trailing_7d_average_kg),
            "weight_weekly_rate_kg": _decimal(self.body_weekly_rate_kg),
            "latest_weight_age_days": self.body_latest_measurement_age_days,
            "represented_weight_days": self.body_represented_day_count,
            "weight_coverage_span_days": self.body_coverage_span_days,
            "days_with_records_7d": self.nutrition_days_recorded_7d,
            "days_with_records_28d": self.nutrition_days_recorded_28d,
            "average_recorded_calories_7d": _decimal(self.nutrition_average_calories_7d),
            "average_recorded_protein_g_7d": _decimal(self.nutrition_average_protein_g_7d),
            "includes_estimates": self.nutrition_includes_estimates_7d,
            "next_meal": self.next_meal_status,
            "next_meal_reason_codes": list(self.next_meal_reason_codes),
            "quality_flags": list(self.nutrition_quality_flags),
            "limitation_codes": list(self.limitation_codes),
            "limitation": (
                "Only recorded evidence is known. Never infer missing nutrition, "
                "causality, or target changes."
            ),
        }


@dataclass(frozen=True)
class AIReviewContent:
    summary: str
    attention_items: tuple[str, ...]
    evidence_notes: tuple[str, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        fields = (
            ("summary", (self.summary,), 1, 320),
            ("attention_items", self.attention_items, 0, 220),
            ("evidence_notes", self.evidence_notes, 0, 220),
            ("limitations", self.limitations, 1, 220),
        )
        total = 0
        for name, values, minimum, maximum_length in fields:
            if len(values) < minimum or (name != "summary" and len(values) > 4):
                raise ValueError(f"invalid {name} count")
            for value in values:
                if not isinstance(value, str) or value != value.strip() or not value:
                    raise ValueError(f"invalid {name} text")
                if len(value) > maximum_length or any(character.isdigit() for character in value):
                    raise ValueError(f"unsupported {name} content")
                if any(ord(character) < 32 and character not in "\n\t" for character in value):
                    raise ValueError(f"invalid {name} control character")
                total += len(value)
        if total > 1_400:
            raise ValueError("AI review response is oversized")


@dataclass(frozen=True)
class AIReviewResult:
    status: AIReviewStatus
    snapshot: AIReviewSnapshot
    review: AIReviewContent | None
    failure_code: AIReviewFailureCode | None

    def __post_init__(self) -> None:
        if self.status is AIReviewStatus.AVAILABLE:
            if self.review is None or self.failure_code is not None:
                raise ValueError("available review must contain content only")
        elif self.review is not None or self.failure_code is None:
            raise ValueError("unavailable review must contain one failure code")


def _decimal(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


__all__ = [
    "AI_REVIEW_PROMPT_VERSION",
    "AI_REVIEW_SNAPSHOT_VERSION",
    "AIReviewContent",
    "AIReviewFailureCode",
    "AIReviewResult",
    "AIReviewSnapshot",
    "AIReviewStatus",
]
