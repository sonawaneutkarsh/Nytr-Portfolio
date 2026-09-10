"""Frozen plan-artifact to consumed nutrition evidence projection tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from nutrition_agent.db.sql_repos import SqlConsumptionRepository
from nutrition_agent.domain.nutrition.ledger import NutritionAuthority


def _row(candidate: dict[str, object]) -> tuple[object, ...]:
    return (
        UUID(int=1),
        datetime(2026, 9, 4, 16, tzinfo=UTC),
        UUID(int=2),
        UUID(int=3),
        UUID(int=4),
        0,
        "post_workout_lunch",
        1,
        "candidate-1",
        {"artifact_kind": "daily_plan", "slots": [{"candidates": [candidate]}]},
    )


def test_strict_projection_uses_frozen_plan_totals_and_names() -> None:
    candidate: dict[str, object] = {
        "candidate_id": "candidate-1",
        "lines": [
            {"name_normalized": "Chicken"},
            {"name_normalized": "Rice"},
        ],
        "totals": {
            "confidence": "official_component_sum",
            "declared_unavailable": [],
            "quantities": {"calories_kcal": "700.50", "protein_g": "45.25"},
        },
    }

    result = SqlConsumptionRepository._ledger_evidence_from_row(_row(candidate))

    assert result.item_name == "Chicken + Rice"
    assert result.authority is NutritionAuthority.OFFICIAL
    assert result.calories_kcal == Decimal("700.50")
    assert result.protein_g == Decimal("45.25")
    assert result.plan_item_id == UUID(int=4)


def test_configurable_projection_preserves_partial_estimate_and_configuration() -> None:
    candidate: dict[str, object] = {
        "candidate_id": "candidate-1",
        "candidate_kind": "configurable_estimate",
        "lines": [],
        "configurable_estimate": {
            "definition": {
                "display_name": "CYO Halal Bowl",
                "configuration_summary": "rice, chicken, quinoa, eggs; no sauce",
                "estimate": {
                    "state": "partial_estimate",
                    "unknown_nutrients": ["sodium_mg"],
                },
            }
        },
        "totals": {
            "confidence": "estimated",
            "declared_unavailable": [],
            "quantities": {
                "calories_kcal": "604.27781682500",
                "protein_g": "45.7386516982500",
            },
        },
    }

    result = SqlConsumptionRepository._ledger_evidence_from_row(_row(candidate))

    assert result.item_name == "CYO Halal Bowl"
    assert result.configuration_summary == "rice, chicken, quinoa, eggs; no sauce"
    assert result.authority is NutritionAuthority.PARTIAL
    assert result.calories_kcal == Decimal("604.27781682500")
    assert "sodium_mg" in result.unknown_nutrients
    assert "Stacks official" not in result.provenance_summary


def test_artifact_identity_mismatch_and_non_string_quantity_fail_closed() -> None:
    mismatched: dict[str, object] = {
        "candidate_id": "other",
        "lines": [{"name_normalized": "Chicken"}],
        "totals": {
            "confidence": "official_published",
            "declared_unavailable": [],
            "quantities": {"calories_kcal": "500", "protein_g": "30"},
        },
    }
    with pytest.raises(ValueError, match="does not match"):
        SqlConsumptionRepository._ledger_evidence_from_row(_row(mismatched))

    bad_quantity = dict(mismatched)
    bad_quantity["candidate_id"] = "candidate-1"
    bad_quantity["totals"] = {
        "confidence": "official_published",
        "declared_unavailable": [],
        "quantities": {"calories_kcal": 500.0, "protein_g": "30"},
    }
    with pytest.raises(ValueError, match="decimal strings"):
        SqlConsumptionRepository._ledger_evidence_from_row(_row(bad_quantity))


def test_unknown_candidate_kind_and_estimate_state_fail_closed() -> None:
    unknown_kind: dict[str, object] = {
        "candidate_id": "candidate-1",
        "candidate_kind": "future_kind",
        "lines": [{"name_normalized": "Chicken"}],
        "totals": {
            "confidence": "official_published",
            "declared_unavailable": [],
            "quantities": {"calories_kcal": "500", "protein_g": "30"},
        },
    }
    with pytest.raises(ValueError, match="candidate kind"):
        SqlConsumptionRepository._ledger_evidence_from_row(_row(unknown_kind))

    unknown_state: dict[str, object] = {
        "candidate_id": "candidate-1",
        "candidate_kind": "configurable_estimate",
        "lines": [],
        "configurable_estimate": {
            "definition": {
                "display_name": "CYO Halal Bowl",
                "configuration_summary": "rice, chicken; no sauce",
                "estimate": {
                    "state": "future_state",
                    "unknown_nutrients": [],
                },
            }
        },
        "totals": {
            "confidence": "estimated",
            "declared_unavailable": [],
            "quantities": {"calories_kcal": "600", "protein_g": "40"},
        },
    }
    with pytest.raises(ValueError, match="estimate state"):
        SqlConsumptionRepository._ledger_evidence_from_row(_row(unknown_state))
