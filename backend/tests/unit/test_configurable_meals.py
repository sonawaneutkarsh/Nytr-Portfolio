from __future__ import annotations

from decimal import Decimal

import pytest

from nutrition_agent.domain.configurable_meals import (
    BurgerBase,
    BurgerCheese,
    BurgerProtein,
    BurgerSauce,
    BurgerSelection,
    BurgerTopping,
    ComponentNutritionReference,
    ComponentPortion,
    ComponentPortionPolicy,
    ConfigurableMealError,
    DeliBread,
    DeliForm,
    DeliModifier,
    DeliProtein,
    DeliSelection,
    EstimateState,
    EvidenceClass,
    EvidenceReference,
    HalalBase,
    HalalBowlSelection,
    HalalProtein,
    HalalSauce,
    HalalTopping,
    PortionRule,
    PortionUnit,
    SelectedComponent,
    choose_nutrition_reference,
    estimate_configurable_nutrition,
    materialize_halal_components,
)
from nutrition_agent.domain.nutrition.facts import (
    ALL_NUTRIENT_KEYS,
    NutritionFacts,
    presence_of,
)
from nutrition_agent.domain.owner_meal_config import (
    HALAL_PORTION_POLICY,
    OWNER_CRISPY_CHICKPEA_HALAL_BOWL,
    OWNER_CRISPY_CHICKPEA_HALAL_BOWL_DEFINITION,
    OWNER_HOME_BREAKFAST_DRAFT,
    OWNER_MEAL_PREFERENCES,
    OWNER_MEAL_PREFERENCES_V1,
    OWNER_USUAL_HALAL_BOWL,
)
from nutrition_agent.domain.stacks.entities import Confidence, NutrientKey


def _evidence(source_class: EvidenceClass) -> EvidenceReference:
    return EvidenceReference(
        source_class=source_class,
        reference_id=f"test-{source_class.value}",
        version="test.v1",
        description="test evidence",
        citation_urls=("https://example.invalid/reference",)
        if source_class is EvidenceClass.EXTERNAL_REFERENCE
        else (),
    )


def _facts(
    confidence: Confidence,
    values: dict[NutrientKey, str] | None = None,
) -> NutritionFacts:
    source = values or {key: "1" for key in ALL_NUTRIENT_KEYS}
    return NutritionFacts(
        quantities={key: Decimal(value) for key, value in source.items()},
        published_zero=frozenset(),
        declared_unavailable=frozenset(),
        confidence=confidence,
    )


def _reference(
    component_id: str,
    source_class: EvidenceClass = EvidenceClass.EXTERNAL_REFERENCE,
    *,
    complete: bool = True,
    caveats: tuple[str, ...] = (),
) -> ComponentNutritionReference:
    confidence = (
        Confidence.OFFICIAL_PUBLISHED
        if source_class is EvidenceClass.STACKS_OFFICIAL
        else Confidence.ESTIMATED
    )
    values = None if complete else {NutrientKey.CALORIES_KCAL: "100"}
    return ComponentNutritionReference(
        component_id=component_id,
        basis_amount=Decimal("1"),
        basis_unit=PortionUnit.OUNCE,
        facts=_facts(confidence, values),
        evidence=_evidence(source_class),
        caveats=caveats,
    )


def _halal(
    *,
    bases: tuple[HalalBase, ...] = (HalalBase.TURMERIC_BASMATI_RICE,),
    proteins: tuple[HalalProtein, ...] = (HalalProtein.JAMAICAN_HALAL_CHICKEN_THIGH,),
    toppings: tuple[HalalTopping, ...] = (),
    sauces: tuple[HalalSauce, ...] = (),
) -> HalalBowlSelection:
    return HalalBowlSelection(bases, proteins, toppings, sauces)


