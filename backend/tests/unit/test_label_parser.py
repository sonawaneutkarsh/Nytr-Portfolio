from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from nutrition_agent.domain.stacks.entities import NutritionSourceState
from nutrition_agent.infrastructure.stacks_source.label_parser import LabelPageParser

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "stacks"


def parse(filename: str):
    html = (FIXTURES / filename).read_text(encoding="utf-8")
    return LabelPageParser().parse(html)


def test_standard_label_matches_expected_contract() -> None:
    result = parse("nutrition_label_standard_roast_chicken.html")
    assert result.ok and result.value is not None
    label = result.value
    expected = json.loads(
        (FIXTURES / "expected" / "nutrition_label_roast_chicken.expected.json").read_text()
    )

    assert label.invalid_mid_sentinel is False
    assert label.placeholder is False
    assert label.source_state is NutritionSourceState.PROFILE_AVAILABLE
    assert label.item_name == "Demo Protein Plate"
    assert label.serving_basis_raw == "1 SERVG"
    assert label.calories_text == "600"
    assert label.allergens == tuple(expected["allergens"])
    assert label.ingredients_raw == expected["ingredients_raw"]

    import re

    amount_re = re.compile(r"^(\d+(?:\.\d+)?)(g|mg|mcg)$")
    by_key = {row.key.value if row.key else row.raw_label: row for row in label.rows}
    nutrients = expected["nutrients"]
    for key, want in nutrients.items():
        if key == "calories_kcal":
            continue  # carried by label.calories_text, asserted above
        row = by_key.get(key)
        assert row is not None, f"missing nutrient row {key}"
        match = amount_re.match(row.amount_text) if row.amount_text else None
        got = Decimal(match.group(1)) if match else None
        want_dec = Decimal(str(want)) if want is not None else None
        assert got == want_dec, f"{key}: {got} != {want_dec}"

    # dash-rendered %DVs are null, printed %DVs survive
    fat_row = by_key["total_fat_g"]
    assert fat_row.dv_percent == Decimal("26")
    trans_row = by_key["trans_fat_g"]
    assert trans_row.amount_text == "0g"  # real published zero, kept
    assert trans_row.dv_percent is None


def test_current_card_label_matches_captured_contract() -> None:
    result = parse("nutrition_label_current_card_2026-08-27.html")

    assert result.ok and result.value is not None
    label = result.value
    assert label.item_name == "Sample Sandwich"
    assert label.serving_basis_raw == "1 EACH"
    assert label.calories_text == "500"
    assert label.placeholder is False
    assert label.source_state is NutritionSourceState.PROFILE_AVAILABLE
    assert label.allergens == ("Wheat", "Soy")

    from nutrition_agent.domain.stacks.entities import NutrientKey

    by_key = {row.key: row for row in label.rows}
    assert len(label.rows) == 14
    assert by_key[NutrientKey.TOTAL_FAT_G].amount_text == "18g"
    assert by_key[NutrientKey.SODIUM_MG].amount_text == "700mg"
    assert by_key[NutrientKey.PROTEIN_G].amount_text == "30g"
    assert by_key[NutrientKey.ADDED_SUGARS_G].amount_text == "1g"
    assert by_key[NutrientKey.TRANS_FAT_G].amount_text == "0g"
    assert by_key[NutrientKey.TRANS_FAT_G].dv_percent is None


def test_current_card_dash_amount_is_unavailable_not_zero() -> None:
    html = (FIXTURES / "nutrition_label_current_card_2026-08-27.html").read_text()
    result = LabelPageParser().parse(
        html.replace('<div class="fact-amount">30g</div>', '<div class="fact-amount">—</div>')
    )

    assert result.ok and result.value is not None
    from nutrition_agent.domain.stacks.entities import NutrientKey

    by_key = {row.key: row for row in result.value.rows}
    assert by_key[NutrientKey.PROTEIN_G].amount_text is None


def _minimal_current_card(
    *rows: tuple[str, str, str],
    calories: str | None,
    ingredients: str | None = None,
) -> str:
    calories_html = (
        f'<span class="summary-value"><strong>Calories:</strong> {calories}</span>'
        if calories is not None
        else ""
    )
    rows_html = "".join(
        '<div class="fact-row">'
        f'<div class="fact-name">{name}</div>'
        f'<div class="fact-amount">{amount}</div>'
        f'<div class="fact-dv">{dv}</div>'
        "</div>"
        for name, amount, dv in rows
    )
    ingredients_html = (
        f"<h2>Ingredients</h2><p>{ingredients}</p>" if ingredients is not None else ""
    )
    return f"""
    <main>
      <h1 class="recipe-title">Retained Current Card Shape</h1>
      <section class="nutrition-facts-card">
        <div class="nutrition-summary">
          <span class="summary-value"><strong>Serving Size</strong> 1 SERVG</span>
          {calories_html}
        </div>
        {rows_html}
      </section>
      {ingredients_html}
    </main>
    """


