from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.barcode_foods import (
    ImportBarcodeFoodUseCase,
    LookupBarcodeFoodUseCase,
)
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.application.manual_foods import (
    CreateCustomFoodUseCase,
    RecordManualFoodUseCase,
)
from nutrition_agent.application.ports import DuplicateManualFoodError
from nutrition_agent.db.in_memory_repos import (
    InMemoryCustomFoodRepository,
    InMemoryHealthBodyMassRepository,
)
from nutrition_agent.domain.nutrition.barcodes import (
    MASS_BASIS_REFUSAL,
    VOLUME_BASIS_REFUSAL,
    BarcodeBasisReason,
    BarcodeProductChanged,
    BarcodeProductIncomplete,
    BarcodeProductNotFound,
    BarcodeProviderUnavailable,
    BarcodeServingEvidenceRefused,
    OwnerServingEvidence,
    apply_owner_serving,
)
from nutrition_agent.domain.nutrition.custom_foods import (
    CustomFoodAuthority,
    CustomFoodVersion,
    ManualMealPeriod,
)
from nutrition_agent.infrastructure.open_food_facts import OpenFoodFactsProvider


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 9, 12, tzinfo=UTC)


class Ids:
    value = 1

    def new_id(self) -> UUID:
        result = UUID(int=self.value)
        self.value += 1
        return result


def _provider(payload: dict[str, object], status: int = 200) -> OpenFoodFactsProvider:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=payload))
    return OpenFoodFactsProvider(
        "Nytr/1.0 (owner@example.com)",
        client=httpx.Client(transport=transport),
        clock=Clock().now,
        min_interval_seconds=0,
    )


def _nutrition(
    per: str,
    values: dict[str, tuple[object, str]],
    *,
    source: str = "packaging",
    preparation: str = "as_sold",
) -> dict[str, object]:
    return {
        "input_sets": [
            {
                "source": source,
                "preparation": preparation,
                "per": per,
                "nutrients": {
                    name: {"value": value, "unit": unit} for name, (value, unit) in values.items()
                },
            }
        ]
    }


def test_exact_per_serving_parse_converts_only_source_values() -> None:
    product = _provider(
        {
            "product": {
                "code": "012345678905",
                "product_name": "Plain Yogurt",
                "brands": "Example Dairy",
                "serving_size": "170 g",
                "nutrition": _nutrition(
                    "serving",
                    {
                        "energy-kcal": (100, "kcal"),
                        "proteins": (17, "g"),
                        "carbohydrates": (6, "g"),
                        "fat": (0, "g"),
                        "sodium": ("0.065", "g"),
                    },
                ),
            }
        }
    ).lookup("012345678905")

    assert product.name == "Plain Yogurt"
    assert product.serving_description == "170 g"
    assert product.serving_amount == "1"
    assert product.serving_unit == "serving"
    assert product.nutrition.calories_kcal == Decimal("100")
    assert product.nutrition.sodium_mg == Decimal("65.000")
    assert product.nutrition.fiber_g is None
    assert product.provenance.scanned_barcode == "012345678905"
    assert product.provenance.provider_code == "012345678905"
    assert product.provenance.nutrition_basis == "per_serving"


def test_100g_fallback_is_explicit_and_missing_values_stay_unknown() -> None:
    product = _provider(
        {
            "product": {
                "code": "12345678",
                "product_name": "Dry Food",
                "nutrition": _nutrition(
                    "100g",
                    {"energy-kcal": ("350.5", "kcal"), "proteins": ("12", "g")},
                ),
            }
        }
    ).lookup("12345678")
    assert product.serving_description == "100 g"
    assert product.serving_amount == "100"
    assert product.serving_unit == "g"
    assert product.nutrition.carbohydrate_g is None
    assert product.provenance.nutrition_basis == "per_100g"


def test_100ml_fallback_preserves_liquid_basis_and_ignores_computed_values() -> None:
    nutrition = {
        "input_sets": [
            {
                "source": "packaging",
                "preparation": "as_sold",
                "per": "100ml",
                "nutrients": {
                    "energy-kcal": {"value": 42, "unit": "kcal"},
                    "carbohydrates": {"value": 10.6, "unit": "g"},
                    "proteins": {"value_computed": 0, "unit": "g"},
                },
            }
        ]
    }
    product = _provider(
        {
            "product": {
                "code": "5449000000996",
                "product_name": "Cola",
                "nutrition": nutrition,
            }
        }
    ).lookup("5449000000996")
    assert product.serving_description == "100 mL"
    assert product.serving_amount == "100"
    assert product.serving_unit == "ml"
    assert product.nutrition.calories_kcal == Decimal("42")
    assert product.nutrition.protein_g is None
    assert product.provenance.nutrition_basis == "per_100ml"


