"""Versioned food-quantity signals using FDA label Daily Values, not diagnoses."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

QUALITY_POLICY_VERSION = "nytr.fda-label-context.v1"
# Adult/age 4+ general labeling references, not personalized prescriptions.
DAILY_VALUES = {
    "saturated_fat_g": (Decimal("20"), "Saturated fat", "g"),
    "sodium_mg": (Decimal("2300"), "Sodium", "mg"),
    "fiber_g": (Decimal("28"), "Fiber", "g"),
    "added_sugars_g": (Decimal("50"), "Added sugar", "g"),
    "cholesterol_mg": (Decimal("300"), "Cholesterol", "mg"),
}


def assess_nutrition_quality(
    quantities: Mapping[str, str], *, confidence: str
) -> dict[str, object]:
    findings: list[dict[str, str]] = []
    missing: list[str] = []
    for key, (daily_value, label, unit) in DAILY_VALUES.items():
        raw = quantities.get(key)
        try:
            value = Decimal(raw) if raw is not None else None
        except InvalidOperation:
            value = None
        if value is None or not value.is_finite() or value < 0:
            missing.append(key)
            continue
        percent = value / daily_value * Decimal(100)
        band = "high" if percent >= 20 else "low" if percent <= 5 else "moderate"
        findings.append(
            {
                "nutrient": key,
                "label": label,
                "amount": str(value),
                "unit": unit,
                "daily_value_percent": str(percent.quantize(Decimal("0.1"))),
                "band": band,
                "message": f"{label}: {band} contribution to the daily reference.",
            }
        )
    return {
        "policy_version": QUALITY_POLICY_VERSION,
        "confidence": confidence,
        "scope": "selected_quantity",
        "findings": findings,
        "missing_nutrients": missing,
        "notice": (
            "FDA label references, not personal limits. Balance choices across the day; "
            "unlogged intake is unknown."
        ),
    }


def candidate_quality(candidate: object) -> dict[str, object] | None:
    if not isinstance(candidate, dict):
        return None
    totals = candidate.get("totals")
    if not isinstance(totals, dict) or not isinstance(totals.get("quantities"), dict):
        return None
    quantities = {
        key: value
        for key, value in totals["quantities"].items()
        if isinstance(key, str) and isinstance(value, str)
    }
    return assess_nutrition_quality(quantities, confidence=str(totals.get("confidence", "unknown")))
