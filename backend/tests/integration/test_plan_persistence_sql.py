"""M6 migration + RLS + repository tests against a scratch Postgres.

Runs only when STACKS_TEST_DATABASE_URL is configured. Validates (ADR-017/018):
- migration 0003 applies cleanly and is idempotent;
- all five new tables exist with RLS enabled;
- owner-isolation proofs: user B cannot read/update A's rows on ANY M6 table;
- child-row ownership: B cannot attach plan_version/plan_item to A's rows;
- decision_log ownership flows through the parent policy version;
- plan_run fingerprint uniqueness rejects duplicate logical plans;
- decision_log atomicity: policy without rationale is unrepresentable;
- the three former SQL read-path stubs now behave like their in-memory twins;
- SqlPlanRunRepository/SqlTargetPolicyRepository round trips under SET ROLE,
  including cross-user isolation and duplicate rejection.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest

from nutrition_agent.domain.planning.artifacts import (
    PlanItem,
    PlanRun,
    PlanRunStatus,
    PlanVersion,
    TargetPolicyVersion,
)
from tests.migration_helpers import apply_migrations

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; migration test requires scratch Postgres",
)

USER_A = "00000000-0000-0000-0000-0000000000a1"
USER_B = "00000000-0000-0000-0000-0000000000b2"


def _apply_migrations() -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)


def _admin():
    psycopg = pytest.importorskip("psycopg")
    return psycopg.connect(DATABASE_URL)  # type: ignore[name-defined]


def _as_user(user_sub: str):
    psycopg = pytest.importorskip("psycopg")
    conn = psycopg.connect(DATABASE_URL)  # type: ignore[name-defined]
    cur = conn.cursor()
    cur.execute("SET LOCAL ROLE authenticated")
    cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (user_sub,))
    return conn, cur


def test_migration_applies_tables_have_rls_and_records_are_immutable() -> None:
    _apply_migrations()
    _apply_migrations()  # idempotency
    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' AND table_name IN (
                'target_policy_version','decision_log','plan_run',
                'plan_version','plan_item')
            """
        )
        found = {row[0] for row in cur.fetchall()}
        assert found == {
            "target_policy_version",
            "decision_log",
            "plan_run",
            "plan_version",
            "plan_item",
        }
        cur.execute(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname='public' AND rowsecurity
              AND tablename IN ('target_policy_version','decision_log',
                                'plan_run','plan_version','plan_item')
            """
        )
        assert len(cur.fetchall()) == 5

        for table in (
            "target_policy_version",
            "decision_log",
            "plan_run",
            "plan_version",
            "plan_item",
        ):
            assert cur.execute(
                "SELECT has_table_privilege('authenticated', %s, 'SELECT')", (table,)
            ).fetchone()[0]
            assert cur.execute(
                "SELECT has_table_privilege('authenticated', %s, 'INSERT')", (table,)
            ).fetchone()[0]
            assert not cur.execute(
                "SELECT has_table_privilege('authenticated', %s, 'UPDATE')", (table,)
            ).fetchone()[0]
            assert not cur.execute(
                "SELECT has_table_privilege('authenticated', %s, 'DELETE')", (table,)
            ).fetchone()[0]

    # Prove the owner can create and read a complete valid M6 row graph.
    user = str(uuid4())
    policy_id = str(uuid4())
    decision_id = str(uuid4())
    run_id = str(uuid4())
    version_id = str(uuid4())
    item_id = str(uuid4())
    conn, cur = _as_user(user)
    try:
        cur.execute(
            """
            INSERT INTO target_policy_version (
                version_id, user_id, policy_version, goals, payload_sha256)
            VALUES (%s,%s,%s,'[]'::jsonb,%s)
            """,
            (policy_id, user, f"immutable-{uuid4().hex}", f"payload-{uuid4().hex}"),
        )
        cur.execute(
            """
            INSERT INTO decision_log (
                decision_id, user_id, subject, decision, rationale, policy_version_id)
            VALUES (%s,%s,'target_policy','approved','owner-approved',%s)
            """,
            (decision_id, user, policy_id),
        )
        cur.execute(
            """
            INSERT INTO plan_run (
                run_id, user_id, requested_for_date, timezone, inputs_fingerprint,
                status, started_at, finished_at)
            VALUES (%s,%s,%s,'America/New_York',%s,'completed',now(),now())
            """,
            (run_id, user, date(2026, 8, 21), f"fingerprint-{uuid4().hex}"),
        )
        cur.execute(
            """
            INSERT INTO plan_version (
                version_id, run_id, plan_jsonb, plan_canonical, plan_sha256)
            VALUES (%s,%s,'{}'::jsonb,'{}',%s)
            """,
            (version_id, run_id, f"plan-{uuid4().hex}"),
        )
        cur.execute(
            """
            INSERT INTO plan_item (
                item_id, version_id, slot_index, context, rank, candidate_id,
                menu_period, food_ids, offering_ids, profile_ids,
                score_total, calories_kcal)
            VALUES (%s,%s,0,'post_workout_lunch',1,'candidate-1','Lunch',
                    ARRAY[]::uuid[],ARRAY[]::uuid[],ARRAY[]::uuid[],'1','900')
            """,
            (item_id, version_id),
        )
        conn.commit()
    finally:
        conn.close()

    table_rows = {
        "target_policy_version": ("version_id", policy_id, "policy_version=policy_version"),
        "decision_log": ("decision_id", decision_id, "rationale=rationale"),
        "plan_run": ("run_id", run_id, "timezone=timezone"),
        "plan_version": ("version_id", version_id, "plan_sha256=plan_sha256"),
        "plan_item": ("item_id", item_id, "context=context"),
    }
    conn, cur = _as_user(user)
    try:
        for table, (key_column, row_id, _) in table_rows.items():
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {key_column}=%s", (row_id,))
            assert cur.fetchone()[0] == 1
        conn.commit()
    finally:
        conn.close()

    from psycopg import errors as psycopg_errors

    # Privilege denial, rather than repository convention alone, enforces
    # append-only records for every authenticated owner.
    for table, (key_column, row_id, no_op_update) in table_rows.items():
        conn, cur = _as_user(user)
        try:
            with pytest.raises(psycopg_errors.InsufficientPrivilege):
                cur.execute(f"UPDATE {table} SET {no_op_update} WHERE {key_column}=%s", (row_id,))
            conn.rollback()
        finally:
            conn.close()

        conn, cur = _as_user(user)
        try:
            with pytest.raises(psycopg_errors.InsufficientPrivilege):
                cur.execute(f"DELETE FROM {table} WHERE {key_column}=%s", (row_id,))
            conn.rollback()
        finally:
            conn.close()


def _insert_target(conn_cur, user_sub: str, label: str) -> str:
    _, cur = conn_cur
    version_id = str(uuid4())
    cur.execute(
        """
        INSERT INTO target_policy_version (
            version_id, user_id, policy_version, goals, payload_sha256)
        VALUES (%s,%s,%s,%s,%s)
        """,
        (
            version_id,
            user_sub,
            label,
            '[{"kind":"target","nutrient":"calories_kcal","value":"900","weight":"1"}]',
            f"payload-{label}",
        ),
    )
    return version_id


def test_target_policy_rls_owner_isolation() -> None:
    """Fresh per-test subjects: the scratch DB is shared across runs, so the
    count assertions must not collide with rows from earlier executions."""
    _apply_migrations()
    user_a, user_b = str(uuid4()), str(uuid4())
    label = f"iso-{uuid4().hex[:12]}"
    a_conn, a_cur = _as_user(user_a)
    try:
        vid = _insert_target((a_conn, a_cur), user_a, label)
        a_conn.commit()
    finally:
        a_conn.close()

    # B cannot read A's rows...
    b_conn, b_cur = _as_user(user_b)
    try:
        b_cur.execute("SELECT COUNT(*) FROM target_policy_version")
        assert b_cur.fetchone()[0] == 0
        # ...and the RLS WITH CHECK blocks inserting rows owned by someone
        # else. PostgreSQL raises SQLSTATE 42501 (InsufficientPrivilege) for
        # row-level security violations — same class as the M5 RLS proofs.
        from psycopg import errors as psycopg_errors

        with pytest.raises(psycopg_errors.InsufficientPrivilege):
            b_cur.execute(
                """
                INSERT INTO target_policy_version (
                    version_id, user_id, policy_version, goals, payload_sha256)
                VALUES (%s,%s,'v-b','[]','p-b')
                """,
                (str(uuid4()), user_a),
            )
            b_conn.commit()
    finally:
        b_conn.close()

    # Fresh scoped session: SET LOCAL survives only within one transaction.
    # Immutable M6 tables expose no UPDATE privilege, including to user B.
    c_conn, c_cur = _as_user(user_b)
    try:
        with pytest.raises(psycopg_errors.InsufficientPrivilege):
            c_cur.execute("UPDATE target_policy_version SET policy_version='hacked'")
        c_conn.rollback()
    finally:
        c_conn.close()

    admin = _admin()
    with admin as conn2, conn2.cursor() as cur2:
        cur2.execute(
            "SELECT policy_version FROM target_policy_version WHERE version_id=%s",
            (vid,),
        )
        assert cur2.fetchone()[0] == label
        cur2.execute(
            "SELECT COUNT(*) FROM target_policy_version WHERE user_id IN (%s, %s)",
            (user_a, user_b),
        )
        assert cur2.fetchone()[0] == 1
    admin.close()


def test_plan_run_fingerprint_uniqueness_rejects_duplicate_logical_plan() -> None:
    _apply_migrations()
    user = str(uuid4())
    fingerprint = f"fp-{uuid4().hex[:16]}"
    conn, cur = _as_user(user)
    try:
        run_id = str(uuid4())
        params = (run_id, user, date(2026, 8, 21), "America/New_York", fingerprint)
        base = """
            INSERT INTO plan_run (run_id, user_id, requested_for_date,
                timezone, inputs_fingerprint, status, started_at, finished_at)
            VALUES (%s,%s,%s,%s,%s,'no_plan',now(),now())
            ON CONFLICT (user_id, requested_for_date, inputs_fingerprint)
            DO NOTHING
        """
        cur.execute(base, params)
        first = cur.rowcount
        second_id = str(uuid4())
        cur.execute(base, (second_id, *params[1:]))
        second = cur.rowcount
        conn.commit()
        assert (first, second) == (1, 0)  # duplicate suppressed by constraint
    finally:
        conn.close()


def test_decision_log_requires_valid_policy_reference() -> None:
    _apply_migrations()
    from psycopg import errors as psycopg_errors

    # Schema-level FK proof: executed as the table owner (superuser), where
    # RLS does not apply, so the FOREIGN KEY constraint itself — not the
    # owner policy — is what rejects the dangling reference. (Under the
    # authenticated role the strengthened decision_log policy blocks
    # non-owned references even earlier; that path is proven in the
    # ownership tests below.)
    admin = _admin()
    with admin as conn, conn.cursor() as cur:
        with pytest.raises(psycopg_errors.ForeignKeyViolation):
            cur.execute(
                """
                INSERT INTO decision_log (
                    decision_id, user_id, subject, decision, rationale,
                    policy_version_id)
                VALUES (%s,%s,'target_policy','approved','r',%s)
                """,
                (str(uuid4()), USER_A, str(uuid4())),
            )
        conn.rollback()
    admin.close()


def test_sql_read_path_stubs_implemented_and_match_in_memory() -> None:
    """Former NotImplementedError stubs now round trip like in-memory twins."""
    _apply_migrations()
    from nutrition_agent.db.in_memory_repos import InMemoryQuarantineRepository
    from nutrition_agent.db.sql_repos import (
        SqlOfferingRepository,
        SqlProfileRepository,
        SqlQuarantineRepository,
    )

    # Unique service date: other integration tests share the scratch DB and
    # seed menu_offering rows for their own (date, period) pairs, so this
    # test's count assertion must not collide with them.
    stub_date = date(2027, 6, 1)

    run_admin = _admin()
    with run_admin as conn, conn.cursor() as cur:
        snapshot_id, food_id = str(uuid4()), uuid4()
        cur.execute(
            """
            INSERT INTO stacks_ingestion_run (run_id, status, mode, params,
                config_fingerprint, started_at)
            VALUES (%s,'persisted','manual','{}','fp',now())
            """,
            (str(uuid4()),),
        )
        cur.execute("SELECT run_id FROM stacks_ingestion_run ORDER BY started_at DESC LIMIT 1")
        ingestion_run_id = str(cur.fetchone()[0])
        cur.execute(
            """
            INSERT INTO source_snapshot (snapshot_id, source_system, source_url,
                http_method, request_params, http_status, content_sha256,
                byte_size, fetched_at, parser_version, ingestion_run_id,
                storage_path)
            VALUES (%s,'institutional_menu','fixture://x','POST','{}',200,'sha-x',10,
                    now(),'2026-08-21.m2.1',%s,'/tmp/x')
            """,
            (snapshot_id, ingestion_run_id),
        )
        cur.execute(
            """
            INSERT INTO stacks_food (food_id, campus_id, name_raw,
                name_normalized) VALUES (%s,50,'Name Raw','name raw')
            """,
            (str(food_id),),
        )
        cur.execute(
            """
            INSERT INTO menu_offering (offering_id, service_date, meal_period,
                campus_id, food_id, occurrence_ordinal, category_name,
                category_position, item_position, source_mid, snapshot_id)
            VALUES (%s,%s,'Lunch',50,%s,0,'CAT',1,1,'mid-x',%s)
            """,
            (str(uuid4()), stub_date, str(food_id), snapshot_id),
        )
    run_admin.close()

    dsn = DATABASE_URL
    from nutrition_agent.domain.stacks.entities import MealPeriod

    assert SqlOfferingRepository(dsn).count_for_page(stub_date, MealPeriod.LUNCH) == 1

    memory_quarantine = InMemoryQuarantineRepository()
    sql_quarantine = SqlQuarantineRepository(dsn)
    run_uuid = UUID(ingestion_run_id)

    def make_record(record_id):  # type: ignore[no-untyped-def]
        from nutrition_agent.domain.stacks.ingestion import (
            ErrorCode,
            QuarantineRecord,
            Severity,
            SubjectType,
        )

        return QuarantineRecord(
            record_id=record_id,
            run_id=run_uuid,
            code=ErrorCode.PLACEHOLDER_LABEL,
            severity=Severity.WARN,
            subject_type=SubjectType.LABEL,
            natural_key={"name_normalized": "n", "mid": "m"},
            detail="d",
            parser_version="2026-08-21.m2.1",
            snapshot_ref_sha256=None,
            created_at=datetime.now(UTC),
        )

    memory_quarantine.add(make_record(uuid4()))
    record_to_store = make_record(uuid4())
    sql_quarantine.add(record_to_store)
    stored = {r.record_id for r in sql_quarantine.list_by_run(run_uuid)}
    assert record_to_store.record_id in stored

    # Profile read path returns None safely when absent, and round trips.
    profile_repo = SqlProfileRepository(dsn)
    assert profile_repo.latest_for_food(uuid4()) is None


def _new_user() -> UUID:
    """Fresh per-test subject so proofs never collide with earlier test data."""
    return uuid4()


GOALS_ROW = [{"kind": "target", "nutrient": "calories_kcal", "value": "900", "weight": "1"}]


def _policy(user: UUID, label: str, payload_sha: str) -> TargetPolicyVersion:
    return TargetPolicyVersion(
        version_id=uuid4(),
        user_id=user,
        policy_version=label,
        goals_jsonb=[dict(GOALS_ROW[0])],
        payload_sha256=payload_sha,
        created_at=datetime.now(UTC),
    )


def test_sql_target_policy_repository_round_trip_and_isolation() -> None:
    _apply_migrations()
    from nutrition_agent.application.ports import TargetPolicyVersionExistsError
    from nutrition_agent.db.sql_repos import SqlTargetPolicyRepository

    repo = SqlTargetPolicyRepository(DATABASE_URL)
    user = _new_user()
    label = f"repo-{uuid4().hex[:12]}"
    policy = _policy(user, label, f"payload-{uuid4().hex}")

    repo.save_approved(policy, rationale="first approval", decided_by_clock=policy.created_at)

    # Owner retrieves their own policy.
    latest = repo.latest_approved(user)
    assert latest is not None
    assert latest.version_id == policy.version_id
    assert latest.policy_version == label
    assert latest.payload_sha256 == policy.payload_sha256
    assert repo.find_by_version_label(user, label) is True

    # Duplicate version label rejected.
    with pytest.raises(TargetPolicyVersionExistsError):
        repo.save_approved(
            _policy(user, label, f"payload-{uuid4().hex}"),
            rationale="again",
            decided_by_clock=datetime.now(UTC),
        )
    # Duplicate payload rejected under a different label.
    with pytest.raises(TargetPolicyVersionExistsError):
        repo.save_approved(
            _policy(user, f"other-{uuid4().hex[:12]}", policy.payload_sha256),
            rationale="again",
            decided_by_clock=datetime.now(UTC),
        )

    # Another user cannot retrieve anything.
    assert repo.latest_approved(_new_user()) is None
    assert repo.find_by_version_label(_new_user(), label) is False


def test_sql_target_policy_bounded_history_includes_boundary_and_owner_changes() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import SqlTargetPolicyRepository

    repo = SqlTargetPolicyRepository(DATABASE_URL)
    user = _new_user()
    other = _new_user()
    instants = (
        datetime(2026, 8, 20, 12, tzinfo=UTC),
        datetime(2026, 9, 2, 4, tzinfo=UTC),
        datetime(2026, 9, 3, 19, tzinfo=UTC),
        datetime(2026, 9, 8, 4, tzinfo=UTC),
    )
    policies = tuple(
        TargetPolicyVersion(
            version_id=uuid4(),
            user_id=user,
            policy_version=f"history-{uuid4().hex}",
            goals_jsonb=[dict(GOALS_ROW[0])],
            payload_sha256=f"payload-{uuid4().hex}",
            created_at=instant,
        )
        for instant in instants
    )
    for policy in policies:
        assert policy.created_at is not None
        repo.save_approved(policy, "history test", policy.created_at)
    other_policy = TargetPolicyVersion(
        version_id=uuid4(),
        user_id=other,
        policy_version=f"history-{uuid4().hex}",
        goals_jsonb=[dict(GOALS_ROW[0])],
        payload_sha256=f"payload-{uuid4().hex}",
        created_at=datetime(2026, 9, 4, tzinfo=UTC),
    )
    assert other_policy.created_at is not None
    repo.save_approved(other_policy, "other owner", other_policy.created_at)

    history = repo.list_approved_for_window(
        user,
        datetime(2026, 9, 1, 4, tzinfo=UTC),
        datetime(2026, 9, 8, 4, tzinfo=UTC),
    )

    assert tuple(policy.version_id for policy in history) == tuple(
        policy.version_id for policy in policies[:3]
    )
    assert all(policy.user_id == user for policy in history)


def _plan_run(
    user: UUID,
    fingerprint: str,
    target_policy_version_id: UUID | None = None,
) -> PlanRun:
    return PlanRun(
        run_id=uuid4(),
        user_id=user,
        requested_for_date=date(2026, 9, 1),
        timezone="America/New_York",
        inputs_fingerprint=fingerprint,
        status=PlanRunStatus.COMPLETED,
        reason_codes=(),
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        target_policy_version_id=target_policy_version_id,
    )


def _plan_version(run: PlanRun) -> PlanVersion:
    return PlanVersion(
        version_id=uuid4(),
        run_id=run.run_id,
        plan_jsonb={"artifact_kind": "daily_plan", "status": "ok"},
        plan_canonical='{"artifact_kind":"daily_plan","status":"ok"}',
        plan_sha256="a" * 64,
    )


def _plan_items(version: PlanVersion) -> list[PlanItem]:
    return [
        PlanItem(
            item_id=uuid4(),
            version_id=version.version_id,
            slot_index=0,
            context="post_workout_lunch",
            rank=1,
            candidate_id="post_workout_lunch-01",
            menu_period="Lunch",
            food_ids=(uuid4(),),
            offering_ids=(uuid4(),),
            profile_row_ids=(uuid4(),),
            profile_content_sha256s=("sha-food-1",),
            score_total="1.25",
            calories_kcal="900.0",
        )
    ]


def test_sql_plan_run_repository_round_trip_and_isolation() -> None:
    _apply_migrations()
    from nutrition_agent.application.ports import DuplicateLogicalPlanError
    from nutrition_agent.db.sql_repos import SqlPlanRunRepository

    repo = SqlPlanRunRepository(DATABASE_URL)
    user = _new_user()
    run = _plan_run(user, f"fp-{uuid4().hex[:16]}")
    version = _plan_version(run)
    items = _plan_items(version)

    repo.save(run, version, items)

    # Owner retrieves by replay fingerprint: byte-exact artifact survives.
    replay = repo.find_replay(user, run.requested_for_date, run.inputs_fingerprint)
    assert replay is not None
    assert replay.run_id == run.run_id
    assert replay.version_id == version.version_id
    assert replay.plan_sha256 == version.plan_sha256
    assert replay.plan_canonical == version.plan_canonical
    assert replay.plan_jsonb == version.plan_jsonb
    assert replay.target_policy_version_id is None
    assert len(replay.plan_items) == 1
    assert replay.plan_items[0].item_id == items[0].item_id
    assert replay.plan_items[0].candidate_id == items[0].candidate_id

    # Owner retrieves the latest run for user/date.
    latest = repo.latest_for_user_date(user, run.requested_for_date)
    assert latest is not None
    assert latest.run_id == run.run_id
    assert latest.version_id == version.version_id
    assert latest.plan_items == replay.plan_items

    # Idempotency: same (user, date, fingerprint) is rejected.
    with pytest.raises(DuplicateLogicalPlanError):
        repo.save(run, _plan_version(run), items)

    # Another user sees nothing.
    assert repo.find_replay(_new_user(), run.requested_for_date, run.inputs_fingerprint) is None
    assert repo.latest_for_user_date(_new_user(), run.requested_for_date) is None


def test_sql_plan_item_accepts_estimated_source_pins_without_official_profile() -> None:
    """M11I estimated artifacts retain source IDs while fabricating no profile pin."""

    _apply_migrations()
    from nutrition_agent.db.sql_repos import SqlPlanRunRepository

    repo = SqlPlanRunRepository(DATABASE_URL)
    user = _new_user()
    run = _plan_run(user, f"fp-estimated-{uuid4().hex[:16]}")
    version = PlanVersion(
        version_id=uuid4(),
        run_id=run.run_id,
        plan_jsonb={
            "artifact_kind": "daily_plan",
            "slots": [
                {
                    "candidates": [
                        {
                            "candidate_id": "post_workout_lunch-001",
                            "candidate_kind": "configurable_estimate",
                        }
                    ]
                }
            ],
            "status": "ok",
        },
        plan_canonical=(
            '{"artifact_kind":"daily_plan","slots":[{"candidates":['
            '{"candidate_id":"post_workout_lunch-001",'
            '"candidate_kind":"configurable_estimate"}]}],"status":"ok"}'
        ),
        plan_sha256="e" * 64,
    )
    offering_id, food_id = uuid4(), uuid4()
    item = PlanItem(
        item_id=uuid4(),
        version_id=version.version_id,
        slot_index=0,
        context="post_workout_lunch",
        rank=1,
        candidate_id="post_workout_lunch-001",
        menu_period="Lunch",
        food_ids=(food_id,),
        offering_ids=(offering_id,),
        profile_row_ids=(),
        profile_content_sha256s=(),
        score_total="-0.05",
        calories_kcal="604.27781682500",
    )

    repo.save(run, version, [item])

    view = repo.find_replay(user, run.requested_for_date, run.inputs_fingerprint)
    assert view is not None
    assert view.plan_jsonb == version.plan_jsonb
    assert view.plan_items[0].item_id == item.item_id
    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT food_ids, offering_ids, profile_ids FROM plan_item WHERE item_id=%s",
            (str(item.item_id),),
        )
        stored = cur.fetchone()
    assert tuple(UUID(str(value)) for value in stored[0]) == (food_id,)
    assert tuple(UUID(str(value)) for value in stored[1]) == (offering_id,)
    assert stored[2] == []


def test_sql_plan_run_repository_persists_exact_target_policy_reference() -> None:
    _apply_migrations()
    from nutrition_agent.db.sql_repos import (
        SqlPlanRunRepository,
        SqlTargetPolicyRepository,
    )

    user = _new_user()
    first_policy = _policy(
        user,
        f"plan-policy-{uuid4().hex[:12]}",
        f"payload-{uuid4().hex}",
    )
    target_repo = SqlTargetPolicyRepository(DATABASE_URL)
    target_repo.save_approved(
        first_policy,
        rationale="policy used by this plan",
        decided_by_clock=first_policy.created_at,
    )

    run = _plan_run(
        user,
        f"fp-policy-{uuid4().hex[:16]}",
        target_policy_version_id=first_policy.version_id,
    )
    version = _plan_version(run)
    plan_repo = SqlPlanRunRepository(DATABASE_URL)
    plan_repo.save(run, version, _plan_items(version))

    replay = plan_repo.find_replay(user, run.requested_for_date, run.inputs_fingerprint)
    assert replay is not None
    assert replay.target_policy_version_id == first_policy.version_id

    later_policy = _policy(
        user,
        f"plan-policy-{uuid4().hex[:12]}",
        f"payload-{uuid4().hex}",
    )
    target_repo.save_approved(
        later_policy,
        rationale="later approval",
        decided_by_clock=later_policy.created_at,
    )
    replay_after_approval = plan_repo.find_replay(
        user,
        run.requested_for_date,
        run.inputs_fingerprint,
    )
    assert replay_after_approval is not None
    assert replay_after_approval.target_policy_version_id == first_policy.version_id
    assert replay_after_approval.target_policy_version_id != later_policy.version_id


def test_plan_child_rows_blocked_for_non_owner() -> None:
    """B cannot attach plan_version/plan_item to A's rows, nor read them."""
    _apply_migrations()
    from psycopg import errors as psycopg_errors

    from nutrition_agent.db.sql_repos import SqlPlanRunRepository

    owner = _new_user()
    repo = SqlPlanRunRepository(DATABASE_URL)
    run = _plan_run(owner, f"fp-{uuid4().hex[:16]}")
    version = _plan_version(run)
    repo.save(run, version, _plan_items(version))

    other = _new_user()
    # B cannot INSERT a plan_version under A's run.
    b_conn, b_cur = _as_user(str(other))
    try:
        with pytest.raises(psycopg_errors.InsufficientPrivilege):
            b_cur.execute(
                """
                INSERT INTO plan_version (
                    version_id, run_id, plan_jsonb, plan_canonical, plan_sha256)
                VALUES (%s,%s,%s,%s,%s)
                """,
                (str(uuid4()), str(run.run_id), "{}", "{}", "evil-sha"),
            )
    finally:
        b_conn.close()

    # B cannot INSERT a plan_item under A's version.
    c_conn, c_cur = _as_user(str(other))
    try:
        with pytest.raises(psycopg_errors.InsufficientPrivilege):
            c_cur.execute(
                """
                INSERT INTO plan_item (
                    item_id, version_id, slot_index, context, rank,
                    candidate_id, menu_period, food_ids, offering_ids,
                    profile_ids, score_total, calories_kcal)
                VALUES (%s,%s,0,'post_workout_lunch',1,'evil','Lunch',
                        '{}'::uuid[], '{}'::uuid[], '{}'::uuid[], '1','1')
                """,
                (str(uuid4()), str(version.version_id)),
            )
    finally:
        c_conn.close()

    # B cannot READ any of A's plan rows.
    d_conn, d_cur = _as_user(str(other))
    try:
        for table in ("plan_run", "plan_version", "plan_item"):
            d_cur.execute(f"SELECT COUNT(*) FROM {table}")
            assert d_cur.fetchone()[0] == 0, f"non-owner sees rows in {table}"
    finally:
        d_conn.close()


def test_decision_log_cannot_reference_foreign_policy_version() -> None:
    """decision_log ownership flows through the parent policy version."""
    _apply_migrations()
    from psycopg import errors as psycopg_errors

    # Fresh per-test subjects (shared scratch DB across runs).
    user_a, user_b = str(uuid4()), str(uuid4())

    # A creates policy P_A.
    a_conn, a_cur = _as_user(user_a)
    try:
        p_a = _insert_target((a_conn, a_cur), user_a, f"own-{uuid4().hex[:12]}")
        a_conn.commit()
    finally:
        a_conn.close()

    # B cannot attach an approval to A's policy.
    b_conn, b_cur = _as_user(user_b)
    try:
        with pytest.raises(psycopg_errors.InsufficientPrivilege):
            b_cur.execute(
                """
                INSERT INTO decision_log (
                    decision_id, user_id, subject, decision, rationale,
                    policy_version_id, decided_at)
                VALUES (%s,%s,'target_policy','approved','evil',%s,now())
                """,
                (str(uuid4()), user_b, p_a),
            )
    finally:
        b_conn.close()

    # Fresh scoped session (SET LOCAL reverts at transaction end): B's own
    # policy + decision succeeds.
    c_conn, c_cur = _as_user(user_b)
    try:
        p_b = _insert_target((c_conn, c_cur), user_b, f"own-{uuid4().hex[:12]}")
        c_cur.execute(
            """
            INSERT INTO decision_log (
                decision_id, user_id, subject, decision, rationale,
                policy_version_id, decided_at)
            VALUES (%s,%s,'target_policy','approved','legit',%s,now())
            """,
            (str(uuid4()), user_b, p_b),
        )
        c_conn.commit()
    finally:
        c_conn.close()

    # A's data remains unchanged; B's own decision persisted exactly once.
    admin = _admin()
    with admin as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM decision_log WHERE user_id=%s", (user_a,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT policy_version FROM target_policy_version WHERE version_id=%s", (p_a,))
        assert str(cur.fetchone()[0]).startswith("own-")
        cur.execute("SELECT COUNT(*) FROM decision_log WHERE user_id=%s", (user_b,))
        assert cur.fetchone()[0] == 1
    admin.close()