def test_standardized_packaging_basis_wins_over_duplicate_serving_values() -> None:
    nutrition = {
        "input_sets": [
            {
                "source": "packaging",
                "preparation": "as_sold",
                "per": "100ml",
                "nutrients": {"energy-kcal": {"value": 42, "unit": "kcal"}},
            },
            {
                "source": "packaging",
                "preparation": "as_sold",
                "per": "serving",
                "per_quantity": 330,
                "nutrients": {"energy-kcal": {"value": 42, "unit": "kcal"}},
            },
        ]
    }
    product = _provider(
        {
            "product": {
                "code": "5449000000996",
                "product_name": "Cola",
                "serving_size": "1 portion (330 ml)",
                "nutrition": nutrition,
            }
        }
    ).lookup("5449000000996")

    assert product.serving_description == "100 mL"
    assert product.serving_amount == "100"
    assert product.serving_unit == "ml"
    assert product.nutrition.calories_kcal == Decimal("42")
    assert product.provenance.nutrition_basis == "per_100ml"


def test_not_found_incomplete_and_invalid_barcode_fail_closed() -> None:
    with pytest.raises(ValueError, match="App/Version"):
        OpenFoodFactsProvider("generic-client")
    with pytest.raises(BarcodeProductNotFound):
        _provider({}, status=404).lookup("12345678")
    with pytest.raises(BarcodeProductIncomplete):
        _provider(
            {"product": {"code": "12345678", "product_name": "Unknown", "nutrition": {}}}
        ).lookup("12345678")
    with pytest.raises(BarcodeProductIncomplete, match="exact packaging nutrition"):
        _provider(
            {
                "product": {
                    "code": "12345678",
                    "product_name": "Estimated only",
                    "nutrition": _nutrition(
                        "100g", {"energy-kcal": (100, "kcal")}, source="estimate"
                    ),
                }
            }
        ).lookup("12345678")
    with pytest.raises(ValueError, match="ASCII digits"):
        _provider({}).lookup("1234 5678")
    with pytest.raises(BarcodeProviderUnavailable, match="different product code"):
        _provider(
            {
                "product": {
                    "code": "87654321",
                    "product_name": "Wrong product",
                    "nutrition": _nutrition("100g", {"energy-kcal": ("100", "kcal")}),
                }
            }
        ).lookup("12345678")


def test_reviewed_import_is_versioned_idempotent_and_uses_existing_consumption() -> None:
    provider = _provider(
        {
            "product": {
                "code": "012345678905",
                "product_name": "Plain Yogurt",
                "serving_size": "170 g",
                "nutrition": _nutrition(
                    "serving",
                    {"energy-kcal": ("100", "kcal"), "proteins": ("17", "g")},
                ),
            }
        }
    )
    repo, ids = InMemoryCustomFoodRepository(), Ids()
    use_case = ImportBarcodeFoodUseCase(
        provider=provider,
        repository=repo,
        create_food=CreateCustomFoodUseCase(repo, Clock(), ids),
    )
    preview = provider.lookup("012345678905")
    digest = preview.provenance.payload_sha256
    assert digest is not None
    first = use_case.execute(
        user_id=UUID(int=100),
        barcode="012345678905",
        expected_payload_sha256=digest,
    )
    replay = use_case.execute(
        user_id=UUID(int=100),
        barcode="012345678905",
        expected_payload_sha256=digest,
    )
    assert first.created
    assert not replay.created
    assert replay.version == first.version

    recorded = (
        RecordManualFoodUseCase(repo, Clock(), ids)
        .execute(
            user_id=UUID(int=100),
            food_id=first.version.food_id,
            food_version_id=first.version.version_id,
            consumed_amount=Decimal("1"),
            consumed_unit="serving",
            meal_period=ManualMealPeriod.BREAKFAST,
            client_event_id=UUID(int=900),
        )
        .entry
    )
    assert recorded.source_system == "barcode_open_food_facts"
    assert recorded.nutrition_authority == "external_reference"
    assert digest in recorded.provenance_summary


def test_import_rejects_changed_payload_after_review() -> None:
    first_provider = _provider(
        {
            "product": {
                "code": "12345678",
                "product_name": "Food",
                "nutrition": _nutrition("100g", {"energy-kcal": ("100", "kcal")}),
            }
        }
    )
    changed_provider = _provider(
        {
            "product": {
                "code": "12345678",
                "product_name": "Food",
                "nutrition": _nutrition("100g", {"energy-kcal": ("110", "kcal")}),
            }
        }
    )
    digest = first_provider.lookup("12345678").provenance.payload_sha256
    assert digest is not None
    repo = InMemoryCustomFoodRepository()
    with pytest.raises(BarcodeProductChanged, match="changed after review"):
        ImportBarcodeFoodUseCase(
            provider=changed_provider,
            repository=repo,
            create_food=CreateCustomFoodUseCase(repo, Clock(), Ids()),
        ).execute(
            user_id=UUID(int=100),
            barcode="12345678",
            expected_payload_sha256=digest,
        )