def test_current_card_blank_amount_with_dash_dv_is_unavailable() -> None:
    result = LabelPageParser().parse(
        _minimal_current_card(
            ("Total Fat", "4.2g", "6%"),
            ("Vitamin D", "", "—"),
            ("Protein", "5g", "—"),
            calories="63",
            ingredients="Retained fixture ingredients",
        )
    )

    assert result.ok and result.value is not None
    from nutrition_agent.domain.stacks.entities import NutrientKey

    by_key = {row.key: row for row in result.value.rows}
    assert by_key[NutrientKey.TOTAL_FAT_G].amount_text == "4.2g"
    assert by_key[NutrientKey.VITAMIN_D_MCG].amount_text is None
    assert by_key[NutrientKey.VITAMIN_D_MCG].dv_percent is None
    assert by_key[NutrientKey.PROTEIN_G].amount_text == "5g"


def test_current_card_blank_dash_reaches_placeholder_detection() -> None:
    result = LabelPageParser().parse(
        _minimal_current_card(
            ("Total Fat", "0g", "0%"),
            ("Added Sugars", "", "—"),
            calories="0",
            ingredients="PLACE HOLDER",
        )
    )

    assert result.ok and result.value is not None
    assert result.value.placeholder is True
    added_sugars = next(row for row in result.value.rows if row.raw_label == "Added Sugars")
    assert added_sugars.amount_text is None


def test_current_card_with_only_blank_dash_rows_still_fails_closed() -> None:
    result = LabelPageParser().parse(
        _minimal_current_card(
            ("Total Fat", "", "—"),
            ("Protein", "", "—"),
            calories=None,
        )
    )

    assert not result.ok
    assert result.failure is not None
    assert result.failure.code == "NUTRITION_MALFORMED"
    assert "no authoritative nutrient values" in result.failure.detail


def test_complete_current_card_with_all_values_unavailable_is_source_incomplete() -> None:
    expected_rows = (
        "Total Fat",
        "Saturated Fat",
        "Trans Fat",
        "Cholesterol",
        "Sodium",
        "Vitamin D",
        "Calcium",
        "Total Carbohydrate",
        "Dietary Fiber",
        "Sugars",
        "Added Sugars",
        "Protein",
        "Iron",
        "Potassium",
    )
    result = LabelPageParser().parse(
        _minimal_current_card(
            *((name, "", "—") for name in expected_rows),
            calories=None,
        )
    )

    assert result.ok and result.value is not None
    assert result.value.source_state is NutritionSourceState.SOURCE_INCOMPLETE
    assert len(result.value.rows) == 14
    assert all(row.amount_text is None and row.dv_percent is None for row in result.value.rows)


def test_current_card_blank_amount_with_populated_dv_still_fails_closed() -> None:
    result = LabelPageParser().parse(
        _minimal_current_card(
            ("Total Fat", "", "5%"),
            calories="63",
        )
    )

    assert not result.ok
    assert result.failure is not None
    assert result.failure.code == "NUTRITION_MALFORMED"
    assert "unparsable amount ''" in result.failure.detail


def test_current_card_label_without_nutrient_rows_fails_closed() -> None:
    html = """
    <main class="nutrition-page">
      <h1 class="recipe-title">Apparently Valid Label</h1>
      <section class="nutrition-facts-card">
        <h2 class="nutrition-facts-title">Nutrition Facts</h2>
        <div class="nutrition-summary">
          <span class="summary-value"><strong>Serving Size</strong> 1 EACH</span>
          <span class="summary-value"><strong>Calories:</strong> 100</span>
        </div>
      </section>
    </main>
    """

    result = LabelPageParser().parse(html)

    assert not result.ok
    assert result.failure is not None
    assert result.failure.code == "NUTRITION_MALFORMED"
    assert "no recognized nutrient rows" in result.failure.detail


def test_current_card_label_with_incomplete_fact_row_fails_closed() -> None:
    html = """
    <section class="nutrition-facts-card">
      <h2>Nutrition Facts</h2>
      <div class="fact-row">
        <div class="fact-name">Total Fat</div>
        <div class="fact-amount">1g</div>
      </div>
    </section>
    """

    result = LabelPageParser().parse(html)

    assert not result.ok
    assert result.failure is not None
    assert "missing name, amount, or daily value" in result.failure.detail


