"""Explicit, versioned owner dietary constraints for deterministic planning.

Stacks publishes a useful ``Contains Pork`` tag but no complete animal-source
taxonomy. This module combines structured source facts with explicit
ingredient/recipe evidence, exact source-name classifications, and narrow
deterministic token rules. It performs no fuzzy matching and encodes no
religious inference.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from nutrition_agent.domain.planning.menu_view import OfferingView
from nutrition_agent.domain.stacks.entities import DietaryTag


class AnimalSource(StrEnum):
    BEEF = "beef"
    PORK = "pork"
    CHICKEN = "chicken"
    TURKEY = "turkey"
    FISH = "fish"
    SEAFOOD = "seafood"
    SHRIMP = "shrimp"
    TUNA = "tuna"
    LAMB = "lamb"
    MUTTON = "mutton"
    GOAT = "goat"
    OTHER_MAMMAL = "other_mammal"
    PLANT = "plant"
    DAIRY = "dairy"
    EGG = "egg"
    UNKNOWN = "unknown"


class AnimalSourceEvidence(StrEnum):
    STRUCTURED_DIETARY_TAG = "structured_dietary_tag"
    EXPLICIT_INGREDIENT_TEXT = "explicit_ingredient_text"
    EXPLICIT_RECIPE_TEXT = "explicit_recipe_text"
    EXACT_SOURCE_NAME = "exact_source_name"
    OWNER_CONFIGURATION = "owner_configuration"
    DETERMINISTIC_SOURCE_TERM = "deterministic_source_term"
    AMBIGUOUS_SOURCE_NAME = "ambiguous_source_name"
    NOT_CLASSIFIED = "not_classified"


class DietaryClassification(StrEnum):
    SAFE_ALLOWED_ANIMAL = "safe_allowed_animal"
    SAFE_NONMEAT = "safe_nonmeat"
    DISALLOWED_ANIMAL = "disallowed_animal"
    UNKNOWN_MEAT_SOURCE = "unknown_meat_source"


@dataclass(frozen=True)
class AnimalSourceRule:
    """One exact Stacks ``name_normalized`` classification."""

    source: AnimalSource
    evidence: AnimalSourceEvidence

    def __post_init__(self) -> None:
        if self.evidence not in {
            AnimalSourceEvidence.EXACT_SOURCE_NAME,
            AnimalSourceEvidence.OWNER_CONFIGURATION,
        }:
            raise ValueError("exact animal-source rules require exact-name evidence")


@dataclass(frozen=True)
class DietaryPolicy:
    """Hard owner constraints; unclassified neutral foods remain eligible.

    ``ambiguous_animal_tokens`` is intentionally limited to generic meat
    constructs whose animal source cannot be inferred safely. An empty
    ``allowed_animal_sources`` preserves the historical denylist behavior. A
    non-empty value makes the known animal-source set an explicit allowlist;
    ordinary foods with no meat evidence remain safe non-meat rather than
    becoming unknown meat.
    """

    version: str
    disallowed_animal_sources: frozenset[AnimalSource]
    exact_name_rules: Mapping[str, AnimalSourceRule]
    ambiguous_animal_tokens: frozenset[str]
    allowed_animal_sources: frozenset[AnimalSource] = frozenset()
    ambiguous_source_tokens: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("dietary policy version is required")
        disallowed = frozenset(self.disallowed_animal_sources)
        if not disallowed or AnimalSource.UNKNOWN in disallowed:
            raise ValueError("dietary policy requires explicit known disallowed sources")
        if any(not isinstance(source, AnimalSource) for source in disallowed):
            raise TypeError("disallowed animal sources must be AnimalSource values")
        allowed = frozenset(self.allowed_animal_sources)
        if any(source not in _ANIMAL_PROTEIN_SOURCES for source in allowed):
            raise ValueError("allowed animal sources must be explicit animal proteins")
        if allowed & disallowed:
            raise ValueError("animal sources cannot be both allowed and disallowed")
        rules = dict(self.exact_name_rules)
        if any(not name.strip() or name != " ".join(name.split()) for name in rules):
            raise ValueError("dietary aliases must be exact whitespace-normalized names")
        if any(not isinstance(rule, AnimalSourceRule) for rule in rules.values()):
            raise TypeError("dietary aliases must map to AnimalSourceRule values")
        tokens = frozenset(self.ambiguous_animal_tokens)
        if not tokens or any(
            not token or token != token.lower() or " " in token for token in tokens
        ):
            raise ValueError("ambiguous dietary tokens must be lowercase single tokens")
        source_tokens = frozenset(self.ambiguous_source_tokens)
        if any(not token or token != token.lower() or " " in token for token in source_tokens):
            raise ValueError("ambiguous source tokens must be lowercase single tokens")
        object.__setattr__(self, "disallowed_animal_sources", disallowed)
        object.__setattr__(self, "allowed_animal_sources", allowed)
        object.__setattr__(self, "exact_name_rules", MappingProxyType(rules))
        object.__setattr__(self, "ambiguous_animal_tokens", tokens)
        object.__setattr__(self, "ambiguous_source_tokens", source_tokens)


@dataclass(frozen=True)
class AnimalSourceAssessment:
    source: AnimalSource | None
    evidence: AnimalSourceEvidence
    classification: DietaryClassification
    detail: str


def assess_animal_source(
    offering: OfferingView,
    policy: DietaryPolicy,
) -> AnimalSourceAssessment:
    """Classify only evidence the policy can defend; never infer from a model."""

    if not policy.allowed_animal_sources:
        return _assess_legacy_denylist(offering, policy)
    return _assess_allowlisted_animal_policy(offering, policy)


def _assess_legacy_denylist(
    offering: OfferingView,
    policy: DietaryPolicy,
) -> AnimalSourceAssessment:
    """Preserve the exact M11K evidence order and denylist behavior."""

    tags = set(offering.dietary_tags)
    if DietaryTag.CONTAINS_PORK in tags:
        return _assessment(
            AnimalSource.PORK,
            AnimalSourceEvidence.STRUCTURED_DIETARY_TAG,
            "Stacks structured Contains Pork tag",
            policy,
        )

    profile = offering.profile
    if profile is not None:
        ingredient_source = _legacy_prohibited_source(profile.ingredients_raw)
        if ingredient_source is not None:
            return _assessment(
                ingredient_source,
                AnimalSourceEvidence.EXPLICIT_INGREDIENT_TEXT,
                "explicit animal source in Stacks ingredient text",
                policy,
            )
        for component in profile.ingredient_components or ():
            recipe_source = _legacy_prohibited_source(component.text)
            if recipe_source is not None:
                return _assessment(
                    recipe_source,
                    AnimalSourceEvidence.EXPLICIT_RECIPE_TEXT,
                    "explicit animal source in Stacks recipe text",
                    policy,
                )

    exact = policy.exact_name_rules.get(offering.name_normalized)
    if exact is not None:
        return _assessment(
            exact.source,
            exact.evidence,
            f"exact source-name rule: {offering.name_normalized}",
            policy,
        )

    if tags & {DietaryTag.MEATLESS, DietaryTag.VEGAN}:
        return _assessment(
            AnimalSource.PLANT,
            AnimalSourceEvidence.STRUCTURED_DIETARY_TAG,
            "Stacks structured Meatless/Vegan tag",
            policy,
        )

    tokens = frozenset(_name_tokens(offering.name_normalized))
    ambiguous = sorted(tokens & policy.ambiguous_animal_tokens)
    if ambiguous:
        return _assessment(
            AnimalSource.UNKNOWN,
            AnimalSourceEvidence.AMBIGUOUS_SOURCE_NAME,
            f"unresolved animal source for token(s): {','.join(ambiguous)}",
            policy,
        )
    return _assessment(
        None,
        AnimalSourceEvidence.NOT_CLASSIFIED,
        "no animal-source claim was needed or established",
        policy,
    )


def _assess_allowlisted_animal_policy(
    offering: OfferingView,
    policy: DietaryPolicy,
) -> AnimalSourceAssessment:
    """Use the first safe evidence only after ruling out higher-risk evidence."""

    tags = set(offering.dietary_tags)
    if DietaryTag.CONTAINS_PORK in tags:
        return _assessment(
            AnimalSource.PORK,
            AnimalSourceEvidence.STRUCTURED_DIETARY_TAG,
            "Stacks structured Contains Pork tag",
            policy,
        )

    safe: AnimalSourceAssessment | None = None
    if tags & {DietaryTag.MEATLESS, DietaryTag.VEGAN}:
        safe = _assessment(
            AnimalSource.PLANT,
            AnimalSourceEvidence.STRUCTURED_DIETARY_TAG,
            "Stacks structured Meatless/Vegan tag",
            policy,
        )

    profile = offering.profile
    if profile is not None:
        source_texts = (
            (profile.ingredients_raw, AnimalSourceEvidence.EXPLICIT_INGREDIENT_TEXT),
            *(
                (component.text, AnimalSourceEvidence.EXPLICIT_RECIPE_TEXT)
                for component in profile.ingredient_components or ()
            ),
        )
        for source_text, evidence in source_texts:
            source = _source_from_text(
                source_text,
                policy.ambiguous_source_tokens,
                include_nonmeat=False,
            )
            if source is None:
                continue
            assessment = _assessment(
                source,
                evidence,
                "explicit animal source in Stacks ingredient/recipe text",
                policy,
            )
            if _is_unsafe(assessment):
                return assessment
            if safe is None:
                safe = assessment

    exact = policy.exact_name_rules.get(offering.name_normalized)
    if exact is not None:
        assessment = _assessment(
            exact.source,
            exact.evidence,
            f"exact source-name rule: {offering.name_normalized}",
            policy,
        )
        if _is_unsafe(assessment):
            return assessment
        if safe is None:
            safe = assessment

    if safe is not None:
        return safe

    source = _source_from_text(
        offering.name_normalized,
        policy.ambiguous_animal_tokens,
        include_nonmeat=True,
    )
    if source is not None:
        tokens = frozenset(_name_tokens(offering.name_normalized))
        ambiguous = sorted(tokens & policy.ambiguous_animal_tokens)
        evidence = (
            AnimalSourceEvidence.AMBIGUOUS_SOURCE_NAME
            if source is AnimalSource.UNKNOWN
            else AnimalSourceEvidence.DETERMINISTIC_SOURCE_TERM
        )
        detail = (
            f"unresolved animal source for token(s): {','.join(ambiguous)}"
            if source is AnimalSource.UNKNOWN
            else f"explicit source term in normalized name: {source.value}"
        )
        return _assessment(source, evidence, detail, policy)
    return _assessment(
        None,
        AnimalSourceEvidence.NOT_CLASSIFIED,
        "no animal-source claim was needed or established",
        policy,
    )


def _is_unsafe(assessment: AnimalSourceAssessment) -> bool:
    return assessment.classification in {
        DietaryClassification.DISALLOWED_ANIMAL,
        DietaryClassification.UNKNOWN_MEAT_SOURCE,
    }


def _name_tokens(value: str) -> tuple[str, ...]:
    normalized = "".join(character.lower() if character.isalnum() else " " for character in value)
    return tuple(normalized.split())


def _legacy_prohibited_source(value: str) -> AnimalSource | None:
    """M11K's exact beef/pork ingredient classifier, retained byte-for-byte in meaning."""

    tokens = frozenset(_name_tokens(value))
    if tokens & {"pork", "porcine", "swine"}:
        return AnimalSource.PORK
    if "pig" in tokens and tokens & {"belly", "fat", "meat", "shoulder"}:
        return AnimalSource.PORK
    if tokens & {"beef", "bovine"}:
        return AnimalSource.BEEF
    if "cow" in tokens and tokens & {"flesh", "meat"}:
        return AnimalSource.BEEF
    return None


