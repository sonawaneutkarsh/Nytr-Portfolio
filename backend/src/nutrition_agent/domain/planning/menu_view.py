"""Caller-supplied read models over ingested menu data.

Built ONLY from menu_offering rows of the requested (date, period) — never from
the stacks_food catalog. Presence in the catalog never implies availability.

Period states (distinct and honest):
- present with offerings: normal planning input
- present with explicitly_empty=True: VALIDATED source emptiness carried through
  from the ingestion layer's empty-period state
- period key absent: caller-side data gap (MENU_DATA_UNAVAILABLE) — never
  reported as "Stacks had zero food"
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from uuid import UUID

from nutrition_agent.domain.nutrition.meal import MealLine
from nutrition_agent.domain.stacks.entities import (
    DietaryTag,
    MealPeriod,
    NutritionProfile,
    NutritionSourceState,
)


@dataclass(frozen=True)
class OfferingView:
    offering_id: UUID
    food_id: UUID
    name_normalized: str
    source_mid: str
    occurrence_ordinal: int
    category_name: str
    dietary_tags: tuple[DietaryTag, ...]
    profile: NutritionProfile | None
    profile_sha256: str | None
    snapshot_sha256: str
    nutrition_source_state: NutritionSourceState | None = None
    nutrition_snapshot_sha256: str | None = None


@dataclass(frozen=True)
class PeriodMenu:
    offerings: tuple[OfferingView, ...]
    explicitly_empty: bool = False

    def __post_init__(self) -> None:
        if self.explicitly_empty and self.offerings:
            raise ValueError("explicitly_empty period cannot carry offerings")


@dataclass(frozen=True)
class MenuDayView:
    service_date: date
    periods: Mapping[MealPeriod, PeriodMenu]
    fetched_at: datetime
    snapshot_sha256: str
    campus_id: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "periods", MappingProxyType(dict(self.periods)))

    def period(self, menu_period: MealPeriod) -> PeriodMenu | None:
        found = self.periods.get(menu_period)
        if found is None:
            return None
        return found


@dataclass(frozen=True)
class CandidateLineRef:
    """Provenance for one line of a candidate: the offering it came from."""

    offering_id: UUID
    food_id: UUID
    name_normalized: str
    source_mid: str
    occurrence_ordinal: int
    category_name: str
    dietary_tags: tuple[DietaryTag, ...]
    profile_sha256: str
    parser_version: str
    line: MealLine
