"""Authenticated read-only API tests for detailed training history."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import jwt
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.detailed_training import (
    GetDetailedTrainingSessionUseCase,
    ListDetailedTrainingSessionsUseCase,
)
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.db.in_memory_repos import (
    InMemoryDetailedTrainingRepository,
    InMemoryHealthBodyMassRepository,
)
from nutrition_agent.infrastructure.hevy_detail_source import HevyFixtureProvider

SECRET = "test-secret-for-hs256-signing-only-32-bytes-min"
AUDIENCE = "authenticated"
USER_A = UUID(int=0xA)
USER_B = UUID(int=0xB)
NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hevy"


class _Clock:
    def now(self) -> datetime:
        return NOW


def _token(subject: UUID = USER_A) -> str:
    issued = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(subject),
            "aud": AUDIENCE,
            "iat": int(issued.timestamp()),
            "exp": int((issued + timedelta(minutes=10)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )


def _client(repo: InMemoryDetailedTrainingRepository | None) -> TestClient:
    settings = HealthApiSettings(None, SECRET, None, AUDIENCE)
    return TestClient(
        create_health_app(
            HealthApiDeps(
                settings,
                TokenVerifier(settings),
                HealthBodyMassSyncUseCase(
                    HealthSyncDeps(InMemoryHealthBodyMassRepository(), _Clock())
                ),
                training_detail_list_use_case=(
                    ListDetailedTrainingSessionsUseCase(repo) if repo is not None else None
                ),
                training_detail_get_use_case=(
                    GetDetailedTrainingSessionUseCase(repo) if repo is not None else None
                ),
            )
        )
    )


def _seed() -> InMemoryDetailedTrainingRepository:
    ids = iter((UUID(int=1), UUID(int=2)))
    repo = InMemoryDetailedTrainingRepository(ids=lambda: next(ids))
    payloads = (
        (FIXTURES / "events_page_1.json").read_bytes(),
        (FIXTURES / "events_page_2.json").read_bytes(),
    )
    repo.apply_import(USER_A, HevyFixtureProvider(payloads).load_changes(None), NOW)
    return repo


def test_list_and_detail_are_owner_scoped_and_preserve_exact_values() -> None:
    client = _client(_seed())
    headers = {"Authorization": f"Bearer {_token()}"}
    listing = client.get("/v1/training/sessions", headers=headers)
    assert listing.status_code == 200
    sessions = listing.json()["sessions"]
    assert [item["title"] for item in sessions] == ["Push Day", "Lower Day"]
    assert sessions[0]["exercise_count"] == 6
    assert sessions[0]["set_count"] == 9

    detail = client.get(
        "/v1/training/sessions/00000000-0000-0000-0000-000000000001", headers=headers
    )
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["source_system"] == "hevy"
    assert payload["exercises"][0]["sets"][1]["load"] == {
        "value": "65",
        "unit": "kg",
    }
    assert payload["exercises"][3]["sets"][0]["load"] is None
    assert payload["parser_version"] == "hevy-public-api.v1"


def test_auth_is_required_and_another_owner_cannot_read_detail() -> None:
    client = _client(_seed())
    assert client.get("/v1/training/sessions").status_code == 401
    response = client.get(
        "/v1/training/sessions/00000000-0000-0000-0000-000000000001",
        headers={"Authorization": f"Bearer {_token(USER_B)}"},
    )
    assert response.status_code == 404


def test_missing_storage_fails_closed_and_malformed_id_is_rejected() -> None:
    client = _client(None)
    headers = {"Authorization": f"Bearer {_token()}"}
    unavailable = client.get("/v1/training/sessions", headers=headers)
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "storage_unavailable"
    assert client.get("/v1/training/sessions/not-a-uuid", headers=headers).status_code == 422


def test_list_limit_is_validated_at_api_boundary() -> None:
    client = _client(_seed())
    headers = {"Authorization": f"Bearer {_token()}"}
    assert client.get("/v1/training/sessions?limit=0", headers=headers).status_code == 422
    assert client.get("/v1/training/sessions?limit=101", headers=headers).status_code == 422
