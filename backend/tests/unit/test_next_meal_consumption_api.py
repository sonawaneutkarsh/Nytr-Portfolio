from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.application.next_meal_consumption import (
    GetNextMealConsumptionUseCase,
    RecordNextMealConsumptionUseCase,
)
from nutrition_agent.db.in_memory_repos import (
    InMemoryHealthBodyMassRepository,
    InMemoryNextMealConsumptionRepository,
    InMemoryNextMealRecommendationRepository,
)
from nutrition_agent.domain.next_meal import NextMealStatus
from tests.unit.test_next_meal_consumption import OTHER, OWNER, Clock, Ids, _recommendation

SECRET = "m16b-next-meal-consumption-api-secret"
AUDIENCE = "authenticated"


def _headers(user_id: UUID) -> dict[str, str]:
    issued = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(user_id),
            "aud": AUDIENCE,
            "iat": int(issued.timestamp()),
            "exp": int((issued + timedelta(minutes=10)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _client() -> tuple[
    TestClient,
    InMemoryNextMealRecommendationRepository,
    InMemoryNextMealConsumptionRepository,
]:
    settings = HealthApiSettings(
        database_url=None,
        jwt_secret=SECRET,
        supabase_url=None,
        jwt_audience=AUDIENCE,
    )
    health = HealthBodyMassSyncUseCase(HealthSyncDeps(InMemoryHealthBodyMassRepository(), Clock()))
    app = create_health_app(HealthApiDeps(settings, TokenVerifier(settings), health))
    recommendations = InMemoryNextMealRecommendationRepository()
    recommendations.save(_recommendation())
    consumptions = InMemoryNextMealConsumptionRepository()
    app.state.next_meal_repository = recommendations
    app.state.next_meal_consumption_record_use_case = RecordNextMealConsumptionUseCase(
        recommendations=recommendations,
        consumptions=consumptions,
        clock=Clock(),
        ids=Ids(),
    )
    app.state.next_meal_consumption_get_use_case = GetNextMealConsumptionUseCase(consumptions)
    return TestClient(app), recommendations, consumptions


def test_explicit_consumption_is_authenticated_and_rejects_client_authority() -> None:
    client, _, consumptions = _client()
    path = f"/v1/recommendations/next-meal/{UUID(int=1)}/consumption"
    assert client.post(path, json={"client_event_id": str(UUID(int=2))}).status_code == 401
    response = client.post(
        path,
        headers=_headers(OWNER),
        json={
            "client_event_id": str(UUID(int=2)),
            "user_id": str(OTHER),
            "calories_kcal": "9999",
        },
    )
    assert response.status_code == 422
    assert consumptions.entries == {}


def test_record_exact_replay_conflict_and_retrieval() -> None:
    client, _, consumptions = _client()
    path = f"/v1/recommendations/next-meal/{UUID(int=1)}/consumption"
    payload = {"client_event_id": str(UUID(int=20))}

    created = client.post(path, headers=_headers(OWNER), json=payload)
    assert created.status_code == 201
    assert created.json()["created"] is True
    assert created.json()["state"] == "eaten"
    assert created.json()["calories_kcal"] == "640"
    assert created.json()["protein_g"] == "44.5"
    replay = client.post(path, headers=_headers(OWNER), json=payload)
    assert replay.status_code == 200
    assert replay.json()["entry_id"] == created.json()["entry_id"]
    assert replay.json()["recorded_at"] == created.json()["recorded_at"]
    assert len(consumptions.entries) == 1

    conflict = client.post(
        path,
        headers=_headers(OWNER),
        json={"client_event_id": str(UUID(int=21))},
    )
    assert conflict.status_code == 409
    fetched = client.get(path, headers=_headers(OWNER))
    assert fetched.status_code == 200
    assert fetched.json()["entry_id"] == created.json()["entry_id"]


def test_cross_owner_and_failed_recommendation_are_not_consumable() -> None:
    client, recommendations, consumptions = _client()
    path = f"/v1/recommendations/next-meal/{UUID(int=1)}/consumption"
    assert (
        client.post(
            path,
            headers=_headers(OTHER),
            json={"client_event_id": str(UUID(int=30))},
        ).status_code
        == 404
    )
    failed = _recommendation(
        recommendation_id=UUID(int=31),
        status=NextMealStatus.NO_APPROVED_PROTEIN_TARGET,
    )
    recommendations.save(failed)
    failed_response = client.post(
        f"/v1/recommendations/next-meal/{failed.recommendation_id}/consumption",
        headers=_headers(OWNER),
        json={"client_event_id": str(UUID(int=32))},
    )
    assert failed_response.status_code == 409
    assert failed_response.json()["error"]["code"] == "next_meal_not_consumable"
    assert consumptions.entries == {}


def test_recommendation_retrieval_creates_zero_consumption() -> None:
    client, _, consumptions = _client()
    response = client.get("/v1/recommendations/next-meal/latest", headers=_headers(OWNER))
    assert response.status_code == 200
    assert response.json()["status"] == "recommended"
    assert consumptions.entries == {}


def test_recommendation_generation_creates_zero_consumption() -> None:
    client, recommendations, consumptions = _client()

    class Generator:
        def execute(self, **_: object):
            return recommendations.save(_recommendation(recommendation_id=UUID(int=90)))

    client.app.state.next_meal_generate_use_case = Generator()
    response = client.post(
        "/v1/recommendations/next-meal",
        headers=_headers(OWNER),
        json={
            "local_date": "2026-09-05",
            "timezone": "America/New_York",
            "client_request_id": str(UUID(int=91)),
        },
    )
    assert response.status_code == 201
    assert consumptions.entries == {}
    missing = client.get(
        f"/v1/recommendations/next-meal/{UUID(int=1)}/consumption",
        headers=_headers(OWNER),
    )
    assert missing.status_code == 404
    assert consumptions.entries == {}
