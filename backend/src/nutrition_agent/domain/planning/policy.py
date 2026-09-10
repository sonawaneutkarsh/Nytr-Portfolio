"""Versioned planner policy: allocation shares, bounds, caps, freshness.

All numeric policy here is explicit configuration data. ``slot_shares`` are
ALLOCATION POLICY — deterministic bookkeeping for splitting an approved daily
TargetSet across meal slots. They carry NO scientific or clinical claim about
ideal meal composition.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from nutrition_agent.domain.configurable_meals import EstimateState
from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.planning.dietary import DietaryPolicy
from nutrition_agent.domain.stacks.entities import (
    DietaryTag,
    MealPeriod,
    NutrientKey,
)


@dataclass(frozen=True)
class OfferingPreference:
    """One exact-name, soft ranking preference in a versioned planner policy.

    Names use the existing Stacks ``name_normalized`` representation. Matching
    is deliberately exact: no substring, fuzzy, or model-derived inference is
    allowed at the planner boundary.
    """

    preference_id: str
    normalized_name_aliases: frozenset[str]
    bonus: Decimal

    def __post_init__(self) -> None:
        if not self.preference_id.strip():
            raise ValueError("offering preference id is required")
        if not isinstance(self.bonus, Decimal):
            raise TypeError("offering preference bonus must be Decimal")
        if self.bonus <= 0:
            raise ValueError("offering preference bonus must be positive")
        aliases = frozenset(self.normalized_name_aliases)
        if not aliases or any(not alias.strip() for alias in aliases):
            raise ValueError("offering preference aliases must be non-empty normalized names")
        if any(alias != " ".join(alias.split()) for alias in aliases):
            raise ValueError("offering preference aliases must already be whitespace-normalized")
        object.__setattr__(self, "normalized_name_aliases", aliases)


class UnsupportedEstimatedTargetBehavior(StrEnum):
    REJECT_CANDIDATE = "reject_candidate"


@dataclass(frozen=True)
class EstimatedMealPolicy:
    """Explicit opt-in contract for the non-strict configurable-meal lane."""

    allowlisted_configuration_identities: frozenset[str]
    permitted_estimate_states: frozenset[EstimateState]
    required_known_dimensions: frozenset[NutrientKey]
    permitted_scored_dimensions: frozenset[NutrientKey]
    unsupported_target_behavior: UnsupportedEstimatedTargetBehavior
    uncertainty_penalty: Decimal
    pairing_allowed: bool = False

    def __post_init__(self) -> None:
        identities = frozenset(self.allowlisted_configuration_identities)
        states = frozenset(self.permitted_estimate_states)
        required = frozenset(self.required_known_dimensions)
        scored = frozenset(self.permitted_scored_dimensions)
        if not identities or any(not identity.strip() for identity in identities):
            raise ValueError("estimated meal policy requires explicit configuration identities")
        if not states:
            raise ValueError("estimated meal policy requires permitted estimate states")
        if any(not isinstance(state, EstimateState) for state in states):
            raise TypeError("permitted estimate states must be EstimateState values")
        if not required or not required <= scored:
            raise ValueError("required estimated dimensions must be permitted for scoring")
        if any(not isinstance(key, NutrientKey) for key in required | scored):
            raise TypeError("estimated dimensions must be NutrientKey values")
        if not isinstance(
            self.unsupported_target_behavior,
            UnsupportedEstimatedTargetBehavior,
        ):
            raise TypeError("unsupported target behavior must be explicit")
        if not isinstance(self.uncertainty_penalty, Decimal):
            raise TypeError("estimated uncertainty penalty must be Decimal")
        if self.uncertainty_penalty <= 0:
            raise ValueError("estimated uncertainty penalty must be positive")
        if self.pairing_allowed:
            raise ValueError("estimated meal pairing is not supported")
        object.__setattr__(self, "allowlisted_configuration_identities", identities)
        object.__setattr__(self, "permitted_estimate_states", states)
        object.__setattr__(self, "required_known_dimensions", required)
        object.__setattr__(self, "permitted_scored_dimensions", scored)


@dataclass(frozen=True)
class SlotPolicy:
    """Per-context hard constraints and metadata."""

    context: MealContext
    exclude_tags: tuple[DietaryTag, ...] = ()
    calorie_min: Decimal = Decimal("0")
    calorie_max: Decimal = Decimal("10000")
    portable_preferred: bool = False


@dataclass(frozen=True)
class PlannerPolicy:
    version: str
    context_period: Mapping[MealContext, MealPeriod]
    slot_shares: Mapping[MealContext, Mapping[NutrientKey, Decimal]]
    offering_preferences: tuple[OfferingPreference, ...] = ()
    estimated_meal_policy: EstimatedMealPolicy | None = None
    dietary_policy: DietaryPolicy | None = None
    max_candidates_per_slot: int = 200
    max_menu_age: timedelta = timedelta(hours=20)

    def __post_init__(self) -> None:
        object.__setattr__(self, "context_period", MappingProxyType(dict(self.context_period)))
        object.__setattr__(
            self,
            "slot_shares",
            MappingProxyType(
                {
                    context: MappingProxyType(dict(shares))
                    for context, shares in self.slot_shares.items()
                }
            ),
        )
        object.__setattr__(self, "offering_preferences", tuple(self.offering_preferences))
        if self.estimated_meal_policy is not None and not isinstance(
            self.estimated_meal_policy, EstimatedMealPolicy
        ):
            raise TypeError("estimated_meal_policy must be EstimatedMealPolicy or None")
        if self.dietary_policy is not None and not isinstance(self.dietary_policy, DietaryPolicy):
            raise TypeError("dietary_policy must be DietaryPolicy or None")
        if self.max_candidates_per_slot < 1:
            raise ValueError("max_candidates_per_slot must be >= 1")
        for context, shares in self.slot_shares.items():
            if context not in self.context_period:
                raise ValueError(f"slot_shares context {context} lacks a period mapping")
            for key, share in shares.items():
                if share < 0:
                    raise ValueError(f"share for {key} must be >= 0")
        preference_ids: set[str] = set()
        preference_aliases: set[str] = set()
        for preference in self.offering_preferences:
            if not isinstance(preference, OfferingPreference):
                raise TypeError("offering_preferences must contain OfferingPreference values")
            if preference.preference_id in preference_ids:
                raise ValueError(f"duplicate offering preference id: {preference.preference_id}")
            overlap = preference_aliases & preference.normalized_name_aliases
            if overlap:
                raise ValueError(f"offering preference alias appears in multiple rules: {overlap}")
            preference_ids.add(preference.preference_id)
            preference_aliases.update(preference.normalized_name_aliases)
