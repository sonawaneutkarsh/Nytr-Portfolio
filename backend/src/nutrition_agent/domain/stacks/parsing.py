"""Typed parse results shared by all Stacks parsers.

Parsers return ParseResult values and never raise across module boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Generic, TypeVar

from nutrition_agent.domain.stacks.entities import (
    DietaryTag,
    MealPeriod,
    NutrientKey,
    NutritionSourceState,
)

T = TypeVar("T")


@dataclass(frozen=True)
class ParseFailure:
    code: str
    detail: str
    selector_path: str


@dataclass(frozen=True)
class ParseResult(Generic[T]):
    value: T | None
    failure: ParseFailure | None

    @property
    def ok(self) -> bool:
        return self.failure is None

    @classmethod
    def ok_result(cls, value: T) -> ParseResult[T]:
        return cls(value=value, failure=None)

    @classmethod
    def err(cls, code: str, detail: str, selector_path: str = "") -> ParseResult[T]:
        return cls(
            value=None,
            failure=ParseFailure(code=code, detail=detail, selector_path=selector_path),
        )


@dataclass(frozen=True)
class ParsedMenuItem:
    name_raw: str
    mid_instance: str
    dietary_tags: tuple[DietaryTag, ...]
    category_position: int
    item_position: int


@dataclass(frozen=True)
class ParsedCategory:
    name: str
    position: int
    items: tuple[ParsedMenuItem, ...]


@dataclass(frozen=True)
class ParsedMenuPage:
    requested_date: date
    requested_meal: MealPeriod
    campus_id: int
    selection_echo_ok: bool
    date_window: tuple[date, ...]
    categories: tuple[ParsedCategory, ...]
    empty_period: bool
    item_failures: tuple[ParseFailure, ...]


@dataclass(frozen=True)
class ParsedNutrientRow:
    key: NutrientKey | None
    raw_label: str
    amount_text: str | None
    dv_percent: Decimal | None


@dataclass(frozen=True)
class ParsedLabel:
    item_name: str | None
    serving_basis_raw: str | None
    calories_text: str | None
    rows: tuple[ParsedNutrientRow, ...]
    ingredients_raw: str | None
    allergens: tuple[str, ...]
    invalid_mid_sentinel: bool
    placeholder: bool
    source_state: NutritionSourceState = NutritionSourceState.PROFILE_AVAILABLE


@dataclass(frozen=True)
class ParsedReportRow:
    name: str
    portion: str
    qty: Decimal | None
    calories: Decimal | None
    fiber_g: Decimal | None
    added_sugar_g: Decimal | None
    total_fat_g: Decimal | None
    protein_g: Decimal | None
    cholesterol_mg: Decimal | None
    vitamin_d_mcg: Decimal | None
    calcium_mg: Decimal | None
    iron_mg: Decimal | None
    potassium_mg: Decimal | None
    sodium_mg: Decimal | None
    saturated_fat_g: Decimal | None
    trans_fat_g: Decimal | None
    is_totals_row: bool


@dataclass(frozen=True)
class ParsedReport:
    location_label: str | None
    date_label: str | None
    rows: tuple[ParsedReportRow, ...]
