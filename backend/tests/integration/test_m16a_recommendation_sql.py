from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from nutrition_agent.application.ports import (
    DuplicateNextMealRecommendationError,
    ProteinProposalDecisionConflictError,
)
from nutrition_agent.application.target_policy import target_policy_payload_sha256
from nutrition_agent.db.sql_repos import (
    SqlNextMealRecommendationRepository,
    SqlProteinTargetProposalRepository,
)
from nutrition_agent.domain.health.trend import BodyMassObservation
from nutrition_agent.domain.next_meal import (
    NextMealStatus,
    failure_artifact,
    recommendation_from_artifact,
)
from nutrition_agent.domain.planning.artifacts import DecisionLogEntry, TargetPolicyVersion
from nutrition_agent.domain.protein_target import (
    OWNER_PROTEIN_TARGET_POLICY_V1,
    ProteinProposalDecisionValue,
    ProteinTargetProposalDecision,
    build_protein_target_proposal,
)
from tests.migration_helpers import apply_migrations

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="STACKS_TEST_DATABASE_URL not configured; SQL test requires scratch Postgres",
)


def test_m16a_owner_rls_immutable_replay_and_atomic_protein_approval() -> None:
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)
    user = uuid4()
    other = uuid4()
    target_id = uuid4()
    sample_id = uuid4()
    now = datetime(2026, 9, 5, 16, tzinfo=UTC)
    goals = [{"nutrient": "calories_kcal", "kind": "target", "value": "2200", "weight": "1"}]
    target = TargetPolicyVersion(
        version_id=target_id,
        user_id=user,
        policy_version="sql-m16a-prior",
        goals_jsonb=goals,
        payload_sha256=target_policy_payload_sha256(goals),
        created_at=now,
    )
    other_target_id = uuid4()
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO target_policy_version
               (version_id,user_id,policy_version,goals,payload_sha256,created_at)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (
                target_id,
                user,
                target.policy_version,
                psycopg.types.json.Jsonb(goals),
                target.payload_sha256,
                now,
            ),
        )
        cur.execute(
            """INSERT INTO target_policy_version
               (version_id,user_id,policy_version,goals,payload_sha256,created_at)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (
                other_target_id,
                other,
                "sql-m16a-other",
                psycopg.types.json.Jsonb(goals),
                target.payload_sha256,
                now,
            ),
        )
        cur.execute(
            """INSERT INTO health_body_mass_sample
               (id,user_id,hk_sample_uuid,value_kg,sample_start,sample_end)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (uuid4(), user, sample_id, "72.0", now, now),
        )

    proposal = build_protein_target_proposal(
        proposal_id=uuid4(),
        user_id=user,
        prior_target=target,
        body_mass=BodyMassObservation(sample_id, Decimal("72.0"), now),
        generated_at=now,
        policy=OWNER_PROTEIN_TARGET_POLICY_V1,
    )
    proposals = SqlProteinTargetProposalRepository(DATABASE_URL)
    stored, created = proposals.save(proposal)
    assert created is True
    assert proposals.save(proposal)[1] is False
    assert proposals.latest(other) is None
    with pytest.raises(psycopg.errors.CheckViolation):
        proposals.save(
            replace(
                proposal,
                proposal_id=uuid4(),
                body_mass_kg=Decimal("NaN"),
                evidence_digest="b" * 64,
            )
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        proposals.save(
            replace(
                proposal,
                proposal_id=uuid4(),
                evidence_digest="z" * 64,
            )
        )
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM target_policy_version WHERE user_id=%s", (user,))
        assert cur.fetchone()[0] == 1  # proposal generation never auto-approves

    approved_goals = goals + [
        {
            "nutrient": "protein_g",
            "kind": "floor",
            "value": str(stored.proposed_protein_g),
            "weight": "1",
        }
    ]
    resulting = TargetPolicyVersion(
        version_id=uuid4(),
        user_id=user,
        policy_version=f"sql-{uuid4()}",
        goals_jsonb=approved_goals,
        payload_sha256=target_policy_payload_sha256(approved_goals),
        created_at=now,
    )
    terminal = ProteinTargetProposalDecision(
        decision_id=uuid4(),
        user_id=user,
        proposal_id=proposal.proposal_id,
        decision=ProteinProposalDecisionValue.APPROVED,
        rationale="owner approved proposal",
        client_event_id=uuid4(),
        resulting_target_policy_version_id=resulting.version_id,
        decided_at=now,
    )
    decision_log = DecisionLogEntry(
        decision_id=uuid4(),
        user_id=user,
        subject="target_policy",
        decision="approved",
        rationale=terminal.rationale,
        policy_version_id=resulting.version_id,
        decided_at=now,
    )
    assert proposals.decide(proposal, terminal, resulting, decision_log).created is True
    assert proposals.decide(proposal, terminal, resulting, decision_log).created is False
    assert proposals.find_decision(user, proposal.proposal_id) == terminal
    assert proposals.find_decision(other, proposal.proposal_id) is None
    with pytest.raises(ProteinProposalDecisionConflictError):
        proposals.decide(
            proposal,
            replace(terminal, rationale="conflicting replay"),
            resulting,
            decision_log,
        )

    artifact = failure_artifact(
        local_date=date(2026, 9, 5),
        timezone="UTC",
        decision_at=now,
        status=NextMealStatus.NO_REMAINING_MEAL_OPPORTUNITY,
        reason_codes=("all_stacks_windows_elapsed",),
    )
    recommendation = recommendation_from_artifact(
        recommendation_id=uuid4(),
        user_id=user,
        client_request_id=uuid4(),
        local_date=date(2026, 9, 5),
        timezone="UTC",
        decision_at=now,
        target_policy_version_id=resulting.version_id,
        status=NextMealStatus.NO_REMAINING_MEAL_OPPORTUNITY,
        reason_codes=("all_stacks_windows_elapsed",),
        artifact=artifact,
    )
    recommendations = SqlNextMealRecommendationRepository(DATABASE_URL)
    assert recommendations.save(recommendation).created is True
    assert recommendations.save(recommendation).created is False
    recalculated = replace(
        recommendation,
        recommendation_id=uuid4(),
        decision_at=now + timedelta(seconds=1),
        artifact_sha256="f" * 64,
    )
    replay = recommendations.save(recalculated)
    assert replay.created is False
    assert replay.recommendation == recommendation
    assert (
        recommendations.find_by_client_request_id(user, recommendation.client_request_id)
        == recommendation
    )
    with pytest.raises(DuplicateNextMealRecommendationError):
        recommendations.save(replace(recommendation, timezone="America/New_York"))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        recommendations.save(
            replace(
                recommendation,
                recommendation_id=uuid4(),
                client_request_id=uuid4(),
                target_policy_version_id=other_target_id,
            )
        )
    assert recommendations.latest(other) is None

    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE authenticated")
        cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(user),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "UPDATE next_meal_recommendation SET status='recommended' "
                "WHERE recommendation_id=%s",
                (recommendation.recommendation_id,),
            )
        conn.rollback()

    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE authenticated")
        cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(user),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "DELETE FROM next_meal_recommendation WHERE recommendation_id=%s",
                (recommendation.recommendation_id,),
            )
        conn.rollback()

    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT privilege_type FROM information_schema.role_table_grants
               WHERE grantee='authenticated'
                 AND table_name IN (
                     'protein_target_proposal',
                     'protein_target_proposal_decision',
                     'next_meal_recommendation')"""
        )
        assert {row[0] for row in cur.fetchall()} == {"INSERT", "SELECT"}
        cur.execute(
            "SELECT has_column_privilege('authenticated', "
            "'next_meal_recommendation', 'status', 'UPDATE')"
        )
        assert cur.fetchone()[0] is False

    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT
                 (SELECT count(*) FROM target_policy_version WHERE user_id=%s),
                 (SELECT count(*) FROM protein_target_proposal_decision WHERE user_id=%s),
                 (SELECT count(*) FROM plan_consumption WHERE user_id=%s),
                 (SELECT count(*) FROM manual_food_consumption WHERE user_id=%s)""",
            (user, user, user, user),
        )
        assert cur.fetchone() == (2, 1, 0, 0)
