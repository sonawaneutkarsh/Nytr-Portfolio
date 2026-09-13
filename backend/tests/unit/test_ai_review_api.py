"""Authenticated M21 API boundary tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import UUID

import jwt
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.routes_ai_review import get_ai_review_use_case
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.ai_review import GenerateAIReviewUseCase
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.db.in_memory_repos import InMemoryHealthBodyMassRepository
from nutrition_agent.domain.ai_review import AIReviewContent, AIReviewResult, AIReviewStatus
from tests.unit.test_ai_review import _snapshot

SECRET = "ai-review-api-secret-for-hs256-tests"
AUDIENCE = "authenticated"
USER = UUID("00000000-0000-0000-0000-000000000021")


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 8, 12, tzinfo=UTC)


class _UseCase:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, date, str]] = []

    def snapshot(self, *, user_id, as_of_date, timezone):
        self.calls.append((user_id, as_of_date, timezone))
        return _snapshot()

    def execute(self, *, user_id: UUID, as_of_date: date, timezone: str) -> AIReviewResult:
        self.calls.append((user_id, as_of_date, timezone))
        return AIReviewResult(
            status=AIReviewStatus.AVAILABLE,
            snapshot=_snapshot(),
            review=AIReviewContent(
                summary="Recorded evidence is incomplete.",
                attention_items=("Weight evidence is stale.",),
                evidence_notes=("Only recorded evidence is included.",),
                limitations=("This explanation is non-authoritative.",),
            ),
            failure_code=None,
        )


def _headers() -> dict[str, str]:
    issued = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(USER),
            "aud": AUDIENCE,
            "iat": int(issued.timestamp()),
            "exp": int((issued + timedelta(minutes=10)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _client() -> tuple[TestClient, _UseCase]:
    settings = HealthApiSettings(None, SECRET, None, AUDIENCE)
    health = HealthBodyMassSyncUseCase(HealthSyncDeps(InMemoryHealthBodyMassRepository(), _Clock()))
    use_case = _UseCase()
    app = create_health_app(
        HealthApiDeps(
            settings,
            TokenVerifier(settings),
            health,
            ai_review_use_case=cast(GenerateAIReviewUseCase, use_case),
        )
    )
    app.dependency_overrides[get_ai_review_use_case] = lambda: use_case
    return TestClient(app), use_case


def test_review_requires_auth_and_derives_owner_from_jwt_without_writes() -> None:
    client, use_case = _client()
    payload = {"as_of_date": "2026-09-08", "timezone": "UTC"}
    assert client.post("/v1/review/current", json=payload).status_code == 401

    response = client.post("/v1/review/current", json=payload, headers=_headers())
    assert response.status_code == 200
    assert use_case.calls == [(USER, date(2026, 9, 8), "UTC")]
    body = response.json()
    assert body["status"] == "available"
    assert body["prompt_version"] == "owner-ai-review-prompt.v2"
    assert body["snapshot"]["snapshot_version"] == "owner-ai-review-snapshot.v2"
    assert "authoritative" in body["authority_notice"]


def test_client_cannot_submit_owner_or_authoritative_facts() -> None:
    client, use_case = _client()
    for extra in (
        {"user_id": str(UUID(int=99))},
        {"calories_kcal": "9999"},
        {"weight_kg": "1"},
        {"workouts": []},
    ):
        response = client.post(
            "/v1/review/current",
            json={"as_of_date": "2026-09-08", "timezone": "UTC", **extra},
            headers=_headers(),
        )
        assert response.status_code == 422
    assert use_case.calls == []


def test_on_device_snapshot_is_authenticated_minimized_and_read_only():
    client, use = _client()
    url = "/v1/review/snapshot?as_of_date=2026-09-08&timezone=UTC"
    assert client.get(url).status_code == 401
    response = client.get(url, headers=_headers())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    model_input = response.json()["model_input"]
    assert {
        "goal",
        "weight_evidence",
        "nutrition_evidence",
        "calorie_target_available",
        "protein_target_available",
        "next_meal",
        "includes_estimates",
        "quality_flags",
        "limitation",
        "goal_band_status",
        "calorie_target_kcal",
        "protein_target_g",
        "recorded_item_count",
        "recorded_calories_kcal",
        "recorded_protein_g",
        "weight_weekly_rate_kg",
        "days_with_records_7d",
        "days_with_records_28d",
        "limitation_codes",
    }.issubset(model_input)
    assert model_input["nutrition_evidence"] == "recorded_partial"
    assert use.calls == [(USER, date(2026, 9, 8), "UTC")]
    import json

    text = json.dumps(model_input)
    assert model_input["calorie_target_kcal"] == "2200"
    assert model_input["recorded_calories_kcal"] == "450"
    assert model_input["protein_target_g"] == "120"
    for forbidden in (str(USER), "2026-09-08", "UTC", "IGNORE ALL"):
        assert forbidden not in text
