from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from nutrition_agent.domain.stacks.entities import (
    Confidence,
    MealPeriod,
    NormalizedOfferingInput,
    NutrientKey,
    Provenance,
    ServingBasisKind,
)
from nutrition_agent.infrastructure.stacks_source.label_parser import LabelPageParser
from nutrition_agent.infrastructure.stacks_source.normalizer import (
    assign_occurrence_ordinals,
    normalize_name,
    profile_from_parsed,
    split_ingredient_components,
)

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "stacks"


def test_name_normalization_handles_entities_and_whitespace() -> None:
    assert normalize_name("Pepperoni  Pizza Slice") == "Pepperoni Pizza Slice"
    assert normalize_name("4 Cheese Macaroni &amp; Cheese") == "4 Cheese Macaroni & Cheese"
    assert normalize_name("  #7 Roast Chicken & Provolone ") == "#7 Roast Chicken & Provolone"


def _offering(name: str, cat: int, item: int) -> NormalizedOfferingInput:
    return NormalizedOfferingInput(
        service_date=date(2026, 8, 21),
        meal_period=MealPeriod.LUNCH,
        campus_id=50,
        name_raw=name,
        name_normalized=name,
        source_mid=str(item),
        dietary_tags=(),
        category_name="X",
        category_position=cat,
        item_position=item,
    )


def test_occurrence_ordinals_increment_per_duplicate() -> None:
    inputs = (_offering("A", 1, 1), _offering("B", 1, 2), _offering("A", 2, 1))
    ordinals = assign_occurrence_ordinals(inputs)
    assert [ordinals[0], ordinals[1], ordinals[2]] == [0, 0, 1]


def test_ingredient_component_split_handles_nesting() -> None:
    raw = "Shredded Lettuce (Lettuce), Sliced Tomatoes (Tomatoes), Olive Oil"
    parts = split_ingredient_components(raw)
    assert parts is not None
    assert [c.component_name for c in parts] == [
        "Shredded Lettuce",
        "Sliced Tomatoes",
        "Olive Oil",
    ]
    nested = (
        "Chicken Breast (Chicken (chicken breast, broth, water, salt.)), "
        "Hoagie Roll (water, wheat flour)"
    )
    parts2 = split_ingredient_components(nested)
    assert parts2 is not None and len(parts2) == 2


def _provenance() -> Provenance:
    return Provenance(
        snapshot_id=uuid4(),
        content_sha256="deadbeef",
        source_url="fixture://label",
        parser_version="test",
        fetched_at=datetime.now(UTC),
    )


def test_profile_from_standard_label_has_full_provenance_and_confidence() -> None:
    html = (FIXTURES / "nutrition_label_standard_roast_chicken.html").read_text(encoding="utf-8")
    parsed = LabelPageParser().parse(html)
    assert parsed.ok and parsed.value

    profile = profile_from_parsed(parsed.value, UUID(int=1), _provenance())
    assert profile.confidence == Confidence.OFFICIAL_PUBLISHED
    assert profile.serving_basis_raw == "1 SERVG"
    assert profile.serving_basis_kind == ServingBasisKind.UNITLESS_SERVINGS
    assert profile.nutrients[NutrientKey.CALORIES_KCAL].value == Decimal(600)
    assert profile.nutrients[NutrientKey.TRANS_FAT_G].value == Decimal(0)
    assert profile.unavailable_fields == ()
    assert profile.allergens == ()
    assert profile.ingredient_components is not None
    assert len(profile.ingredient_components) >= 3
    assert profile.provenance.content_sha256 == "deadbeef"


def test_placeholder_label_never_becomes_a_profile() -> None:
    html = (FIXTURES / "nutrition_label_placeholder_halal_bowl.html").read_text(encoding="utf-8")
    parsed = LabelPageParser().parse(html)
    assert parsed.ok and parsed.value
    assert parsed.value.placeholder is True
