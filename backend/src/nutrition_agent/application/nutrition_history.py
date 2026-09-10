"""Bounded owner-scoped orchestration for seven-day nutrition history."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from nutrition_agent.application.daily_nutrition_ledger import (
    InvalidDailyNutritionLedgerRequest,
    absolute_day_bounds,
    daily_target_from_policy,
)
from nutrition_agent.application.ports import (
    DailyNutritionLedgerRepository,
    TargetPolicyRepository,
)
from nutrition_agent.domain.nutrition.history import (
    DailyTargetStatus,
    NutritionHistory7Day,
    build_nutrition_history,
)
from nutrition_agent.domain.nutrition.ledger import (
    ConsumedNutritionEvidence,
    build_daily_nutrition_ledger,
)
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion


class InvalidNutritionHistoryRequest(ValueError):
    """The requested historical interval cannot be resolved safely."""


def _created_at(policy: TargetPolicyVersion) -> datetime:
    value = policy.created_at
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidNutritionHistoryRequest("target policy approval timestamp is unavailable")
    return value


def target_for_day(
    policies: tuple[TargetPolicyVersion, ...],
    start: datetime,
    end: datetime,
) -> tuple[DailyTargetStatus, TargetPolicyVersion | None]:
    active = tuple(policy for policy in policies if _created_at(policy) <= start)
    changes = tuple(policy for policy in policies if start < _created_at(policy) < end)
    if changes:
        return DailyTargetStatus.TARGET_CHANGED_DURING_DAY, None
    if not active:
        return DailyTargetStatus.UNAVAILABLE, None
    return DailyTargetStatus.AVAILABLE, max(active, key=lambda p: (_created_at(p), p.version_id))


class GetNutritionHistory7DayUseCase:
    def __init__(
        self,
        consumption: DailyNutritionLedgerRepository,
        targets: TargetPolicyRepository,
    ) -> None:
        self._consumption = consumption
        self._targets = targets

    def execute(
        self,
        *,
        user_id: UUID,
        end_date: date,
        timezone: str,
    ) -> NutritionHistory7Day:
        if not isinstance(user_id, UUID):
            raise InvalidNutritionHistoryRequest("user_id must be a UUID")
        if not isinstance(end_date, date):
            raise InvalidNutritionHistoryRequest("end_date must be a date")
        try:
            dates = tuple(
                date.fromordinal(end_date.toordinal() - offset) for offset in range(6, -1, -1)
            )
            overall_start, _ = absolute_day_bounds(dates[0], timezone)
            _, overall_end = absolute_day_bounds(dates[-1], timezone)
        except (InvalidDailyNutritionLedgerRequest, ValueError) as exc:
            raise InvalidNutritionHistoryRequest(str(exc)) from exc

        evidence = self._consumption.list_eaten_evidence(user_id, overall_start, overall_end)
        policies = self._targets.list_approved_for_window(
            user_id,
            overall_start,
            overall_end,
        )
        zone = ZoneInfo(timezone)
        grouped: dict[date, list[ConsumedNutritionEvidence]] = {
            local_date: [] for local_date in dates
        }
        for item in evidence:
            local_date = item.recorded_at.astimezone(zone).date()
            if local_date in grouped:
                grouped[local_date].append(item)

        resolved = []
        for local_date in dates:
            start, end = absolute_day_bounds(local_date, timezone)
            target_status, policy = target_for_day(policies, start, end)
            target = daily_target_from_policy(policy) if policy is not None else None
            ledger = build_daily_nutrition_ledger(
                local_date=local_date,
                timezone=timezone,
                consumed_items=tuple(grouped[local_date]),
                target=target,
            )
            resolved.append((ledger, target_status))
        return build_nutrition_history(timezone=timezone, ledgers=tuple(resolved))


__all__ = [
    "GetNutritionHistory7DayUseCase",
    "InvalidNutritionHistoryRequest",
    "target_for_day",
]
