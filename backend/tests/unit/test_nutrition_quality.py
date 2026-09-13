from nutrition_agent.domain.nutrition.quality import assess_nutrition_quality


def test_daily_value_boundaries_unknowns_and_estimate_label() -> None:
    result = assess_nutrition_quality(
        {"sodium_mg": "460", "fiber_g": "1.4", "saturated_fat_g": "3"}, confidence="estimated"
    )
    assert result["confidence"] == "estimated"
    findings = {f["nutrient"]: f for f in result["findings"]}
    assert findings["sodium_mg"]["band"] == "high"
    assert findings["fiber_g"]["band"] == "low"
    assert findings["saturated_fat_g"]["band"] == "moderate"
    assert "added_sugars_g" in result["missing_nutrients"]
    assert "cholesterol_mg" not in findings


def test_missing_invalid_and_zero_are_distinct() -> None:
    result = assess_nutrition_quality(
        {"sodium_mg": "0", "fiber_g": "NaN", "cholesterol_mg": "-1"},
        confidence="official_published",
    )
    assert len(result["findings"]) == 1
    assert result["findings"][0]["amount"] == "0"
    assert "fiber_g" in result["missing_nutrients"]
