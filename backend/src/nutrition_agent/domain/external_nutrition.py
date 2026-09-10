"""Small, versioned external-reference catalog for approved generic ingredients.

Values are copied from the USDA FoodData Central SR Legacy April 2018 JSON
release and expressed per 100 g unless stated otherwise.  This is a committed
reference dataset, not a runtime API integration.  It must never be described
as an exact Stacks recipe or as Stacks-published nutrition.
"""

from __future__ import annotations

from decimal import Decimal

from nutrition_agent.domain.configurable_meals import (
    ComponentNutritionReference,
    EvidenceClass,
    EvidenceReference,
    HalalBase,
    HalalProtein,
    HalalTopping,
    PortionUnit,
)
from nutrition_agent.domain.nutrition.arithmetic import scale_facts
from nutrition_agent.domain.nutrition.facts import NutritionFacts
from nutrition_agent.domain.stacks.entities import Confidence, NutrientKey

USDA_SR_LEGACY_VERSION = "usda-fdc-sr-legacy-2018-04"
USDA_SR_LEGACY_DOWNLOAD = (
    "https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_json_2018-04.zip"
)


def _usda_evidence(fdc_ids: tuple[int, ...], description: str) -> EvidenceReference:
    return EvidenceReference(
        source_class=EvidenceClass.EXTERNAL_REFERENCE,
        reference_id="+".join(f"usda-fdc:{fdc_id}" for fdc_id in fdc_ids),
        version=USDA_SR_LEGACY_VERSION,
        description=description,
        citation_urls=tuple(
            f"https://fdc.nal.usda.gov/fdc-app.html#/food-details/{fdc_id}/nutrients"
            for fdc_id in fdc_ids
        )
        + (USDA_SR_LEGACY_DOWNLOAD,),
    )


def _estimated_facts(values: dict[NutrientKey, str]) -> NutritionFacts:
    return NutritionFacts(
        quantities={key: Decimal(value) for key, value in values.items()},
        published_zero=frozenset(),
        declared_unavailable=frozenset(),
        confidence=Confidence.ESTIMATED,
    )


RICE_COOKED_100G = _estimated_facts(
    {
        NutrientKey.CALORIES_KCAL: "130",
        NutrientKey.PROTEIN_G: "2.69",
        NutrientKey.CARBOHYDRATE_G: "28.2",
        NutrientKey.TOTAL_FAT_G: "0.280",
        NutrientKey.SATURATED_FAT_G: "0.077",
        NutrientKey.FIBER_G: "0.400",
        NutrientKey.SUGARS_G: "0.050",
        NutrientKey.SODIUM_MG: "1.00",
        NutrientKey.CHOLESTEROL_MG: "0.000",
        NutrientKey.VITAMIN_D_MCG: "0.000",
        NutrientKey.CALCIUM_MG: "10.0",
        NutrientKey.IRON_MG: "1.20",
        NutrientKey.POTASSIUM_MG: "35.0",
    }
)

QUINOA_COOKED_100G = _estimated_facts(
    {
        NutrientKey.CALORIES_KCAL: "120",
        NutrientKey.PROTEIN_G: "4.40",
        NutrientKey.CARBOHYDRATE_G: "21.3",
        NutrientKey.TOTAL_FAT_G: "1.92",
        NutrientKey.SATURATED_FAT_G: "0.231",
        NutrientKey.FIBER_G: "2.80",
        NutrientKey.SUGARS_G: "0.870",
        NutrientKey.SODIUM_MG: "7.00",
        NutrientKey.CHOLESTEROL_MG: "0.000",
        NutrientKey.VITAMIN_D_MCG: "0.000",
        NutrientKey.CALCIUM_MG: "17.0",
        NutrientKey.IRON_MG: "1.49",
        NutrientKey.POTASSIUM_MG: "172",
    }
)