def test_concurrent_exact_source_import_resolves_as_idempotent_replay() -> None:
    class ConcurrentWinnerRepository(InMemoryCustomFoodRepository):
        def save_version(self, version: CustomFoodVersion, *, create_identity: bool) -> None:
            super().save_version(version, create_identity=create_identity)
            if create_identity:
                raise DuplicateManualFoodError("simulated concurrent source winner")

    provider = _provider(
        {
            "product": {
                "code": "12345678",
                "product_name": "Food",
                "nutrition": _nutrition("100g", {"energy-kcal": ("100", "kcal")}),
            }
        }
    )
    digest = provider.lookup("12345678").provenance.payload_sha256
    assert digest is not None
    repo = ConcurrentWinnerRepository()
    outcome = ImportBarcodeFoodUseCase(
        provider=provider,
        repository=repo,
        create_food=CreateCustomFoodUseCase(repo, Clock(), Ids()),
    ).execute(
        user_id=UUID(int=100),
        barcode="12345678",
        expected_payload_sha256=digest,
    )
    assert not outcome.created
    assert outcome.version.provenance.payload_sha256 == digest


def test_authenticated_lookup_and_import_routes_preserve_provenance() -> None:
    provider = _provider(
        {
            "product": {
                "code": "012345678905",
                "product_name": "Plain Yogurt",
                "serving_size": "170 g",
                "nutrition": _nutrition(
                    "serving",
                    {"energy-kcal": ("100", "kcal"), "proteins": ("17", "g")},
                ),
            }
        }
    )
    secret = "barcode-test-secret-that-is-at-least-32-bytes"
    settings = HealthApiSettings(
        database_url=None,
        jwt_secret=secret,
        supabase_url=None,
        jwt_audience="authenticated",
    )
    verifier = TokenVerifier(settings)
    health = HealthBodyMassSyncUseCase(HealthSyncDeps(InMemoryHealthBodyMassRepository(), Clock()))
    app = create_health_app(HealthApiDeps(settings, verifier, health))
    repo, ids = InMemoryCustomFoodRepository(), Ids()
    creator = CreateCustomFoodUseCase(repo, Clock(), ids)
    app.state.lookup_barcode_food_use_case = LookupBarcodeFoodUseCase(provider)
    app.state.import_barcode_food_use_case = ImportBarcodeFoodUseCase(
        provider=provider,
        repository=repo,
        create_food=creator,
    )
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(UUID(int=100)),
            "aud": "authenticated",
            "iat": int(now.timestamp()),
            "exp": int(now.timestamp()) + 300,
        },
        secret,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}
    client = TestClient(app)

    lookup = client.get("/v1/nutrition/barcodes/012345678905", headers=headers)
    assert lookup.status_code == 200
    assert lookup.json()["provenance"]["provider"] == "open_food_facts"
    digest = lookup.json()["provenance"]["payload_sha256"]
    imported = client.post(
        "/v1/nutrition/barcodes/012345678905/import",
        headers=headers,
        json={"expected_payload_sha256": digest},
    )
    assert imported.status_code == 201
    assert imported.json()["created"] is True
    assert imported.json()["food"]["provenance"]["payload_sha256"] == digest
    replay = client.post(
        "/v1/nutrition/barcodes/012345678905/import",
        headers=headers,
        json={"expected_payload_sha256": digest},
    )
    assert replay.status_code == 200
    assert replay.json()["created"] is False
    assert client.get("/v1/nutrition/barcodes/012345678905").status_code == 401