def _source_from_text(
    value: str,
    ambiguous_tokens: frozenset[str],
    *,
    include_nonmeat: bool,
) -> AnimalSource | None:
    """Classify explicit source terms without fuzzy or semantic inference."""

    tokens = frozenset(_name_tokens(value))
    if tokens & {"pork", "porcine", "swine"}:
        return AnimalSource.PORK
    if "pig" in tokens and tokens & {"belly", "fat", "meat", "shoulder"}:
        return AnimalSource.PORK
    if tokens & {"beef", "bovine"}:
        return AnimalSource.BEEF
    if "cow" in tokens and tokens & {"flesh", "meat"}:
        return AnimalSource.BEEF
    if "turkey" in tokens:
        return AnimalSource.TURKEY
    if "lamb" in tokens:
        return AnimalSource.LAMB
    if "mutton" in tokens:
        return AnimalSource.MUTTON
    if "goat" in tokens:
        if tokens & {"cheese", "milk", "yogurt"}:
            return AnimalSource.DAIRY if include_nonmeat else None
        return AnimalSource.GOAT
    if tokens & {"bison", "elk", "rabbit", "veal", "venison"}:
        return AnimalSource.OTHER_MAMMAL
    if tokens & ambiguous_tokens:
        return AnimalSource.UNKNOWN
    if "chicken" in tokens:
        return AnimalSource.CHICKEN
    if "tuna" in tokens:
        return AnimalSource.TUNA
    if tokens & {"shrimp", "prawn", "prawns"}:
        return AnimalSource.SHRIMP
    if tokens & {"seafood", "shellfish"}:
        return AnimalSource.SEAFOOD
    if tokens & {"fish", "salmon", "cod", "haddock", "tilapia", "trout"}:
        return AnimalSource.FISH
    if include_nonmeat and tokens & {"vegetarian", "vegan", "veggie", "plant"}:
        return AnimalSource.PLANT
    if include_nonmeat and tokens & {"egg", "eggs"}:
        return AnimalSource.EGG
    if include_nonmeat and tokens & {"cheese", "dairy", "milk", "yogurt"}:
        return AnimalSource.DAIRY
    return None