@pytest.mark.parametrize(
    "bases",
    [(), (HalalBase.TURMERIC_BASMATI_RICE, HalalBase.ICEBERG_ROMAINE_MIX)],
)
def test_halal_requires_exactly_one_base(bases: tuple[HalalBase, ...]) -> None:
    with pytest.raises(ConfigurableMealError, match="exactly one base"):
        _halal(bases=bases).validate()


@pytest.mark.parametrize(
    "proteins",
    [(), (HalalProtein.FALAFEL, HalalProtein.JAMAICAN_HALAL_CHICKEN_THIGH)],
)
def test_halal_requires_exactly_one_protein(proteins: tuple[HalalProtein, ...]) -> None:
    with pytest.raises(ConfigurableMealError, match="exactly one protein"):
        _halal(proteins=proteins).validate()


def test_halal_rejects_more_than_six_topping_slots() -> None:
    toppings = (HalalTopping.QUINOA,) * 7
    with pytest.raises(ConfigurableMealError, match="six topping slots"):
        _halal(toppings=toppings).validate()


def test_halal_rejects_more_than_two_eggs() -> None:
    with pytest.raises(ConfigurableMealError, match="two small eggs"):
        _halal(toppings=(HalalTopping.SMALL_EGG,) * 3).validate()


def test_owner_default_uses_two_egg_and_four_repeated_quinoa_slots() -> None:
    OWNER_USUAL_HALAL_BOWL.validate()
    assert OWNER_USUAL_HALAL_BOWL.toppings.count(HalalTopping.SMALL_EGG) == 2
    assert OWNER_USUAL_HALAL_BOWL.toppings.count(HalalTopping.QUINOA) == 4
    components = materialize_halal_components(OWNER_USUAL_HALAL_BOWL, HALAL_PORTION_POLICY)
    quinoa = [item for item in components if item.component_id == HalalTopping.QUINOA]
    assert len(quinoa) == 4
    assert all(item.portion is not None for item in quinoa)
    assert all(item.portion.amount == Decimal("1") for item in quinoa if item.portion)
    assert all(item.portion.unit is PortionUnit.OUNCE for item in quinoa if item.portion)


def test_owner_crispy_chickpea_variant_uses_exact_six_slots_and_no_sauce() -> None:
    selection = OWNER_CRISPY_CHICKPEA_HALAL_BOWL
    selection.validate()

    assert len(selection.toppings) == 6
    assert selection.toppings.count(HalalTopping.QUINOA) == 4
    assert selection.toppings.count(HalalTopping.SPICY_CRISPY_CHICKPEAS) == 2
    assert selection.sauces == ()
    assert (
        OWNER_CRISPY_CHICKPEA_HALAL_BOWL_DEFINITION.definition_id
        == "owner.cyo_halal_bowl.crispy_chickpeas"
    )


def test_halal_sauces_do_not_consume_slots_and_multiple_are_allowed() -> None:
    selection = _halal(
        toppings=(HalalTopping.QUINOA,) * 6,
        sauces=(HalalSauce.TURKISH_HOT,) * 8 + (HalalSauce.TZATZIKI,),
    )
    selection.validate()
    assert len(selection.toppings) == 6
    assert len(selection.sauces) == 9


def test_unknown_sauce_quantity_remains_unresolved_not_zero() -> None:
    selection = _halal(sauces=(HalalSauce.TURKISH_HOT,))
    components = materialize_halal_components(selection, HALAL_PORTION_POLICY)
    sauce = next(item for item in components if item.component_id == HalalSauce.TURKISH_HOT)
    assert sauce.portion is None
    result = estimate_configurable_nutrition(components, ())
    assert result.state is EstimateState.UNRESOLVED
    assert result.totals is None
    assert any(item.component_id == HalalSauce.TURKISH_HOT for item in result.unresolved_components)


