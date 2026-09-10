"""Deterministic candidate generation: singles + unordered pairs of distinct foods.

M4 composes EXACTLY: one-offering singles, and each unordered {A, B} pair of
DISTINCT food_ids generated once via combinations, lines rendered in canonical
order. Same-food pairs and multi-serving lines do not exist in M4 (deferred).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from decimal import Decimal

from nutrition_agent.domain.nutrition.facts import NutritionFacts
from nutrition_agent.domain.nutrition.meal import ComposedMeal, MealLine, compose_meal
from nutrition_agent.domain.planning.menu_view import CandidateLineRef, OfferingView
from nutrition_agent.domain.stacks.entities import DietaryTag, NutritionProfile


@dataclass(frozen=True)
class Candidate:
    lines: tuple[CandidateLineRef, ...]
    meal: ComposedMeal
    dietary_tags: tuple[DietaryTag, ...]
    category_names: tuple[str, ...]

    @property
    def sort_key(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return (
            tuple(ref.name_normalized for ref in self.lines),
            tuple(ref.source_mid for ref in self.lines),
        )


def _canonical_ref(offering: OfferingView) -> CandidateLineRef:
    assert offering.profile is not None and offering.profile_sha256 is not None
    return CandidateLineRef(
        offering_id=offering.offering_id,
        food_id=offering.food_id,
        name_normalized=offering.name_normalized,
        source_mid=offering.source_mid,
        occurrence_ordinal=offering.occurrence_ordinal,
        category_name=offering.category_name,
        dietary_tags=offering.dietary_tags,
        profile_sha256=offering.profile_sha256,
        parser_version=offering.profile.provenance.parser_version,
        line=MealLine(
            food_id=offering.food_id,
            profile_content_sha256=offering.profile_sha256,
            parser_version=offering.profile.provenance.parser_version,
            servings=Decimal(1),
            serving_basis_raw=offering.profile.serving_basis_raw,
            serving_basis_kind=offering.profile.serving_basis_kind,
            facts=facts_from_profile(offering.profile),
        ),
    )


def facts_from_profile(profile: NutritionProfile) -> NutritionFacts:
    from nutrition_agent.domain.stacks.facts_bridge import facts_from_profile as _bridge

    return _bridge(profile)


def _ref_sort_key(ref: CandidateLineRef) -> tuple[str, str]:
    return (ref.name_normalized, str(ref.food_id))


def generate_candidates(
    eligible: tuple[OfferingView, ...], max_candidates: int
) -> tuple[Candidate, ...]:
    """Singles then unordered distinct-food pairs, canonical order, capped.

    The cap is a deterministic SAFETY CAP applied AFTER canonical generation: it
    bounds work and output size; it does NOT guarantee global optimality when
    truncation occurs.
    """
    ordered = sorted(eligible, key=lambda view: (view.name_normalized, view.source_mid))

    def _candidate(refs: tuple[CandidateLineRef, ...]) -> Candidate:
        tags = tuple(
            sorted({tag for ref in refs for tag in ref.dietary_tags}, key=lambda t: t.value)
        )
        categories = tuple(sorted({ref.category_name for ref in refs}))
        return Candidate(
            lines=refs,
            meal=compose_meal([ref.line for ref in refs]),
            dietary_tags=tags,
            category_names=categories,
        )

    candidates: list[Candidate] = []
    for offering in ordered:
        candidates.append(_candidate((_canonical_ref(offering),)))

    by_food: dict[str, OfferingView] = {}
    for offering in ordered:
        by_food.setdefault(str(offering.food_id), offering)
    unique_foods = sorted(
        by_food.values(), key=lambda view: (view.name_normalized, str(view.food_id))
    )
    for first, second in itertools.combinations(unique_foods, 2):
        pair = tuple(sorted((_canonical_ref(first), _canonical_ref(second)), key=_ref_sort_key))
        candidates.append(_candidate(pair))

    return tuple(candidates[:max_candidates])
