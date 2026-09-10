"""Authenticated compute-on-read M18 longitudinal progress API."""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from nutrition_agent.api.routes_planning import VerifierDep, _authenticated_subject, _error
from nutrition_agent.application.progress import (
    GetLongitudinalProgressUseCase,
    InvalidProgressRequest,
)
from nutrition_agent.domain.health.trend import BodyMassTrendSummary
from nutrition_agent.domain.progress import (
    EvidenceCount,
    LongitudinalProgress,
    ProgressNutritionDay,
    ProgressNutritionSummary,
)

logger = logging.getLogger("nutrition_agent.progress_api")
router = APIRouter(prefix="/v1/analytics", tags=["analytics"])


def get_progress_use_case(request: Request) -> GetLongitudinalProgressUseCase:
    use_case: GetLongitudinalProgressUseCase | None = getattr(
        request.app.state,
        "longitudinal_progress_use_case",
        None,
    )
    if use_case is None:
        raise LookupError("longitudinal progress is not configured")
    return use_case


ProgressDep = Annotated[GetLongitudinalProgressUseCase, Depends(get_progress_use_case)]


def _decimal(value: object) -> str | None:
    return str(value) if value is not None else None


def _counts(values: tuple[EvidenceCount, ...]) -> dict[str, int]:
    return {value.key: value.count for value in values}


def _trend(summary: BodyMassTrendSummary) -> dict[str, object]:
    return {
        "status": summary.status.value,
        "as_of_date": summary.as_of_date.isoformat(),
        "timezone": summary.timezone,
        "algorithm_version": summary.algorithm_version,
        "input_digest": summary.input_digest,
        "latest_measurement_date": (
            summary.latest_measurement_date.isoformat()
            if summary.latest_measurement_date is not None
            else None
        ),
        "latest_measurement_age_days": summary.latest_measurement_age_days,
        "first_measurement_date": (
            summary.first_measurement_date.isoformat()
            if summary.first_measurement_date is not None
            else None
        ),
        "last_measurement_date": (
            summary.last_measurement_date.isoformat()
            if summary.last_measurement_date is not None
            else None
        ),
        "represented_day_count": summary.represented_day_count,
        "coverage_span_days": summary.coverage_span_days,
        "trailing_7d_average_kg": _decimal(summary.trailing_7d_average_kg),
        "weekly_rate_kg": _decimal(summary.weekly_rate_kg),
    }


def _target(day: ProgressNutritionDay) -> dict[str, object] | None:
    target = day.target
    if target is None:
        return None
    return {
        "policy_version_id": str(target.policy_version_id),
        "policy_version": target.policy_version,
        "calories_kcal": _decimal(target.calories_kcal),
        "calories_goal_kind": target.calories_goal_kind,
        "protein_g": _decimal(target.protein_g),
        "protein_goal_kind": target.protein_goal_kind,
    }


def _nutrition_day(day: ProgressNutritionDay) -> dict[str, object]:
    return {
        "local_date": day.local_date.isoformat(),
        "has_recorded_events": day.has_recorded_events,
        "recorded_event_count": day.recorded_event_count,
        "recorded_calories": {
            "state": day.recorded_calories_state.value,
            "value_kcal": _decimal(day.recorded_calories_kcal),
        },
        "recorded_protein": {
            "state": day.recorded_protein_state.value,
            "value_g": _decimal(day.recorded_protein_g),
        },
        "recorded_calorie_target_comparison": day.calorie_target_comparison.value,
        "recorded_protein_target_comparison": day.protein_target_comparison.value,
        "target_status": day.target_status.value,
        "target": _target(day),
        "authority_event_counts": _counts(day.authority_counts),
        "source_event_counts": _counts(day.source_counts),
        "includes_estimates": day.includes_estimates,
    }


def _summary(summary: ProgressNutritionSummary) -> dict[str, object]:
    return {
        "window_days": summary.window_days,
        "start_date": summary.start_date.isoformat(),
        "end_date": summary.end_date.isoformat(),
        "coverage": {
            "days_with_recorded_events": summary.days_with_recorded_events,
            "days_without_recorded_events": summary.days_without_recorded_events,
        },
        "recorded_calories": {
            "quantified_recorded_days": summary.calories.quantified_recorded_days,
            "partial_recorded_days": summary.calories.partial_recorded_days,
            "unavailable_recorded_days": summary.calories.unavailable_recorded_days,
            "average_recorded_kcal": _decimal(summary.calories.average_recorded_value),
            "average_denominator_days": summary.calories.average_denominator_days,
        },
        "recorded_protein": {
            "quantified_recorded_days": summary.protein.quantified_recorded_days,
            "partial_recorded_days": summary.protein.partial_recorded_days,
            "unavailable_recorded_days": summary.protein.unavailable_recorded_days,
            "average_recorded_g": _decimal(summary.protein.average_recorded_value),
            "average_denominator_days": summary.protein.average_denominator_days,
        },
        "recorded_calorie_target_comparisons": {
            "eligible_day_count": summary.calorie_target_comparisons.eligible_day_count,
            "below": summary.calorie_target_comparisons.below,
            "at": summary.calorie_target_comparisons.at,
            "above": summary.calorie_target_comparisons.above,
            "unavailable": summary.calorie_target_comparisons.unavailable,
        },
        "recorded_protein_target_comparisons": {
            "eligible_day_count": summary.protein_target_comparisons.eligible_day_count,
            "below": summary.protein_target_comparisons.below,
            "at_or_above": summary.protein_target_comparisons.at_or_above,
            "unavailable": summary.protein_target_comparisons.unavailable,
        },
        "authority_event_counts": _counts(summary.authority_event_counts),
        "source_event_counts": _counts(summary.source_event_counts),
        "includes_estimates": summary.includes_estimates,
    }


