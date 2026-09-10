"""Compute-on-read orchestration for evidence-bounded longitudinal progress."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from nutrition_agent.application.daily_nutrition_ledger import (
    InvalidDailyNutritionLedgerRequest,
    absolute_day_bounds,
    daily_target_from_policy,
)
from nutrition_agent.application.nutrition_history import target_for_day
from nutrition_agent.application.ports import (
    BodyMassHistoryRepository,
    DailyNutritionLedgerRepository,
    GoalPolicyRepository,
    TargetPolicyRepository,
)
from nutrition_agent.domain.health.trend import (
    aggregate_daily_body_mass,
    calculate_body_mass_trend,
)
from nutrition_agent.domain.nutrition.ledger import (
    ConsumedNutritionEvidence,
    build_daily_nutrition_ledger,
)
from nutrition_agent.domain.progress import (
    BODY_WINDOW_DAYS,
    LOGGING_COVERAGE_LIMITATION,
    NO_CAUSALITY_LIMITATION,
    NUTRITION_WINDOW_DAYS,
    PROGRESS_POLICY_VERSION,
    RECORD_TIME_LIMITATION,
    TRAILING_SUMMARY_DAYS,
    LongitudinalProgress,
    TargetChangeAnnotation,
    build_progress_nutrition_day,
    summarize_progress_nutrition,
)
from nutrition_agent.domain.target_review import interpret_goal_band


class InvalidProgressRequest(ValueError):
    """The requested deterministic progress window cannot be resolved safely."""


def _dates_ending(as_of_date: date, count: int) -> tuple[date, ...]:
    try:
        return tuple(
            date.fromordinal(as_of_date.toordinal() - offset) for offset in range(count - 1, -1, -1)
        )
    except ValueError as exc:
        raise InvalidProgressRequest("as_of_date cannot form the required progress window") from exc


def _created_at(policy: object) -> datetime:
    value = getattr(policy, "created_at", None)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidProgressRequest("target policy approval timestamp is unavailable")
    return value


class GetLongitudinalProgressUseCase:
    def __init__(
        self,
        *,
        body_mass: BodyMassHistoryRepository,
        consumption: DailyNutritionLedgerRepository,
        targets: TargetPolicyRepository,
        goals: GoalPolicyRepository,
    ) -> None:
        self._body_mass = body_mass
        self._consumption = consumption
        self._targets = targets
        self._goals = goals

    def execute(
        self,
        *,
        user_id: UUID,
        as_of_date: date,
        timezone: str,
    ) -> LongitudinalProgress:
        if not isinstance(user_id, UUID):
            raise InvalidProgressRequest("user_id must be a UUID")
        if not isinstance(as_of_date, date):
            raise InvalidProgressRequest("as_of_date must be a date")
        try:
            body_dates = _dates_ending(as_of_date, BODY_WINDOW_DAYS)
            nutrition_dates = _dates_ending(as_of_date, NUTRITION_WINDOW_DAYS)
            body_start, _ = absolute_day_bounds(body_dates[0], timezone)
            _, body_end = absolute_day_bounds(body_dates[-1], timezone)
            nutrition_start, _ = absolute_day_bounds(nutrition_dates[0], timezone)
            _, nutrition_end = absolute_day_bounds(nutrition_dates[-1], timezone)
            zone = ZoneInfo(timezone)
        except (InvalidDailyNutritionLedgerRequest, ValueError) as exc:
            if isinstance(exc, InvalidProgressRequest):
                raise
            raise InvalidProgressRequest(str(exc)) from exc

        observations = self._body_mass.list_active(user_id, body_start, body_end)
        body_points = aggregate_daily_body_mass(observations, timezone)
        trend = calculate_body_mass_trend(
            user_id=user_id,
            observations=observations,
            as_of_date=as_of_date,
            timezone=timezone,
        )

        evidence = self._consumption.list_eaten_evidence(
            user_id,
            nutrition_start,
            nutrition_end,
        )
        policies = self._targets.list_approved_for_window(
            user_id,
            nutrition_start,
            nutrition_end,
        )
        grouped: dict[date, list[ConsumedNutritionEvidence]] = {
            local_date: [] for local_date in nutrition_dates
        }
        for item in evidence:
            local_date = item.recorded_at.astimezone(zone).date()
            if local_date in grouped:
                grouped[local_date].append(item)

        days = []
        for local_date in nutrition_dates:
            start, end = absolute_day_bounds(local_date, timezone)
            target_status, policy = target_for_day(policies, start, end)
            target = daily_target_from_policy(policy) if policy is not None else None
            ledger = build_daily_nutrition_ledger(
                local_date=local_date,
                timezone=timezone,
                consumed_items=tuple(grouped[local_date]),
                target=target,
            )
            days.append(build_progress_nutrition_day(ledger, target_status))
        nutrition_days = tuple(days)

        changes = []
        for policy in policies:
            effective_at = _created_at(policy)
            if not nutrition_start <= effective_at < nutrition_end:
                continue
            target = daily_target_from_policy(policy)
            changes.append(
                TargetChangeAnnotation(
                    effective_at=effective_at,
                    effective_local_date=effective_at.astimezone(zone).date(),
                    target_policy_version_id=policy.version_id,
                    target_policy_version=policy.policy_version,
                    calories_kcal=target.calories_kcal,
                    calories_goal_kind=target.calories_goal_kind,
                    protein_g=target.protein_g,
                    protein_goal_kind=target.protein_goal_kind,
                )
            )
        target_changes = tuple(
            sorted(changes, key=lambda item: (item.effective_at, item.target_policy_version_id))
        )

        goal = self._goals.latest(user_id)
        goal_interpretation = (
            interpret_goal_band(trend=trend, goal_policy=goal) if goal is not None else None
        )
        return LongitudinalProgress(
            policy_version=PROGRESS_POLICY_VERSION,
            as_of_date=as_of_date,
            timezone=timezone,
            body_window_start_date=body_dates[0],
            body_window_end_date=body_dates[-1],
            body_daily_medians=body_points,
            body_trend_28d=trend,
            current_goal=goal,
            goal_interpretation=goal_interpretation,
            nutrition_window_start_date=nutrition_dates[0],
            nutrition_window_end_date=nutrition_dates[-1],
            nutrition_days=nutrition_days,
            nutrition_summary_7d=summarize_progress_nutrition(
                nutrition_days[-TRAILING_SUMMARY_DAYS:]
            ),
            nutrition_summary_28d=summarize_progress_nutrition(nutrition_days),
            target_changes=target_changes,
            limitation_codes=(
                LOGGING_COVERAGE_LIMITATION,
                RECORD_TIME_LIMITATION,
                NO_CAUSALITY_LIMITATION,
            ),
        )


__all__ = ["GetLongitudinalProgressUseCase", "InvalidProgressRequest"]
