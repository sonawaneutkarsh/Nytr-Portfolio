"""Training-aware projection of server-owned daily-plan inputs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from nutrition_agent.application.daily_plan import DailyPlanInputs
from nutrition_agent.application.server_inputs import ServerInputsProvider
from nutrition_agent.domain.nutrition.targets import TargetSet
from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.planning.schedule import (
    BlockKind,
    DaySchedule,
    ScheduleBlock,
    ScheduleException,
    Weekday,
)
from nutrition_agent.domain.stacks.entities import MealPeriod
from nutrition_agent.domain.training.context import TrainingDayContext
from nutrition_agent.domain.training.meal_context import (
    OWNER_TRAINING_AWARE_MEAL_CONTEXT_POLICY_V1,
    MealContextSelection,
    TrainingAwareMealContextPolicy,
    select_lunch_meal_context,
)


class TrainingDayContextResolver(Protocol):
    def execute(
        self,
        *,
        user_id: UUID,
        local_date: date,
        timezone: str,
    ) -> TrainingDayContext: ...


@dataclass(frozen=True)
class TrainingAwareInputsResult:
    inputs: DailyPlanInputs
    selections: tuple[MealContextSelection, ...]


def apply_training_aware_meal_context(
    inputs: DailyPlanInputs,
    training_context: TrainingDayContext | None,
    policy: TrainingAwareMealContextPolicy = OWNER_TRAINING_AWARE_MEAL_CONTEXT_POLICY_V1,
) -> TrainingAwareInputsResult:
    """Project the effective dated Lunch context without mutating base schedule data."""

    matching_exceptions = tuple(
        exception for exception in inputs.exceptions if exception.date == inputs.requested_for_date
    )
    selected_exception = (
        max(matching_exceptions, key=lambda item: item.version) if matching_exceptions else None
    )
    base_day = (
        selected_exception.day
        if selected_exception is not None
        else inputs.schedule.days.get(Weekday.from_date(inputs.requested_for_date))
    )
    if base_day is None:
        return TrainingAwareInputsResult(inputs=inputs, selections=())

    zone = ZoneInfo(inputs.timezone)
    selections: list[MealContextSelection] = []
    projected_blocks: list[ScheduleBlock] = []
    for block in base_day.blocks:
        context = block.meal_context
        is_lunch = (
            block.kind is BlockKind.STACKS_MEAL
            and context is not None
            and inputs.policy.context_period.get(context) is MealPeriod.LUNCH
        )
        if not is_lunch:
            projected_blocks.append(block)
            continue

        lunch_starts_at = datetime.combine(
            inputs.requested_for_date,
            block.start,
            tzinfo=zone,
        )
        selection = select_lunch_meal_context(
            local_date=inputs.requested_for_date,
            timezone=inputs.timezone,
            lunch_starts_at=lunch_starts_at,
            training_context=training_context,
            policy=policy,
        )
        selections.append(selection)
        projected_blocks.append(
            replace(
                block,
                label=(
                    "Stacks post-workout lunch"
                    if selection.selected_context is MealContext.POST_WORKOUT_LUNCH
                    else "Stacks lunch"
                ),
                meal_context=selection.selected_context,
            )
        )

    if not selections:
        return TrainingAwareInputsResult(inputs=inputs, selections=())

    effective_version = _effective_schedule_version(
        inputs.schedule.version,
        selected_exception,
        tuple(selections),
    )
    effective_day = DaySchedule(weekday=base_day.weekday, blocks=tuple(projected_blocks))
    effective_exception = ScheduleException(
        date=inputs.requested_for_date,
        version=effective_version,
        day=effective_day,
    )
    effective_exceptions = tuple(
        exception for exception in inputs.exceptions if exception.date != inputs.requested_for_date
    ) + (effective_exception,)
    effective_schedule = replace(inputs.schedule, version=effective_version)
    return TrainingAwareInputsResult(
        inputs=replace(
            inputs,
            schedule=effective_schedule,
            exceptions=effective_exceptions,
        ),
        selections=tuple(selections),
    )


def _effective_schedule_version(
    base_schedule_version: str,
    selected_exception: ScheduleException | None,
    selections: tuple[MealContextSelection, ...],
) -> str:
    selection_identity = "+".join(
        f"{selection.policy_version}"
        f".{selection.training_policy_version or 'training-evidence-unavailable'}"
        f".{selection.reason_code.value}"
        for selection in selections
    )
    base_identity = (
        f"{base_schedule_version}.{selected_exception.version}"
        if selected_exception is not None
        else base_schedule_version
    )
    return f"{base_identity}+{selection_identity}"


@dataclass(frozen=True)
class TrainingAwareServerInputsProvider:
    """Decorate canonical input assembly with owner-scoped training evidence."""

    base: ServerInputsProvider
    training_contexts: TrainingDayContextResolver | None
    policy: TrainingAwareMealContextPolicy = OWNER_TRAINING_AWARE_MEAL_CONTEXT_POLICY_V1

    def build(
        self,
        *,
        user_id: UUID,
        requested_for_date: date,
        timezone: str,
        targets: TargetSet,
        target_policy_version_id: UUID | None = None,
    ) -> DailyPlanInputs:
        inputs = self.base.build(
            user_id=user_id,
            requested_for_date=requested_for_date,
            timezone=timezone,
            targets=targets,
            target_policy_version_id=target_policy_version_id,
        )
        training_context: TrainingDayContext | None = None
        if self.training_contexts is not None:
            try:
                training_context = self.training_contexts.execute(
                    user_id=user_id,
                    local_date=requested_for_date,
                    timezone=timezone,
                )
            except Exception:  # fail closed to neutral Lunch when evidence is unavailable
                training_context = None
        return apply_training_aware_meal_context(
            inputs,
            training_context,
            self.policy,
        ).inputs


__all__ = [
    "TrainingAwareInputsResult",
    "TrainingAwareServerInputsProvider",
    "TrainingDayContextResolver",
    "apply_training_aware_meal_context",
]
