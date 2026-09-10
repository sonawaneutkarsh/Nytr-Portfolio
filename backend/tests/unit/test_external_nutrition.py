from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from nutrition_agent.domain.configurable_meals import (
    OUNCE_IN_GRAMS,
    EstimateState,
    EvidenceClass,
    HalalSauce,
    PortionUnit,
    estimate_configurable_nutrition,
    materialize_halal_components,
)
from nutrition_agent.domain.external_nutrition import (
    CHICKEN_THIGH_ROASTED_MEAT_ONLY_100G,
    EGG_HARD_BOILED_SMALL,
    QUINOA_COOKED_100G,
    RICE_COOKED_100G,
    USDA_SR_LEGACY_VERSION,
    USUAL_HALAL_EXTERNAL_REFERENCES,
)
from nutrition_agent.domain.owner_meal_config import (
    HALAL_PORTION_POLICY,
    OWNER_CRISPY_CHICKPEA_HALAL_BOWL,
    OWNER_CRISPY_CHICKPEA_HALAL_BOWL_DEFINITION,
    OWNER_USUAL_HALAL_BOWL,
)
from nutrition_agent.domain.stacks.entities import Confidence, NutrientKey


def test_usda_reference_values_are_versioned_cited_and_decimal() -> None:
    assert USDA_SR_LEGACY_VERSION == "usda-fdc-sr-legacy-2018-04"
    assert RICE_COOKED_100G.quantities[NutrientKey.CALORIES_KCAL] == Decimal("130")
    assert QUINOA_COOKED_100G.quantities[NutrientKey.PROTEIN_G] == Decimal("4.40")
    assert CHICKEN_THIGH_ROASTED_MEAT_ONLY_100G.quantities[NutrientKey.TOTAL_FAT_G] == Decimal(
        "8.15"
    )
    assert EGG_HARD_BOILED_SMALL.quantities[NutrientKey.CALORIES_KCAL] == Decimal("58.90")
    for reference in USUAL_HALAL_EXTERNAL_REFERENCES:
        assert reference.evidence.source_class is EvidenceClass.EXTERNAL_REFERENCE
        assert reference.evidence.version == USDA_SR_LEGACY_VERSION
        assert reference.evidence.citation_urls
        assert reference.facts.confidence is Confidence.ESTIMATED


def test_owner_usual_halal_bowl_has_deterministic_partial_estimate() -> None:
    components = materialize_halal_components(OWNER_USUAL_HALAL_BOWL, HALAL_PORTION_POLICY)
    result = estimate_configurable_nutrition(components, USUAL_HALAL_EXTERNAL_REFERENCES)

    assert result.state is EstimateState.PARTIAL_ESTIMATE
    assert result.totals is not None
    assert result.totals.confidence is Confidence.ESTIMATED
    assert result.totals.quantities[NutrientKey.CALORIES_KCAL] == Decimal("604.27781682500")
    assert result.totals.quantities[NutrientKey.PROTEIN_G] == Decimal("45.7386516982500")
    assert result.unknown_nutrients == frozenset(
        {NutrientKey.ADDED_SUGARS_G, NutrientKey.TRANS_FAT_G}
    )
    assert len(result.resolved_components) == 8
    assert result.unresolved_components == ()
    assert len(result.caveats) == 4
    assert result.strict_eligible is False


def test_crispy_chickpea_variant_preserves_missing_reference_as_unresolved() -> None:
    definition = OWNER_CRISPY_CHICKPEA_HALAL_BOWL_DEFINITION
    result = definition.estimate

    assert definition.selected_components == materialize_halal_components(
        OWNER_CRISPY_CHICKPEA_HALAL_BOWL,
        HALAL_PORTION_POLICY,
    )
    assert result.state is EstimateState.PARTIAL_ESTIMATE
    assert result.totals is not None
    assert result.totals.confidence is Confidence.ESTIMATED
    assert len(result.unresolved_components) == 2
    assert {component.component_id for component in result.unresolved_components} == {
        "spicy_crispy_chickpeas"
    }
    assert all(
        component.reason == "no compatible nutrition reference is available"
        for component in result.unresolved_components
    )
    assert all(
        reference.component_id != "spicy_crispy_chickpeas"
        for reference in definition.nutrition_references
    )
    assert result.strict_eligible is False


def test_owner_portions_and_external_nutrition_provenance_remain_distinct() -> None:
    components = materialize_halal_components(OWNER_USUAL_HALAL_BOWL, HALAL_PORTION_POLICY)
    result = estimate_configurable_nutrition(components, USUAL_HALAL_EXTERNAL_REFERENCES)
    assert {item.portion.evidence.source_class for item in result.resolved_components} == {
        EvidenceClass.OWNER_OBSERVED_CONFIGURATION
    }
    assert {
        item.nutrition_reference.evidence.source_class for item in result.resolved_components
    } == {EvidenceClass.EXTERNAL_REFERENCE}
    assert all(
        item.nutrition_reference.evidence.source_class is not EvidenceClass.STACKS_OFFICIAL
        for item in result.resolved_components
    )


def test_exact_nist_ounce_conversion_drives_observed_mass_scaling() -> None:
    assert OUNCE_IN_GRAMS == Decimal("28.349523125")
    components = materialize_halal_components(OWNER_USUAL_HALAL_BOWL, HALAL_PORTION_POLICY)
    result = estimate_configurable_nutrition(components, USUAL_HALAL_EXTERNAL_REFERENCES)
    rice = next(
        item for item in result.resolved_components if item.component_id == "turmeric_basmati_rice"
    )
    assert rice.portion.unit is PortionUnit.OUNCE
    assert rice.multiplier == Decimal("1.133980925")
    quinoa = [item for item in result.resolved_components if item.component_id == "quinoa"]
    assert len(quinoa) == 4
    assert all(item.multiplier == Decimal("0.28349523125") for item in quinoa)


def test_unpublished_added_sugar_and_trans_fat_are_not_zero_filled() -> None:
    result = estimate_configurable_nutrition(
        materialize_halal_components(OWNER_USUAL_HALAL_BOWL, HALAL_PORTION_POLICY),
        USUAL_HALAL_EXTERNAL_REFERENCES,
    )
    assert result.totals is not None
    assert NutrientKey.ADDED_SUGARS_G not in result.totals.quantities
    assert NutrientKey.TRANS_FAT_G not in result.totals.quantities
    assert result.totals.published_zero == frozenset()


def test_unknown_sauce_keeps_known_bowl_as_partial_subtotal_without_zero_fill() -> None:
    selection = replace(
        OWNER_USUAL_HALAL_BOWL,
        sauces=(HalalSauce.TURKISH_HOT,),
    )
    result = estimate_configurable_nutrition(
        materialize_halal_components(selection, HALAL_PORTION_POLICY),
        USUAL_HALAL_EXTERNAL_REFERENCES,
    )

    assert result.state is EstimateState.PARTIAL_ESTIMATE
    assert result.totals is not None
    assert result.totals.quantities[NutrientKey.CALORIES_KCAL] == Decimal("604.27781682500")
    assert result.unresolved_components[0].component_id == HalalSauce.TURKISH_HOT.value
    assert result.unresolved_components[0].reason == "component portion is unknown"
    assert result.strict_eligible is False
