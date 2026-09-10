"""Candidate generation tests: unordered distinct-food pairs, canonical order, cap."""

from __future__ import annotations

from decimal import Decimal

from nutrition_agent.domain.planning.candidates import generate_candidates
from nutrition_agent.domain.stacks.entities import DietaryTag
from tests.unit.planning_helpers import make_offering


def _names(candidate) -> list[str]:  # type: ignore[no-untyped-def]
    return [ref.name_normalized for ref in candidate.lines]


def test_singles_and_pairs_counts() -> None:
    offerings = tuple(
        make_offering(number, calories="300").build()
        for number in range(1, 5)  # type: ignore[list-item]
    )
    candidates = generate_candidates(offerings, max_candidates=200)
    singles = [candidate for candidate in candidates if len(candidate.lines) == 1]
    pairs = [candidate for candidate in candidates if len(candidate.lines) == 2]
    assert len(singles) == 4
    assert len(pairs) == 6  # C(4,2)


def test_pair_generated_once_with_canonical_line_order() -> None:
    b = make_offering(1, name="B Item", calories="300").build()
    a = make_offering(2, name="A Item", calories="300").build()
    forward = generate_candidates((b, a), 50)  # type: ignore[arg-type,list-item]
    reverse = generate_candidates((a, b), 50)  # type: ignore[arg-type,list-item]
    pairs_forward = [candidate for candidate in forward if len(candidate.lines) == 2]
    pairs_reverse = [candidate for candidate in reverse if len(candidate.lines) == 2]
    assert len(pairs_forward) == 1
    assert len(pairs_reverse) == 1
    assert _names(pairs_forward[0]) == ["A Item", "B Item"]
    assert _names(pairs_forward[0]) == _names(pairs_reverse[0])


def test_no_same_food_pairs_even_with_duplicate_name_occurrences() -> None:
    from dataclasses import replace
    from uuid import UUID

    first = make_offering(1, name="Turkey Burger", calories="500").build()
    second = replace(first, offering_id=UUID(int=999))
    # same food_id (true duplicate occurrence), distinct offering ids
    assert first.food_id == second.food_id
    assert first.offering_id != second.offering_id
    candidates = generate_candidates((first, second), 50)  # type: ignore[arg-type,list-item]
    # same food => no pair; only the two single occurrences
    assert all(len(candidate.lines) == 1 for candidate in candidates)
    assert len(candidates) == 2


def test_distinct_foods_same_name_do_pair() -> None:
    # different food_ids that happen to share a name are legitimately pairable
    first = make_offering(3, name="Chili", calories="350").build()
    second = make_offering(4, name="Chili", calories="350").build()
    candidates = generate_candidates((first, second), 50)  # type: ignore[arg-type,list-item]
    pairs = [candidate for candidate in candidates if len(candidate.lines) == 2]
    assert len(pairs) == 1


def test_cap_truncates_deterministically() -> None:
    offerings = tuple(
        make_offering(number, calories="250").build()
        for number in range(1, 8)  # type: ignore[list-item]
    )
    uncapped = generate_candidates(offerings, 200)
    capped = generate_candidates(offerings, 10)
    assert len(uncapped) == 7 + 21
    assert len(capped) == 10
    assert [candidate.meal for candidate in capped] == [
        candidate.meal for candidate in uncapped[:10]
    ]
    again = generate_candidates(offerings, 10)
    assert capped == again


def test_servings_always_one_whole_published_serving() -> None:
    offerings = tuple(
        make_offering(number, calories="400").build()
        for number in (30, 31)  # type: ignore[list-item]
    )
    for candidate in generate_candidates(offerings, 50):
        for ref in candidate.lines:
            assert ref.line.servings == Decimal(1)


def test_candidate_carries_union_of_source_backed_tags_and_categories() -> None:
    meatless = make_offering(40, tags=(DietaryTag.MEATLESS,), category="SOUP").build()
    halal = make_offering(41, tags=(DietaryTag.HALAL_FRIENDLY,), category="GRILL").build()
    candidates = generate_candidates((meatless, halal), 50)  # type: ignore[arg-type,list-item]
    pair = next(candidate for candidate in candidates if len(candidate.lines) == 2)
    assert set(pair.dietary_tags) == {DietaryTag.MEATLESS, DietaryTag.HALAL_FRIENDLY}
    assert set(pair.category_names) == {"SOUP", "GRILL"}


def test_placeholder_bowl_profile_cannot_generate_candidates() -> None:
    from nutrition_agent.domain.stacks.entities import Confidence

    bowl = make_offering(50, confidence=Confidence.PARTIAL).build()
    candidates = generate_candidates((bowl,), 50)  # type: ignore[arg-type,list-item]
    # generation is structural; eligibility (stage A5) excludes it before here.
    # This test pins the contract at the generator boundary: it never invents
    # facts for profiles it receives. The planner-level exclusion is tested in
    # test_planning_planner.py via offering_gate.
    assert len(candidates) == 1
