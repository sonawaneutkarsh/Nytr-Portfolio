"""M7 Step 2 migration, append-only privilege, and adversarial RLS proofs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from tests.migration_helpers import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[3] / "backend" / "migrations"
MIGRATION_0004 = MIGRATIONS / "0004_consumption.sql"
DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; migration test requires scratch Postgres",
)


@dataclass(frozen=True)
class _PlanGraph:
    user_id: str
    run_id: str
    version_id: str
    item_id: str


def _apply_migrations() -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)


def _apply_0004() -> None:
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:  # type: ignore[name-defined]
        cur.execute(MIGRATION_0004.read_text(encoding="utf-8"))


def _admin():
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    return psycopg.connect(DATABASE_URL)  # type: ignore[name-defined]


def _as_user(user_sub: str):
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    conn = psycopg.connect(DATABASE_URL)  # type: ignore[name-defined]
    cur = conn.cursor()
    cur.execute("SET LOCAL ROLE authenticated")
    cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (user_sub,))
    return conn, cur


def _insert_plan_graph(user_id: str) -> _PlanGraph:
    graph = _PlanGraph(
        user_id=user_id,
        run_id=str(uuid4()),
        version_id=str(uuid4()),
        item_id=str(uuid4()),
    )
    conn, cur = _as_user(user_id)
    try:
        cur.execute(
            """
            INSERT INTO plan_run (
                run_id, user_id, requested_for_date, timezone,
                inputs_fingerprint, status, started_at, finished_at)
            VALUES (%s,%s,'2026-08-21','America/New_York',%s,
                    'completed',now(),now())
            """,
            (graph.run_id, graph.user_id, f"consumption-{uuid4().hex}"),
        )
        cur.execute(
            """
            INSERT INTO plan_version (
                version_id, run_id, plan_jsonb, plan_canonical, plan_sha256)
            VALUES (%s,%s,'{}'::jsonb,'{}',%s)
            """,
            (graph.version_id, graph.run_id, f"plan-{uuid4().hex}"),
        )
        cur.execute(
            """
            INSERT INTO plan_item (
                item_id, version_id, slot_index, context, rank, candidate_id,
                menu_period, food_ids, offering_ids, profile_ids,
                score_total, calories_kcal)
            VALUES (%s,%s,0,'post_workout_lunch',1,%s,'Lunch',
                    '{}'::uuid[],'{}'::uuid[],'{}'::uuid[],'1','900')
            """,
            (graph.item_id, graph.version_id, f"candidate-{uuid4().hex}"),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return graph


def _insert_consumption(
    cur,
    *,
    user_id: str,
    run_id: str,
    version_id: str,
    item_id: str,
    state: str = "eaten",
    client_event_id: str | None = None,
    entry_id: str | None = None,
) -> tuple[str, str]:
    stored_entry_id = entry_id or str(uuid4())
    stored_event_id = client_event_id or str(uuid4())
    cur.execute(
        """
        INSERT INTO plan_consumption (
            entry_id, user_id, plan_run_id, plan_version_id, item_id,
            state, client_event_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            stored_entry_id,
            user_id,
            run_id,
            version_id,
            item_id,
            state,
            stored_event_id,
        ),
    )
    return stored_entry_id, stored_event_id


def _admin_consumption_row(entry_id: str):
    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_id, plan_run_id, plan_version_id, item_id,
                   state, client_event_id, recorded_at
            FROM plan_consumption WHERE entry_id=%s
            """,
            (entry_id,),
        )
        return cur.fetchone()


def test_0004_applies_reapplies_and_has_expected_security_metadata() -> None:
    _apply_migrations()
    _apply_0004()
    _apply_migrations()

    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT is_nullable, data_type
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name='plan_run'
              AND column_name='target_policy_version_id'
            """
        )
        assert cur.fetchone() == ("YES", "uuid")

        cur.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid='plan_run'::regclass
              AND conname='fk_plan_run_target_policy_version'
            """
        )
        target_fk = cur.fetchone()
        assert target_fk is not None
        assert target_fk[0] == (
            "FOREIGN KEY (target_policy_version_id) REFERENCES target_policy_version(version_id)"
        )

        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name='plan_consumption'
            ORDER BY ordinal_position
            """
        )
        assert [row[0] for row in cur.fetchall()] == [
            "entry_id",
            "user_id",
            "plan_run_id",
            "plan_version_id",
            "item_id",
            "state",
            "client_event_id",
            "recorded_at",
        ]

        cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid='plan_consumption'::regclass")
        assert cur.fetchone()[0] is True
        cur.execute("SELECT policyname FROM pg_policies WHERE tablename='plan_consumption'")
        assert {row[0] for row in cur.fetchall()} == {"plan_consumption_owner_all"}

        for privilege, expected in (
            ("SELECT", True),
            ("INSERT", True),
            ("UPDATE", False),
            ("DELETE", False),
        ):
            cur.execute(
                "SELECT has_table_privilege('authenticated', 'plan_consumption', %s)",
                (privilege,),
            )
            assert cur.fetchone()[0] is expected

        cur.execute(
            """
            SELECT indexdef FROM pg_indexes
            WHERE schemaname='public' AND tablename='plan_consumption'
              AND indexname='ix_plan_consumption_user_run'
            """
        )
        assert "(user_id, plan_run_id)" in cur.fetchone()[0]


