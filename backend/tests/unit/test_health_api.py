"""API tests for the M5 health endpoints (offline; HS256 tokens signed locally)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.health_sync import (
    HealthBodyMassSyncUseCase,
    HealthSyncDeps,
)
from nutrition_agent.db.in_memory_repos import InMemoryHealthBodyMassRepository

SECRET = "test-secret-for-hs256-signing-only-32-bytes-min"
AUDIENCE = "authenticated"
SUBJECT_A = "00000000-0000-0000-0000-0000000000a1"
SUBJECT_B = "00000000-0000-0000-0000-0000000000b2"


class _FixedClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


def _token(subject: str = SUBJECT_A, **claims: object) -> str:
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "sub": subject,
        "aud": AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=10)).timestamp()),
    }
    payload.update(claims)
    return jwt.encode(payload, SECRET, algorithm="HS256")


def _sample_raw(uuid_int: int = 1) -> dict[str, object]:
    return {
        "sample_uuid": f"00000000-0000-0000-0000-{uuid_int:012x}",
        "value": "68.039",
        "sample_start": "2026-08-21T07:12:00+00:00",
        "sample_end": "2026-08-21T07:12:00+00:00",
    }


@pytest.fixture()
def client() -> TestClient:
    settings = HealthApiSettings(
        database_url=None,
        jwt_secret=SECRET,
        supabase_url=None,
        jwt_audience=AUDIENCE,
    )
    verifier = TokenVerifier(settings)
    repo = InMemoryHealthBodyMassRepository()
    use_case = HealthBodyMassSyncUseCase(HealthSyncDeps(repo, _FixedClock()))
    app = create_health_app(HealthApiDeps(settings, verifier, use_case))
    return TestClient(app)


def _sync(client: TestClient, token: str | None, **payload: object):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = {"client_batch_id": str(uuid4()), **payload}
    return client.post("/v1/health/body-mass/sync", json=body, headers=headers)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def test_missing_token_rejected_401(client: TestClient) -> None:
    response = _sync(client, None, added=[_sample_raw()])
    assert response.status_code == 401


def test_garbage_token_rejected_401(client: TestClient) -> None:
    assert _sync(client, "not.a.jwt", added=[]).status_code == 401


def test_expired_token_rejected_401(client: TestClient) -> None:
    expired = _token(exp=int((datetime.now(UTC) - timedelta(hours=1)).timestamp()))
    assert _sync(client, expired, added=[]).status_code == 401


def test_wrong_audience_token_rejected_401(client: TestClient) -> None:
    assert _sync(client, _token(aud="someone-else"), added=[]).status_code == 401


def test_wrong_signature_rejected_401(client: TestClient) -> None:
    forged = jwt.encode(
        {"sub": SUBJECT_A, "aud": AUDIENCE},
        "some-other-secret-that-is-long-enough-32b",
        algorithm="HS256",
    )
    assert _sync(client, forged, added=[]).status_code == 401


def test_auth_not_configured_yields_503_not_bypass() -> None:
    settings = HealthApiSettings(
        database_url=None, jwt_secret=None, supabase_url=None, jwt_audience=AUDIENCE
    )
    repo = InMemoryHealthBodyMassRepository()
    use_case = HealthBodyMassSyncUseCase(HealthSyncDeps(repo, _FixedClock()))
    client = TestClient(
        create_health_app(HealthApiDeps(settings, TokenVerifier(settings), use_case))
    )
    response = _sync(client, _token(), added=[])
    assert response.status_code == 503


def test_asymmetric_verifier_uses_supabase_auth_jwks_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_urls: list[str] = []

    class _RecordingJWKClient:
        def __init__(self, url: str) -> None:
            requested_urls.append(url)

    monkeypatch.setattr("nutrition_agent.api.auth.PyJWKClient", _RecordingJWKClient)
    settings = HealthApiSettings(
        database_url=None,
        jwt_secret=None,
        supabase_url="https://project-ref.supabase.co",
        jwt_audience=AUDIENCE,
    )

    TokenVerifier(settings)

    assert requested_urls == ["https://project-ref.supabase.co/auth/v1/.well-known/jwks.json"]


# ---------------------------------------------------------------------------
# Sync endpoint behavior
# ---------------------------------------------------------------------------


def test_valid_batch_accepted_with_counts_and_latest(client: TestClient) -> None:
    response = _sync(client, _token(), added=[_sample_raw()])
    assert response.status_code == 200
    body = response.json()
    assert body["accepted_added"] == 1
    assert body["duplicate_added"] == 0
    latest = body["latest_sample"]
    assert latest is not None and latest["value_kg"] == "68.039"


def test_duplicate_batch_upload_is_idempotent(client: TestClient) -> None:
    first = _sync(client, _token(), added=[_sample_raw()])
    second = _sync(client, _token(), added=[_sample_raw()])
    third = _sync(client, _token(), added=[_sample_raw()])
    assert (first.json()["accepted_added"], second.json()["duplicate_added"]) == (1, 1)
    assert third.json()["duplicate_added"] == 1


def test_user_isolation_between_two_subjects(client: TestClient) -> None:
    assert _sync(client, _token(), added=[_sample_raw()]).status_code == 200
    # Same sample UUID, different subject: distinct rows.
    other = _sync(client, _token(SUBJECT_B), added=[_sample_raw()])
    assert other.status_code == 200

    status_a = client.get("/v1/health/sync-status", headers={"Authorization": f"Bearer {_token()}"})
    status_b = client.get(
        "/v1/health/sync-status", headers={"Authorization": f"Bearer {_token(SUBJECT_B)}"}
    )
    assert status_a.json()["record_count"] == 1
    assert status_b.json()["record_count"] == 1


def test_domain_rejection_maps_to_400(client: TestClient) -> None:
    bad = _sample_raw()
    bad["value"] = "999"  # out of bounds
    response = _sync(client, _token(), added=[bad])
    assert response.status_code == 400
    errors = response.json()["error"]["detail"]
    assert any("outside" in e["reason"] for e in errors)


def test_malformed_json_types_map_to_400(client: TestClient) -> None:
    # Non-list `added` is a semantic (not schema-shape) error: the domain
    # validator rejects it, so it maps to 400 rather than 422.
    response = _sync(client, _token(), added="not-a-list")
    assert response.status_code == 400


def test_malformed_body_maps_to_422(client: TestClient) -> None:
    response = client.post(
        "/v1/health/body-mass/sync",
        content=b"{definitely not json",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_token()}",
        },
    )
    assert response.status_code == 422


def test_oversize_batch_maps_to_413(client: TestClient) -> None:
    big = [_sample_raw(i) for i in range(501)]
    assert _sync(client, _token(), added=big).status_code == 413


def test_deletion_round_trip_via_api(client: TestClient) -> None:
    sample_uuid = "00000000-0000-0000-0000-000000000001"
    assert _sync(client, _token(), added=[_sample_raw()]).status_code == 200
    deleted = _sync(client, _token(), deleted=[{"sample_uuid": sample_uuid}])
    assert deleted.status_code == 200
    assert deleted.json()["applied_deletions"] == 1

    status = client.get(
        "/v1/health/sync-status", headers={"Authorization": f"Bearer {_token()}"}
    ).json()
    assert status["tombstone_count"] == 1
    assert status["latest_sample"] is None  # no active samples remain


def test_sync_status_requires_auth(client: TestClient) -> None:
    assert client.get("/v1/health/sync-status", headers={}).status_code == 401


def test_no_weight_values_in_logs(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.INFO, logger="nutrition_agent.health_api"):
        _sync(client, _token(), added=[_sample_raw()])
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "68.039" not in joined
    for record in caplog.records:
        for arg in record.args or ():
            assert "68.039" not in str(arg)


def test_healthz_liveness_leaks_no_configuration(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_schema_lists_only_expected_routes(client: TestClient) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    assert paths == {
        "/healthz",
        "/v1/body-goals",
        "/v1/body-goals/profile",
        "/v1/body-goals/waist",
        "/v1/body-goals/starting-target/proposals",
        "/v1/body-goals/starting-target/proposals/{proposal_id}/decision",
        "/v1/analytics/progress",
        "/v1/goal-policies",
        "/v1/goal-policies/latest",
        "/v1/health/body-mass/trend",
        "/v1/health/body-mass/sync",
        "/v1/health/sync-status",
        "/v1/health/workouts/sync",
        "/v1/nutrition/daily-ledger",
        "/v1/nutrition/history",
        "/v1/nutrition/custom-foods",
        "/v1/nutrition/custom-foods/{food_id}/versions",
        "/v1/nutrition/barcodes/{barcode}",
        "/v1/nutrition/barcodes/{barcode}/import",
        "/v1/nutrition/manual-consumption",
        "/v1/nutrition/manual-consumption/{entry_id}/preview-correction",
        "/v1/nutrition/manual-consumption/{entry_id}/corrections",
        "/v1/nutrition/manual-consumption/{entry_id}/void",
        "/v1/recommendations/next-meal",
        "/v1/recommendations/next-meal/latest",
        "/v1/recommendations/next-meal/{recommendation_id}/consumption",
        "/v1/review/current",
        "/v1/review/snapshot",
        "/v1/nutrition/food-preview",
        "/v1/plans/day",
        "/v1/plans/day/generate",
        "/v1/plans/{run_id}/consumption",
        "/v1/target-policies",
        "/v1/target-policies/latest",
        "/v1/target-policies/protein-proposals",
        "/v1/target-policies/protein-proposals/latest",
        "/v1/target-policies/protein-proposals/{proposal_id}/decision",
        "/v1/target-reviews",
        "/v1/target-reviews/{review_id}/decision",
        "/v1/training/analytics/recent",
        "/v1/training/exercises",
        "/v1/training/exercises/{source_exercise_id}/history",
        "/v1/training/hevy/sync",
        "/v1/training/sessions",
        "/v1/training/sessions/{revision_id}",
    }
