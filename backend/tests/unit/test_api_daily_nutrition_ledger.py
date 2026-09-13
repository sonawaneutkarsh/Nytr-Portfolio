"""Authenticated M13A daily nutrition ledger API tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import jwt
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.routes_nutrition import get_daily_nutrition_ledger_use_case
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.daily_nutrition_ledger import GetDailyNutritionLedgerUseCase
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.db.in_memory_repos import (
    InMemoryHealthBodyMassRepository,
    InMemoryTargetPolicyRepository,
)
from nutrition_agent.domain.nutrition.ledger import (
    ConsumedNutritionEvidence,
    NutritionAuthority,
)
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion

SECRET = "daily-ledger-api-secret-for-hs256"
AUDIENCE = "authenticated"
USER = UUID("00000000-0000-0000-0000-0000000000a1")


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 4, 12, tzinfo=UTC)


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
                recorded_at=datetime(2026, 9, 4, 16, tzinfo=UTC),
                plan_run_id=UUID(int=2),
                plan_version_id=UUID(int=3),
                plan_item_id=UUID(int=4),
                meal_context="post_workout_lunch",
                candidate_id="candidate-1",
                item_name="CYO Halal Bowl",
                configuration_summary="rice, chicken, quinoa, eggs; no sauce",
                authority=NutritionAuthority.PARTIAL,
                confidence="partial",
                calories_kcal=Decimal("604.27781682500"),
                protein_g=Decimal("45.7386516982500"),
                unknown_nutrients=("sodium_mg",),
                provenance_summary=(
                    "Owner-observed configuration with external reference nutrition"
                ),
                serving_description="one bowl",
                serving_amount=Decimal("1"),
                serving_unit="serving",
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


def _settings() -> HealthApiSettings:
    return HealthApiSettings(
        database_url=None,
        jwt_secret=SECRET,
        supabase_url=None,
        jwt_audience=AUDIENCE,
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


def _client(evidence: _Evidence | None = None) -> TestClient:
    settings = _settings()
    health = HealthBodyMassSyncUseCase(HealthSyncDeps(InMemoryHealthBodyMassRepository(), _Clock()))
    app = create_health_app(HealthApiDeps(settings, TokenVerifier(settings), health))
    targets = InMemoryTargetPolicyRepository()
    targets.save_approved(
        TargetPolicyVersion(
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
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
        ),
        rationale="owner approved",
        decided_by_clock=datetime(2026, 9, 1, tzinfo=UTC),
    )
    use_case = GetDailyNutritionLedgerUseCase(evidence or _Evidence(), targets)
    app.state.daily_nutrition_ledger_use_case = use_case
    app.dependency_overrides[get_daily_nutrition_ledger_use_case] = lambda: use_case
    return TestClient(app)


def test_daily_ledger_requires_auth_and_returns_exact_decimal_strings() -> None:
    client = _client()
    assert (
        client.get(
            "/v1/nutrition/daily-ledger?date=2026-09-04&timezone=America%2FNew_York"
        ).status_code
        == 401
    )

    response = client.get(
        "/v1/nutrition/daily-ledger?date=2026-09-04&timezone=America%2FNew_York",
        headers=_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["known_calories_consumed"] == "604.27781682500"
    assert body["known_protein_g_consumed"] == "45.7386516982500"
    assert body["remaining_known_calories"] == "1895.72218317500"
    assert body["nutrition_completeness"] == "partial"
    assert body["nutrition_authorities"] == ["partial"]
    assert body["consumed_items"][0]["plan_item_id"] == str(UUID(int=4))
    assert body["consumed_items"][0]["configuration_summary"] is not None
    assert body["consumed_items"][0]["serving_amount"] == "1"
    assert body["consumed_items"][0]["serving_unit"] == "serving"


def test_invalid_timezone_and_storage_failure_are_safe() -> None:
    invalid = _client().get(
        "/v1/nutrition/daily-ledger?date=2026-09-04&timezone=Mars%2FOlympus",
        headers=_headers(),
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_daily_ledger_request"

    failed = _client(_BrokenEvidence()).get(
        "/v1/nutrition/daily-ledger?date=2026-09-04&timezone=America%2FNew_York",
        headers=_headers(),
    )
    assert failed.status_code == 503
    assert failed.json() == {"error": {"code": "storage_unavailable", "detail": "retry later"}}
