"""Production-composition proof for the authenticated M9 trend API."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from nutrition_agent.api.app import create_health_app
from nutrition_agent.db.sql_repos import SqlHealthBodyMassRepository
from nutrition_agent.domain.health.entities import BodyMassSample, SyncBatch
from tests.migration_helpers import apply_migrations

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; requires scratch Postgres",
)

SECRET = "test-secret-for-hs256-signing-only-32-bytes-min"
AUDIENCE = "authenticated"
USER_A = UUID("00000000-0000-0000-0000-000000009a11")
USER_B = UUID("00000000-0000-0000-0000-000000009b12")
AS_OF = date(2026, 8, 28)


def _token(user_id: UUID) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "aud": AUDIENCE,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )


def _sample(sample_id: int, day: date, value: str) -> BodyMassSample:
    measured_at = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    return BodyMassSample(
        sample_uuid=UUID(int=sample_id),
        value_kg=Decimal(value),
        sample_start=measured_at,
        sample_end=measured_at,
    )


def _counts() -> tuple[int, ...]:
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        values: list[int] = []
        for table in (
            "health_body_mass_sample",
            "target_policy_version",
            "decision_log",
            "plan_run",
        ):
            cur.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608 - fixed test identifiers
            values.append(int(cur.fetchone()[0]))
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables"
            " WHERE table_schema='public' AND table_name LIKE %s",
            ("%trend%",),
        )
        values.append(int(cur.fetchone()[0]))
        return tuple(values)


def test_production_trend_api_is_owner_scoped_and_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        cur.execute(
            "DELETE FROM health_body_mass_sample WHERE user_id IN (%s, %s)",
            (str(USER_A), str(USER_B)),
        )

    repo = SqlHealthBodyMassRepository(DATABASE_URL)
    repo.apply_batch(
        USER_A,
        SyncBatch(
            UUID(int=0xA),
            tuple(
                _sample(index + 1, AS_OF + timedelta(days=offset), value)
                for index, (offset, value) in enumerate(
                    zip(
                        (-20, -18, -16, -14, -12, -6, 0),
                        ("68.1", "68.2", "68.3", "68.4", "68.5", "68.6", "68.7"),
                        strict=True,
                    )
                )
            ),
            (),
        ),
    )
    repo.apply_batch(
        USER_B,
        SyncBatch(UUID(int=0xB), (_sample(100, AS_OF, "99.999"),), ()),
    )
    before = _counts()

    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", AUDIENCE)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    client = TestClient(create_health_app(database_url=DATABASE_URL))
    response = client.get(
        "/v1/health/body-mass/trend",
        params={"as_of_date": AS_OF.isoformat(), "timezone": "UTC"},
        headers={"Authorization": f"Bearer {_token(USER_A)}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["represented_day_count"] == 7
    assert "99.999" not in response.text
    assert _counts() == before
