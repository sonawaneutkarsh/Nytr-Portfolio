"""M6 in-memory plan/target persistence: round trip, idempotency, ownership."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from nutrition_agent.application.ports import (
    DuplicateLogicalPlanError,
    TargetPolicyVersionExistsError,
)
from nutrition_agent.db.in_memory_repos import (
    InMemoryPlanRunRepository,
    InMemoryTargetPolicyRepository,
)
from nutrition_agent.domain.planning.artifacts import (
    PlanItem,
    PlanRun,
    PlanRunStatus,
    PlanVersion,
    TargetPolicyVersion,
)

USER_A = UUID("00000000-0000-0000-0000-0000000000a1")
USER_B = UUID("00000000-0000-0000-0000-0000000000b2")
PLAN_DATE = date(2026, 8, 21)
NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _run(
    user_id: UUID = USER_A,
    fingerprint: str = "fp-1",
    target_policy_version_id: UUID | None = None,
) -> PlanRun:
    return PlanRun(
        run_id=UUID(int=1),
        user_id=user_id,
        requested_for_date=PLAN_DATE,
        timezone="America/New_York",
        inputs_fingerprint=fingerprint,
        status=PlanRunStatus.COMPLETED,
        reason_codes=(),
        started_at=NOW,
        finished_at=NOW,
        target_policy_version_id=target_policy_version_id,
    )


def _version(run: PlanRun) -> PlanVersion:
    return PlanVersion(
        version_id=UUID(int=2),
        run_id=run.run_id,
        plan_jsonb={"artifact_kind": "daily_plan", "status": "ok"},
        plan_canonical='{"artifact_kind":"daily_plan","status":"ok"}',
        plan_sha256="deadbeef" * 8,
    )


def _items(version: PlanVersion) -> list[PlanItem]:
    return [
        PlanItem(
            item_id=UUID(int=3),
            version_id=version.version_id,
            slot_index=0,
            context="post_workout_lunch",
            rank=1,
            candidate_id="post_workout_lunch-01",
            menu_period="Lunch",
            food_ids=(UUID(int=10),),
            offering_ids=(UUID(int=100),),
            profile_row_ids=(UUID(int=1000),),
            profile_content_sha256s=("sha-food-1",),
            score_total="1.25",
            calories_kcal="400.0",
        )
    ]


class TestPlanRunRepository:
    def test_round_trip_preserves_every_pinned_field(self) -> None:
        repo = InMemoryPlanRunRepository()
        run = _run(target_policy_version_id=UUID(int=777))
        version = _version(run)
        repo.save(run, version, _items(version))

        view = repo.find_replay(USER_A, PLAN_DATE, "fp-1")
        assert view is not None
        assert view.run_id == run.run_id
        assert view.version_id == version.version_id
        assert view.plan_sha256 == version.plan_sha256
        assert view.plan_jsonb == version.plan_jsonb
        # BYTE-EXACT canonical artifact survives storage.
        assert view.plan_canonical == version.plan_canonical
        assert view.target_policy_version_id == UUID(int=777)
        assert [item.item_id for item in view.plan_items] == [UUID(int=3)]
        assert view.plan_items[0].version_id == version.version_id
        assert (view.plan_items[0].slot_index, view.plan_items[0].rank) == (0, 1)
        assert view.plan_items[0].candidate_id == "post_workout_lunch-01"

    def test_null_target_policy_reference_remains_compatible(self) -> None:
        repo = InMemoryPlanRunRepository()
        run = _run(target_policy_version_id=None)
        version = _version(run)
        repo.save(run, version, _items(version))

        view = repo.find_replay(USER_A, PLAN_DATE, run.inputs_fingerprint)
        assert view is not None
        assert view.target_policy_version_id is None

    def test_duplicate_logical_plan_rejected(self) -> None:
        repo = InMemoryPlanRunRepository()
        run = _run()
        version = _version(run)
        repo.save(run, version, _items(version))
        with pytest.raises(DuplicateLogicalPlanError):
            repo.save(run, _version(run), _items(version))

    def test_same_date_different_fingerprint_is_new_run_not_duplicate(self) -> None:
        repo = InMemoryPlanRunRepository()
        first_run = _run(fingerprint="fp-1")
        first_version = _version(first_run)
        repo.save(first_run, first_version, _items(first_version))

        second_run = PlanRun(
            run_id=UUID(int=9),
            user_id=USER_A,
            requested_for_date=PLAN_DATE,
            timezone="America/New_York",
            inputs_fingerprint="fp-2",
            status=PlanRunStatus.NO_PLAN,
            reason_codes=("empty_menu_period",),
            started_at=NOW,
            finished_at=NOW,
        )
        second_version = _version(second_run)
        repo.save(second_run, second_version, [])
        assert repo.find_replay(USER_A, PLAN_DATE, "fp-2") is not None
        latest = repo.latest_for_user_date(USER_A, PLAN_DATE)
        assert latest is not None and latest.run_id in {first_run.run_id, second_run.run_id}

    def test_ownership_user_b_cannot_see_user_a(self) -> None:
        repo = InMemoryPlanRunRepository()
        run = _run()
        version = _version(run)
        repo.save(run, version, _items(version))
        assert repo.find_replay(USER_B, PLAN_DATE, "fp-1") is None
        assert repo.latest_for_user_date(USER_B, PLAN_DATE) is None


class TestTargetPolicyRepository:
    def _policy(self, label: str = "v1") -> TargetPolicyVersion:
        return TargetPolicyVersion(
            version_id=UUID(int=int(label == "v1") or 7),
            user_id=USER_A,
            policy_version=label,
            goals_jsonb=[
                {"kind": "target", "nutrient": "calories_kcal", "value": "900", "weight": "1"}
            ],
            payload_sha256=f"payload-{label}",
            created_at=NOW,
        )

    def test_save_and_latest_round_trip_with_rationale_linkage(self) -> None:
        repo = InMemoryTargetPolicyRepository()
        policy = self._policy()
        repo.save_approved(policy, rationale="lean bulk start", decided_by_clock=NOW)
        assert repo.rationale_for(policy.version_id) == "lean bulk start"
        latest = repo.latest_approved(USER_A)
        assert latest is not None and latest.payload_sha256 == policy.payload_sha256

    def test_duplicate_label_or_payload_rejected(self) -> None:
        repo = InMemoryTargetPolicyRepository()
        repo.save_approved(self._policy("v1"), rationale="r", decided_by_clock=NOW)
        with pytest.raises(TargetPolicyVersionExistsError):
            repo.save_approved(self._policy("v1"), rationale="again", decided_by_clock=NOW)

    def test_payload_dedup_per_user(self) -> None:
        repo = InMemoryTargetPolicyRepository()
        base = self._policy("v1")
        repo.save_approved(base, rationale="first", decided_by_clock=NOW)
        twin = TargetPolicyVersion(
            version_id=UUID(int=99),
            user_id=USER_A,
            policy_version="v2-different-label-same-payload",
            goals_jsonb=base.goals_jsonb,
            payload_sha256=base.payload_sha256,
            created_at=NOW,
        )
        with pytest.raises(TargetPolicyVersionExistsError):
            repo.save_approved(twin, rationale="dup payload", decided_by_clock=NOW)

    def test_ownership_isolation(self) -> None:
        repo = InMemoryTargetPolicyRepository()
        repo.save_approved(self._policy("v1"), rationale="r", decided_by_clock=NOW)
        assert repo.latest_approved(USER_B) is None
        assert repo.find_by_version_label(USER_B, "v1") is False
