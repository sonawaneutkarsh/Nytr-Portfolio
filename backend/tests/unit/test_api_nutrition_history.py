"""Authenticated M13B seven-day nutrition history API tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import jwt
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.routes_nutrition import get_nutrition_history_use_case
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.application.nutrition_history import GetNutritionHistory7DayUseCase
from nutrition_agent.db.in_memory_repos import (
    InMemoryHealthBodyMassRepository,
    InMemoryTargetPolicyRepository,
)
from nutrition_agent.domain.nutrition.ledger import (
    ConsumedNutritionEvidence,
    NutritionAuthority,
)
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion

SECRET = "nutrition-history-api-secret-for-hs256"
AUDIENCE = "authenticated"
USER = UUID("00000000-0000-0000-0000-0000000000a1")


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 7, 12, tzinfo=UTC)


class _Evidence:
    def list_eaten_evidence(
        self,
        user_id: UUID,
        start_inclusive: datetime,
        end_exclusive: datetime,
    ) -> tuple[ConsumedNutritionEvidence, ...]:
        assert user_id == USER
        assert start_inclusive < end_exclusive
        return (
            ConsumedNutritionEvidence(
                entry_id=UUID(int=1),
                recorded_at=datetime(2026, 9, 7, 16, tzinfo=UTC),
                plan_run_id=UUID(int=2),
                plan_version_id=UUID(int=3),
                plan_item_id=UUID(int=4),
                meal_context="lunch",
                candidate_id="candidate-1",
                item_name="Chicken meal",
                configuration_summary=None,
                authority=NutritionAuthority.OFFICIAL,
                confidence="official_published",
                calories_kcal=Decimal("500.25"),
                protein_g=Decimal("30.5"),
                unknown_nutrients=(),
                provenance_summary="Frozen historical nutrition",
            ),
        )


class _BrokenEvidence(_Evidence):
    def list_eaten_evidence(
        self,
        user_id: UUID,
        start_inclusive: datetime,
        end_exclusive: datetime,
    ) -> tuple[ConsumedNutritionEvidence, ...]:
        del user_id, start_inclusive, end_exclusive
        raise RuntimeError("private storage detail")


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


def _client(evidence: _Evidence | None = None) -> TestClient:
    settings = HealthApiSettings(
        database_url=None,
        jwt_secret=SECRET,
        supabase_url=None,
        jwt_audience=AUDIENCE,
    )
    health = HealthBodyMassSyncUseCase(HealthSyncDeps(InMemoryHealthBodyMassRepository(), _Clock()))
    app = create_health_app(HealthApiDeps(settings, TokenVerifier(settings), health))
    targets = InMemoryTargetPolicyRepository()
    policy = TargetPolicyVersion(
        version_id=UUID(int=10),
        user_id=USER,
        policy_version="owner-target.v1",
        goals_jsonb=[
            {
                "nutrient": "calories_kcal",
                "kind": "target",
                "value": "2500",
                "weight": "1",
            },
            {
                "nutrient": "protein_g",
                "kind": "floor",
                "value": "150",
                "weight": "1",
            },
        ],
        payload_sha256="a" * 64,
        created_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    assert policy.created_at is not None
    targets.save_approved(policy, "owner approved", policy.created_at)
    use_case = GetNutritionHistory7DayUseCase(evidence or _Evidence(), targets)
    app.state.nutrition_history_use_case = use_case
    app.dependency_overrides[get_nutrition_history_use_case] = lambda: use_case
    return TestClient(app)


def test_history_requires_auth_and_preserves_decimal_strings() -> None:
    path = "/v1/nutrition/history?end_date=2026-09-07&timezone=America%2FNew_York"
    client = _client()
    assert client.get(path).status_code == 401

    response = client.get(path, headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert (body["start_date"], body["end_date"]) == ("2026-09-01", "2026-09-07")
    assert len(body["days"]) == 7
    last = body["days"][-1]
    assert last["consumed_event_count"] == 1
    assert "consumed_item_count" not in last
    assert last["known_calories_consumed"] == "500.25"
    assert last["known_protein_g_consumed"] == "30.5"
    assert last["target_status"] == "available"
    assert last["calorie_target"] == "2500"
    assert last["protein_target_g"] == "150"
    assert last["calorie_adherence"] == "below_target"
    assert body["summary"]["known_calories_total"] == "500.25"


def test_history_rejects_invalid_timezone_and_hides_storage_details() -> None:
    invalid = _client().get(
        "/v1/nutrition/history?end_date=2026-09-07&timezone=Mars%2FOlympus",
        headers=_headers(),
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_nutrition_history_request"

    failed = _client(_BrokenEvidence()).get(
        "/v1/nutrition/history?end_date=2026-09-07&timezone=America%2FNew_York",
        headers=_headers(),
    )
    assert failed.status_code == 503
    assert failed.json() == {"error": {"code": "storage_unavailable", "detail": "retry later"}}
