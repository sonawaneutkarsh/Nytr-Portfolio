"""Versioned synthetic meal configuration used by the portfolio demo.

These constants are illustrative structure only. They are not institutional
availability, published nutrition facts, or owner records.
"""

from __future__ import annotations

from decimal import Decimal

from nutrition_agent.domain.configurable_meals import (
    ComponentPortionPolicy,
    ConfigurableMealDefinition,
    DeliForm,
    DeliFormFamily,
    DeliModifier,
    DeliPreference,
    DeliProtein,
    EvidenceClass,
    EvidenceReference,
    HalalBase,
    HalalBowlSelection,
    HalalProtein,
    HalalSauce,
    HalalTopping,
    HomeOatsMilkshakeDraft,
    OwnerMealPreferences,
    PortionRule,
    PortionUnit,
    PreferenceLevel,
    ProteinPreferences,
    materialize_halal_components,
)
from nutrition_agent.domain.external_nutrition import USUAL_HALAL_EXTERNAL_REFERENCES

OWNER_OBSERVED_CONFIG_VERSION = "harrisburg-stacks-owner-observed.v1"
HALAL_TEMPLATE_VERSION = "harrisburg-stacks-cyo-halal.v1"
OWNER_PREFERENCE_VERSION = "owner-meal-preferences.v1"
OWNER_PREFERENCE_VERSION_V2 = "owner-meal-preferences.v2"

OWNER_OBSERVED_EVIDENCE = EvidenceReference(
    source_class=EvidenceClass.OWNER_OBSERVED_CONFIGURATION,
    reference_id="harrisburg-stacks-in-person-observation",
    version=OWNER_OBSERVED_CONFIG_VERSION,
    description=(
        "Owner-recorded Stacks configuration controls and approximate served portions; "
        "not Penn State-published nutrition or availability."
    ),
)


def _observed_halal_portions() -> dict[str, PortionRule]:
    one_ounce = PortionRule(Decimal("1"), PortionUnit.OUNCE)
    portions = {
        HalalBase.TURMERIC_BASMATI_RICE.value: PortionRule(Decimal("4"), PortionUnit.OUNCE),
        HalalProtein.JAMAICAN_HALAL_CHICKEN_THIGH.value: PortionRule(
            Decimal("4"), PortionUnit.OUNCE
        ),
        HalalTopping.SMALL_EGG.value: PortionRule(Decimal("1"), PortionUnit.EACH_SMALL),
        HalalSauce.TZATZIKI.value: one_ounce,
    }
    for topping in HalalTopping:
        if topping is not HalalTopping.SMALL_EGG:
            portions[topping.value] = one_ounce
    return portions


HALAL_PORTION_POLICY = ComponentPortionPolicy(
    policy_version=OWNER_OBSERVED_CONFIG_VERSION,
    evidence=OWNER_OBSERVED_EVIDENCE,
    portions=_observed_halal_portions(),
)

OWNER_USUAL_HALAL_BOWL = HalalBowlSelection(
    bases=(HalalBase.TURMERIC_BASMATI_RICE,),
    proteins=(HalalProtein.JAMAICAN_HALAL_CHICKEN_THIGH,),
    toppings=(
        HalalTopping.SMALL_EGG,
        HalalTopping.SMALL_EGG,
        HalalTopping.QUINOA,
        HalalTopping.QUINOA,
        HalalTopping.QUINOA,
        HalalTopping.QUINOA,
    ),
)

OWNER_USUAL_HALAL_BOWL_DEFINITION = ConfigurableMealDefinition(
    definition_id="owner.cyo_halal_bowl",
    definition_version="owner-cyo-halal-bowl.v1",
    display_name="CYO Halal Bowl",
    source_name_normalized="CYO Halal Bowl",
    campus_id=50,
    template_version=HALAL_TEMPLATE_VERSION,
    configuration_version=OWNER_OBSERVED_CONFIG_VERSION,
    configuration_summary=(
        "4 oz cooked turmeric basmati rice, 4 oz cooked Jamaican halal chicken thigh, "
        "2 small eggs, 4 x 1 oz quinoa, no sauces"
    ),
    selected_components=materialize_halal_components(
        OWNER_USUAL_HALAL_BOWL,
        HALAL_PORTION_POLICY,
    ),
    nutrition_references=USUAL_HALAL_EXTERNAL_REFERENCES,
)

