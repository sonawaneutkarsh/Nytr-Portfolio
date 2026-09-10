from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from nutrition_agent.application.protein_target import (
    CreateProteinTargetProposalUseCase,
    DecideProteinTargetProposalUseCase,
    ProteinProposalConflict,
    ProteinProposalStale,
    ProteinProposalUnavailable,
)
from nutrition_agent.application.target_policy import target_policy_payload_sha256
from nutrition_agent.db.in_memory_repos import (
    InMemoryHealthBodyMassRepository,
    InMemoryProteinTargetProposalRepository,
    InMemoryTargetPolicyRepository,
)
from nutrition_agent.domain.health.entities import BodyMassSample, SampleDeletion, SyncBatch
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion
from nutrition_agent.domain.protein_target import ProteinProposalDecisionValue

USER = UUID(int=101)
NOW = datetime(2026, 9, 5, 16, tzinfo=UTC)


class _Clock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class _Ids:
    def __init__(self, start: int = 1000) -> None:
        self.value = start

    def new_id(self) -> UUID:
        self.value += 1
        return UUID(int=self.value)


def _target(*, version_id: int = 1, created_at: datetime | None = None, protein=None):
    goals: list[dict[str, str]] = [
        {"nutrient": "calories_kcal", "kind": "target", "value": "2200", "weight": "1"}
    ]
    if protein is not None:
        goals.append(
            {"nutrient": "protein_g", "kind": "floor", "value": str(protein), "weight": "1"}
        )
    return TargetPolicyVersion(
        version_id=UUID(int=version_id),
        user_id=USER,
        policy_version=f"target.v{version_id}",
        goals_jsonb=goals,
        payload_sha256=target_policy_payload_sha256(goals),
        created_at=created_at or NOW - timedelta(days=30),
    )


def _sample(sample_id: int, measured_at: datetime, kilograms: str = "67.494852"):
    return BodyMassSample(
        sample_uuid=UUID(int=sample_id),
        value_kg=Decimal(kilograms),
        sample_start=measured_at,
        sample_end=measured_at,
        source_name="Health",
        source_bundle_id="com.apple.Health",
    )


def _batch(*, added=(), deletions=()) -> SyncBatch:
    return SyncBatch(client_batch_id=UUID(int=9000), added=tuple(added), deletions=tuple(deletions))


def _proposal_setup():
    bodies = InMemoryHealthBodyMassRepository()
    targets = InMemoryTargetPolicyRepository()
    target = _target()
    targets.save_approved(target, "owner approved", target.created_at)
    proposals = InMemoryProteinTargetProposalRepository(targets=targets)
    clock = _Clock()
    create = CreateProteinTargetProposalUseCase(
        body_mass=bodies,
        targets=targets,
        proposals=proposals,
        clock=clock,
        ids=_Ids(),
    )
    decide = DecideProteinTargetProposalUseCase(
        targets=targets, proposals=proposals, clock=clock, ids=_Ids(2000)
    )
    return bodies, targets, proposals, clock, create, decide


def test_proposal_selects_latest_valid_active_sample_and_replays_original() -> None:
    bodies, targets, proposals, clock, create, _ = _proposal_setup()
    bodies.apply_batch(
        USER,
        _batch(
            added=(
                _sample(1, NOW - timedelta(days=8)),
                _sample(2, NOW - timedelta(days=2), "66"),
                _sample(3, NOW - timedelta(days=1), "67.494852"),
                _sample(4, NOW + timedelta(days=2), "90"),
            )
        ),
    )
    bodies.apply_batch(USER, _batch(deletions=(SampleDeletion(UUID(int=3)),)))

    proposal, created = create.execute(user_id=USER)
    assert created is True
    assert proposal.body_mass_sample_uuid == UUID(int=2)
    assert proposal.proposed_protein_g == Decimal("116")
    assert len(targets.policies) == 1
    assert proposals.decisions == {}

    clock.value += timedelta(hours=1)
    replay, replay_created = create.execute(user_id=USER)
    assert replay_created is False
    assert replay == proposal


