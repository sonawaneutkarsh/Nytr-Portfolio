"""Application orchestration for immutable next-meal recommendations."""

from __future__ import annotations

from datetime import date
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nutrition_agent.application.daily_nutrition_ledger import GetDailyNutritionLedgerUseCase
from nutrition_agent.application.ports import (
    Clock,
    DuplicateNextMealRecommendationError,
    IdGenerator,
    NextMealRecommendationRepository,
    TargetPolicyRepository,
)
from nutrition_agent.application.server_inputs import MenuDayUnavailableError, ServerInputsProvider
from nutrition_agent.application.target_policy import build_domain_target_set
from nutrition_agent.domain.next_meal import (
    NextMealRecommendation,
    NextMealStatus,
    PersistNextMealOutcome,
    build_next_meal_artifact,
    failure_artifact,
    recommendation_from_artifact,
)


class InvalidNextMealRequest(ValueError):
    pass


class NextMealRequestConflict(Exception):
    pass


class GenerateNextMealRecommendationUseCase:
    def __init__(
        self,
        *,
        ledger: GetDailyNutritionLedgerUseCase,
        targets: TargetPolicyRepository,
        inputs: ServerInputsProvider,
        recommendations: NextMealRecommendationRepository,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._ledger = ledger
        self._targets = targets
        self._inputs = inputs
        self._recommendations = recommendations
        self._clock = clock
        self._ids = ids

    def execute(
        self,
        *,
        user_id: UUID,
        client_request_id: UUID,
        local_date: date,
        timezone: str,
    ) -> PersistNextMealOutcome:
        try:
            zone = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise InvalidNextMealRequest("timezone must be a valid IANA identifier") from exc
        existing = self._recommendations.find_by_client_request_id(user_id, client_request_id)
        if existing is not None:
            if existing.local_date != local_date or existing.timezone != timezone:
                raise NextMealRequestConflict(
                    "client request id was already used with different request inputs"
                )
            return PersistNextMealOutcome(existing, False)
        now = self._clock.now()
        if now.astimezone(zone).date() != local_date:
            raise InvalidNextMealRequest("next-meal generation is only available for today")

        ledger = self._ledger.execute(user_id=user_id, local_date=local_date, timezone=timezone)
        ledger_target = ledger.target
        target = (
            self._targets.find_by_version_id(user_id, ledger_target.policy_version_id)
            if ledger_target is not None
            else None
        )
        if target is None:
            status = NextMealStatus.NO_APPROVED_TARGET_POLICY
            reasons: tuple[str, ...] = ("approved_target_required",)
            artifact = failure_artifact(
                local_date=local_date,
                timezone=timezone,
                decision_at=now,
                status=status,
                reason_codes=reasons,
                ledger=ledger,
            )
            recommendation = recommendation_from_artifact(
                recommendation_id=self._ids.new_id(),
                user_id=user_id,
                client_request_id=client_request_id,
                local_date=local_date,
                timezone=timezone,
                decision_at=now,
                target_policy_version_id=None,
                status=status,
                reason_codes=reasons,
                artifact=artifact,
            )
            return self._save(recommendation)

        try:
            target_set = build_domain_target_set(target.goals_jsonb)
            inputs = self._inputs.build(
                user_id=user_id,
                requested_for_date=local_date,
                timezone=timezone,
                targets=target_set,
                target_policy_version_id=target.version_id,
            )
        except MenuDayUnavailableError:
            status = NextMealStatus.MENU_DATA_UNAVAILABLE
            reasons = ("accepted_stacks_menu_unavailable",)
            artifact = failure_artifact(
                local_date=local_date,
                timezone=timezone,
                decision_at=now,
                status=status,
                reason_codes=reasons,
                ledger=ledger,
            )
            artifact["target_policy_version_id"] = str(target.version_id)
            artifact["target_policy_version"] = target.policy_version
        else:
            status, reasons, artifact = build_next_meal_artifact(
                local_date=local_date,
                timezone=timezone,
                decision_at=now,
                ledger=ledger,
                schedule=inputs.schedule,
                exceptions=tuple(inputs.exceptions),
                menu=inputs.menu,
                planner_policy=inputs.policy,
                slot_policies=dict(inputs.slot_policies),
                configurable_meal_definitions=tuple(inputs.configurable_meal_definitions),
                target_policy_version_id=target.version_id,
                target_policy_version=target.policy_version,
            )
        recommendation = recommendation_from_artifact(
            recommendation_id=self._ids.new_id(),
            user_id=user_id,
            client_request_id=client_request_id,
            local_date=local_date,
            timezone=timezone,
            decision_at=now,
            target_policy_version_id=target.version_id,
            status=status,
            reason_codes=reasons,
            artifact=artifact,
        )
        return self._save(recommendation)

    def _save(self, recommendation: NextMealRecommendation) -> PersistNextMealOutcome:
        try:
            return self._recommendations.save(recommendation)
        except DuplicateNextMealRecommendationError as exc:
            raise NextMealRequestConflict(str(exc)) from exc


__all__ = [
    "GenerateNextMealRecommendationUseCase",
    "InvalidNextMealRequest",
    "NextMealRequestConflict",
]
