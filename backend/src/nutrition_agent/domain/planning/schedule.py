"""Schedule domain model: weekly rules + dated exceptions as pure data.

No schedule logic may live in planner conditionals; days resolve through
resolve_day() from versioned WeeklySchedule/ScheduleException values.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, time
from enum import StrEnum
from types import MappingProxyType

from nutrition_agent.domain.planning.context import MealContext, MealSlot


class Weekday(StrEnum):
    MONDAY = "Monday"
    TUESDAY = "Tuesday"
    WEDNESDAY = "Wednesday"
    THURSDAY = "Thursday"
    FRIDAY = "Friday"
    SATURDAY = "Saturday"
    SUNDAY = "Sunday"

    @classmethod
    def from_date(cls, value: date) -> Weekday:
        return cls(value.strftime("%A"))


class BlockKind(StrEnum):
    HOME_PRE_WORKOUT_MEAL = "home_pre_workout_meal"
    WORKOUT = "workout"
    STACKS_MEAL = "stacks_meal"
    CLASS = "class"
    FIXED_COMMITMENT = "fixed_commitment"
    FREE = "free"


@dataclass(frozen=True)
class ScheduleBlock:
    kind: BlockKind
    start: time
    end: time
    label: str
    meal_context: MealContext | None = None

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError("schedule block start must be before end")
        if self.kind is BlockKind.STACKS_MEAL and self.meal_context is None:
            raise ValueError("STACKS_MEAL block requires a meal_context")
        if self.kind is not BlockKind.STACKS_MEAL and self.meal_context is not None:
            raise ValueError("only STACKS_MEAL blocks may carry a meal_context")


@dataclass(frozen=True)
class DaySchedule:
    weekday: Weekday
    blocks: tuple[ScheduleBlock, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "blocks",
            tuple(sorted(self.blocks, key=lambda block: (block.start, block.end))),
        )


@dataclass(frozen=True)
class WeeklySchedule:
    version: str
    timezone: str
    days: Mapping[Weekday, DaySchedule]

    def __post_init__(self) -> None:
        object.__setattr__(self, "days", MappingProxyType(dict(self.days)))


@dataclass(frozen=True)
class ScheduleException:
    """Full-day override for one calendar date; day=None removes all Stacks slots."""

    date: date
    version: str
    day: DaySchedule | None


def _latest_exception(
    exceptions: Iterable[ScheduleException], day: date
) -> ScheduleException | None:
    matches = [exc for exc in exceptions if exc.date == day]
    if not matches:
        return None
    return max(matches, key=lambda exc: exc.version)


def resolve_day(
    schedule: WeeklySchedule,
    exceptions: Iterable[ScheduleException],
    day: date,
    period_for: Mapping[MealContext, object],
) -> tuple[MealSlot, ...]:
    """Resolve one calendar date into ordered Stacks MealSlots.

    A matching exception replaces the weekly day entirely (day=None => no slots).
    HOME_PRE_WORKOUT_MEAL and other non-STACKS blocks never produce slots.
    ``period_for`` maps MealContext -> MealPeriod and MUST come from the caller's
    versioned PlannerPolicy; windows are never used to infer periods.
    """
    exception = _latest_exception(exceptions, day)
    if exception is not None:
        day_schedule = exception.day
    else:
        day_schedule = schedule.days.get(Weekday.from_date(day))
    if day_schedule is None:
        return ()
    slots: list[MealSlot] = []
    for block in day_schedule.blocks:
        if block.kind is not BlockKind.STACKS_MEAL or block.meal_context is None:
            continue
        context = block.meal_context
        if context not in period_for:
            raise ValueError(f"policy lacks period mapping for context {context}")
        slots.append(
            MealSlot(
                slot_date=day,
                context=context,
                window=(block.start, block.end),
                menu_period=period_for[context],  # type: ignore[arg-type]
            )
        )
    return tuple(slots)