def test_placeholder_label_detected_and_zeroes_flagged() -> None:
    result = parse("nutrition_label_placeholder_halal_bowl.html")
    assert result.ok and result.value is not None
    label = result.value
    assert label.placeholder is True
    assert label.source_state is NutritionSourceState.SOURCE_PLACEHOLDER
    assert label.ingredients_raw == "PLACE HOLDER"
    assert label.calories_text == "0"

    from nutrition_agent.domain.stacks.entities import NutrientKey

    by_key = {row.key: row for row in label.rows}
    added = by_key[NutrientKey.ADDED_SUGARS_G]
    assert added.amount_text is None  # blank amount -> unavailable, never zero


def test_invalid_mid_sentinel_detected() -> None:
    sentinel_html = (
        "<html><body>Nutrition information is not available for the selected item.</body></html>"
    )
    result = LabelPageParser().parse(sentinel_html)
    assert result.ok and result.value is not None
    assert result.value.invalid_mid_sentinel is True
    assert result.value.source_state is NutritionSourceState.SOURCE_UNAVAILABLE


def test_foodpro_component_label_parses() -> None:
    result = parse("foodpro_label_component_shawarma.html")
    assert result.ok and result.value is not None
    label = result.value
    assert label.item_name == "Demo protein"
    assert label.serving_basis_raw == "1 SERVG"
    assert label.calories_text == "220"
    assert label.allergens == ("None declared.",)

    from nutrition_agent.domain.stacks.entities import NutrientKey

    by_key = {row.key: row for row in label.rows}
    protein = by_key[NutrientKey.PROTEIN_G]
    assert protein.amount_text == "30g"
    calcium = by_key[NutrientKey.CALCIUM_MG]
    assert calcium.amount_text is None  # %DV-only row on FoodPro layout
    assert calcium.dv_percent == Decimal("2")
    carbs = by_key[NutrientKey.CARBOHYDRATE_G]  # 'Tot. Carb.' alias, second column pair
    assert carbs.amount_text == "2g"


def test_malformed_amount_fails_closed() -> None:
    html = (FIXTURES / "nutrition_label_standard_roast_chicken.html").read_text(encoding="utf-8")
    mutated = html.replace(
        '<div class="fact-amount">20g</div>', '<div class="fact-amount">lots</div>'
    )
    result = LabelPageParser().parse(mutated)
    assert not result.ok
    assert result.failure is not None
    assert result.failure.code == "NUTRITION_MALFORMED"
    assert "84g" in result.failure.detail or "lots" in result.failure.detail


@pytest.mark.parametrize(
    ("fixture", "state", "unavailable_count"),
    (
        (
            "retained_2026-08-27_assorted_scones_biscotti.html",
            NutritionSourceState.SOURCE_PLACEHOLDER,
            0,
        ),
        (
            "retained_2026-08-27_cyo_burger.html",
            NutritionSourceState.SOURCE_PLACEHOLDER,
            0,
        ),
        (
            "retained_2026-08-27_cyo_halal_bowl.html",
            NutritionSourceState.SOURCE_PLACEHOLDER,
            0,
        ),
        (
            "retained_2026-08-27_cyo_chicken_sandwich.html",
            NutritionSourceState.SOURCE_INCOMPLETE,
            14,
        ),
        (
            "retained_demo_incomplete_b.html",
            NutritionSourceState.SOURCE_INCOMPLETE,
            14,
        ),
        (
            "retained_2026-08-27_turkey_bacon.html",
            NutritionSourceState.PROFILE_AVAILABLE,
            0,
        ),
        (
            "retained_2026-08-27_turkey_burger.html",
            NutritionSourceState.PROFILE_AVAILABLE,
            0,
        ),
    ),
)
def test_retained_live_label_shapes_have_exact_source_classification(
    fixture: str,
    state: NutritionSourceState,
    unavailable_count: int,
) -> None:
    result = parse(fixture)

    assert result.ok and result.value is not None
    assert result.value.source_state is state
    assert sum(row.amount_text is None for row in result.value.rows) == unavailable_count
    if state is NutritionSourceState.PROFILE_AVAILABLE:
        assert result.value.calories_text not in {None, "0"}
        assert result.value.placeholder is False
    elif state is NutritionSourceState.SOURCE_PLACEHOLDER:
        assert result.value.placeholder is True
        assert result.value.calories_text == "0"
    else:
        assert result.value.calories_text is None
        assert result.value.placeholder is False