def test_whey_serving_protein_survives_partial_100g_set_and_exact_preview() -> None:
    import json
    from pathlib import Path

    payload = json.loads(
        (Path(__file__).parents[1] / "fixtures/barcode/whey_serving.json").read_text()
    )
    provider = _provider(payload)
    product = provider.lookup("012345678905")
    assert product.nutrition.protein_g == Decimal("25")
    assert product.nutrition.calories_kcal == Decimal("120")
    assert product.serving_amount == "30"
    assert product.serving_unit == "g"
    assert product.serving_description == "1 serving (30 g)"
    repo, ids = InMemoryCustomFoodRepository(), Ids()
    imported = ImportBarcodeFoodUseCase(
        provider=provider, repository=repo, create_food=CreateCustomFoodUseCase(repo, Clock(), ids)
    ).execute(
        user_id=UUID(int=100),
        barcode="012345678905",
        expected_payload_sha256=product.provenance.payload_sha256,
    )
    assert repo.consumptions == {}  # import is not intake evidence
    use = RecordManualFoodUseCase(repo, Clock(), ids)
    version, amount, factor, facts = use.preview(
        user_id=UUID(int=100),
        food_id=imported.version.food_id,
        food_version_id=imported.version.version_id,
        amount=Decimal("80"),
        unit="g",
    )
    recorded = use.execute(
        user_id=UUID(int=100),
        food_id=version.food_id,
        food_version_id=version.version_id,
        consumed_amount=amount,
        consumed_unit="g",
        meal_period=ManualMealPeriod.BREAKFAST,
        client_event_id=UUID(int=701),
    )
    assert facts == recorded.entry.nutrition
    assert factor == recorded.entry.portion_factor
    assert recorded.entry.consumed_amount == Decimal("80")
    assert len(repo.consumptions) == 1
    assert facts.fiber_g is None
    for count in ("1", "1.5", "2"):
        _, physical, _, nutrition = use.preview(
            user_id=UUID(int=100),
            food_id=version.food_id,
            food_version_id=version.version_id,
            amount=Decimal(count),
            unit="servings",
        )
        assert physical == Decimal(count) * 30
        assert nutrition.protein_g == Decimal(count) * 25
    with pytest.raises(LookupError):
        use.preview(
            user_id=UUID(int=101),
            food_id=version.food_id,
            food_version_id=version.version_id,
            amount=Decimal(1),
            unit="servings",
        )


def test_legacy_normalized_protein_does_not_use_contributor_units() -> None:
    product = _provider(
        {
            "product": {
                "code": "12345678",
                "product_name": "Synthetic legacy whey",
                "nutrition_data_per": "100g",
                "serving_size": "1 scoop",
                "nutriments": {
                    "proteins_value": "80",
                    "proteins_100g": "80",
                    "proteins_unit": "",
                    "energy-kcal_value": "400",
                    "energy-kcal_100g": "400",
                },
            }
        }
    ).lookup("12345678")
    assert product.nutrition.protein_g == Decimal(80)
    assert product.serving_amount == "100"  # scoop mass never guessed
    assert product.nutrition.fiber_g is None


def test_legacy_explicit_100ml_suffix_preserves_volume_basis() -> None:
    product = _provider(
        {
            "product": {
                "code": "87654321",
                "product_name": "Synthetic legacy drink",
                "nutrition_data_per": "100ml",
                "nutriments": {
                    "proteins_value": "3.4",
                    "proteins_100ml": "3.4",
                    "energy-kcal_value": "50",
                    "energy-kcal_100ml": "50",
                },
            }
        }
    ).lookup("87654321")
    assert product.serving_description == "100 mL"
    assert product.serving_amount == "100"
    assert product.serving_unit == "ml"
    assert product.nutrition.calories_kcal == Decimal("50")
    assert product.nutrition.protein_g == Decimal("3.4")


def test_free_text_quantity_does_not_classify_liquid_but_structured_packaging_does() -> None:
    nutrition = {
        "input_sets": [
            {
                "source": "packaging",
                "preparation": "as_sold",
                "per": "100g",
                "nutrients": {"energy-kcal": {"value": 90, "unit": "kcal"}},
            },
            {
                "source": "packaging",
                "preparation": "as_sold",
                "per": "100ml",
                "nutrients": {"energy-kcal": {"value": 40, "unit": "kcal"}},
            },
        ]
    }
    free_text = _provider(
        {
            "product": {
                "code": "66666666",
                "product_name": "Bottle",
                "quantity": "500 ml",
                "nutrition": nutrition,
            }
        }
    ).lookup("66666666")
    assert free_text.serving_unit == "g"
    assert free_text.nutrition.calories_kcal == Decimal("90")

    structured = _provider(
        {
            "product": {
                "code": "77777777",
                "product_name": "Bottle",
                "packagings": [{"quantity_per_unit_value": 50, "quantity_per_unit_unit": "cl"}],
                "nutrition": nutrition,
            }
        }
    ).lookup("77777777")
    assert structured.serving_unit == "ml"
    assert structured.nutrition.calories_kcal == Decimal("40")


def test_serving_metadata_changes_review_digest_and_invalid_unit_falls_back() -> None:
    import json
    from pathlib import Path

    payload = json.loads(
        (Path(__file__).parents[1] / "fixtures/barcode/whey_serving.json").read_text()
    )
    original = _provider(payload).lookup("012345678905")
    payload["product"]["serving_quantity_unit"] = "scoop"
    fallback = _provider(payload).lookup("012345678905")
    assert fallback.serving_amount == "100"
    assert fallback.provenance.payload_sha256 != original.provenance.payload_sha256


