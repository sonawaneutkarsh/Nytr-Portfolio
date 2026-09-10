"""Meal context and resolved meal slots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from enum import StrEnum

from nutrition_agent.domain.stacks.entities import MealPeriod


class MealContext(StrEnum):
    LUNCH = "lunch"
    POST_WORKOUT_LUNCH = "post_workout_lunch"
    DINNER = "dinner"


@dataclass(frozen=True)
class MealSlot:
    """One Stacks meal opportunity resolved from the schedule for a date.

    ``window`` is the user's availability (USER HEURISTIC context, not an
    authoritative service window); ``menu_period`` is the publication bucket the
    planner consults, assigned by policy mapping — never inferred from the clock.
    """

    slot_date: date
    context: MealContext
    window: tuple[time, time]
    menu_period: MealPeriod
