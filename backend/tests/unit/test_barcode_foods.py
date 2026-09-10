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
    BarcodeProductChanged,
    BarcodeProductIncomplete,
    BarcodeProductNotFound,
    BarcodeProviderUnavailable,
)
from nutrition_agent.domain.nutrition.custom_foods import CustomFoodVersion, ManualMealPeriod
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
        "Nytr/1.0 (contact: demo@example.invalid)",
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
    assert product.serving_description == "100 ml"
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

    assert product.serving_description == "100 ml"
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