def _assessment(
    source: AnimalSource | None,
    evidence: AnimalSourceEvidence,
    detail: str,
    policy: DietaryPolicy,
) -> AnimalSourceAssessment:
    if source is AnimalSource.UNKNOWN:
        classification = DietaryClassification.UNKNOWN_MEAT_SOURCE
    elif source in policy.disallowed_animal_sources:
        classification = DietaryClassification.DISALLOWED_ANIMAL
    elif source in _ANIMAL_PROTEIN_SOURCES:
        if policy.allowed_animal_sources and source not in policy.allowed_animal_sources:
            classification = DietaryClassification.DISALLOWED_ANIMAL
        else:
            classification = DietaryClassification.SAFE_ALLOWED_ANIMAL
    else:
        classification = DietaryClassification.SAFE_NONMEAT
    return AnimalSourceAssessment(source, evidence, classification, detail)


_ANIMAL_PROTEIN_SOURCES = frozenset(
    {
        AnimalSource.BEEF,
        AnimalSource.PORK,
        AnimalSource.CHICKEN,
        AnimalSource.TURKEY,
        AnimalSource.FISH,
        AnimalSource.SEAFOOD,
        AnimalSource.SHRIMP,
        AnimalSource.TUNA,
        AnimalSource.LAMB,
        AnimalSource.MUTTON,
        AnimalSource.GOAT,
        AnimalSource.OTHER_MAMMAL,
    }
)


__all__ = [
    "AnimalSource",
    "AnimalSourceAssessment",
    "AnimalSourceEvidence",
    "AnimalSourceRule",
    "DietaryClassification",
    "DietaryPolicy",
    "assess_animal_source",
]
