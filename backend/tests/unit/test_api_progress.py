"""Authenticated M18 evidence-bounded progress API tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import jwt
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.routes_progress import get_progress_use_case
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.application.progress import GetLongitudinalProgressUseCase
from nutrition_agent.db.in_memory_repos import (
    InMemoryGoalPolicyRepository,
    InMemoryHealthBodyMassRepository,
    InMemoryTargetPolicyRepository,
)
from nutrition_agent.domain.nutrition.ledger import (
    ConsumedNutritionEvidence,
    NutritionAuthority,
)

SECRET = "progress-api-secret-for-hs256-tests"
AUDIENCE = "authenticated"
USER = UUID("00000000-0000-0000-0000-0000000000a1")


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 8, 12, tzinfo=UTC)


class _Body:
    def list_active(
        self,
        user_id: UUID,
        start_inclusive: datetime,
        end_exclusive: datetime,
    ) -> tuple[object, ...]:
        assert user_id == USER
        assert start_inclusive < end_exclusive
        return ()


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
                recorded_at=datetime(2026, 9, 8, 16, tzinfo=UTC),
                plan_run_id=UUID(int=2),
                plan_version_id=UUID(int=3),
                plan_item_id=UUID(int=4),
                meal_context="lunch",
                candidate_id="candidate-1",
                item_name="Recorded meal",
                configuration_summary=None,
                authority=NutritionAuthority.ESTIMATED,
                confidence="estimated",
                calories_kcal=Decimal("500.25"),
                protein_g=None,
                unknown_nutrients=("protein_g",),
                provenance_summary="Frozen historical evidence",
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
        raise RuntimeError("private database detail")


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
    use_case = GetLongitudinalProgressUseCase(
        body_mass=_Body(),
        consumption=evidence or _Evidence(),
        targets=InMemoryTargetPolicyRepository(),
        goals=InMemoryGoalPolicyRepository(),
    )
    app.state.longitudinal_progress_use_case = use_case
    app.dependency_overrides[get_progress_use_case] = lambda: use_case
    return TestClient(app)


def test_progress_requires_auth_and_returns_fixed_evidence_bounded_windows() -> None:
    path = "/v1/analytics/progress?as_of_date=2026-09-08&timezone=America%2FNew_York"
    client = _client()
    assert client.get(path).status_code == 401

    response = client.get(path, headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["policy_version"] == "owner-longitudinal-progress.v1"
    assert body["as_of_date"] == "2026-09-08"
    assert body["timezone"] == "America/New_York"
    assert body["body"]["window_days"] == 90
    assert (body["body"]["start_date"], body["body"]["end_date"]) == (
        "2026-06-11",
        "2026-09-08",
    )
    assert body["goal"] == {
        "status": "unavailable",
        "mode": None,
        "desired_rate_kg_per_week": None,
        "observed_rate_kg_per_week": None,
        "acceptable_rate_lower_kg_per_week": None,
        "acceptable_rate_upper_kg_per_week": None,
    }
    nutrition = body["nutrition"]
    assert nutrition["window_days"] == 28
    assert (nutrition["start_date"], nutrition["end_date"]) == (
        "2026-08-12",
        "2026-09-08",
    )
    assert len(nutrition["days"]) == 28
    last = nutrition["days"][-1]
    assert last["recorded_calories"] == {"state": "quantified", "value_kcal": "500.25"}
    assert last["recorded_protein"] == {"state": "unavailable", "value_g": None}
    assert last["recorded_calorie_target_comparison"] == "unavailable"
    assert last["includes_estimates"] is True
    assert nutrition["summary_7d"]["recorded_calories"] == {
        "quantified_recorded_days": 1,
        "partial_recorded_days": 0,
        "unavailable_recorded_days": 0,
        "average_recorded_kcal": "500.25",
        "average_denominator_days": 1,
    }
    assert nutrition["summary_28d"]["coverage"] == {
        "days_with_recorded_events": 1,
        "days_without_recorded_events": 27,
    }
    assert body["limitations"]["codes"] == [
        "recorded_events_do_not_prove_complete_intake",
        "consumption_is_attributed_by_server_recorded_time",
        "nutrition_and_body_weight_are_descriptive_not_causal",
    ]
    assert "user_id" not in response.text


def test_progress_validates_query_without_exposing_an_owner_selector() -> None:
    client = _client()
    missing = client.get("/v1/analytics/progress", headers=_headers())
    assert missing.status_code == 422

    invalid = client.get(
        "/v1/analytics/progress?as_of_date=2026-09-08&timezone=Mars%2FOlympus",
        headers=_headers(),
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_progress_request"

    operation = client.get("/openapi.json").json()["paths"]["/v1/analytics/progress"]["get"]
    assert {item["name"] for item in operation["parameters"]} == {
        "as_of_date",
        "timezone",
        "authorization",
    }


def test_progress_hides_storage_details() -> None:
    response = _client(_BrokenEvidence()).get(
        "/v1/analytics/progress?as_of_date=2026-09-08&timezone=UTC",
        headers=_headers(),
    )
    assert response.status_code == 503
    assert response.json() == {"error": {"code": "storage_unavailable", "detail": "retry later"}}