def test_contradictory_serving_mass_is_never_assigned_to_source_nutrients() -> None:
    import json
    from pathlib import Path

    payload = json.loads(
        (Path(__file__).parents[1] / "fixtures/barcode/whey_serving.json").read_text()
    )
    payload["product"]["serving_quantity"] = 40
    # The explicit source is for 30 g, so use the compatible 100 g set.
    fallback = _provider(payload).lookup("012345678905")
    assert fallback.nutrition.protein_g is None
    assert fallback.serving_amount == "40"
    payload["product"]["nutrition"]["input_sets"] = [
        item for item in payload["product"]["nutrition"]["input_sets"] if item["per"] == "serving"
    ]
    with pytest.raises(BarcodeProductIncomplete):
        _provider(payload).lookup("012345678905")


def _liquid_basis_cases() -> dict[str, dict[str, object]]:
    import json
    from pathlib import Path

    return json.loads(
        (Path(__file__).parents[1] / "fixtures/barcode/liquid_basis_cases.json").read_text()
    )


def test_liquid_and_solid_basis_selection_uses_only_structured_physical_evidence() -> None:
    cases = _liquid_basis_cases()

    milk = _provider(cases["chocolate_milk_serving_ml"]).lookup("11111111")
    assert milk.policy_version == "barcode-food-import.v3"
    assert milk.serving_description == "1 serving (240 mL)"
    assert milk.serving_amount == "240"
    assert milk.serving_unit == "ml"
    assert milk.nutrition.calories_kcal == Decimal("120.0")
    assert milk.nutrition.protein_g == Decimal("8.16")
    assert milk.provenance.nutrition_basis == "per_100ml"

    beverage = _provider(cases["beverage_100ml_only"]).lookup("22222222")
    assert beverage.serving_description == "100 mL"
    assert beverage.serving_amount == "100"
    assert beverage.serving_unit == "ml"
    assert beverage.nutrition.calories_kcal == Decimal("42")

    mass_only = _provider(cases["beverage_mass_only"]).lookup("33333333")
    assert mass_only.serving_description == "100 g"
    assert mass_only.serving_amount == "100"
    assert mass_only.serving_unit == "g"
    assert mass_only.nutrition.calories_kcal == Decimal("48")

    bottle = _provider(cases["ambiguous_bottle"]).lookup("44444444")
    assert bottle.serving_description == "1 bottle"
    assert bottle.serving_amount == "1"
    assert bottle.serving_unit == "serving"
    assert "ml" not in bottle.serving_description.casefold()

    solid = _provider(cases["solid_gram_basis"]).lookup("55555555")
    assert solid.serving_description == "100 g"
    assert solid.serving_unit == "g"
    assert solid.nutrition.calories_kcal == Decimal("350")


def test_liquid_physical_and_serving_previews_scale_from_exact_volume_basis() -> None:
    product = _provider(_liquid_basis_cases()["chocolate_milk_serving_ml"]).lookup("11111111")
    repo, ids = InMemoryCustomFoodRepository(), Ids()
    imported = ImportBarcodeFoodUseCase(
        provider=_provider(_liquid_basis_cases()["chocolate_milk_serving_ml"]),
        repository=repo,
        create_food=CreateCustomFoodUseCase(repo, Clock(), ids),
    ).execute(
        user_id=UUID(int=100),
        barcode="11111111",
        expected_payload_sha256=product.provenance.payload_sha256,
    )
    preview = RecordManualFoodUseCase(repo, Clock(), ids).preview
    expected = {
        ("200", "ml"): (Decimal("200"), Decimal("100.00"), Decimal("6.800")),
        ("300", "ml"): (Decimal("300"), Decimal("150.00"), Decimal("10.200")),
        ("0.5", "servings"): (Decimal("120.0"), Decimal("60.00"), Decimal("4.080")),
        ("1.5", "servings"): (Decimal("360.0"), Decimal("180.00"), Decimal("12.240")),
    }
    for (amount, unit), (physical, calories, protein) in expected.items():
        _, actual_physical, _, nutrition = preview(
            user_id=UUID(int=100),
            food_id=imported.version.food_id,
            food_version_id=imported.version.version_id,
            amount=Decimal(amount),
            unit=unit,
        )
        assert actual_physical == physical
        assert nutrition.calories_kcal == calories
        assert nutrition.protein_g == protein


