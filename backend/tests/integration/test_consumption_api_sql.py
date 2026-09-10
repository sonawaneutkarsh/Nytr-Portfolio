"""HTTP consumption proof against PostgreSQL RLS and append-only storage."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.db.in_memory_repos import InMemoryHealthBodyMassRepository
from tests.integration.test_consumption_repository_sql import _apply_migrations, _persist_graph

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")
SECRET = "consumption-api-sql-test-secret-for-hs256"
AUDIENCE = "authenticated"

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; consumption API test requires Postgres",
)


class _Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


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


def _headers(user_id: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id)}"}


def test_consumption_http_sql_replay_listing_and_cross_user_isolation() -> None:
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    _apply_migrations()
    graph = _persist_graph(uuid4())
    attacker = uuid4()
    event_id = uuid4()

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
    client = TestClient(app)
    path = f"/v1/plans/{graph.run_id}/consumption"
    body = {
        "plan_version_id": str(graph.version_id),
        "item_id": str(graph.item_ids[0]),
        "state": "eaten",
        "client_event_id": str(event_id),
    }

    first = client.post(path, json=body, headers=_headers(graph.user_id))
    replay = client.post(path, json=body, headers=_headers(graph.user_id))
    owner_list = client.get(path, headers=_headers(graph.user_id))
    foreign_list = client.get(path, headers=_headers(attacker))
    foreign_post = client.post(path, json=body, headers=_headers(attacker))

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert owner_list.status_code == 200
    assert owner_list.json() == {"entries": [first.json()]}
    assert foreign_list.status_code == 200
    assert foreign_list.json() == {"entries": []}
    assert foreign_post.status_code == 404
    assert foreign_post.json()["error"]["code"] == "consumption_target_not_found"

    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT entry_id, user_id, plan_run_id, plan_version_id, item_id,
                   state, client_event_id, recorded_at
            FROM plan_consumption
            WHERE plan_run_id=%s
            """,
            (str(graph.run_id),),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert str(rows[0][0]) == first.json()["entry_id"]
    assert UUID(str(rows[0][1])) == graph.user_id
    assert UUID(str(rows[0][2])) == graph.run_id
    assert UUID(str(rows[0][3])) == graph.version_id
    assert UUID(str(rows[0][4])) == graph.item_ids[0]
    assert rows[0][5] == "eaten"
    assert UUID(str(rows[0][6])) == event_id
    assert rows[0][7].isoformat() == first.json()["recorded_at"]
