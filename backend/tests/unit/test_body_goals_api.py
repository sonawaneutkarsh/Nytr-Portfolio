from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID

import jwt
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.body_goals import SaveBodyGoalProfileUseCase
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.db.in_memory_repos import InMemoryHealthBodyMassRepository
from nutrition_agent.domain.body_goals import (
    PROFILE_POLICY_VERSION,
    ActivityLevel,
    BodyGoalProfileVersion,
    FormulaSex,
    profile_digest,
)

SECRET = "body-goals-api-secret-for-hs256-tests"
OWNER = UUID("00000000-0000-0000-0000-000000000053")


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 12, 12, tzinfo=UTC)


class ProfileUseCase:
    def __init__(self) -> None:
        self.owners: list[UUID] = []

    def execute(
        self,
        *,
        user_id: UUID,
        height_cm: Decimal,
        date_of_birth: date,
        formula_sex: FormulaSex,
        activity_level: ActivityLevel,
        target_weight_kg: Decimal | None,
    ) -> BodyGoalProfileVersion:
        self.owners.append(user_id)
        return BodyGoalProfileVersion(
            UUID(int=1),
            user_id,
            PROFILE_POLICY_VERSION,
            height_cm,
            date_of_birth,
            formula_sex,
            activity_level,
            target_weight_kg,
            profile_digest(height_cm, date_of_birth, formula_sex, activity_level, target_weight_kg),
            Clock().now(),
        )


def headers() -> dict[str, str]:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(OWNER),
            "aud": "authenticated",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def client() -> tuple[TestClient, ProfileUseCase]:
    settings = HealthApiSettings(None, SECRET, None, "authenticated")
    health = InMemoryHealthBodyMassRepository()
    app = create_health_app(
        HealthApiDeps(
            settings,
            TokenVerifier(settings),
            HealthBodyMassSyncUseCase(HealthSyncDeps(health, Clock())),
            body_mass_repository=health,
        )
    )
    use_case = ProfileUseCase()
    app.state.body_goals_save_profile_use_case = cast(SaveBodyGoalProfileUseCase, use_case)
    return TestClient(app), use_case


def test_profile_is_authenticated_and_owner_is_derived_from_jwt() -> None:
    api, use_case = client()
    payload = {
        "height_cm": "175",
        "date_of_birth": "1995-06-01",
        "formula_sex": "male",
        "activity_level": "lightly_active",
        "target_weight_kg": None,
    }
    assert api.post("/v1/body-goals/profile", json=payload).status_code == 401
    response = api.post("/v1/body-goals/profile", json=payload, headers=headers())
    assert response.status_code == 201
    assert response.json()["provenance"] == "owner_entered"
    assert use_case.owners == [OWNER]


def test_profile_rejects_client_supplied_owner_and_unknown_fields() -> None:
    api, use_case = client()
    response = api.post(
        "/v1/body-goals/profile",
        json={
            "height_cm": "175",
            "date_of_birth": "1995-06-01",
            "formula_sex": "male",
            "activity_level": "sedentary",
            "target_weight_kg": None,
            "user_id": str(UUID(int=99)),
        },
        headers=headers(),
    )
    assert response.status_code == 422
    assert use_case.owners == []