def test_external_nutrition_is_estimated_and_never_stacks_official() -> None:
    components = materialize_halal_components(_halal(), HALAL_PORTION_POLICY)
    refs = tuple(_reference(item.component_id) for item in components if item.portion)
    result = estimate_configurable_nutrition(components, refs)
    assert result.state is EstimateState.COMPLETE_ESTIMATE
    assert result.totals is not None
    assert result.totals.confidence is Confidence.ESTIMATED
    assert result.strict_eligible is False
    assert {
        item.nutrition_reference.evidence.source_class for item in result.resolved_components
    } == {EvidenceClass.EXTERNAL_REFERENCE}
    assert {item.portion.evidence.source_class for item in result.resolved_components} == {
        EvidenceClass.OWNER_OBSERVED_CONFIGURATION
    }


def test_stacks_official_reference_wins_but_owner_portion_prevents_strict_sum() -> None:
    component = materialize_halal_components(_halal(), HALAL_PORTION_POLICY)[0]
    external = _reference(component.component_id)
    official = _reference(component.component_id, EvidenceClass.STACKS_OFFICIAL)
    chosen = choose_nutrition_reference(component, (external, official))
    assert chosen is not None
    assert chosen[0] is official
    result = estimate_configurable_nutrition((component,), (external, official))
    assert result.totals is not None
    assert result.totals.confidence is Confidence.VERIFIED_INTERNAL_RECIPE
    assert result.strict_eligible is False


def test_missing_component_reference_and_missing_nutrients_stay_missing() -> None:
    components = materialize_halal_components(_halal(), HALAL_PORTION_POLICY)
    rice = components[0]
    result = estimate_configurable_nutrition(
        (rice,), (_reference(rice.component_id, complete=False),)
    )
    assert result.state is EstimateState.PARTIAL_ESTIMATE
    assert result.totals is not None
    assert result.totals.quantities == {NutrientKey.CALORIES_KCAL: Decimal("400")}
    assert NutrientKey.PROTEIN_G in result.unknown_nutrients
    assert NutrientKey.PROTEIN_G not in result.totals.quantities
    assert presence_of(result.totals, NutrientKey.PROTEIN_G).value == "unknown_absent"
    assert result.strict_eligible is False


def test_reference_caveat_keeps_otherwise_complete_result_partial() -> None:
    component = materialize_halal_components(_halal(), HALAL_PORTION_POLICY)[0]
    result = estimate_configurable_nutrition(
        (component,),
        (_reference(component.component_id, caveats=("preparation differs",)),),
    )
    assert result.state is EstimateState.PARTIAL_ESTIMATE
    assert result.caveats == ("preparation differs",)


def test_partial_official_subtotal_can_never_claim_strict_eligibility() -> None:
    official_evidence = _evidence(EvidenceClass.STACKS_OFFICIAL)
    resolved = SelectedComponent(
        component_id="published_component",
        portion=ComponentPortion(Decimal("1"), PortionUnit.OUNCE, official_evidence),
    )
    unresolved = SelectedComponent(component_id="unresolved_component", portion=None)
    result = estimate_configurable_nutrition(
        (resolved, unresolved),
        (_reference("published_component", EvidenceClass.STACKS_OFFICIAL),),
    )

    assert result.state is EstimateState.PARTIAL_ESTIMATE
    assert result.totals is not None
    assert result.totals.confidence is Confidence.OFFICIAL_COMPONENT_SUM
    assert result.unresolved_components[0].component_id == "unresolved_component"
    assert result.unresolved_components[0].reason == "component portion is unknown"
    assert result.strict_eligible is False


def test_portion_and_policy_inputs_reject_float_or_untyped_values() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        PortionRule(1.0, PortionUnit.OUNCE)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="PortionUnit"):
        PortionRule(Decimal("1"), "oz")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="PortionRule"):
        ComponentPortionPolicy(
            policy_version="test.v1",
            evidence=_evidence(EvidenceClass.OWNER_OBSERVED_CONFIGURATION),
            portions={"quinoa": Decimal("1")},  # type: ignore[dict-item]
        )


