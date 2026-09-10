"""Owner-scoped read orchestration for the deterministic daily nutrition ledger."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nutrition_agent.application.ports import (
    DailyNutritionLedgerRepository,
    TargetPolicyRepository,
)
from nutrition_agent.application.target_policy import build_domain_target_set
from nutrition_agent.domain.nutrition.ledger import (
    DailyNutritionLedger,
    DailyNutritionTarget,
    build_daily_nutrition_ledger,
)
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion
from nutrition_agent.domain.stacks.entities import NutrientKey


class InvalidDailyNutritionLedgerRequest(ValueError):
    """The requested local-day boundary cannot be determined safely."""


def daily_target_from_policy(policy: TargetPolicyVersion) -> DailyNutritionTarget:
    """Convert one approved policy record to the shared display target."""
    domain_targets = build_domain_target_set(policy.goals_jsonb)
    calorie_goal = domain_targets.goals.get(NutrientKey.CALORIES_KCAL)
    protein_goal = domain_targets.goals.get(NutrientKey.PROTEIN_G)
    return DailyNutritionTarget(
        policy_version_id=policy.version_id,
        policy_version=policy.policy_version,
        calories_kcal=calorie_goal.value if calorie_goal is not None else None,
        calories_goal_kind=calorie_goal.kind.value if calorie_goal is not None else None,
        protein_g=protein_goal.value if protein_goal is not None else None,
        protein_goal_kind=protein_goal.kind.value if protein_goal is not None else None,
    )


def absolute_day_bounds(local_date: date, timezone: str) -> tuple[datetime, datetime]:
    if not timezone:
        raise InvalidDailyNutritionLedgerRequest("timezone is required")
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidDailyNutritionLedgerRequest(
            "timezone must be a valid IANA identifier"
        ) from exc
    start_local = datetime.combine(local_date, time.min, tzinfo=zone)
    end_local = datetime.combine(
        local_date.fromordinal(local_date.toordinal() + 1), time.min, tzinfo=zone
    )
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


class GetDailyNutritionLedgerUseCase:
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
        local_date: date,
        timezone: str,
    ) -> DailyNutritionLedger:
        if not isinstance(user_id, UUID):
            raise InvalidDailyNutritionLedgerRequest("user_id must be a UUID")
        if not isinstance(local_date, date):
            raise InvalidDailyNutritionLedgerRequest("local_date must be a date")
        start, end = absolute_day_bounds(local_date, timezone)
        items = self._consumption.list_eaten_evidence(user_id, start, end)

        policy = self._targets.latest_approved(user_id)
        target = daily_target_from_policy(policy) if policy is not None else None

        return build_daily_nutrition_ledger(
            local_date=local_date,
            timezone=timezone,
            consumed_items=items,
            target=target,
        )


__all__ = [
    "GetDailyNutritionLedgerUseCase",
    "InvalidDailyNutritionLedgerRequest",
    "absolute_day_bounds",
    "daily_target_from_policy",
]