OWNER_CRISPY_CHICKPEA_HALAL_BOWL = HalalBowlSelection(
    bases=(HalalBase.TURMERIC_BASMATI_RICE,),
    proteins=(HalalProtein.JAMAICAN_HALAL_CHICKEN_THIGH,),
    toppings=(
        HalalTopping.SPICY_CRISPY_CHICKPEAS,
        HalalTopping.SPICY_CRISPY_CHICKPEAS,
        HalalTopping.QUINOA,
        HalalTopping.QUINOA,
        HalalTopping.QUINOA,
        HalalTopping.QUINOA,
    ),
)

OWNER_CRISPY_CHICKPEA_HALAL_BOWL_DEFINITION = ConfigurableMealDefinition(
    definition_id="owner.cyo_halal_bowl.crispy_chickpeas",
    definition_version="owner-cyo-halal-bowl-crispy-chickpeas.v1",
    display_name="CYO Halal Bowl — Crispy Chickpeas",
    source_name_normalized="CYO Halal Bowl",
    campus_id=50,
    template_version=HALAL_TEMPLATE_VERSION,
    configuration_version=OWNER_OBSERVED_CONFIG_VERSION,
    configuration_summary=(
        "4 oz cooked turmeric basmati rice, 4 oz cooked Jamaican halal chicken thigh, "
        "2 x 1 oz spicy crispy chickpeas, 4 x 1 oz quinoa, no sauces"
    ),
    selected_components=materialize_halal_components(
        OWNER_CRISPY_CHICKPEA_HALAL_BOWL,
        HALAL_PORTION_POLICY,
    ),
    # No cited nutrition reference for Spicy Crispy Chickpeas exists in the
    # repository. The definition is preserved, but its unresolved components
    # keep it ineligible for estimated planning.
    nutrition_references=USUAL_HALAL_EXTERNAL_REFERENCES,
)

OWNER_PROTEIN_PREFERENCES = ProteinPreferences(
    policy_version=OWNER_PREFERENCE_VERSION,
    allowed=frozenset(
        {
            HalalProtein.FALAFEL.value,
            HalalProtein.JAMAICAN_HALAL_CHICKEN_THIGH.value,
            DeliProtein.OVEN_ROASTED_CHICKEN.value,
        }
    ),
    # No disallowed protein is inferred from religion or from an incomplete
    # observation.  Additions require an explicit owner preference revision.
    disallowed=frozenset(),
    evidence=OWNER_OBSERVED_EVIDENCE,
)

OWNER_MEAL_PREFERENCES_V1 = OwnerMealPreferences(
    policy_version=OWNER_PREFERENCE_VERSION,
    halal_default=OWNER_USUAL_HALAL_BOWL,
    deli_default=DeliPreference(
        form_family=DeliFormFamily.SUB,
        protein=DeliProtein.OVEN_ROASTED_CHICKEN,
        bread=None,
        removed_components=frozenset({DeliModifier.CHEESE, DeliModifier.MAYONNAISE}),
        additions=frozenset({DeliModifier.CUCUMBER}),
    ),
    burger_preference=PreferenceLevel.LOW,
    protein_preferences=OWNER_PROTEIN_PREFERENCES,
)

OWNER_MEAL_PREFERENCES = OwnerMealPreferences(
    policy_version=OWNER_PREFERENCE_VERSION_V2,
    halal_default=OWNER_USUAL_HALAL_BOWL,
    deli_default=DeliPreference(
        form_family=DeliFormFamily.SUB,
        protein=DeliProtein.OVEN_ROASTED_CHICKEN,
        bread=None,
        removed_components=frozenset({DeliModifier.OLIVE_OIL}),
        additions=frozenset({DeliModifier.CUCUMBER}),
        preferred_forms=(DeliForm.HALF_SUB, DeliForm.WHOLE_SUB),
        retained_components=frozenset({DeliModifier.CHEESE}),
    ),
    burger_preference=PreferenceLevel.LOW,
    protein_preferences=OWNER_PROTEIN_PREFERENCES,
)

OWNER_HOME_BREAKFAST_DRAFT = HomeOatsMilkshakeDraft(
    oat_scoops=Decimal("2"),
    milk_volume_ml=Decimal("250"),
)
