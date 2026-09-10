"""Authenticated API proofs for M14C compute-on-read training analytics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import jwt
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.application.training_analytics import (
    GetExerciseIndexUseCase,
    GetExerciseTrainingHistoryUseCase,
    GetRecentTrainingAnalyticsUseCase,
)
from nutrition_agent.db.in_memory_repos import (
    InMemoryDetailedTrainingRepository,
    InMemoryHealthBodyMassRepository,
)
from nutrition_agent.domain.training.detail import (
    DetailedTrainingImportBatch,
    DetailedTrainingSession,
    DetailedTrainingSet,
    DetailedTrainingSourceSystem,
    ExercisePerformance,
    TrainingLoad,
    TrainingLoadUnit,
    TrainingSetType,
)

SECRET = "test-secret-for-m14c-api-only-32-bytes"
AUDIENCE = "authenticated"
USER_A = UUID(int=0xA)
USER_B = UUID(int=0xB)
NOW = datetime(2026, 9, 4, 12, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


def _token(user_id: UUID) -> str:
    return jwt.encode(
        {
            "sub": str(user_id),
            "aud": AUDIENCE,
            "iat": int(NOW.timestamp()),
            "exp": int((NOW + timedelta(days=365)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )


def _session() -> DetailedTrainingSession:
    return DetailedTrainingSession(
        source_system=DetailedTrainingSourceSystem.HEVY,
        source_session_id="api-session",
        source_revision="api-revision",
        title="Pull 1",
        started_at=datetime(2026, 9, 3, 14, tzinfo=UTC),
        ended_at=datetime(2026, 9, 3, 15, tzinfo=UTC),
        exercises=(
            ExercisePerformance(
                occurrence_identity="api-row:0",
                source_exercise_id="api-row",
                display_name="Cable Row",
                exercise_order=0,
                sets=(
                    DetailedTrainingSet(
                        set_identity="0:0",
                        set_index=0,
                        set_type=TrainingSetType.NORMAL,
                        reps=10,
                        load=TrainingLoad(Decimal("36.28743275485118"), TrainingLoadUnit.KILOGRAM),
                    ),
                ),
            ),
        ),
        parser_version="hevy-public-api.v1",
        source_payload_sha256="a" * 64,
        source_updated_at=NOW,
    )


def _client(*, with_analytics: bool = True) -> TestClient:
    settings = HealthApiSettings(None, SECRET, None, AUDIENCE)
    verifier = TokenVerifier(settings)
    health = HealthBodyMassSyncUseCase(HealthSyncDeps(InMemoryHealthBodyMassRepository(), _Clock()))
    detail = InMemoryDetailedTrainingRepository(ids=lambda: UUID(int=100))
    detail.apply_import(USER_A, DetailedTrainingImportBatch((_session(),), (), True), NOW)
    deps = HealthApiDeps(
        settings,
        verifier,
        health,
        training_analytics_recent=(
            GetRecentTrainingAnalyticsUseCase(detail) if with_analytics else None
        ),
        training_analytics_index=(GetExerciseIndexUseCase(detail) if with_analytics else None),
        training_analytics_history=(
            GetExerciseTrainingHistoryUseCase(detail) if with_analytics else None
        ),
    )
    return TestClient(create_health_app(deps))


def _headers(user_id: UUID = USER_A) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id)}"}


def test_recent_requires_auth_and_returns_factual_session_metrics() -> None:
    client = _client()
    assert client.get("/v1/training/analytics/recent").status_code == 401

    response = client.get("/v1/training/analytics/recent", headers=_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["policy_version"] == "owner-training-analytics.v1"
    assert body["sessions"][0]["title"] == "Pull 1"
    assert body["sessions"][0]["volume_kg_reps"] == "362.87432754851180"


def test_exercise_index_is_owner_scoped_and_ignores_client_user_id() -> None:
    client = _client()
    own = client.get(
        "/v1/training/exercises",
        params={
            "as_of_date": "2026-09-04",
            "timezone": "America/New_York",
            "user_id": str(USER_B),
        },
        headers=_headers(USER_A),
    )
    other = client.get(
        "/v1/training/exercises",
        params={"as_of_date": "2026-09-04", "timezone": "UTC"},
        headers=_headers(USER_B),
    )

    assert own.status_code == 200
    assert own.json()["exercises"][0]["source_exercise_id"] == "api-row"
    assert other.status_code == 200 and other.json()["exercises"] == []


def test_history_uses_source_identity_and_returns_delta_pr_scope() -> None:
    response = _client().get(
        "/v1/training/exercises/api-row/history",
        params={
            "source_system": "hevy",
            "as_of_date": "2026-09-04",
            "timezone": "America/New_York",
            "limit": 50,
        },
        headers=_headers(),
    )

    assert response.status_code == 200
    coaching = response.json()["coaching"]
    assert coaching["policy_version"] == "owner-training-coaching.v1"
    assert coaching["analytics_policy_version"] == "owner-training-analytics.v1"
    assert coaching["status"] == "unavailable"
    assert coaching["action"] is None
    assert coaching["target"] is None
    assert coaching["reason_codes"] == ["history_incomplete"]
    body = response.json()
    assert body["source_exercise_id"] == "api-row"
    assert body["comparison"]["reason_codes"] == ["no_previous_session"]
    assert body["pr_evidence"][0]["scope"] == "within_bounded_synced_hevy_history"
    assert body["completeness"]["lifetime_guaranteed"] is False


def test_invalid_timezone_missing_identity_bounds_and_storage_fail_closed() -> None:
    client = _client()
    invalid = client.get(
        "/v1/training/exercises",
        params={"as_of_date": "2026-09-04", "timezone": "not/a-zone"},
        headers=_headers(),
    )
    missing = client.get(
        "/v1/training/exercises/unknown/history",
        params={"as_of_date": "2026-09-04", "timezone": "UTC"},
        headers=_headers(),
    )
    bounded = client.get(
        "/v1/training/analytics/recent",
        params={"limit": 51},
        headers=_headers(),
    )
    unavailable = _client(with_analytics=False).get(
        "/v1/training/analytics/recent",
        headers=_headers(),
    )

    assert invalid.status_code == 400
    assert missing.status_code == 404
    assert bounded.status_code == 422
    assert unavailable.status_code == 503


def test_recent_owner_isolation() -> None:
    client = _client()
    response = client.get("/v1/training/analytics/recent", headers=_headers(USER_B))
    assert response.status_code == 200
    assert response.json()["sessions"] == []


def test_empty_source_exercise_id_returns_404() -> None:
    client = _client()
    response = client.get(
        "/v1/training/exercises//history",
        params={
            "source_system": "hevy",
            "as_of_date": "2026-09-04",
            "timezone": "UTC",
        },
        headers=_headers(),
    )
    assert response.status_code in (400, 404)
