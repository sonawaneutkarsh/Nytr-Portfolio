"""M7 Step 7 API tests for latest approved target-policy reads."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.routes_planning import get_planning_targets
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.db.in_memory_repos import (
    InMemoryHealthBodyMassRepository,
    InMemoryTargetPolicyRepository,
)
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion

SECRET = "target-policy-read-test-secret-for-hs256"
AUDIENCE = "authenticated"
USER_A = UUID("00000000-0000-0000-0000-0000000000a1")
USER_B = UUID("00000000-0000-0000-0000-0000000000b2")
APPROVED_AT = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return APPROVED_AT


def _token(user_id: UUID = USER_A) -> str:
    issued = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "aud": AUDIENCE,
            "iat": int(issued.timestamp()),
            "exp": int((issued + timedelta(minutes=10)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )


def _settings() -> HealthApiSettings:
    return HealthApiSettings(
        database_url=None,
        jwt_secret=SECRET,
        supabase_url=None,
        jwt_audience=AUDIENCE,
    )


def _app() -> tuple[TestClient, InMemoryTargetPolicyRepository]:
    settings = _settings()
    health = HealthBodyMassSyncUseCase(HealthSyncDeps(InMemoryHealthBodyMassRepository(), _Clock()))
    app = create_health_app(HealthApiDeps(settings, TokenVerifier(settings), health))
    targets = InMemoryTargetPolicyRepository()
    app.state.planning_target_repository = targets
    app.dependency_overrides[get_planning_targets] = lambda: targets
    return TestClient(app), targets


def _headers(user_id: UUID = USER_A) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id)}"}


def _seed(
    targets: InMemoryTargetPolicyRepository,
    *,
    user_id: UUID = USER_A,
    value: str = "901.2300",
) -> TargetPolicyVersion:
    policy = TargetPolicyVersion(
        version_id=UUID(int=701),
        user_id=user_id,
        policy_version="lean-bulk-display.v1",
        goals_jsonb=[
            {
                "nutrient": "calories_kcal",
                "kind": "target",
                "value": value,
                "weight": "1.000",
            }
        ],
        payload_sha256="a" * 64,
        created_at=APPROVED_AT,
    )
    targets.save_approved(policy, rationale="approved", decided_by_clock=APPROVED_AT)
    return policy


def test_latest_policy_requires_valid_authentication() -> None:
    client, _ = _app()

    missing = client.get("/v1/target-policies/latest")
    invalid = client.get(
        "/v1/target-policies/latest",
        headers={"Authorization": "Bearer garbage"},
    )

    assert missing.status_code == invalid.status_code == 401
    assert missing.json()["error"]["code"] == "invalid_token"
    assert invalid.json()["error"]["code"] == "invalid_token"


def test_no_approved_policy_returns_200_null() -> None:
    client, _ = _app()
    response = client.get("/v1/target-policies/latest", headers=_headers())
    assert response.status_code == 200
    assert response.json() is None


def test_latest_policy_returns_exact_metadata_and_decimal_strings() -> None:
    client, targets = _app()
    policy = _seed(targets)

    response = client.get("/v1/target-policies/latest", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {
        "version_id": str(policy.version_id),
        "policy_version": policy.policy_version,
        "goals": policy.goals_jsonb,
        "payload_sha256": policy.payload_sha256,
        "approved_at": APPROVED_AT.isoformat(),
    }
    assert response.json()["goals"][0]["value"] == "901.2300"
    assert response.json()["goals"][0]["weight"] == "1.000"
    assert isinstance(response.json()["goals"][0]["value"], str)


def test_latest_policy_is_owner_scoped_without_cross_user_disclosure() -> None:
    client, targets = _app()
    _seed(targets, user_id=USER_A)

    owner = client.get("/v1/target-policies/latest", headers=_headers(USER_A))
    other = client.get("/v1/target-policies/latest", headers=_headers(USER_B))

    assert owner.status_code == other.status_code == 200
    assert owner.json() is not None
    assert other.json() is None


def test_latest_policy_without_dsn_fails_closed() -> None:
    settings = _settings()
    health = HealthBodyMassSyncUseCase(HealthSyncDeps(InMemoryHealthBodyMassRepository(), _Clock()))
    app = create_health_app(HealthApiDeps(settings, TokenVerifier(settings), health))

    response = TestClient(app).get("/v1/target-policies/latest", headers=_headers())

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"


def test_latest_policy_logging_redacts_identity_and_goal_values(
    caplog,
) -> None:
    client, targets = _app()
    sensitive_value = "987.65432109"
    _seed(targets, value=sensitive_value)
    token = _token()

    with caplog.at_level(logging.INFO, logger="nutrition_agent.planning_api"):
        response = client.get(
            "/v1/target-policies/latest",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "subject_hash" in joined
    for sensitive in (token, str(USER_A), sensitive_value, "calories_kcal"):
        assert sensitive not in joined
