"""Property/invariant tests for Decimal-only facts arithmetic (stdlib only).

Exhaustive small-grid properties plus a fixed-seed fuzz for missing-value
propagation and derived-presence rules.
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Iterable
from decimal import Decimal

import pytest

from nutrition_agent.domain.nutrition.arithmetic import scale_facts, sum_facts
from nutrition_agent.domain.nutrition.facts import (
    NutrientPresence,
    NutritionFacts,
    is_strict_eligible,
    presence_of,
)
from nutrition_agent.domain.stacks.entities import Confidence, NutrientKey

GRID_VALUES: tuple[Decimal, ...] = tuple(Decimal(v) for v in ("0", "0.1", "1", "3.7"))
GRID_KEYS: tuple[NutrientKey, ...] = (
    NutrientKey.CALORIES_KCAL,
    NutrientKey.PROTEIN_G,
    NutrientKey.SODIUM_MG,
)
SEED = 20260821
FUZZ_ITERATIONS = 200


def _facts_from_values(
    values: dict[NutrientKey, Decimal],
    declared: Iterable[NutrientKey] = (),
    confidence: Confidence = Confidence.OFFICIAL_PUBLISHED,
) -> NutritionFacts:
    published = frozenset(key for key, value in values.items() if value == 0)
    return NutritionFacts(
        quantities=dict(values),
        published_zero=published,
        declared_unavailable=frozenset(declared),
        confidence=confidence,
    )


def _full_facts(
    values: dict[NutrientKey, Decimal], confidence: Confidence = Confidence.OFFICIAL_PUBLISHED
) -> NutritionFacts:
    """Facts over ALL keys (strict-eligible shape) with given values for GRID_KEYS."""
    full = {key: Decimal("1") for key in NutrientKey}
    full.update(values)
    published = frozenset(key for key, value in values.items() if value == 0)
    return NutritionFacts(
        quantities=full,
        published_zero=published,
        declared_unavailable=frozenset(),
        confidence=confidence,
    )


def _assert_equal_facts(left: NutritionFacts, right: NutritionFacts) -> None:
    assert left.quantities == right.quantities
    assert left.published_zero == right.published_zero
    assert left.declared_unavailable == right.declared_unavailable
    assert left.confidence == right.confidence


def _grid_facts() -> list[NutritionFacts]:
    results = []
    for combo in itertools.product(GRID_VALUES, repeat=2):
        values = dict(zip(GRID_KEYS[:2], combo, strict=True))
        results.append(_facts_from_values(values))
    return results


def test_sum_commutative_exhaustive_grid() -> None:
    grid = _grid_facts()
    for a in grid:
        for b in grid:
            assert sum_facts([a, b]).quantities == sum_facts([b, a]).quantities


def test_sum_associative_exhaustive_grid() -> None:
    grid = _grid_facts()
    pick = grid[:6]
    for a in pick:
        for b in pick:
            for c in pick:
                left = sum_facts([sum_facts([a, b]), c]).quantities
                right = sum_facts([a, sum_facts([b, c])]).quantities
                assert left == right


def test_sum_identity_and_single_part() -> None:
    facts = _facts_from_values({NutrientKey.CALORIES_KCAL: Decimal("3.7")})
    assert sum_facts([facts]).quantities == facts.quantities


def test_scale_by_one_identity() -> None:
    facts = _facts_from_values({NutrientKey.PROTEIN_G: Decimal("69.2")})
    scaled = scale_facts(facts, Decimal(1))
    _assert_equal_facts(scaled, facts)


def test_distributivity_exhaustive_grid() -> None:
    k = Decimal("2.5")
    grid = _grid_facts()
    for a in grid[:8]:
        for b in grid[:8]:
            left = scale_facts(sum_facts([a, b]), k).quantities
            right = sum_facts([scale_facts(a, k), scale_facts(b, k)]).quantities
            assert left == right


def test_sum_facts_requires_parts() -> None:
    import pytest

    with pytest.raises(ValueError, match="at least one part"):
        sum_facts([])


def test_missing_value_propagation_seeded_fuzz() -> None:
    rng = random.Random(SEED)
    for _ in range(FUZZ_ITERATIONS):
        parts: list[NutritionFacts] = []
        for _part_index in range(rng.randint(1, 3)):
            values = {
                key: Decimal(rng.choice(["0", "0.5", "2", "10"]))
                for key in rng.sample(list(GRID_KEYS), rng.randint(0, 3))
            }
            declared = [key for key in GRID_KEYS if key not in values and rng.random() < 0.3]
            parts.append(_facts_from_values(values, declared=declared))
        result = sum_facts(parts)
        # derived facts never carry published_zero
        assert result.published_zero == frozenset()
        for key in GRID_KEYS:
            computable_in_all = all(key in part.quantities for part in parts)
            if computable_in_all:
                expected = sum((part.quantities[key] for part in parts), Decimal(0))
                assert result.quantities[key] == expected
                assert key not in result.declared_unavailable
            else:
                assert key not in result.quantities, "missing input must not be zero-filled"
                any_declared = any(key in part.declared_unavailable for part in parts)
                if any_declared:
                    assert key in result.declared_unavailable
                else:
                    assert key not in result.declared_unavailable


def test_published_zero_inputs_participate_as_plain_zero_addends() -> None:
    zero = _facts_from_values({NutrientKey.CALORIES_KCAL: Decimal(0)})
    positive = _facts_from_values({NutrientKey.CALORIES_KCAL: Decimal("12.5")})
    result = sum_facts([zero, positive])
    assert result.quantities[NutrientKey.CALORIES_KCAL] == Decimal("12.5")
    assert result.published_zero == frozenset()
    assert presence_of(result, NutrientKey.CALORIES_KCAL) is NutrientPresence.KNOWN_VALUE


def test_sum_of_all_zero_inputs_is_known_zero_not_published() -> None:
    a = _facts_from_values({NutrientKey.CALORIES_KCAL: Decimal(0)})
    b = _facts_from_values({NutrientKey.CALORIES_KCAL: Decimal(0)})
    result = sum_facts([a, b])
    assert result.quantities[NutrientKey.CALORIES_KCAL] == Decimal("0")
    assert result.published_zero == frozenset()
    assert presence_of(result, NutrientKey.CALORIES_KCAL) is NutrientPresence.KNOWN_VALUE


def test_scale_rejects_nonpositive_servings() -> None:
    facts = _facts_from_values({NutrientKey.CALORIES_KCAL: Decimal("100")})
    with pytest.raises(ValueError, match="servings must be positive"):
        scale_facts(facts, Decimal(0))
    with pytest.raises(ValueError, match="servings must be positive"):
        scale_facts(facts, Decimal("-1"))


def test_scale_accepts_positive_decimal_servings() -> None:
    facts = _facts_from_values({NutrientKey.SODIUM_MG: Decimal("3.3")})
    scaled = scale_facts(facts, Decimal("0.5"))
    assert scaled.quantities[NutrientKey.SODIUM_MG] == Decimal("1.65")


def test_scale_preserves_decimal_type_and_scales_values() -> None:
    facts = _facts_from_values({NutrientKey.SODIUM_MG: Decimal("3.3")})
    scaled = scale_facts(facts, Decimal("2"))
    assert scaled.quantities[NutrientKey.SODIUM_MG] == Decimal("6.6")
    assert isinstance(scaled.quantities[NutrientKey.SODIUM_MG], Decimal)


def test_declared_unavailable_survives_scaling() -> None:
    facts = NutritionFacts(
        quantities={NutrientKey.CALORIES_KCAL: Decimal(100)},
        published_zero=frozenset(),
        declared_unavailable=frozenset({NutrientKey.FIBER_G}),
        confidence=Confidence.OFFICIAL_PUBLISHED,
    )
    scaled = scale_facts(facts, Decimal(2))
    assert NutrientKey.FIBER_G in scaled.declared_unavailable
    assert scaled.published_zero == frozenset()


def test_strict_eligibility_shape() -> None:
    strict = _full_facts({NutrientKey.CALORIES_KCAL: Decimal("500")})
    assert is_strict_eligible(strict)

    partial_conf = _full_facts({}, confidence=Confidence.PARTIAL)
    assert not is_strict_eligible(partial_conf)

    estimated_conf = _full_facts({}, confidence=Confidence.ESTIMATED)
    assert not is_strict_eligible(estimated_conf)

    incomplete = NutritionFacts(
        quantities={NutrientKey.CALORIES_KCAL: Decimal(100)},
        published_zero=frozenset(),
        declared_unavailable=frozenset(),
        confidence=Confidence.OFFICIAL_PUBLISHED,
    )
    assert not is_strict_eligible(incomplete)

    with_gap = NutritionFacts(
        quantities={key: Decimal(1) for key in NutrientKey if key is not NutrientKey.IRON_MG},
        published_zero=frozenset(),
        declared_unavailable=frozenset(),
        confidence=Confidence.OFFICIAL_PUBLISHED,
    )
    assert not is_strict_eligible(with_gap)