@pytest.mark.parametrize(
    "measured_at",
    (NOW - timedelta(days=7, microseconds=1), NOW + timedelta(microseconds=1)),
)
def test_proposal_fails_closed_for_stale_or_future_sample(measured_at: datetime) -> None:
    bodies, _, _, _, create, _ = _proposal_setup()
    bodies.apply_batch(USER, _batch(added=(_sample(1, measured_at),)))
    with pytest.raises(ProteinProposalUnavailable, match="recent persisted"):
        create.execute(user_id=USER)


def test_proposal_requires_sample_and_suppresses_no_change_proposal() -> None:
    bodies, targets, proposals, clock, create, _ = _proposal_setup()
    with pytest.raises(ProteinProposalUnavailable, match="recent persisted"):
        create.execute(user_id=USER)

    bodies.apply_batch(USER, _batch(added=(_sample(1, NOW),)))
    matching = _target(version_id=2, created_at=NOW - timedelta(days=1), protein="119.0")
    targets.save_approved(matching, "existing protein", matching.created_at)
    with pytest.raises(ProteinProposalUnavailable, match="already matches"):
        create.execute(user_id=USER)
    assert proposals.proposals == {}
    assert clock.now() == NOW


def test_reject_is_terminal_idempotent_and_never_appends_target() -> None:
    bodies, targets, _, _, create, decide = _proposal_setup()
    bodies.apply_batch(USER, _batch(added=(_sample(1, NOW),)))
    proposal, _ = create.execute(user_id=USER)
    event_id = UUID(int=3000)

    first = decide.execute(
        user_id=USER,
        proposal_id=proposal.proposal_id,
        decision=ProteinProposalDecisionValue.REJECTED,
        client_event_id=event_id,
        rationale="Owner declined",
    )
    replay = decide.execute(
        user_id=USER,
        proposal_id=proposal.proposal_id,
        decision=ProteinProposalDecisionValue.REJECTED,
        client_event_id=event_id,
        rationale="Owner declined",
    )
    assert first.created is True
    assert replay.created is False
    assert replay.decision == first.decision
    assert len(targets.policies) == 1

    with pytest.raises(ProteinProposalConflict):
        decide.execute(
            user_id=USER,
            proposal_id=proposal.proposal_id,
            decision=ProteinProposalDecisionValue.REJECTED,
            client_event_id=event_id,
            rationale="Different request",
        )


def test_approval_appends_floor_and_preserves_prior_then_stale_approval_fails() -> None:
    bodies, targets, _, clock, create, decide = _proposal_setup()
    bodies.apply_batch(USER, _batch(added=(_sample(1, NOW),)))
    proposal, _ = create.execute(user_id=USER)
    prior = targets.policies[UUID(int=1)]

    approved = decide.execute(
        user_id=USER,
        proposal_id=proposal.proposal_id,
        decision=ProteinProposalDecisionValue.APPROVED,
        client_event_id=UUID(int=4000),
        rationale="Owner approved",
    )
    assert approved.created is True
    assert len(targets.policies) == 2
    assert targets.policies[UUID(int=1)] == prior
    resulting_id = approved.decision.resulting_target_policy_version_id
    assert resulting_id is not None
    protein = next(
        goal
        for goal in targets.policies[resulting_id].goals_jsonb
        if goal["nutrient"] == "protein_g"
    )
    assert protein == {"nutrient": "protein_g", "kind": "floor", "value": "119", "weight": "1"}

    clock.value += timedelta(seconds=1)
    bodies.apply_batch(USER, _batch(added=(_sample(2, clock.value, "70"),)))
    later, _ = create.execute(user_id=USER)
    replacement = _target(
        version_id=99,
        created_at=clock.value + timedelta(seconds=1),
        protein="130",
    )
    targets.save_approved(replacement, "changed elsewhere", replacement.created_at)
    with pytest.raises(ProteinProposalStale):
        decide.execute(
            user_id=USER,
            proposal_id=later.proposal_id,
            decision=ProteinProposalDecisionValue.APPROVED,
            client_event_id=UUID(int=4001),
            rationale="Stale approval",
        )