def test_v3_reimport_appends_version_without_changing_historical_import() -> None:
    provider = _provider(_liquid_basis_cases()["chocolate_milk_serving_ml"])
    product = provider.lookup("11111111")
    repo, ids = InMemoryCustomFoodRepository(), Ids()
    creator = CreateCustomFoodUseCase(repo, Clock(), ids)
    historical = creator.execute(
        user_id=UUID(int=100),
        name=product.name,
        brand=product.brand,
        serving_description="100 g",
        serving_amount=Decimal("100"),
        serving_unit="g",
        nutrition=product.nutrition,
        provenance=replace(product.provenance, payload_sha256="0" * 64),
    )

    corrected = ImportBarcodeFoodUseCase(
        provider=provider, repository=repo, create_food=creator
    ).execute(
        user_id=UUID(int=100),
        barcode="11111111",
        expected_payload_sha256=product.provenance.payload_sha256,
    )

    assert corrected.created
    assert corrected.version.food_id == historical.food_id
    assert corrected.version.version_id != historical.version_id
    assert corrected.version.serving_unit == "ml"
    assert repo.find_version(UUID(int=100), historical.food_id, historical.version_id) == historical


# ---------------------------------------------------------------------------
# Owner-entered serving evidence (barcode-food-import.v3).
#
# The real chocolate-milk record supplies exact per-100-g nutrition and no
# volume evidence at all: no serving_size, no serving_quantity, no
# product_quantity, and an empty packagings list. 100 g is therefore the only
# authoritative automatic basis, and the owner needs a safe, explicit way to
# state the label serving without Nytr ever inventing a density.
# ---------------------------------------------------------------------------


def test_mass_only_structured_record_stays_in_grams_with_an_explicit_reason() -> None:
    """CASE C: structured mass nutrition and zero volume evidence."""
    product = _provider(_liquid_basis_cases()["mass_only_no_quantity_evidence"]).lookup("66666666")

    assert product.serving_description == "100 g"
    assert product.serving_amount == "100"
    assert product.serving_unit == "g"
    assert product.provenance.nutrition_basis == "per_100g"
    assert product.basis_reason == "no_trustworthy_volume_evidence"
    assert product.provenance.serving_authority is None
    assert "ml" not in product.serving_description.casefold()


def test_basis_reason_classifies_every_case_without_leaking_source_detail() -> None:
    cases = _liquid_basis_cases()
    observed = {
        name: _provider(cases[name]).lookup(code).basis_reason
        for name, code in (
            ("chocolate_milk_serving_ml", "11111111"),
            ("beverage_100ml_only", "22222222"),
            ("beverage_mass_only", "33333333"),
            ("ambiguous_bottle", "44444444"),
            ("solid_gram_basis", "55555555"),
            ("mass_only_no_quantity_evidence", "66666666"),
        )
    }
    assert observed == {
        "chocolate_milk_serving_ml": "structured_serving_volume",
        "beverage_100ml_only": "explicit_per_100ml",
        "beverage_mass_only": "no_trustworthy_volume_evidence",
        "ambiguous_bottle": "source_serving_without_physical_quantity",
        "solid_gram_basis": "no_trustworthy_volume_evidence",
        "mass_only_no_quantity_evidence": "no_trustworthy_volume_evidence",
    }
    for reason in observed.values():
        assert reason in {member.value for member in BarcodeBasisReason}


def test_owner_serving_names_a_volume_when_source_nutrition_is_per_serving() -> None:
    """CASE D: per-serving nutrition plus an owner-verified serving volume."""
    product = _provider(_liquid_basis_cases()["ambiguous_bottle"]).lookup("44444444")
    assert product.provenance.nutrition_basis == "per_serving"
    assert product.serving_unit == "serving"

    corrected = apply_owner_serving(
        product, OwnerServingEvidence(amount=Decimal("240"), unit="ml", label="bottle")
    )

    assert corrected.serving_description == "1 bottle (240 mL)"
    assert corrected.serving_amount == "240"
    assert corrected.serving_unit == "ml"
    # Nutrition is already attached to the serving, so nothing is rescaled.
    assert corrected.nutrition == product.nutrition
    assert corrected.nutrition.calories_kcal == Decimal("180")
    assert corrected.provenance.serving_authority == "owner_entered"
    assert corrected.provenance.authority is CustomFoodAuthority.OPEN_FOOD_FACTS
    assert corrected.basis_reason == "owner_entered_serving"
    # The reviewed provider snapshot is untouched and still verifiable.
    assert corrected.provenance.payload_sha256 == product.provenance.payload_sha256
    assert corrected.provenance.nutrition_basis == "per_serving"


def test_owner_serving_refuses_to_convert_mass_nutrition_into_millilitres() -> None:
    """CASE E: per-100-g nutrition plus an owner volume must fail closed."""
    product = _provider(_liquid_basis_cases()["mass_only_no_quantity_evidence"]).lookup("66666666")

    with pytest.raises(BarcodeServingEvidenceRefused) as refusal:
        apply_owner_serving(
            product, OwnerServingEvidence(amount=Decimal("240"), unit="ml", label="bottle")
        )

    assert str(refusal.value) == (
        "This product's nutrition is mass-based, so Nytr cannot convert it to mL "
        "without a verified serving nutrition basis."
    )
    assert str(refusal.value) == MASS_BASIS_REFUSAL
    # No density was assumed anywhere: 1 g/mL would have produced these.
    assert product.nutrition.calories_kcal == Decimal("62.5")
    assert product.serving_unit == "g"