@pytest.mark.parametrize(
    ("form", "bread"),
    [
        (DeliForm.HALF_SUB, DeliBread.WHEAT),
        (DeliForm.WHOLE_SUB, DeliBread.WHITE),
        (DeliForm.WRAP, None),
        (DeliForm.PANINI, None),
        (DeliForm.BOWL, None),
    ],
)
def test_deli_form_variants_validate(form: DeliForm, bread: DeliBread | None) -> None:
    DeliSelection(form, DeliProtein.OVEN_ROASTED_CHICKEN, bread).validate()


def test_deli_sub_requires_bread_and_bowl_forbids_it() -> None:
    with pytest.raises(ConfigurableMealError, match="requires wheat or white"):
        DeliSelection(DeliForm.WHOLE_SUB, DeliProtein.OVEN_ROASTED_CHICKEN, None).validate()
    with pytest.raises(ConfigurableMealError, match="only sub forms"):
        DeliSelection(DeliForm.BOWL, DeliProtein.OVEN_ROASTED_CHICKEN, DeliBread.WHEAT).validate()


def test_owner_deli_preference_preserves_half_whole_choices_and_unknown_bread() -> None:
    preference = OWNER_MEAL_PREFERENCES.deli_default
    assert preference.protein is DeliProtein.OVEN_ROASTED_CHICKEN
    assert preference.preferred_forms == (DeliForm.HALF_SUB, DeliForm.WHOLE_SUB)
    assert preference.retained_components == frozenset({DeliModifier.CHEESE})
    assert preference.removed_components == frozenset({DeliModifier.OLIVE_OIL})
    assert preference.additions == frozenset({DeliModifier.CUCUMBER})
    assert preference.unresolved_fields == ("sub_size", "sub_bread")


def test_historical_deli_preference_v1_is_unchanged() -> None:
    preference = OWNER_MEAL_PREFERENCES_V1.deli_default

    assert preference.preferred_forms == ()
    assert preference.retained_components == frozenset()
    assert preference.removed_components == frozenset(
        {DeliModifier.CHEESE, DeliModifier.MAYONNAISE}
    )
    assert preference.additions == frozenset({DeliModifier.CUCUMBER})
    assert preference.unresolved_fields == ("sub_size", "sub_bread")


def test_deli_preference_cannot_remove_and_retain_the_same_component() -> None:
    with pytest.raises(ValueError, match="both retained and removed"):
        type(OWNER_MEAL_PREFERENCES.deli_default)(
            form_family=OWNER_MEAL_PREFERENCES.deli_default.form_family,
            protein=DeliProtein.OVEN_ROASTED_CHICKEN,
            bread=None,
            removed_components=frozenset({DeliModifier.CHEESE}),
            additions=frozenset(),
            preferred_forms=(DeliForm.WHOLE_SUB,),
            retained_components=frozenset({DeliModifier.CHEESE}),
        )


def test_deli_removals_and_additions_are_bounded_to_observed_sets() -> None:
    valid = DeliSelection(
        DeliForm.WHOLE_SUB,
        DeliProtein.OVEN_ROASTED_CHICKEN,
        DeliBread.WHEAT,
        removed_components=frozenset({DeliModifier.CHEESE, DeliModifier.MAYONNAISE}),
        additions=frozenset({DeliModifier.CUCUMBER}),
    )
    valid.validate()
    with pytest.raises(ConfigurableMealError, match="unverified removable"):
        DeliSelection(
            DeliForm.WHOLE_SUB,
            DeliProtein.OVEN_ROASTED_CHICKEN,
            DeliBread.WHITE,
            removed_components=frozenset({DeliModifier.CUCUMBER}),
        ).validate()