def test_target_policy_reference_is_nullable_and_foreign_keyed() -> None:
    _apply_migrations()
    psycopg = pytest.importorskip("psycopg")
    user_id = str(uuid4())
    policy_id = str(uuid4())
    null_run_id = str(uuid4())
    pinned_run_id = str(uuid4())

    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO target_policy_version (
                version_id, user_id, policy_version, goals, payload_sha256)
            VALUES (%s,%s,%s,'[]'::jsonb,%s)
            """,
            (policy_id, user_id, f"policy-{uuid4().hex}", f"payload-{uuid4().hex}"),
        )
        cur.execute(
            """
            INSERT INTO plan_run (
                run_id, user_id, requested_for_date, timezone,
                inputs_fingerprint, status, started_at, finished_at,
                target_policy_version_id)
            VALUES (%s,%s,'2026-08-21','America/New_York',%s,
                    'no_plan',now(),now(),NULL)
            """,
            (null_run_id, user_id, f"legacy-{uuid4().hex}"),
        )
        cur.execute(
            """
            INSERT INTO plan_run (
                run_id, user_id, requested_for_date, timezone,
                inputs_fingerprint, status, started_at, finished_at,
                target_policy_version_id)
            VALUES (%s,%s,'2026-08-22','America/New_York',%s,
                    'completed',now(),now(),%s)
            """,
            (pinned_run_id, user_id, f"pinned-{uuid4().hex}", policy_id),
        )

    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT target_policy_version_id FROM plan_run WHERE run_id=%s",
            (null_run_id,),
        )
        assert cur.fetchone()[0] is None
        cur.execute(
            "SELECT target_policy_version_id FROM plan_run WHERE run_id=%s",
            (pinned_run_id,),
        )
        assert str(cur.fetchone()[0]) == policy_id

    conn = _admin()
    try:
        cur = conn.cursor()
        with pytest.raises(psycopg.errors.ForeignKeyViolation):  # type: ignore[attr-defined]
            cur.execute(
                """
                INSERT INTO plan_run (
                    run_id, user_id, requested_for_date, timezone,
                    inputs_fingerprint, status, started_at, finished_at,
                    target_policy_version_id)
                VALUES (%s,%s,'2026-08-23','America/New_York',%s,
                        'completed',now(),now(),%s)
                """,
                (str(uuid4()), user_id, f"dangling-{uuid4().hex}", str(uuid4())),
            )
        conn.rollback()
    finally:
        conn.close()


def test_consumption_state_foreign_keys_and_idempotency_constraint() -> None:
    _apply_migrations()
    psycopg = pytest.importorskip("psycopg")
    graph = _insert_plan_graph(str(uuid4()))

    accepted: list[str] = []
    with _admin() as conn, conn.cursor() as cur:
        for state in ("eaten", "skipped", "unavailable", "alternative"):
            entry_id, _ = _insert_consumption(
                cur,
                user_id=graph.user_id,
                run_id=graph.run_id,
                version_id=graph.version_id,
                item_id=graph.item_id,
                state=state,
            )
            accepted.append(entry_id)
    assert len(accepted) == 4

    conn = _admin()
    try:
        cur = conn.cursor()
        with pytest.raises(psycopg.errors.CheckViolation):  # type: ignore[attr-defined]
            _insert_consumption(
                cur,
                user_id=graph.user_id,
                run_id=graph.run_id,
                version_id=graph.version_id,
                item_id=graph.item_id,
                state="consumed",
            )
        conn.rollback()
    finally:
        conn.close()

    bad_references = (
        (str(uuid4()), graph.version_id, graph.item_id),
        (graph.run_id, str(uuid4()), graph.item_id),
        (graph.run_id, graph.version_id, str(uuid4())),
    )
    for run_id, version_id, item_id in bad_references:
        conn = _admin()
        try:
            cur = conn.cursor()
            with pytest.raises(psycopg.errors.ForeignKeyViolation):  # type: ignore[attr-defined]
                _insert_consumption(
                    cur,
                    user_id=graph.user_id,
                    run_id=run_id,
                    version_id=version_id,
                    item_id=item_id,
                )
            conn.rollback()
        finally:
            conn.close()

    event_id = str(uuid4())
    with _admin() as conn, conn.cursor() as cur:
        _insert_consumption(
            cur,
            user_id=graph.user_id,
            run_id=graph.run_id,
            version_id=graph.version_id,
            item_id=graph.item_id,
            client_event_id=event_id,
        )

    conn = _admin()
    try:
        cur = conn.cursor()
        with pytest.raises(psycopg.errors.UniqueViolation):  # type: ignore[attr-defined]
            _insert_consumption(
                cur,
                user_id=graph.user_id,
                run_id=graph.run_id,
                version_id=graph.version_id,
                item_id=graph.item_id,
                state="skipped",
                client_event_id=event_id,
            )
        conn.rollback()
    finally:
        conn.close()


def test_owner_insert_select_and_append_only_privileges() -> None:
    _apply_migrations()
    psycopg = pytest.importorskip("psycopg")
    graph = _insert_plan_graph(str(uuid4()))
    conn, cur = _as_user(graph.user_id)
    try:
        entry_id, event_id = _insert_consumption(
            cur,
            user_id=graph.user_id,
            run_id=graph.run_id,
            version_id=graph.version_id,
            item_id=graph.item_id,
            state="eaten",
        )
        conn.commit()
    finally:
        conn.close()

    conn, cur = _as_user(graph.user_id)
    try:
        cur.execute(
            "SELECT state, recorded_at FROM plan_consumption WHERE entry_id=%s",
            (entry_id,),
        )
        state, recorded_at = cur.fetchone()
        assert state == "eaten"
        assert recorded_at is not None
        conn.commit()
    finally:
        conn.close()

    conn, cur = _as_user(graph.user_id)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
            cur.execute(
                "UPDATE plan_consumption SET state='skipped' WHERE entry_id=%s",
                (entry_id,),
            )
        conn.rollback()
    finally:
        conn.close()

    conn, cur = _as_user(graph.user_id)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
            cur.execute("DELETE FROM plan_consumption WHERE entry_id=%s", (entry_id,))
        conn.rollback()
    finally:
        conn.close()

    stored = _admin_consumption_row(entry_id)
    assert stored is not None
    assert stored[4] == "eaten"
    assert str(stored[5]) == event_id
    assert stored[6] == recorded_at


def test_rls_blocks_cross_user_and_mixed_parent_attacks() -> None:
    _apply_migrations()
    psycopg = pytest.importorskip("psycopg")
    graph_a = _insert_plan_graph(str(uuid4()))
    graph_b = _insert_plan_graph(str(uuid4()))
    graph_b_alt = _insert_plan_graph(graph_b.user_id)

    conn, cur = _as_user(graph_a.user_id)
    try:
        entry_a, _ = _insert_consumption(
            cur,
            user_id=graph_a.user_id,
            run_id=graph_a.run_id,
            version_id=graph_a.version_id,
            item_id=graph_a.item_id,
            state="unavailable",
        )
        conn.commit()
    finally:
        conn.close()
    original_a = _admin_consumption_row(entry_a)

    conn, cur = _as_user(graph_b.user_id)
    try:
        cur.execute("SELECT COUNT(*) FROM plan_consumption WHERE entry_id=%s", (entry_a,))
        assert cur.fetchone()[0] == 0
        conn.commit()
    finally:
        conn.close()

    # The declared row owner is independently protected: B cannot claim A's
    # user_id even when every referenced parent belongs consistently to A.
    conn, cur = _as_user(graph_b.user_id)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
            _insert_consumption(
                cur,
                user_id=graph_a.user_id,
                run_id=graph_a.run_id,
                version_id=graph_a.version_id,
                item_id=graph_a.item_id,
            )
        conn.rollback()
    finally:
        conn.close()
    assert _admin_consumption_row(entry_a) == original_a

    attacks = (
        ("foreign_run", graph_a.run_id, graph_b.version_id, graph_b.item_id),
        ("foreign_version", graph_b.run_id, graph_a.version_id, graph_b.item_id),
        ("foreign_item", graph_b.run_id, graph_b.version_id, graph_a.item_id),
        ("b_run_a_version", graph_b.run_id, graph_a.version_id, graph_a.item_id),
        ("b_run_b_version_a_item", graph_b.run_id, graph_b.version_id, graph_a.item_id),
        ("a_run_b_version_b_item", graph_a.run_id, graph_b.version_id, graph_b.item_id),
        (
            "same_owner_cross_parent",
            graph_b.run_id,
            graph_b_alt.version_id,
            graph_b_alt.item_id,
        ),
    )
    for _, run_id, version_id, item_id in attacks:
        conn, cur = _as_user(graph_b.user_id)
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):  # type: ignore[attr-defined]
                _insert_consumption(
                    cur,
                    user_id=graph_b.user_id,
                    run_id=run_id,
                    version_id=version_id,
                    item_id=item_id,
                )
            conn.rollback()
        finally:
            conn.close()
        assert _admin_consumption_row(entry_a) == original_a

    with _admin() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM plan_consumption WHERE user_id=%s", (graph_b.user_id,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(*) FROM plan_consumption WHERE entry_id=%s", (entry_a,))
        assert cur.fetchone()[0] == 1
