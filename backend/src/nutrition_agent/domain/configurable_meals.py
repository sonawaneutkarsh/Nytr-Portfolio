"""Configurable-meal rules and evidence-aware estimated nutrition.

The public Stacks source does not publish configuration rules or physical
component portions for CYO meals.  This module therefore keeps three concerns
structurally separate:

* template rules: an explicitly versioned configuration contract;
* portion evidence: usually an owner-observed configuration;
* nutrition evidence: an exact Stacks label or a cited external reference.

No configurable result produced from external nutrition is Stacks-authoritative
or strict-planner eligible.  Missing quantities and missing nutrients remain
missing; they are never filled with zero.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from nutrition_agent.domain.nutrition.arithmetic import scale_facts, sum_facts
from nutrition_agent.domain.nutrition.facts import (
    ALL_NUTRIENT_KEYS,
    NutritionFacts,
    is_strict_eligible,
)
from nutrition_agent.domain.nutrition.serialization import facts_to_dict
from nutrition_agent.domain.stacks.entities import Confidence, NutrientKey


class EvidenceClass(StrEnum):
    STACKS_OFFICIAL = "stacks_official"
    EXTERNAL_REFERENCE = "external_reference"
    OWNER_OBSERVED_CONFIGURATION = "owner_observed_configuration"
    UNKNOWN = "unknown"


class PortionUnit(StrEnum):
    GRAM = "g"
    OUNCE = "oz"
    EACH_SMALL = "each_small"
    MILLILITER = "mL"
    UNKNOWN = "unknown"


class EstimateState(StrEnum):
    COMPLETE_ESTIMATE = "complete_estimate"
    PARTIAL_ESTIMATE = "partial_estimate"
    UNRESOLVED = "unresolved"


class PreferenceLevel(StrEnum):
    LOW = "low"
    NEUTRAL = "neutral"
    HIGH = "high"


class ConfigurableMealError(ValueError):
    """Raised when a concrete configuration violates its versioned template."""


@dataclass(frozen=True)
class EvidenceReference:
    source_class: EvidenceClass
    reference_id: str
    version: str
    description: str
    citation_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.source_class, EvidenceClass):
            raise TypeError("evidence source_class must be an EvidenceClass")
        if not self.reference_id.strip() or not self.version.strip():
            raise ValueError("evidence reference id and version are required")
        if not self.description.strip():
            raise ValueError("evidence description is required")
        if any(not url.strip() for url in self.citation_urls):
            raise ValueError("evidence citation URLs cannot be blank")
        if self.source_class is EvidenceClass.EXTERNAL_REFERENCE and not self.citation_urls:
            raise ValueError("external nutrition evidence requires a citation URL")


@dataclass(frozen=True)
class PortionRule:
    amount: Decimal
    unit: PortionUnit

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError("portion amounts must be Decimal")
        if not isinstance(self.unit, PortionUnit):
            raise TypeError("portion unit must be a PortionUnit")
        if self.amount <= 0:
            raise ValueError("portion amounts must be positive")
        if self.unit is PortionUnit.UNKNOWN:
            raise ValueError("a resolved portion cannot use the unknown unit")


@dataclass(frozen=True)
class ComponentPortionPolicy:
    policy_version: str
    evidence: EvidenceReference
    portions: Mapping[str, PortionRule]

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ValueError("component portion policy version is required")
        if self.evidence.source_class is not EvidenceClass.OWNER_OBSERVED_CONFIGURATION:
            raise ValueError("component portion policy must be owner-observed configuration")
        portions = dict(self.portions)
        if any(not component_id.strip() for component_id in portions):
            raise ValueError("component portion policy IDs cannot be blank")
        if any(not isinstance(rule, PortionRule) for rule in portions.values()):
            raise TypeError("component portion policy values must be PortionRule")
        object.__setattr__(self, "portions", MappingProxyType(portions))


@dataclass(frozen=True)
class ComponentPortion:
    amount: Decimal
    unit: PortionUnit
    evidence: EvidenceReference

    def __post_init__(self) -> None:
        PortionRule(self.amount, self.unit)


@dataclass(frozen=True)
class ComponentNutritionReference:
    component_id: str
    basis_amount: Decimal
    basis_unit: PortionUnit
    facts: NutritionFacts
    evidence: EvidenceReference
    caveats: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.component_id.strip():
            raise ValueError("nutrition reference component_id is required")
        PortionRule(self.basis_amount, self.basis_unit)
        if any(not caveat.strip() for caveat in self.caveats):
            raise ValueError("nutrition-reference caveats cannot be blank")
        if self.evidence.source_class not in {
            EvidenceClass.STACKS_OFFICIAL,
            EvidenceClass.EXTERNAL_REFERENCE,
        }:
            raise ValueError("nutrition must come from Stacks or a cited external reference")
        if (
            self.evidence.source_class is EvidenceClass.EXTERNAL_REFERENCE
            and self.facts.confidence is not Confidence.ESTIMATED
        ):
            raise ValueError("external nutrition facts must carry estimated confidence")
        if (
            self.evidence.source_class is EvidenceClass.STACKS_OFFICIAL
            and self.facts.confidence is not Confidence.OFFICIAL_PUBLISHED
        ):
            raise ValueError("Stacks nutrition facts must carry official_published confidence")


@dataclass(frozen=True)
class SelectedComponent:
    component_id: str
    portion: ComponentPortion | None

    def __post_init__(self) -> None:
        if not self.component_id.strip():
            raise ValueError("selected component_id is required")
        if self.portion is not None and not isinstance(self.portion, ComponentPortion):
            raise TypeError("selected component portion must be ComponentPortion or None")


@dataclass(frozen=True)
class ResolvedComponent:
    component_id: str
    portion: ComponentPortion
    nutrition_reference: ComponentNutritionReference
    multiplier: Decimal

    def __post_init__(self) -> None:
        if self.component_id != self.nutrition_reference.component_id:
            raise ValueError("resolved component does not match its nutrition reference")
        if not isinstance(self.multiplier, Decimal):
            raise TypeError("component multiplier must be Decimal")
        if self.multiplier <= 0:
            raise ValueError("component multiplier must be positive")


@dataclass(frozen=True)
class UnresolvedComponent:
    component_id: str
    reason: str

    def __post_init__(self) -> None:
        if not self.component_id.strip() or not self.reason.strip():
            raise ValueError("unresolved component id and reason are required")


@dataclass(frozen=True)
class ConfigurableNutritionEstimate:
    """Evidence-aware result whose ``totals`` may be only a resolved subtotal.

    Only ``COMPLETE_ESTIMATE`` represents a complete configured-meal total.
    ``PARTIAL_ESTIMATE`` preserves the arithmetic for resolved components while
    keeping every unresolved component, unavailable field, caveat, and unknown
    nutrient explicit.
    """

    state: EstimateState
    totals: NutritionFacts | None
    resolved_components: tuple[ResolvedComponent, ...]
    unresolved_components: tuple[UnresolvedComponent, ...]
    unknown_nutrients: frozenset[NutrientKey]
    caveats: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "resolved_components", tuple(self.resolved_components))
        object.__setattr__(self, "unresolved_components", tuple(self.unresolved_components))
        object.__setattr__(self, "unknown_nutrients", frozenset(self.unknown_nutrients))
        object.__setattr__(self, "caveats", tuple(self.caveats))
        if any(not caveat.strip() for caveat in self.caveats):
            raise ValueError("estimate caveats cannot be blank")

        if self.totals is None:
            if self.state is not EstimateState.UNRESOLVED:
                raise ValueError("only an unresolved estimate may omit totals")
            if self.resolved_components:
                raise ValueError("an unresolved estimate cannot contain resolved components")
            if self.unknown_nutrients != ALL_NUTRIENT_KEYS:
                raise ValueError("an unresolved estimate must keep every nutrient unknown")
            return

        if self.state is EstimateState.UNRESOLVED:
            raise ValueError("an unresolved estimate cannot contain totals")
        expected_unknown = frozenset(
            ALL_NUTRIENT_KEYS - set(self.totals.quantities) - set(self.totals.declared_unavailable)
        )
        if self.unknown_nutrients != expected_unknown:
            raise ValueError("estimate unknown nutrients do not match its totals")
        incomplete = bool(
            self.unresolved_components
            or self.unknown_nutrients
            or self.totals.declared_unavailable
            or self.caveats
        )
        if self.state is EstimateState.COMPLETE_ESTIMATE and incomplete:
            raise ValueError("complete estimate cannot contain unresolved evidence")
        if self.state is EstimateState.PARTIAL_ESTIMATE and not incomplete:
            raise ValueError("partial estimate requires explicit unresolved evidence")

    @property
    def strict_eligible(self) -> bool:
        return (
            self.state is EstimateState.COMPLETE_ESTIMATE
            and self.totals is not None
            and is_strict_eligible(self.totals)
        )


# NIST Handbook 44-2026, Appendix C. Exact avoirdupois conversion.
OUNCE_IN_GRAMS = Decimal("28.349523125")
UNIT_CONVERSION_VERSION = "nist-hb44-2026"
UNIT_CONVERSION_CITATION = "https://doi.org/10.6028/NIST.HB.44-2026"


def _multiplier(
    portion: ComponentPortion, reference: ComponentNutritionReference
) -> Decimal | None:
    if portion.unit is reference.basis_unit:
        return portion.amount / reference.basis_amount
    if portion.unit is PortionUnit.OUNCE and reference.basis_unit is PortionUnit.GRAM:
        return (portion.amount * OUNCE_IN_GRAMS) / reference.basis_amount
    return None


_AUTHORITY_RANK: dict[EvidenceClass, int] = {
    EvidenceClass.STACKS_OFFICIAL: 0,
    EvidenceClass.EXTERNAL_REFERENCE: 1,
    EvidenceClass.OWNER_OBSERVED_CONFIGURATION: 2,
    EvidenceClass.UNKNOWN: 3,
}


def choose_nutrition_reference(
    component: SelectedComponent,
    references: Sequence[ComponentNutritionReference],
) -> tuple[ComponentNutritionReference, Decimal] | None:
    """Choose the strongest compatible reference, rejecting ambiguous authority."""
    if component.portion is None:
        return None
    compatible = [
        (reference, multiplier)
        for reference in references
        if reference.component_id == component.component_id
        and (multiplier := _multiplier(component.portion, reference)) is not None
    ]
    if not compatible:
        return None
    best_rank = min(_AUTHORITY_RANK[item[0].evidence.source_class] for item in compatible)
    best = [
        item for item in compatible if _AUTHORITY_RANK[item[0].evidence.source_class] == best_rank
    ]
    if len(best) != 1:
        raise ValueError(
            f"ambiguous nutrition references for {component.component_id!r} at one authority"
        )
    return best[0]


def _derived_confidence(resolved: Sequence[ResolvedComponent]) -> Confidence:
    if any(
        item.nutrition_reference.evidence.source_class is EvidenceClass.EXTERNAL_REFERENCE
        for item in resolved
    ):
        return Confidence.ESTIMATED
    if any(
        item.portion.evidence.source_class is not EvidenceClass.STACKS_OFFICIAL for item in resolved
    ):
        return Confidence.VERIFIED_INTERNAL_RECIPE
    return Confidence.OFFICIAL_COMPONENT_SUM


def estimate_configurable_nutrition(
    components: Sequence[SelectedComponent],
    references: Sequence[ComponentNutritionReference],
) -> ConfigurableNutritionEstimate:
    """Aggregate only resolved components and preserve every missing fact."""
    resolved: list[ResolvedComponent] = []
    unresolved: list[UnresolvedComponent] = []
    scaled: list[NutritionFacts] = []
    caveats: list[str] = []

    for component in components:
        if component.portion is None:
            unresolved.append(
                UnresolvedComponent(component.component_id, "component portion is unknown")
            )
            continue
        chosen = choose_nutrition_reference(component, references)
        if chosen is None:
            unresolved.append(
                UnresolvedComponent(
                    component.component_id,
                    "no compatible nutrition reference is available",
                )
            )
            continue
        reference, multiplier = chosen
        resolved_component = ResolvedComponent(
            component_id=component.component_id,
            portion=component.portion,
            nutrition_reference=reference,
            multiplier=multiplier,
        )
        resolved.append(resolved_component)
        scaled.append(scale_facts(reference.facts, multiplier))
        caveats.extend(reference.caveats)

    if not scaled:
        return ConfigurableNutritionEstimate(
            state=EstimateState.UNRESOLVED,
            totals=None,
            resolved_components=(),
            unresolved_components=tuple(unresolved),
            unknown_nutrients=frozenset(ALL_NUTRIENT_KEYS),
            caveats=tuple(dict.fromkeys(caveats)),
        )

    summed = sum_facts(scaled)
    totals = NutritionFacts(
        quantities=summed.quantities,
        published_zero=frozenset(),
        declared_unavailable=summed.declared_unavailable,
        confidence=_derived_confidence(resolved),
    )
    unknown = frozenset(
        ALL_NUTRIENT_KEYS - set(totals.quantities) - set(totals.declared_unavailable)
    )
    state = (
        EstimateState.COMPLETE_ESTIMATE
        if not unresolved and not unknown and not totals.declared_unavailable and not caveats
        else EstimateState.PARTIAL_ESTIMATE
    )
    return ConfigurableNutritionEstimate(
        state=state,
        totals=totals,
        resolved_components=tuple(resolved),
        unresolved_components=tuple(unresolved),
        unknown_nutrients=unknown,
        caveats=tuple(dict.fromkeys(caveats)),
    )


def _evidence_document(evidence: EvidenceReference) -> dict[str, object]:
    return {
        "citation_urls": list(evidence.citation_urls),
        "description": evidence.description,
        "reference_id": evidence.reference_id,
        "source_class": evidence.source_class.value,
        "version": evidence.version,
    }


def _selected_component_document(component: SelectedComponent) -> dict[str, object]:
    portion: dict[str, object] | None = None
    if component.portion is not None:
        portion = {
            "amount": str(component.portion.amount),
            "evidence": _evidence_document(component.portion.evidence),
            "unit": component.portion.unit.value,
        }
    return {"component_id": component.component_id, "portion": portion}


def _resolved_component_document(component: ResolvedComponent) -> dict[str, object]:
    reference = component.nutrition_reference
    return {
        "component_id": component.component_id,
        "multiplier": str(component.multiplier),
        "nutrition_reference": {
            "basis_amount": str(reference.basis_amount),
            "basis_unit": reference.basis_unit.value,
            "caveats": list(reference.caveats),
            "evidence": _evidence_document(reference.evidence),
            "facts": facts_to_dict(reference.facts),
        },
        "portion": {
            "amount": str(component.portion.amount),
            "evidence": _evidence_document(component.portion.evidence),
            "unit": component.portion.unit.value,
        },
    }


@dataclass(frozen=True)
class ConfigurableMealDefinition:
    """One immutable, explicitly mapped configurable-meal decision input.

    The Stacks alias is availability evidence only. Nutrition remains bound to
    the selected owner-observed portions and cited external references carried
    by this definition.
    """

    definition_id: str
    definition_version: str
    display_name: str
    source_name_normalized: str
    campus_id: int
    template_version: str
    configuration_version: str
    configuration_summary: str
    selected_components: tuple[SelectedComponent, ...]
    nutrition_references: tuple[ComponentNutritionReference, ...]

    def __post_init__(self) -> None:
        required = (
            self.definition_id,
            self.definition_version,
            self.display_name,
            self.source_name_normalized,
            self.template_version,
            self.configuration_version,
            self.configuration_summary,
        )
        if any(not value.strip() for value in required):
            raise ValueError("configurable meal identity and description are required")
        if self.campus_id <= 0:
            raise ValueError("configurable meal campus_id must be positive")
        components = tuple(self.selected_components)
        references = tuple(self.nutrition_references)
        if not components or not references:
            raise ValueError("configurable meal components and references are required")
        object.__setattr__(self, "selected_components", components)
        object.__setattr__(self, "nutrition_references", references)

    @property
    def allowlist_identity(self) -> str:
        return (
            f"{self.definition_id}@{self.definition_version}:"
            f"{self.configuration_version}:{self.evidence_digest}"
        )

    @property
    def estimate(self) -> ConfigurableNutritionEstimate:
        return estimate_configurable_nutrition(
            self.selected_components,
            self.nutrition_references,
        )

    def decision_document(self) -> dict[str, object]:
        """Complete immutable definition/evidence document used by fingerprinting."""

        estimate = self.estimate
        return {
            "campus_id": self.campus_id,
            "configuration_summary": self.configuration_summary,
            "configuration_version": self.configuration_version,
            "definition_id": self.definition_id,
            "definition_version": self.definition_version,
            "display_name": self.display_name,
            "estimate": {
                "caveats": list(estimate.caveats),
                "confidence": estimate.totals.confidence.value if estimate.totals else None,
                "resolved_components": [
                    _resolved_component_document(component)
                    for component in estimate.resolved_components
                ],
                "state": estimate.state.value,
                "totals": facts_to_dict(estimate.totals),
                "unknown_nutrients": sorted(key.value for key in estimate.unknown_nutrients),
                "unresolved_components": [
                    {"component_id": component.component_id, "reason": component.reason}
                    for component in estimate.unresolved_components
                ],
            },
            "selected_components": [
                _selected_component_document(component) for component in self.selected_components
            ],
            "source_name_normalized": self.source_name_normalized,
            "template_version": self.template_version,
        }

    @property
    def evidence_digest(self) -> str:
        canonical = json.dumps(
            self.decision_document(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


class HalalBase(StrEnum):
    ICEBERG_ROMAINE_MIX = "iceberg_romaine_mix"
    TURMERIC_BASMATI_RICE = "turmeric_basmati_rice"


class HalalProtein(StrEnum):
    FALAFEL = "falafel"
    JAMAICAN_HALAL_CHICKEN_THIGH = "jamaican_halal_chicken_thigh"


class HalalTopping(StrEnum):
    KALAMATA_OLIVES = "kalamata_olives"
    CRISPY_RED_ONION = "crispy_red_onion"
    DICED_CUCUMBER = "diced_cucumber"
    DICED_RED_ONION = "diced_red_onion"
    DICED_TOMATOES = "diced_tomatoes"
    FETA = "feta"
    HUMMUS = "hummus"
    JALAPENOS = "jalapenos"
    QUINOA = "quinoa"
    SHREDDED_LETTUCE = "shredded_lettuce"
    SHREDDED_PARMESAN = "shredded_parmesan"
    SPICY_CRISPY_CHICKPEAS = "spicy_crispy_chickpeas"
    SMALL_EGG = "small_egg"


class HalalSauce(StrEnum):
    TURKISH_HOT = "turkish_hot_sauce"
    TZATZIKI = "tzatziki"
    GREEK_VINAIGRETTE = "greek_vinaigrette"
    CILANTRO_LIME = "cilantro_lime_sauce"


@dataclass(frozen=True)
class HalalBowlSelection:
    bases: tuple[HalalBase, ...]
    proteins: tuple[HalalProtein, ...]
    toppings: tuple[HalalTopping, ...] = ()
    sauces: tuple[HalalSauce, ...] = ()

    def validate(self) -> None:
        if len(self.bases) != 1:
            raise ConfigurableMealError("Halal Bowl requires exactly one base")
        if len(self.proteins) != 1:
            raise ConfigurableMealError("Halal Bowl requires exactly one protein")
        if len(self.toppings) > 6:
            raise ConfigurableMealError("Halal Bowl permits at most six topping slots")
        if self.toppings.count(HalalTopping.SMALL_EGG) > 2:
            raise ConfigurableMealError("Halal Bowl permits at most two small eggs")
        if any(not isinstance(value, HalalBase) for value in self.bases):
            raise ConfigurableMealError("Halal Bowl contains an unknown base")
        if any(not isinstance(value, HalalProtein) for value in self.proteins):
            raise ConfigurableMealError("Halal Bowl contains an unknown protein")
        if any(not isinstance(value, HalalTopping) for value in self.toppings):
            raise ConfigurableMealError("Halal Bowl contains an unknown topping")
        if any(not isinstance(value, HalalSauce) for value in self.sauces):
            raise ConfigurableMealError("Halal Bowl contains an unknown sauce")

    def component_ids(self) -> tuple[str, ...]:
        self.validate()
        return tuple(
            value.value for value in (*self.bases, *self.proteins, *self.toppings, *self.sauces)
        )


def materialize_halal_components(
    selection: HalalBowlSelection,
    portion_policy: ComponentPortionPolicy,
) -> tuple[SelectedComponent, ...]:
    return tuple(
        SelectedComponent(
            component_id=component_id,
            portion=(
                ComponentPortion(rule.amount, rule.unit, portion_policy.evidence)
                if (rule := portion_policy.portions.get(component_id)) is not None
                else None
            ),
        )
        for component_id in selection.component_ids()
    )


class BurgerBase(StrEnum):
    POTATO_ROLL = "potato_roll"
    GLUTEN_FREE_ROLL = "gluten_free_roll"
    NO_ROLL = "no_roll"


class BurgerProtein(StrEnum):
    GRILLED_CHICKEN_BREAST = "grilled_chicken_breast"
    FRIED_CHICKEN_BREAST = "fried_chicken_breast"
    BURGER_PATTY = "burger_patty"
    BEYOND_BURGER_PATTY = "beyond_burger_patty"
    BLACK_BEAN_PATTY = "black_bean_patty"


class BurgerCheese(StrEnum):
    AMERICAN = "american"
    PEPPER_JACK = "pepper_jack"
    SWISS = "swiss"
    PROVOLONE = "provolone"


class BurgerTopping(StrEnum):
    BACON = "bacon"
    TURKEY_BACON = "turkey_bacon"
    SLICED_ONION = "sliced_onion"
    SLICED_TOMATO = "sliced_tomato"
    SHREDDED_LETTUCE = "shredded_lettuce"


class BurgerSauce(StrEnum):
    KETCHUP = "ketchup"
    MUSTARD = "mustard"
    BBQ = "bbq_sauce"
    HOT_WING = "hot_wing_sauce"
    AVOCADO_MAYO = "avocado_mayonnaise"
    RANCH = "ranch"
    MAYO = "mayonnaise"


@dataclass(frozen=True)
class BurgerSelection:
    bases: tuple[BurgerBase, ...]
    proteins: tuple[BurgerProtein, ...]
    cheeses: tuple[BurgerCheese, ...] = ()
    toppings: tuple[BurgerTopping, ...] = ()
    sauces: tuple[BurgerSauce, ...] = ()

    def validate(self) -> None:
        if len(self.bases) != 1:
            raise ConfigurableMealError("burger requires exactly one bread/base option")
        if len(self.proteins) != 1:
            raise ConfigurableMealError("burger requires exactly one protein option")
        if len(self.cheeses) > 1:
            raise ConfigurableMealError("burger permits at most one cheese")
        if len(self.toppings) > 7:
            raise ConfigurableMealError("burger permits at most seven toppings")
        if len(self.sauces) > 4:
            raise ConfigurableMealError("burger permits at most four sauces")
        if any(not isinstance(value, BurgerBase) for value in self.bases):
            raise ConfigurableMealError("burger contains an unknown base")
        if any(not isinstance(value, BurgerProtein) for value in self.proteins):
            raise ConfigurableMealError("burger contains an unknown protein")
        if any(not isinstance(value, BurgerCheese) for value in self.cheeses):
            raise ConfigurableMealError("burger contains an unknown cheese")
        if any(not isinstance(value, BurgerTopping) for value in self.toppings):
            raise ConfigurableMealError("burger contains an unknown topping")
        if any(not isinstance(value, BurgerSauce) for value in self.sauces):
            raise ConfigurableMealError("burger contains an unknown sauce")
        if len(set(self.toppings)) != len(self.toppings):
            raise ConfigurableMealError("repeated burger toppings are not verified")
        if len(set(self.sauces)) != len(self.sauces):
            raise ConfigurableMealError("repeated burger sauces are not verified")


class DeliForm(StrEnum):
    HALF_SUB = "half_sub"
    WHOLE_SUB = "whole_sub"
    WRAP = "wrap"
    PANINI = "panini"
    BOWL = "bowl"


class DeliFormFamily(StrEnum):
    SUB = "sub"


class DeliBread(StrEnum):
    WHEAT = "wheat"
    WHITE = "white"


class DeliProtein(StrEnum):
    OVEN_ROASTED_CHICKEN = "oven_roasted_chicken"


class DeliModifier(StrEnum):
    CHEESE = "cheese"
    SHREDDED_LETTUCE = "shredded_lettuce"
    TOMATO = "tomato"
    ONION = "onion"
    ITALIAN_SEASONING = "italian_seasoning"
    OLIVE_OIL = "olive_oil"
    RED_WINE_VINEGAR = "red_wine_vinegar"
    MAYONNAISE = "mayonnaise"
    BANANA_PEPPERS = "banana_peppers"
    CUCUMBER = "cucumber"
    MUSTARD = "mustard"
    PEPPER_RELISH = "pepper_relish"


DELI_ADDITIONS = frozenset(
    {
        DeliModifier.BANANA_PEPPERS,
        DeliModifier.CUCUMBER,
        DeliModifier.MUSTARD,
        DeliModifier.PEPPER_RELISH,
    }
)

DELI_REMOVABLE_COMPONENTS = frozenset(
    {
        DeliModifier.CHEESE,
        DeliModifier.SHREDDED_LETTUCE,
        DeliModifier.TOMATO,
        DeliModifier.ONION,
        DeliModifier.ITALIAN_SEASONING,
        DeliModifier.OLIVE_OIL,
        DeliModifier.RED_WINE_VINEGAR,
        DeliModifier.MAYONNAISE,
    }
)


@dataclass(frozen=True)
class DeliSelection:
    form: DeliForm
    protein: DeliProtein
    bread: DeliBread | None
    removed_components: frozenset[DeliModifier] = frozenset()
    additions: frozenset[DeliModifier] = frozenset()

    def validate(self) -> None:
        if not isinstance(self.form, DeliForm):
            raise ConfigurableMealError("deli contains an unknown form")
        if not isinstance(self.protein, DeliProtein):
            raise ConfigurableMealError("deli contains an unknown supported protein")
        if self.form in {DeliForm.HALF_SUB, DeliForm.WHOLE_SUB}:
            if not isinstance(self.bread, DeliBread):
                raise ConfigurableMealError("sub form requires wheat or white bread")
        elif self.bread is not None:
            raise ConfigurableMealError("only sub forms use the observed sub-bread choice")
        if not self.removed_components <= DELI_REMOVABLE_COMPONENTS:
            raise ConfigurableMealError("deli contains an unverified removable component")
        if not self.additions <= DELI_ADDITIONS:
            raise ConfigurableMealError("deli contains an unverified addition")


@dataclass(frozen=True)
class ProteinPreferences:
    policy_version: str
    allowed: frozenset[str]
    disallowed: frozenset[str]
    evidence: EvidenceReference

    def __post_init__(self) -> None:
        if self.allowed & self.disallowed:
            raise ValueError("a protein cannot be both allowed and disallowed")
        if self.evidence.source_class is not EvidenceClass.OWNER_OBSERVED_CONFIGURATION:
            raise ValueError("protein preferences must be explicit owner configuration")


@dataclass(frozen=True)
class DeliPreference:
    """An owner preference that need not pretend unresolved choices are known."""

    form_family: DeliFormFamily
    protein: DeliProtein
    bread: DeliBread | None
    removed_components: frozenset[DeliModifier]
    additions: frozenset[DeliModifier]
    preferred_forms: tuple[DeliForm, ...] = ()
    retained_components: frozenset[DeliModifier] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.form_family, DeliFormFamily):
            raise TypeError("deli preference form family must be DeliFormFamily")
        if not isinstance(self.protein, DeliProtein):
            raise TypeError("deli preference protein must be DeliProtein")
        if self.bread is not None and not isinstance(self.bread, DeliBread):
            raise TypeError("deli preference bread must be DeliBread or None")
        if not self.removed_components <= DELI_REMOVABLE_COMPONENTS:
            raise ValueError("deli preference contains an unverified removal")
        if not self.additions <= DELI_ADDITIONS:
            raise ValueError("deli preference contains an unverified addition")
        forms = tuple(self.preferred_forms)
        retained = frozenset(self.retained_components)
        if any(not isinstance(form, DeliForm) for form in forms):
            raise TypeError("preferred deli forms must be DeliForm values")
        if len(set(forms)) != len(forms):
            raise ValueError("preferred deli forms cannot repeat")
        if not retained <= DELI_REMOVABLE_COMPONENTS:
            raise ValueError("retained deli components must be recognized default components")
        if retained & self.removed_components:
            raise ValueError("a deli component cannot be both retained and removed")
        object.__setattr__(self, "preferred_forms", forms)
        object.__setattr__(self, "retained_components", retained)

    @property
    def unresolved_fields(self) -> tuple[str, ...]:
        missing = [] if len(self.preferred_forms) == 1 else ["sub_size"]
        if self.bread is None:
            missing.append("sub_bread")
        return tuple(missing)


@dataclass(frozen=True)
class OwnerMealPreferences:
    policy_version: str
    halal_default: HalalBowlSelection
    deli_default: DeliPreference
    burger_preference: PreferenceLevel
    protein_preferences: ProteinPreferences


@dataclass(frozen=True)
class HomeOatsMilkshakeDraft:
    oat_scoops: Decimal
    milk_volume_ml: Decimal
    oats_grams_per_scoop: Decimal | None = None
    milk_type: str | None = None

    def __post_init__(self) -> None:
        for value in (self.oat_scoops, self.milk_volume_ml):
            if not isinstance(value, Decimal):
                raise TypeError("home meal quantities must be Decimal")
            if value <= 0:
                raise ValueError("home meal quantities must be positive")
        if self.oats_grams_per_scoop is not None:
            if not isinstance(self.oats_grams_per_scoop, Decimal):
                raise TypeError("oats grams per scoop must be Decimal")
            if self.oats_grams_per_scoop <= 0:
                raise ValueError("oats grams per scoop must be positive")

    @property
    def unresolved_fields(self) -> tuple[str, ...]:
        missing: list[str] = []
        if self.oats_grams_per_scoop is None:
            missing.append("oats_grams_per_scoop")
        if self.milk_type is None or not self.milk_type.strip():
            missing.append("milk_type")
        return tuple(missing)

    @property
    def nutrition_resolved(self) -> bool:
        return not self.unresolved_fields