def test_deli_half_and_whole_are_distinct_without_a_half_of_whole_derivation() -> None:
    half = DeliSelection(
        DeliForm.HALF_SUB,
        DeliProtein.OVEN_ROASTED_CHICKEN,
        DeliBread.WHEAT,
    )
    whole = DeliSelection(
        DeliForm.WHOLE_SUB,
        DeliProtein.OVEN_ROASTED_CHICKEN,
        DeliBread.WHEAT,
    )

    half.validate()
    whole.validate()
    assert half != whole
    assert half.form is DeliForm.HALF_SUB
    assert whole.form is DeliForm.WHOLE_SUB
    assert not hasattr(half, "nutrition")
    assert not hasattr(half, "estimate")


def test_deli_changes_freeze_choices_without_inventing_component_arithmetic() -> None:
    selection = DeliSelection(
        DeliForm.WHOLE_SUB,
        DeliProtein.OVEN_ROASTED_CHICKEN,
        DeliBread.WHITE,
        removed_components=frozenset({DeliModifier.OLIVE_OIL, DeliModifier.MAYONNAISE}),
        additions=frozenset({DeliModifier.CUCUMBER}),
    )

    selection.validate()
    assert selection.removed_components == frozenset(
        {DeliModifier.OLIVE_OIL, DeliModifier.MAYONNAISE}
    )
    assert selection.additions == frozenset({DeliModifier.CUCUMBER})
    assert not hasattr(selection, "nutrition_adjustment")


def test_burger_known_options_and_caps() -> None:
    BurgerSelection(
        bases=(BurgerBase.NO_ROLL,),
        proteins=(BurgerProtein.GRILLED_CHICKEN_BREAST,),
        cheeses=(BurgerCheese.PROVOLONE,),
        toppings=(BurgerTopping.SLICED_ONION,) * 1,
        sauces=(BurgerSauce.KETCHUP, BurgerSauce.MUSTARD),
    ).validate()
    with pytest.raises(ConfigurableMealError, match="seven toppings"):
        BurgerSelection(
            bases=(BurgerBase.POTATO_ROLL,),
            proteins=(BurgerProtein.BLACK_BEAN_PATTY,),
            toppings=(BurgerTopping.SLICED_TOMATO,) * 8,
        ).validate()
    with pytest.raises(ConfigurableMealError, match="four sauces"):
        BurgerSelection(
            bases=(BurgerBase.POTATO_ROLL,),
            proteins=(BurgerProtein.BEYOND_BURGER_PATTY,),
            sauces=(
                BurgerSauce.KETCHUP,
                BurgerSauce.MUSTARD,
                BurgerSauce.BBQ,
                BurgerSauce.RANCH,
                BurgerSauce.MAYO,
            ),
        ).validate()
    assert {item.value for item in BurgerSauce} == {
        "ketchup",
        "mustard",
        "bbq_sauce",
        "hot_wing_sauce",
        "avocado_mayonnaise",
        "ranch",
        "mayonnaise",
    }


def test_burger_unknown_option_fails_closed() -> None:
    selection = BurgerSelection(
        bases=(BurgerBase.POTATO_ROLL,),
        proteins=(BurgerProtein.GRILLED_CHICKEN_BREAST,),
        sauces=("invented_sauce",),  # type: ignore[arg-type]
    )
    with pytest.raises(ConfigurableMealError, match="unknown sauce"):
        selection.validate()


def test_home_breakfast_remains_unresolved_without_mass_and_milk_type() -> None:
    assert OWNER_HOME_BREAKFAST_DRAFT.oat_scoops == Decimal("2")
    assert OWNER_HOME_BREAKFAST_DRAFT.milk_volume_ml == Decimal("250")
    assert OWNER_HOME_BREAKFAST_DRAFT.unresolved_fields == (
        "oats_grams_per_scoop",
        "milk_type",
    )
    assert OWNER_HOME_BREAKFAST_DRAFT.nutrition_resolved is False


def test_versioned_portion_policy_is_immutable() -> None:
    with pytest.raises(TypeError):
        HALAL_PORTION_POLICY.portions[HalalTopping.QUINOA.value] = HALAL_PORTION_POLICY.portions[
            HalalTopping.QUINOA.value
        ]  # type: ignore[index]
