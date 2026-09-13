from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.application.manual_foods import (
    AdjustManualFoodUseCase,
    CreateCustomFoodUseCase,
    ListCustomFoodsUseCase,
    RecordManualFoodUseCase,
)
from nutrition_agent.db.in_memory_repos import (
    InMemoryCustomFoodRepository,
    InMemoryHealthBodyMassRepository,
)

SECRET = "manual-food-test-secret-at-least-32-bytes"
SUBJECT = UUID("00000000-0000-0000-0000-0000000000a1")


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 4, 12, tzinfo=UTC)


class Ids:
    value = 1

    def new_id(self) -> UUID:
        result = UUID(int=self.value)
        self.value += 1
        return result


def _client() -> TestClient:
    settings = HealthApiSettings(
        database_url=None, jwt_secret=SECRET, supabase_url=None, jwt_audience="authenticated"
    )
    verifier = TokenVerifier(settings)
    health = HealthBodyMassSyncUseCase(HealthSyncDeps(InMemoryHealthBodyMassRepository(), Clock()))
    app = create_health_app(HealthApiDeps(settings, verifier, health))
    repo, ids = InMemoryCustomFoodRepository(), Ids()
    app.state.create_custom_food_use_case = CreateCustomFoodUseCase(repo, Clock(), ids)
    app.state.list_custom_foods_use_case = ListCustomFoodsUseCase(repo)
    app.state.record_manual_food_use_case = RecordManualFoodUseCase(repo, Clock(), ids)
    app.state.adjust_manual_food_use_case = AdjustManualFoodUseCase(repo, Clock(), ids)
    return TestClient(app)


def _headers() -> dict[str, str]:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(SUBJECT),
            "aud": "authenticated",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def test_create_list_record_and_no_client_owner() -> None:
    client = _client()
    body = {
        "name": "Eggs",
        "brand": None,
        "serving_description": "two eggs",
        "serving_amount": "2",
        "serving_unit": "egg",
        "nutrition": {"calories_kcal": "140", "protein_g": "12"},
    }
    created = client.post("/v1/nutrition/custom-foods", json=body, headers=_headers())
    assert created.status_code == 201
    food = created.json()
    assert client.get("/v1/nutrition/custom-foods", headers=_headers()).json()["foods"] == [food]
    record_body = {
        "food_id": food["food_id"],
        "food_version_id": food["version_id"],
        "consumed_amount": "1",
        "consumed_unit": "egg",
        "meal_period": "breakfast",
        "client_event_id": "00000000-0000-0000-0000-000000000099",
    }
    recorded = client.post(
        "/v1/nutrition/manual-consumption",
        headers=_headers(),
        json=record_body,
    )
    assert recorded.status_code == 201
    assert recorded.json()["nutrition"]["calories_kcal"] == "70.0"
    assert recorded.json()["meal_period"] == "breakfast"
    replay = client.post("/v1/nutrition/manual-consumption", headers=_headers(), json=record_body)
    assert replay.status_code == 200
    assert replay.json() == recorded.json()

    body["user_id"] = str(UUID(int=999))
    assert (
        client.post("/v1/nutrition/custom-foods", json=body, headers=_headers()).status_code == 422
    )


def test_routes_require_authentication() -> None:
    client = _client()
    assert client.get("/v1/nutrition/custom-foods").status_code == 401


def test_preview_is_authenticated_read_only_and_matches_persisted_quantity() -> None:
    client = _client()
    food = client.post(
        "/v1/nutrition/custom-foods",
        headers=_headers(),
        json={
            "name": "Synthetic whey",
            "serving_description": "1 serving (30 g)",
            "serving_amount": "30",
            "serving_unit": "g",
            "nutrition": {"calories_kcal": "120", "protein_g": "25"},
        },
    ).json()
    request = {
        "food_id": food["food_id"],
        "food_version_id": food["version_id"],
        "amount": "1.5",
        "unit": "servings",
    }
    assert client.post("/v1/nutrition/food-preview", json=request).status_code == 401
    preview = client.post("/v1/nutrition/food-preview", headers=_headers(), json=request)
    assert preview.status_code == 200
    value = preview.json()
    assert value["consumed_amount"] == "45.0"
    recorded = client.post(
        "/v1/nutrition/manual-consumption",
        headers=_headers(),
        json={
            "food_id": food["food_id"],
            "food_version_id": food["version_id"],
            "consumed_amount": value["consumed_amount"],
            "consumed_unit": value["consumed_unit"],
            "meal_period": "breakfast",
            "client_event_id": str(UUID(int=9001)),
        },
    )
    assert recorded.status_code == 201
    assert recorded.json()["nutrition"] == value["nutrition"]
    assert value["nutrition"]["fiber_g"] is None
    for amount in ("0", "-1", "NaN", "80garbage"):
        assert (
            client.post(
                "/v1/nutrition/food-preview", headers=_headers(), json={**request, "amount": amount}
            ).status_code
            == 400
        )


def test_correction_preview_correction_and_void_are_append_only() -> None:
    client = _client()
    food = client.post(
        "/v1/nutrition/custom-foods",
        headers=_headers(),
        json={
            "name": "Oats",
            "serving_description": "one bowl",
            "serving_amount": "1",
            "serving_unit": "bowl",
            "nutrition": {"calories_kcal": "400", "protein_g": "20"},
        },
    ).json()
    original = client.post(
        "/v1/nutrition/manual-consumption",
        headers=_headers(),
        json={
            "food_id": food["food_id"],
            "food_version_id": food["version_id"],
            "consumed_amount": "1",
            "consumed_unit": "bowl",
            "meal_period": "lunch",
            "client_event_id": str(UUID(int=9100)),
        },
    ).json()
    path = f"/v1/nutrition/manual-consumption/{original['entry_id']}"
    preview = client.post(
        f"{path}/preview-correction",
        headers=_headers(),
        json={"amount": "1.5", "unit": "bowl"},
    )
    assert preview.status_code == 200
    assert preview.json()["nutrition"]["calories_kcal"] == "600.0"
    correction_body = {
        "amount": preview.json()["consumed_amount"],
        "unit": preview.json()["consumed_unit"],
        "client_event_id": str(UUID(int=9101)),
    }
    corrected = client.post(f"{path}/corrections", headers=_headers(), json=correction_body)
    assert corrected.status_code == 201
    assert corrected.json()["replacement"]["nutrition"] == preview.json()["nutrition"]
    replay = client.post(f"{path}/corrections", headers=_headers(), json=correction_body)
    assert replay.status_code == 200
    replacement_id = corrected.json()["replacement_entry_id"]
    removed = client.post(
        f"/v1/nutrition/manual-consumption/{replacement_id}/void",
        headers=_headers(),
        json={"client_event_id": str(UUID(int=9102))},
    )
    assert removed.status_code == 201
    assert removed.json()["kind"] == "void"
    assert removed.json()["replacement"] is None
