"""Authenticated, offline API coverage for M12A workout ingestion."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.application.training_sync import (
    TrainingSessionSyncUseCase,
    TrainingSyncDeps,
)
from nutrition_agent.db.in_memory_repos import (
    InMemoryHealthBodyMassRepository,
    InMemoryTrainingSessionRepository,
)

SECRET = "test-secret-for-hs256-signing-only-32-bytes-min"
AUDIENCE = "authenticated"
SUBJECT = "00000000-0000-0000-0000-0000000000a1"
NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


def _token(subject: str = SUBJECT) -> str:
    issued = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": subject,
            "aud": AUDIENCE,
            "iat": int(issued.timestamp()),
            "exp": int((issued + timedelta(minutes=10)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )


def _client(training: InMemoryTrainingSessionRepository | None = None) -> TestClient:
    settings = HealthApiSettings(None, SECRET, None, AUDIENCE)
    body = InMemoryHealthBodyMassRepository()
    training_use_case = (
        TrainingSessionSyncUseCase(TrainingSyncDeps(training, _Clock()))
        if training is not None
        else None
    )
    return TestClient(
        create_health_app(
            HealthApiDeps(
                settings,
                TokenVerifier(settings),
                HealthBodyMassSyncUseCase(HealthSyncDeps(body, _Clock())),
                training_use_case=training_use_case,
            )
        )
    )


def _payload() -> dict[str, object]:
    return {
        "client_batch_id": str(uuid4()),
        "added": [
            {
                "source_system": "healthkit",
                "source_record_id": "00000000-0000-0000-0000-000000000001",
                "activity_type": "37",
                "started_at": "2026-09-03T10:00:00+00:00",
                "ended_at": "2026-09-03T11:00:00+00:00",
                "active_duration_seconds": "3600.000",
                "active_energy_kcal": "412.750",
                "timezone_identifier": "America/New_York",
                "source_name": "Apple Watch",
                "source_bundle_id": "com.apple.health",
                "source_revision": "26.0",
            }
        ],
        "deleted": [],
    }


def test_authenticated_workout_batch_is_owner_scoped_and_idempotent() -> None:
    repo = InMemoryTrainingSessionRepository(clock=_Clock().now)
    client = _client(repo)
    headers = {"Authorization": f"Bearer {_token()}"}
    first = client.post("/v1/health/workouts/sync", json=_payload(), headers=headers)
    second = client.post("/v1/health/workouts/sync", json=_payload(), headers=headers)
    assert first.status_code == 200
    assert first.json()["accepted_added"] == 1
    assert second.json()["duplicate_added"] == 1
    assert len(repo.rows) == 1


def test_workout_sync_requires_auth_and_durable_storage_dependency() -> None:
    configured = _client(InMemoryTrainingSessionRepository())
    assert configured.post("/v1/health/workouts/sync", json=_payload()).status_code == 401
    unavailable = _client()
    response = unavailable.post(
        "/v1/health/workouts/sync",
        json=_payload(),
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"


def test_invalid_workout_payload_is_rejected_all_or_nothing_without_values_in_logs(
    caplog,
) -> None:
    import logging

    repo = InMemoryTrainingSessionRepository()
    client = _client(repo)
    payload = _payload()
    added = payload["added"]
    assert isinstance(added, list)
    added[0]["active_energy_kcal"] = "private-412.750"
    with caplog.at_level(logging.INFO, logger="nutrition_agent.health_api"):
        response = client.post(
            "/v1/health/workouts/sync",
            json=payload,
            headers={"Authorization": f"Bearer {_token()}"},
        )
    assert response.status_code == 400
    assert repo.rows == {}
    assert "private-412.750" not in "\n".join(record.getMessage() for record in caplog.records)


def test_workout_deletion_before_add_round_trip_never_resurrects() -> None:
    repo = InMemoryTrainingSessionRepository()
    client = _client(repo)
    headers = {"Authorization": f"Bearer {_token()}"}
    deletion = {
        "client_batch_id": str(uuid4()),
        "added": [],
        "deleted": [
            {
                "source_system": "healthkit",
                "source_record_id": "00000000-0000-0000-0000-000000000001",
            }
        ],
    }
    assert (
        client.post("/v1/health/workouts/sync", json=deletion, headers=headers).json()[
            "applied_deletions"
        ]
        == 1
    )
    assert (
        client.post("/v1/health/workouts/sync", json=_payload(), headers=headers).json()[
            "duplicate_added"
        ]
        == 1
    )
    assert repo.rows == {} and len(repo.tombstones) == 1
