"""Pure domain entities for Stacks ingestion.

This module must not import infrastructure, frameworks, or provider SDKs.
Enforced by tests/unit/test_domain_isolation.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class MealPeriod(StrEnum):
    BREAKFAST = "Breakfast"
    LUNCH = "Lunch"
    DINNER = "Dinner"


class DietaryTag(StrEnum):
    MEATLESS = "Meatless"
    VEGAN = "Vegan"
    HALAL_FRIENDLY = "Halal Friendly"
    GLUTEN_FRIENDLY = "Gluten Friendly"
    CONTAINS_PORK = "Contains Pork"


DIETARY_TAG_BY_ALT: dict[str, DietaryTag] = {
    "Meatless": DietaryTag.MEATLESS,
    "Vegan": DietaryTag.VEGAN,
    "Halal Friendly": DietaryTag.HALAL_FRIENDLY,
    "Gluten Friendly - made w/o gluten-containing items": DietaryTag.GLUTEN_FRIENDLY,
    "Contains Pork": DietaryTag.CONTAINS_PORK,
}


class NutrientKey(StrEnum):
    CALORIES_KCAL = "calories_kcal"
    TOTAL_FAT_G = "total_fat_g"
    SATURATED_FAT_G = "saturated_fat_g"
    TRANS_FAT_G = "trans_fat_g"
    CHOLESTEROL_MG = "cholesterol_mg"
    SODIUM_MG = "sodium_mg"
    VITAMIN_D_MCG = "vitamin_d_mcg"
    CALCIUM_MG = "calcium_mg"
    CARBOHYDRATE_G = "carbohydrate_g"
    FIBER_G = "fiber_g"
    SUGARS_G = "sugars_g"
    ADDED_SUGARS_G = "added_sugars_g"
    PROTEIN_G = "protein_g"
    IRON_MG = "iron_mg"
    POTASSIUM_MG = "potassium_mg"


NUTRIENT_KEY_BY_LABEL: dict[str, NutrientKey] = {
    "Total Fat": NutrientKey.TOTAL_FAT_G,
    "Saturated Fat": NutrientKey.SATURATED_FAT_G,
    "Trans Fat": NutrientKey.TRANS_FAT_G,
    "Cholesterol": NutrientKey.CHOLESTEROL_MG,
    "Sodium": NutrientKey.SODIUM_MG,
    "Vitamin D": NutrientKey.VITAMIN_D_MCG,
    "Calcium": NutrientKey.CALCIUM_MG,
    "Total Carbohydrate": NutrientKey.CARBOHYDRATE_G,
    "Dietary Fiber": NutrientKey.FIBER_G,
    "Sugars": NutrientKey.SUGARS_G,
    "Added Sugars": NutrientKey.ADDED_SUGARS_G,
    "Protein": NutrientKey.PROTEIN_G,
    "Iron": NutrientKey.IRON_MG,
    "Potassium": NutrientKey.POTASSIUM_MG,
}

NUTRIENT_UNIT: dict[NutrientKey, str] = {
    NutrientKey.CALORIES_KCAL: "kcal",
    NutrientKey.TOTAL_FAT_G: "g",
    NutrientKey.SATURATED_FAT_G: "g",
    NutrientKey.TRANS_FAT_G: "g",
    NutrientKey.CHOLESTEROL_MG: "mg",
    NutrientKey.SODIUM_MG: "mg",
    NutrientKey.VITAMIN_D_MCG: "mcg",
    NutrientKey.CALCIUM_MG: "mg",
    NutrientKey.CARBOHYDRATE_G: "g",
    NutrientKey.FIBER_G: "g",
    NutrientKey.SUGARS_G: "g",
    NutrientKey.ADDED_SUGARS_G: "g",
    NutrientKey.PROTEIN_G: "g",
    NutrientKey.IRON_MG: "mg",
    NutrientKey.POTASSIUM_MG: "mg",
}


class Confidence(StrEnum):
    OFFICIAL_PUBLISHED = "official_published"
    OFFICIAL_COMPONENT_SUM = "official_component_sum"
    VERIFIED_INTERNAL_RECIPE = "verified_internal_recipe"
    PARTIAL = "partial"
    ESTIMATED = "estimated"


class NutritionSourceState(StrEnum):
    """Authoritative source classification for one displayed menu occurrence."""

    PROFILE_AVAILABLE = "profile_available"
    SOURCE_PLACEHOLDER = "source_placeholder"
    SOURCE_INCOMPLETE = "source_incomplete"
    SOURCE_UNAVAILABLE = "source_unavailable"


class ServingBasisKind(StrEnum):
    UNITLESS_SERVINGS = "unitless_servings"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class NutrientValue:
    value: Decimal | None
    unit: str
    dv_percent: Decimal | None


@dataclass(frozen=True)
class Provenance:
    snapshot_id: UUID
    content_sha256: str
    source_url: str
    parser_version: str
    fetched_at: datetime


@dataclass(frozen=True)
class ComponentStatement:
    component_name: str
    text: str


@dataclass(frozen=True)
class NutritionProfile:
    food_id: UUID
    serving_basis_raw: str
    serving_basis_kind: ServingBasisKind
    nutrients: dict[NutrientKey, NutrientValue]
    unavailable_fields: tuple[NutrientKey, ...]
    extra_fields: dict[str, str]
    ingredients_raw: str
    ingredient_components: tuple[ComponentStatement, ...] | None
    allergens: tuple[str, ...]
    confidence: Confidence
    provenance: Provenance

    def is_fully_published(self) -> bool:
        return self.confidence == Confidence.OFFICIAL_PUBLISHED and not self.unavailable_fields


@dataclass(frozen=True)
class Food:
    food_id: UUID
    campus_id: int
    name_raw: str
    name_normalized: str
    rec_num: str | None = None


@dataclass(frozen=True)
class MenuOffering:
    offering_id: UUID
    service_date: date
    meal_period: MealPeriod
    campus_id: int
    food_id: UUID
    occurrence_ordinal: int
    category_name: str
    category_position: int
    item_position: int
    source_mid: str
    dietary_tags: tuple[DietaryTag, ...] = field(default_factory=tuple)
    profile_id: UUID | None = None
    snapshot_id: UUID | None = None


@dataclass(frozen=True)
class NormalizedOfferingInput:
    service_date: date
    meal_period: MealPeriod
    campus_id: int
    name_raw: str
    name_normalized: str
    source_mid: str
    dietary_tags: tuple[DietaryTag, ...]
    category_name: str
    category_position: int
    item_position: int


@dataclass(frozen=True)
class NormalizedMenuDay:
    service_date: date
    meal_period: MealPeriod
    campus_id: int
    offerings: tuple[NormalizedOfferingInput, ...]
    empty_period: bool
