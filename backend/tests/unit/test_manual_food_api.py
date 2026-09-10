from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.application.manual_foods import (
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