def _content(progress: LongitudinalProgress) -> dict[str, object]:
    goal = progress.current_goal
    interpretation = progress.goal_interpretation
    return {
        "policy_version": progress.policy_version,
        "as_of_date": progress.as_of_date.isoformat(),
        "timezone": progress.timezone,
        "body": {
            "window_days": 90,
            "start_date": progress.body_window_start_date.isoformat(),
            "end_date": progress.body_window_end_date.isoformat(),
            "daily_medians": [
                {
                    "local_date": point.local_date.isoformat(),
                    "median_kg": _decimal(point.median_kg),
                    "observation_count": point.observation_count,
                }
                for point in progress.body_daily_medians
            ],
            "trend_28d": _trend(progress.body_trend_28d),
        },
        "goal": {
            "status": interpretation.status.value if interpretation is not None else "unavailable",
            "mode": goal.direction.value if goal is not None else None,
            "desired_rate_kg_per_week": (
                _decimal(interpretation.desired_rate_kg_per_week)
                if interpretation is not None
                else None
            ),
            "observed_rate_kg_per_week": (
                _decimal(interpretation.observed_rate_kg_per_week)
                if interpretation is not None
                else None
            ),
            "acceptable_rate_lower_kg_per_week": (
                _decimal(interpretation.acceptable_rate_lower_kg_per_week)
                if interpretation is not None
                else None
            ),
            "acceptable_rate_upper_kg_per_week": (
                _decimal(interpretation.acceptable_rate_upper_kg_per_week)
                if interpretation is not None
                else None
            ),
        },
        "nutrition": {
            "window_days": 28,
            "start_date": progress.nutrition_window_start_date.isoformat(),
            "end_date": progress.nutrition_window_end_date.isoformat(),
            "days": [_nutrition_day(day) for day in progress.nutrition_days],
            "summary_7d": _summary(progress.nutrition_summary_7d),
            "summary_28d": _summary(progress.nutrition_summary_28d),
            "target_changes": [
                {
                    "effective_at": change.effective_at.isoformat(),
                    "effective_local_date": change.effective_local_date.isoformat(),
                    "target_policy_version_id": str(change.target_policy_version_id),
                    "target_policy_version": change.target_policy_version,
                    "calories_kcal": _decimal(change.calories_kcal),
                    "calories_goal_kind": change.calories_goal_kind,
                    "protein_g": _decimal(change.protein_g),
                    "protein_goal_kind": change.protein_goal_kind,
                }
                for change in progress.target_changes
            ],
        },
        "limitations": {
            "codes": list(progress.limitation_codes),
            "logging_coverage": (
                "Nytr summarizes recorded events only; unlogged food cannot be inferred."
            ),
            "record_time_attribution": (
                "Nutrition is attributed to the local day of its server-recorded time."
            ),
            "causality": ("Nutrition and body weight are displayed descriptively, not causally."),
        },
    }


@router.get("/progress")
def get_progress(
    verifier: VerifierDep,
    use_case: ProgressDep,
    as_of_date: Annotated[date, Query()],
    timezone: Annotated[str, Query(min_length=1, max_length=255)],
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    started = time.monotonic()
    subject = _authenticated_subject(verifier, authorization)
    if isinstance(subject, JSONResponse):
        return subject
    try:
        progress = use_case.execute(
            user_id=subject,
            as_of_date=as_of_date,
            timezone=timezone,
        )
    except InvalidProgressRequest as exc:
        return _error(400, "invalid_progress_request", str(exc))
    except Exception:  # noqa: BLE001 - never leak health or storage details
        logger.error("longitudinal progress failed: class=storage_or_artifact")
        return _error(503, "storage_unavailable", "retry later")
    logger.info(
        "longitudinal progress returned: subject_hash=%x body_days=%d "
        "recorded_days=%d duration_ms=%d",
        hash(subject) % (2**32),
        len(progress.body_daily_medians),
        progress.nutrition_summary_28d.days_with_recorded_events,
        int((time.monotonic() - started) * 1000),
    )
    return JSONResponse(status_code=200, content=_content(progress))


__all__ = ["router"]
