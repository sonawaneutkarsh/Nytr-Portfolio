"""M10A migration, SQL repository, immutability, and RLS proofs."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from nutrition_agent.application.ports import (
    DuplicateTargetReviewError,
    GoalPolicyVersionExistsError,
)
from nutrition_agent.application.target_policy import build_domain_target_set
from nutrition_agent.application.target_review import PersistTargetReviewUseCase
from nutrition_agent.db.sql_repos import (
    SqlGoalPolicyRepository,
    SqlTargetPolicyRepository,
    SqlTargetReviewRepository,
)
from nutrition_agent.domain.health.trend import BodyMassTrendStatus, BodyMassTrendSummary
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion
from nutrition_agent.domain.target_review import (
    M10A_TARGET_REVIEW_POLICY,
    GoalDirection,
    GoalPolicyVersion,
    TargetReviewReason,
    evaluate_target_review,
    goal_policy_payload_sha256,
)
from tests.migration_helpers import apply_migrations

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; M10A requires scratch Postgres",
)


class _Clock:
    def __init__(self, *values: datetime) -> None:
        self.values = list(values)

    def now(self) -> datetime:
        return self.values.pop(0)


class _Ids:
    def __init__(self, *values: UUID) -> None:
        self.values = list(values)

    def new_id(self) -> UUID:
        return self.values.pop(0)


def _admin():
    psycopg = pytest.importorskip("psycopg")
    return psycopg.connect(DATABASE_URL)  # type: ignore[arg-type]


def _as_user(user_id: UUID):
    psycopg = pytest.importorskip("psycopg")
    conn = psycopg.connect(DATABASE_URL)  # type: ignore[arg-type]
    cur = conn.cursor()
    cur.execute("SET LOCAL ROLE authenticated")
    cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(user_id),))
    return conn, cur


def _goal(user_id: UUID, *, created_at: datetime, suffix: str) -> GoalPolicyVersion:
    rate = Decimal("0.25")
    return GoalPolicyVersion(
        version_id=uuid4(),
        user_id=user_id,
        policy_version=f"goal-{suffix}",
        direction=GoalDirection.GAIN,
        desired_rate_kg_per_week=rate,
        payload_sha256=goal_policy_payload_sha256(GoalDirection.GAIN, rate),
        created_at=created_at,
    )


def _target(user_id: UUID, *, created_at: datetime, suffix: str) -> TargetPolicyVersion:
    return TargetPolicyVersion(
        version_id=uuid4(),
        user_id=user_id,
        policy_version=f"target-{suffix}",
        goals_jsonb=[
            {
                "kind": "target",
                "nutrient": "calories_kcal",
                "value": "2500",
                "weight": "1",
            },
            {
                "kind": "floor",
                "nutrient": "protein_g",
                "value": "120",
                "weight": "2",
            },
        ],
        payload_sha256=uuid4().hex + uuid4().hex,
        created_at=created_at,
    )


def _evaluation(user_id: UUID, suffix: str = "base"):
    assert DATABASE_URL is not None
    approved_at = datetime(2026, 8, 14, 12, tzinfo=UTC)
    goal = _goal(user_id, created_at=datetime(2026, 8, 1, 12, tzinfo=UTC), suffix=suffix)
    target = _target(user_id, created_at=approved_at, suffix=suffix)
    SqlGoalPolicyRepository(DATABASE_URL).save(goal)
    SqlTargetPolicyRepository(DATABASE_URL).save_approved(
        target, rationale="M10A fixture", decided_by_clock=approved_at
    )
    trend = BodyMassTrendSummary(
        as_of_date=date(2026, 8, 28),
        timezone="UTC",
        algorithm_version="body-mass-trend-v1",
        latest_measurement_date=date(2026, 8, 28),
        latest_measurement_age_days=0,
        first_measurement_date=date(2026, 8, 8),
        last_measurement_date=date(2026, 8, 28),
        represented_day_count=7,
        coverage_span_days=20,
        trailing_7d_average_kg=Decimal("70"),
        weekly_rate_kg=Decimal("0"),
        status=BodyMassTrendStatus.READY,
        input_digest=uuid4().hex + uuid4().hex,
    )
    evaluation = evaluate_target_review(
        trend=trend,
        goal_policy=goal,
        review_policy=M10A_TARGET_REVIEW_POLICY,
        current_target_policy=target,
        current_targets=build_domain_target_set(target.goals_jsonb),
        current_target_approved_at=approved_at,
        as_of_date=trend.as_of_date,
    )
    return goal, target, evaluation


def test_migration_0008_applies_reapplies_and_has_append_only_rls_tables() -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)
    apply_migrations(DATABASE_URL)
    with _admin() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' AND table_name IN (
                'goal_policy_version','target_review','target_review_decision')
            """
        )
        assert {row[0] for row in cur.fetchall()} == {
            "goal_policy_version",
            "target_review",
            "target_review_decision",
        }
        cur.execute(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname='public' AND rowsecurity
              AND tablename IN (
                  'goal_policy_version','target_review','target_review_decision')
            """
        )
        assert {row[0] for row in cur.fetchall()} == {
            "goal_policy_version",
            "target_review",
            "target_review_decision",
        }
        for table in ("goal_policy_version", "target_review", "target_review_decision"):
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


def test_goal_policy_repository_round_trip_latest_and_duplicate_constraints() -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)
    user = uuid4()
    repo = SqlGoalPolicyRepository(DATABASE_URL)
    first = _goal(user, created_at=datetime(2026, 8, 1, tzinfo=UTC), suffix=uuid4().hex)
    second = _goal(user, created_at=datetime(2026, 8, 2, tzinfo=UTC), suffix=uuid4().hex)
    second = replace(
        second,
        desired_rate_kg_per_week=Decimal("0.30"),
        payload_sha256=goal_policy_payload_sha256(GoalDirection.GAIN, Decimal("0.30")),
    )
    repo.save(first)
    repo.save(second)
    assert repo.latest(user) == second
    assert repo.find_by_version_id(user, first.version_id) == first
    assert repo.latest(uuid4()) is None
    with pytest.raises(GoalPolicyVersionExistsError):
        repo.save(replace(second, version_id=uuid4()))
    with pytest.raises(GoalPolicyVersionExistsError):
        repo.save(replace(first, version_id=uuid4(), policy_version=f"new-{uuid4().hex}"))


def test_review_repository_round_trip_exact_replay_and_conflict() -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)
    user = uuid4()
    _, _, evaluation = _evaluation(user, uuid4().hex)
    repo = SqlTargetReviewRepository(DATABASE_URL)
    first_at = datetime(2026, 8, 28, 12, tzinfo=UTC)
    second_at = datetime(2026, 8, 28, 13, tzinfo=UTC)
    use_case = PersistTargetReviewUseCase(repo, _Clock(first_at, second_at), _Ids(uuid4(), uuid4()))
    first = use_case.execute(evaluation)
    replay = use_case.execute(evaluation)
    assert first.created is True and replay.created is False
    assert replay.review.review_id == first.review.review_id
    assert replay.review.created_at == first_at
    assert repo.find_by_digest(user, evaluation.recommendation_digest) == first.review

    conflicting = replace(
        evaluation,
        reason_codes=(TargetReviewReason.OBSERVED_ABOVE_BAND,),
    )
    with pytest.raises(DuplicateTargetReviewError):
        PersistTargetReviewUseCase(
            repo,
            _Clock(datetime(2026, 8, 28, 14, tzinfo=UTC)),
            _Ids(uuid4()),
        ).execute(conflicting)


def test_rls_blocks_cross_user_reads_and_cross_owner_references() -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)
    from psycopg import errors as psycopg_errors

    user_a, user_b = uuid4(), uuid4()
    goal, target, evaluation = _evaluation(user_a, uuid4().hex)
    review = (
        PersistTargetReviewUseCase(
            SqlTargetReviewRepository(DATABASE_URL),
            _Clock(datetime(2026, 8, 28, 12, tzinfo=UTC)),
            _Ids(uuid4()),
        )
        .execute(evaluation)
        .review
    )
    decision_id = uuid4()
    conn, cur = _as_user(user_a)
    try:
        cur.execute(
            """
            INSERT INTO target_review_decision (
                decision_id,user_id,review_id,decision,rationale,
                resulting_target_policy_version_id,client_event_id,decided_at)
            VALUES (%s,%s,%s,'approved','owner approval',%s,%s,now())
            """,
            (decision_id, user_a, review.review_id, target.version_id, uuid4()),
        )
        conn.commit()
    finally:
        conn.close()

    conn, cur = _as_user(user_b)
    try:
        cur.execute(
            "SELECT count(*) FROM goal_policy_version WHERE version_id=%s",
            (goal.version_id,),
        )
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM target_review WHERE review_id=%s", (review.review_id,))
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT count(*) FROM target_review_decision WHERE decision_id=%s",
            (decision_id,),
        )
        assert cur.fetchone()[0] == 0
        conn.commit()
    finally:
        conn.close()

    conn, cur = _as_user(user_b)
    try:
        with pytest.raises(psycopg_errors.InsufficientPrivilege):
            cur.execute(
                """
                INSERT INTO target_review_decision (
                    decision_id,user_id,review_id,decision,rationale,
                    resulting_target_policy_version_id,client_event_id,decided_at)
                VALUES (%s,%s,%s,'rejected','cross-owner attack',NULL,%s,now())
                """,
                (uuid4(), user_b, review.review_id, uuid4()),
            )
        conn.rollback()
    finally:
        conn.close()

    conn, cur = _as_user(user_b)
    try:
        with pytest.raises(psycopg_errors.InsufficientPrivilege):
            cur.execute(
                """
                INSERT INTO goal_policy_version (
                    version_id,user_id,policy_version,direction,
                    desired_rate_kg_per_week,payload_sha256,created_at)
                VALUES (%s,%s,%s,'gain',0.25,%s,now())
                """,
                (uuid4(), user_a, f"attack-{uuid4().hex}", uuid4().hex + uuid4().hex),
            )
        conn.rollback()
    finally:
        conn.close()

    conn, cur = _as_user(user_b)
    try:
        with pytest.raises(psycopg_errors.InsufficientPrivilege):
            cur.execute(
                """
                INSERT INTO target_review (
                    review_id,user_id,as_of_date,timezone,trend_algorithm_version,
                    trend_input_digest,goal_policy_version_id,
                    prior_target_policy_version_id,review_policy_version,status,
                    reason_codes,current_calorie_target,recommendation_digest,
                    evaluation_payload,created_at)
                VALUES (%s,%s,%s,'UTC','body-mass-trend-v1',%s,%s,%s,
                        'target-review-v1','within_band',ARRAY['within_band'],2500,
                        %s,'{}'::jsonb,now())
                """,
                (
                    uuid4(),
                    user_b,
                    date(2026, 8, 28),
                    uuid4().hex + uuid4().hex,
                    goal.version_id,
                    target.version_id,
                    uuid4().hex + uuid4().hex,
                ),
            )
        conn.rollback()
    finally:
        conn.close()


def test_owner_cannot_update_or_delete_goal_review_or_decision() -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)
    user = uuid4()
    goal, target, evaluation = _evaluation(user, uuid4().hex)
    review = (
        PersistTargetReviewUseCase(
            SqlTargetReviewRepository(DATABASE_URL),
            _Clock(datetime(2026, 8, 28, 12, tzinfo=UTC)),
            _Ids(uuid4()),
        )
        .execute(evaluation)
        .review
    )
    decision_id, event_id = uuid4(), uuid4()
    conn, cur = _as_user(user)
    try:
        cur.execute(
            """
            INSERT INTO target_review_decision (
                decision_id,user_id,review_id,decision,rationale,
                resulting_target_policy_version_id,client_event_id,decided_at)
            VALUES (%s,%s,%s,'approved','owner approval',%s,%s,now())
            """,
            (decision_id, user, review.review_id, target.version_id, event_id),
        )
        conn.commit()
    finally:
        conn.close()

    from psycopg import errors as psycopg_errors

    rows = (
        ("goal_policy_version", "version_id", goal.version_id, "policy_version=policy_version"),
        ("target_review", "review_id", review.review_id, "timezone=timezone"),
        (
            "target_review_decision",
            "decision_id",
            decision_id,
            "rationale=rationale",
        ),
    )
    for table, key, value, update in rows:
        conn, cur = _as_user(user)
        try:
            with pytest.raises(psycopg_errors.InsufficientPrivilege):
                cur.execute(f"UPDATE {table} SET {update} WHERE {key}=%s", (value,))
            conn.rollback()
        finally:
            conn.close()
        conn, cur = _as_user(user)
        try:
            with pytest.raises(psycopg_errors.InsufficientPrivilege):
                cur.execute(f"DELETE FROM {table} WHERE {key}=%s", (value,))
            conn.rollback()
        finally:
            conn.close()


def test_database_rejects_invalid_goal_sign_and_invalid_review_foreign_keys() -> None:
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)
    from psycopg import errors as psycopg_errors

    with _admin() as conn, conn.cursor() as cur:
        with pytest.raises(psycopg_errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO goal_policy_version (
                    version_id,user_id,policy_version,direction,
                    desired_rate_kg_per_week,payload_sha256,created_at)
                VALUES (%s,%s,%s,'gain',-0.25,%s,now())
                """,
                (uuid4(), uuid4(), f"bad-{uuid4().hex}", uuid4().hex + uuid4().hex),
            )
        conn.rollback()

    with _admin() as conn, conn.cursor() as cur:
        with pytest.raises(psycopg_errors.ForeignKeyViolation):
            cur.execute(
                """
                INSERT INTO target_review (
                    review_id,user_id,as_of_date,timezone,trend_algorithm_version,
                    trend_input_digest,goal_policy_version_id,
                    prior_target_policy_version_id,review_policy_version,status,
                    reason_codes,current_calorie_target,recommendation_digest,
                    evaluation_payload,created_at)
                VALUES (%s,%s,%s,'UTC','body-mass-trend-v1',%s,%s,%s,
                        'target-review-v1','within_band',ARRAY['within_band'],2500,
                        %s,'{}'::jsonb,now())
                """,
                (
                    uuid4(),
                    uuid4(),
                    date(2026, 8, 28),
                    uuid4().hex + uuid4().hex,
                    uuid4(),
                    uuid4(),
                    uuid4().hex + uuid4().hex,
                ),
            )
        conn.rollback()
