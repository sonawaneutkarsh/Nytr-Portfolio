"""Historical plan-policy enrichment proof against PostgreSQL RLS storage."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.db.in_memory_repos import InMemoryHealthBodyMassRepository
from nutrition_agent.domain.planning.artifacts import (
    PlanItem,
    PlanRun,
    PlanRunStatus,
    PlanVersion,
    TargetPolicyVersion,
)
from tests.integration.test_plan_persistence_sql import _apply_migrations

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")
SECRET = "plan-enrichment-sql-test-secret-for-hs256"
AUDIENCE = "authenticated"
PLAN_DATE = date(2037, 8, 21)
GENERATED_AT = datetime(2037, 8, 21, 12, 0, tzinfo=UTC)

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; enrichment test requires Postgres",
)


class _Clock:
    def now(self) -> datetime:
        return GENERATED_AT


def _token(user_id: UUID) -> str:
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


def test_completed_get_resolves_pinned_policy_not_current_latest() -> None:
    assert DATABASE_URL is not None
    _apply_migrations()
    from nutrition_agent.db.sql_repos import SqlPlanRunRepository, SqlTargetPolicyRepository

    user_id = uuid4()
    policy_one = TargetPolicyVersion(
        version_id=uuid4(),
        user_id=user_id,
        policy_version="sql-historical-p1",
        goals_jsonb=[
            {"nutrient": "calories_kcal", "kind": "target", "value": "900", "weight": "1"}
        ],
        payload_sha256="1" * 64,
        created_at=GENERATED_AT - timedelta(hours=2),
    )
    policies = SqlTargetPolicyRepository(DATABASE_URL)
    policies.save_approved(
        policy_one,
        rationale="first",
        decided_by_clock=policy_one.created_at,
    )
    run = PlanRun(
        run_id=uuid4(),
        user_id=user_id,
        requested_for_date=PLAN_DATE,
        timezone="America/New_York",
        inputs_fingerprint=uuid4().hex,
        status=PlanRunStatus.COMPLETED,
        reason_codes=(),
        started_at=GENERATED_AT,
        finished_at=GENERATED_AT,
        target_policy_version_id=policy_one.version_id,
    )
    item_id = uuid4()
    candidate_id = "sql-candidate-1"
    artifact = {
        "artifact_kind": "daily_plan",
        "status": "ok",
        "slots": [{"candidates": [{"candidate_id": candidate_id}]}],
    }
    version = PlanVersion(
        version_id=uuid4(),
        run_id=run.run_id,
        plan_jsonb=artifact,
        plan_canonical=(
            '{"artifact_kind":"daily_plan","slots":[{"candidates":'
            '[{"candidate_id":"sql-candidate-1"}]}],"status":"ok"}'
        ),
        plan_sha256="a" * 64,
    )
    item = PlanItem(
        item_id=item_id,
        version_id=version.version_id,
        slot_index=0,
        context="post_workout_lunch",
        rank=1,
        candidate_id=candidate_id,
        menu_period="Lunch",
        food_ids=(),
        offering_ids=(),
        profile_row_ids=(),
        profile_content_sha256s=(),
        score_total="1",
        calories_kcal="900",
    )
    SqlPlanRunRepository(DATABASE_URL).save(run, version, (item,))
    policy_two = TargetPolicyVersion(
        version_id=uuid4(),
        user_id=user_id,
        policy_version="sql-current-p2",
        goals_jsonb=[
            {"nutrient": "calories_kcal", "kind": "target", "value": "950", "weight": "1"}
        ],
        payload_sha256="2" * 64,
        created_at=GENERATED_AT + timedelta(hours=1),
    )
    policies.save_approved(
        policy_two,
        rationale="second",
        decided_by_clock=policy_two.created_at,
    )
    assert policies.latest_approved(user_id) == policy_two

    settings = HealthApiSettings(
        database_url=DATABASE_URL,
        jwt_secret=SECRET,
        supabase_url=None,
        jwt_audience=AUDIENCE,
    )
    health = HealthBodyMassSyncUseCase(HealthSyncDeps(InMemoryHealthBodyMassRepository(), _Clock()))
    app = create_health_app(
        HealthApiDeps(settings, TokenVerifier(settings), health),
        database_url=DATABASE_URL,
    )
    response = TestClient(app).get(
        f"/v1/plans/day?date={PLAN_DATE.isoformat()}",
        headers={"Authorization": f"Bearer {_token(user_id)}"},
    )

    assert response.status_code == 200
    assert response.json()["target_policy"] == {
        "version_id": str(policy_one.version_id),
        "policy_version": policy_one.policy_version,
        "payload_sha256": policy_one.payload_sha256,
        "approved_at": policy_one.created_at.isoformat(),
    }
    assert response.json()["generated_at"] == GENERATED_AT.isoformat()
    assert response.json()["plan"] == artifact
    assert response.json()["plan_sha256"] == "a" * 64
    assert response.json()["plan_items"] == [
        {
            "item_id": str(item_id),
            "slot_index": 0,
            "rank": 1,
            "candidate_id": candidate_id,
        }
    ]