CHICKEN_THIGH_ROASTED_MEAT_ONLY_100G = _estimated_facts(
    {
        NutrientKey.CALORIES_KCAL: "179",
        NutrientKey.PROTEIN_G: "24.8",
        NutrientKey.CARBOHYDRATE_G: "0.000",
        NutrientKey.TOTAL_FAT_G: "8.15",
        NutrientKey.SATURATED_FAT_G: "2.31",
        NutrientKey.TRANS_FAT_G: "0.044",
        NutrientKey.FIBER_G: "0.000",
        NutrientKey.SUGARS_G: "0.000",
        NutrientKey.SODIUM_MG: "106",
        NutrientKey.CHOLESTEROL_MG: "133",
        NutrientKey.VITAMIN_D_MCG: "0.200",
        NutrientKey.CALCIUM_MG: "9.00",
        NutrientKey.IRON_MG: "1.13",
        NutrientKey.POTASSIUM_MG: "269",
    }
)

EGG_HARD_BOILED_100G = _estimated_facts(
    {
        NutrientKey.CALORIES_KCAL: "155",
        NutrientKey.PROTEIN_G: "12.6",
        NutrientKey.CARBOHYDRATE_G: "1.12",
        NutrientKey.TOTAL_FAT_G: "10.6",
        NutrientKey.SATURATED_FAT_G: "3.27",
        NutrientKey.FIBER_G: "0.000",
        NutrientKey.SUGARS_G: "1.12",
        NutrientKey.SODIUM_MG: "124",
        NutrientKey.CHOLESTEROL_MG: "373",
        NutrientKey.VITAMIN_D_MCG: "2.20",
        NutrientKey.CALCIUM_MG: "50.0",
        NutrientKey.IRON_MG: "1.19",
        NutrientKey.POTASSIUM_MG: "126",
    }
)

# SR Legacy FDC 171287 records a small raw whole egg at 38 g.  That mass is
# applied to FDC 173424's hard-boiled composition.  It remains an estimate: the
# actual Stacks egg's edible mass is not published.
EGG_HARD_BOILED_SMALL = scale_facts(EGG_HARD_BOILED_100G, Decimal("0.38"))

USUAL_HALAL_EXTERNAL_REFERENCES: tuple[ComponentNutritionReference, ...] = (
    ComponentNutritionReference(
        component_id=HalalBase.TURMERIC_BASMATI_RICE.value,
        basis_amount=Decimal("100"),
        basis_unit=PortionUnit.GRAM,
        facts=RICE_COOKED_100G,
        evidence=_usda_evidence(
            (168878,),
            "Rice, white, long-grain, regular, enriched, cooked; values per 100 g.",
        ),
        caveats=(
            "Generic cooked white rice does not include the exact Stacks turmeric-rice "
            "oil, seasoning, or sodium preparation.",
        ),
    ),
    ComponentNutritionReference(
        component_id=HalalProtein.JAMAICAN_HALAL_CHICKEN_THIGH.value,
        basis_amount=Decimal("100"),
        basis_unit=PortionUnit.GRAM,
        facts=CHICKEN_THIGH_ROASTED_MEAT_ONLY_100G,
        evidence=_usda_evidence(
            (172388,),
            "Chicken thigh meat only, cooked, roasted; values per 100 g.",
        ),
        caveats=(
            "Generic roasted thigh does not include the exact Stacks Jamaican-style "
            "seasoning, marinade, or added oil.",
        ),
    ),
    ComponentNutritionReference(
        component_id=HalalTopping.QUINOA.value,
        basis_amount=Decimal("100"),
        basis_unit=PortionUnit.GRAM,
        facts=QUINOA_COOKED_100G,
        evidence=_usda_evidence((168917,), "Quinoa, cooked; values per 100 g."),
        caveats=(
            "The exact Stacks quinoa recipe and any added oil or seasoning are not published.",
        ),
    ),
    ComponentNutritionReference(
        component_id=HalalTopping.SMALL_EGG.value,
        basis_amount=Decimal("1"),
        basis_unit=PortionUnit.EACH_SMALL,
        facts=EGG_HARD_BOILED_SMALL,
        evidence=_usda_evidence(
            (173424, 171287),
            "Hard-boiled egg composition scaled by USDA's 38 g small whole-egg mass.",
        ),
        caveats=("The standardized 38 g small-egg mass is not a measured Stacks serving.",),
    ),
)