def test_owner_serving_refuses_to_convert_volume_nutrition_into_grams() -> None:
    product = _provider(_liquid_basis_cases()["beverage_100ml_only"]).lookup("22222222")
    assert product.provenance.nutrition_basis == "per_100ml"

    with pytest.raises(BarcodeServingEvidenceRefused) as refusal:
        apply_owner_serving(product, OwnerServingEvidence(amount=Decimal("240"), unit="g"))

    assert str(refusal.value) == VOLUME_BASIS_REFUSAL


def test_owner_serving_scales_exactly_within_one_physical_dimension() -> None:
    cases = _liquid_basis_cases()

    mass = _provider(cases["mass_only_no_quantity_evidence"]).lookup("66666666")
    scaled_mass = apply_owner_serving(
        mass, OwnerServingEvidence(amount=Decimal("240"), unit="g", label="carton")
    )
    assert scaled_mass.serving_description == "1 carton (240 g)"
    assert scaled_mass.serving_unit == "g"
    assert scaled_mass.nutrition.calories_kcal == Decimal("62.5") * Decimal("2.4")
    assert scaled_mass.nutrition.protein_g == Decimal("2.5") * Decimal("2.4")

    volume = _provider(cases["beverage_100ml_only"]).lookup("22222222")
    scaled_volume = apply_owner_serving(
        volume, OwnerServingEvidence(amount=Decimal("330"), unit="ml")
    )
    assert scaled_volume.serving_description == "1 serving (330 mL)"
    assert scaled_volume.serving_unit == "ml"
    assert scaled_volume.nutrition.calories_kcal == Decimal("42") * Decimal("3.3")
    assert scaled_volume.provenance.serving_authority == "owner_entered"


def test_owner_serving_rejects_unusable_amounts_and_labels() -> None:
    for amount in (Decimal("0"), Decimal("-1")):
        with pytest.raises(ValueError, match="positive finite"):
            OwnerServingEvidence(amount=amount, unit="ml")
    with pytest.raises(ValueError, match="g or ml"):
        OwnerServingEvidence(amount=Decimal("240"), unit="oz")
    with pytest.raises(ValueError, match="serving, bottle, or carton"):
        OwnerServingEvidence(amount=Decimal("240"), unit="ml", label="jug")


def test_owner_serving_import_appends_a_version_and_leaves_history_intact() -> None:
    """CASE H: owner correction never rewrites an immutable historical version."""
    provider = _provider(_liquid_basis_cases()["ambiguous_bottle"])
    product = provider.lookup("44444444")
    assert product.provenance.payload_sha256 is not None
    repo, ids = InMemoryCustomFoodRepository(), Ids()
    creator = CreateCustomFoodUseCase(repo, Clock(), ids)
    use_case = ImportBarcodeFoodUseCase(provider=provider, repository=repo, create_food=creator)

    source = use_case.execute(
        user_id=UUID(int=100),
        barcode="44444444",
        expected_payload_sha256=product.provenance.payload_sha256,
    )
    assert source.created
    assert source.version.serving_unit == "serving"
    assert source.version.provenance.serving_authority is None

    corrected = use_case.execute(
        user_id=UUID(int=100),
        barcode="44444444",
        expected_payload_sha256=product.provenance.payload_sha256,
        owner_serving=OwnerServingEvidence(amount=Decimal("240"), unit="ml", label="bottle"),
    )

    assert corrected.created
    assert corrected.version.food_id == source.version.food_id
    assert corrected.version.version_id != source.version.version_id
    assert corrected.version.serving_unit == "ml"
    assert corrected.version.serving_amount == Decimal("240")
    assert corrected.version.serving_description == "1 bottle (240 mL)"
    assert corrected.version.provenance.serving_authority == "owner_entered"
    # The earlier source-bound version is byte-identical afterwards.
    assert (
        repo.find_version(UUID(int=100), source.version.food_id, source.version.version_id)
        == source.version
    )

    replay = use_case.execute(
        user_id=UUID(int=100),
        barcode="44444444",
        expected_payload_sha256=product.provenance.payload_sha256,
        owner_serving=OwnerServingEvidence(amount=Decimal("240"), unit="ml", label="bottle"),
    )
    assert replay.created is False
    assert replay.version.version_id == corrected.version.version_id


