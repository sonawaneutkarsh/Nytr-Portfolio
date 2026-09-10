"""Two-stage hard eligibility with explicit reason codes.

Stage A (offering-level) gates what may enter candidate generation.
Stage B (candidate-level) gates what may enter scoring: composed totals must be
strict-complete and total calories must fit SlotPolicy bounds, because two
individually-valid offerings can compose into an invalid total.
The planner is strict-only: no flag relaxes any constraint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from nutrition_agent.domain.nutrition.facts import NutritionFacts, is_strict_eligible
from nutrition_agent.domain.nutrition.meal import ComposedMeal
from nutrition_agent.domain.planning.dietary import (
    DietaryClassification,
    DietaryPolicy,
    assess_animal_source,
)
from nutrition_agent.domain.planning.menu_view import OfferingView, PeriodMenu
from nutrition_agent.domain.planning.policy import SlotPolicy
from nutrition_agent.domain.stacks.entities import NutrientKey


class ReasonCode(StrEnum):
    NO_RESOLVED_MEAL_SLOTS = "no_resolved_meal_slots"
    MENU_DATA_UNAVAILABLE = "menu_data_unavailable"
    EMPTY_MENU_PERIOD = "empty_menu_period"
    MENU_STALE = "menu_stale"
    NO_PROFILE_LINKED = "no_profile_linked"
    NOT_STRICT_ELIGIBLE = "not_strict_eligible"
    DIETARY_FILTER = "dietary_filter"
    CANDIDATE_NOT_STRICT = "candidate_not_strict"
    CANDIDATE_CALORIE_BOUNDS = "candidate_calorie_bounds"
    ESTIMATED_MEAL_INELIGIBLE = "estimated_meal_ineligible"


@dataclass(frozen=True)
class Rejection:
    reason: ReasonCode
    detail: str


def period_gate(period_menu: PeriodMenu | None) -> Rejection | None:
    """A1/A2: data-gap vs validated source emptiness are distinct failures."""
    if period_menu is None:
        return Rejection(
            reason=ReasonCode.MENU_DATA_UNAVAILABLE,
            detail="no menu data was supplied for this period",
        )
    if period_menu.explicitly_empty:
        return Rejection(
            reason=ReasonCode.EMPTY_MENU_PERIOD,
            detail="source validated this period as empty",
        )
    return None


def freshness_gate(
    fetched_at: datetime, plan_at: datetime, max_age_seconds: float
) -> Rejection | None:
    age = (plan_at - fetched_at).total_seconds()
    if age > max_age_seconds:
        return Rejection(
            reason=ReasonCode.MENU_STALE,
            detail=f"menu snapshot age {int(age)}s exceeds policy {int(max_age_seconds)}s",
        )
    return None


def offering_gate(offering: OfferingView, slot_policy: SlotPolicy) -> Rejection | None:
    """A4-A6: profile linked, M3 strict eligibility, dietary tag filters."""
    if offering.profile is None:
        return Rejection(
            reason=ReasonCode.NO_PROFILE_LINKED,
            detail=f"{offering.name_normalized}: no nutrition profile linked",
        )
    facts = facts_of(offering)
    if not is_strict_eligible(facts):
        return Rejection(
            reason=ReasonCode.NOT_STRICT_ELIGIBLE,
            detail=f"{offering.name_normalized}: incomplete or non-official nutrition",
        )
    excluded = set(slot_policy.exclude_tags)
    if excluded and excluded & set(offering.dietary_tags):
        tags = ",".join(sorted(tag.value for tag in excluded & set(offering.dietary_tags)))
        return Rejection(
            reason=ReasonCode.DIETARY_FILTER,
            detail=f"{offering.name_normalized}: excluded tag(s) {tags}",
        )
    return None


def dietary_gate(offering: OfferingView, policy: DietaryPolicy | None) -> Rejection | None:
    """Apply owner hard exclusions before nutrition eligibility and ranking."""

    if policy is None:
        return None
    assessment = assess_animal_source(offering, policy)
    if assessment.classification is DietaryClassification.DISALLOWED_ANIMAL:
        return Rejection(
            reason=ReasonCode.DIETARY_FILTER,
            detail=(
                f"{offering.name_normalized}: excluded animal source "
                f"{assessment.source.value if assessment.source is not None else 'unknown'} "
                f"({assessment.detail})"
            ),
        )
    if assessment.classification is DietaryClassification.UNKNOWN_MEAT_SOURCE:
        return Rejection(
            reason=ReasonCode.DIETARY_FILTER,
            detail=(f"{offering.name_normalized}: animal source is unknown ({assessment.detail})"),
        )
    return None


def facts_of(offering: OfferingView) -> NutritionFacts:
    assert offering.profile is not None
    from nutrition_agent.domain.stacks.facts_bridge import facts_from_profile

    return facts_from_profile(offering.profile)


def candidate_gate(meal: ComposedMeal, slot_policy: SlotPolicy) -> Rejection | None:
    """B1/B2: composed strictness + TOTAL calories within policy bounds."""
    totals = meal.totals
    all_keys = set(NutrientKey)
    if totals.confidence not in {"official_published", "official_component_sum"} or (
        set(totals.quantities.keys()) != all_keys or totals.declared_unavailable
    ):
        return Rejection(
            reason=ReasonCode.CANDIDATE_NOT_STRICT,
            detail="composed totals are not strict-complete",
        )
    calories = totals.quantities[NutrientKey.CALORIES_KCAL]
    if calories < slot_policy.calorie_min or calories > slot_policy.calorie_max:
        return Rejection(
            reason=ReasonCode.CANDIDATE_CALORIE_BOUNDS,
            detail=(
                f"composed calories {calories} outside "
                f"[{slot_policy.calorie_min}, {slot_policy.calorie_max}]"
            ),
        )
    return None
