"""HTTP generation proof using accepted SQL menu pages and RLS plan storage."""

from __future__ import annotations

import hashlib
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
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion
from nutrition_agent.domain.stacks.entities import MealPeriod
from tests.integration.test_menu_day_read_sql import _accept_page
from tests.integration.test_menu_page_version_sql import (
    FETCHED_AT,
    _apply_migrations,
    _unique_service_date,
)

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")
SECRET = "generation-sql-test-secret-for-hs256-signing"
AUDIENCE = "authenticated"
GOALS = [
    {"kind": "target", "nutrient": "calories_kcal", "value": "900", "weight": "1"},
    {"kind": "target", "nutrient": "protein_g", "value": "50", "weight": "1"},
]

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; generation test requires scratch Postgres",
)


class _Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _friday() -> date:
    candidate = _unique_service_date()
    return candidate + timedelta(days=(4 - candidate.weekday()) % 7)


def _token(subject: UUID) -> str:
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


def test_post_generation_uses_sql_menu_reader_rls_storage_and_replay() -> None:
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    _apply_migrations()
    service_date = _friday()
    for index, period in enumerate(MealPeriod):
        _accept_page(
            service_date=service_date,
            period=period,
            fetched_at=FETCHED_AT + timedelta(seconds=index),
        )

    user_id = uuid4()
    policy_id = uuid4()
    from nutrition_agent.db.sql_repos import SqlTargetPolicyRepository

    SqlTargetPolicyRepository(DATABASE_URL).save_approved(
        TargetPolicyVersion(
            version_id=policy_id,
            user_id=user_id,
            policy_version=f"generation-sql-{uuid4().hex}",
            goals_jsonb=[dict(goal) for goal in GOALS],
            payload_sha256=hashlib.sha256(repr(GOALS).encode()).hexdigest(),
            created_at=datetime.now(UTC),
        ),
        rationale="integration proof",
        decided_by_clock=datetime.now(UTC),
    )

    settings = HealthApiSettings(
        database_url=DATABASE_URL,
        jwt_secret=SECRET,
        supabase_url=None,
        jwt_audience=AUDIENCE,
    )
    health = HealthBodyMassSyncUseCase(
        HealthSyncDeps(repository=InMemoryHealthBodyMassRepository(), clock=_Clock())
    )
    app = create_health_app(
        HealthApiDeps(settings, TokenVerifier(settings), health),
        database_url=DATABASE_URL,
    )
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {_token(user_id)}"}
    body = {"date": service_date.isoformat(), "timezone": "America/New_York"}

    first = client.post("/v1/plans/day/generate", json=body, headers=headers)
    replay = client.post("/v1/plans/day/generate", json=body, headers=headers)

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["state"] == "no_plan"
    assert first.json()["reason_codes"] == ["empty_menu_period"]

    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT run_id, target_policy_version_id, status, inputs_fingerprint
            FROM plan_run
            WHERE user_id=%s AND requested_for_date=%s
            """,
            (str(user_id), service_date),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert str(rows[0][0]) == first.json()["run_id"]
    assert UUID(str(rows[0][1])) == policy_id
    assert rows[0][2] == "no_plan"
    assert rows[0][3] == first.json()["inputs_fingerprint"]