def test_owner_serving_consumption_records_that_the_owner_supplied_the_serving() -> None:
    provider = _provider(_liquid_basis_cases()["ambiguous_bottle"])
    product = provider.lookup("44444444")
    assert product.provenance.payload_sha256 is not None
    repo, ids = InMemoryCustomFoodRepository(), Ids()
    imported = ImportBarcodeFoodUseCase(
        provider=provider,
        repository=repo,
        create_food=CreateCustomFoodUseCase(repo, Clock(), ids),
    ).execute(
        user_id=UUID(int=100),
        barcode="44444444",
        expected_payload_sha256=product.provenance.payload_sha256,
        owner_serving=OwnerServingEvidence(amount=Decimal("240"), unit="ml", label="bottle"),
    )

    recorded = (
        RecordManualFoodUseCase(repo, Clock(), ids)
        .execute(
            user_id=UUID(int=100),
            food_id=imported.version.food_id,
            food_version_id=imported.version.version_id,
            consumed_amount=Decimal("240"),
            consumed_unit="ml",
            meal_period=ManualMealPeriod.LUNCH,
            client_event_id=UUID(int=900),
        )
        .entry
    )

    assert recorded.consumed_unit == "ml"
    assert recorded.nutrition_authority == "external_reference"
    assert "serving entered by owner from label" in recorded.provenance_summary
    assert product.provenance.payload_sha256 in recorded.provenance_summary


def test_solid_products_are_unchanged_by_the_owner_serving_path() -> None:
    """CASE G: solids keep their exact gram basis and reject a volume serving."""
    product = _provider(_liquid_basis_cases()["solid_gram_basis"]).lookup("55555555")

    assert product.serving_description == "100 g"
    assert product.serving_unit == "g"
    assert product.nutrition.calories_kcal == Decimal("350")
    with pytest.raises(BarcodeServingEvidenceRefused, match="mass-based"):
        apply_owner_serving(product, OwnerServingEvidence(amount=Decimal("240"), unit="ml"))


def test_import_api_refuses_an_incompatible_owner_serving_with_422() -> None:
    provider = _provider(_liquid_basis_cases()["mass_only_no_quantity_evidence"])
    secret = "barcode-test-secret-that-is-at-least-32-bytes"
    settings = HealthApiSettings(
        database_url=None,
        jwt_secret=secret,
        supabase_url=None,
        jwt_audience="authenticated",
    )
    verifier = TokenVerifier(settings)
    health = HealthBodyMassSyncUseCase(HealthSyncDeps(InMemoryHealthBodyMassRepository(), Clock()))
    app = create_health_app(HealthApiDeps(settings, verifier, health))
    repo, ids = InMemoryCustomFoodRepository(), Ids()
    app.state.lookup_barcode_food_use_case = LookupBarcodeFoodUseCase(provider)
    app.state.import_barcode_food_use_case = ImportBarcodeFoodUseCase(
        provider=provider,
        repository=repo,
        create_food=CreateCustomFoodUseCase(repo, Clock(), ids),
    )
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(UUID(int=100)),
            "aud": "authenticated",
            "iat": int(now.timestamp()),
            "exp": int(now.timestamp()) + 300,
        },
        secret,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}
    client = TestClient(app)

    lookup = client.get("/v1/nutrition/barcodes/66666666", headers=headers)
    assert lookup.status_code == 200
    body = lookup.json()
    assert body["serving_description"] == "100 g"
    assert body["basis_reason"] == "no_trustworthy_volume_evidence"
    assert body["provenance"]["serving_authority"] is None
    digest = body["provenance"]["payload_sha256"]

    refused = client.post(
        "/v1/nutrition/barcodes/66666666/import",
        headers=headers,
        json={
            "expected_payload_sha256": digest,
            "owner_serving": {"amount": "240", "unit": "ml", "label": "bottle"},
        },
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "owner_serving_unsupported"
    assert refused.json()["error"]["detail"] == MASS_BASIS_REFUSAL

    accepted = client.post(
        "/v1/nutrition/barcodes/66666666/import",
        headers=headers,
        json={
            "expected_payload_sha256": digest,
            "owner_serving": {"amount": "240", "unit": "g", "label": "carton"},
        },
    )
    assert accepted.status_code == 201
    food = accepted.json()["food"]
    assert food["serving_description"] == "1 carton (240 g)"
    assert food["serving_unit"] == "g"
    assert food["provenance"]["serving_authority"] == "owner_entered"
    assert food["provenance"]["payload_sha256"] == digest

    rejected_unit = client.post(
        "/v1/nutrition/barcodes/66666666/import",
        headers=headers,
        json={
            "expected_payload_sha256": digest,
            "owner_serving": {"amount": "240", "unit": "oz"},
        },
    )
    assert rejected_unit.status_code == 422
