"""Evidence selection and explicit approval workflow for protein targets."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from nutrition_agent.application.ports import (
    BodyMassHistoryRepository,
    Clock,
    IdGenerator,
    ProteinProposalDecisionConflictError,
    ProteinTargetProposalRepository,
    StaleProteinProposalError,
    TargetPolicyRepository,
)
from nutrition_agent.application.target_policy import target_policy_payload_sha256
from nutrition_agent.domain.planning.artifacts import DecisionLogEntry, TargetPolicyVersion
from nutrition_agent.domain.protein_target import (
    OWNER_PROTEIN_TARGET_POLICY_V1,
    DecideProteinProposalOutcome,
    ProteinProposalDecisionValue,
    ProteinTargetProposal,
    ProteinTargetProposalDecision,
    ProteinTargetProposalPolicy,
    build_protein_target_proposal,
)


class ProteinProposalUnavailable(LookupError):
    pass


class ProteinProposalConflict(Exception):
    pass


class ProteinProposalStale(Exception):
    pass


class CreateProteinTargetProposalUseCase:
    def __init__(
        self,
        *,
        body_mass: BodyMassHistoryRepository,
        targets: TargetPolicyRepository,
        proposals: ProteinTargetProposalRepository,
        clock: Clock,
        ids: IdGenerator,
        policy: ProteinTargetProposalPolicy = OWNER_PROTEIN_TARGET_POLICY_V1,
    ) -> None:
        self._body_mass = body_mass
        self._targets = targets
        self._proposals = proposals
        self._clock = clock
        self._ids = ids
        self._policy = policy

    def execute(self, *, user_id: UUID) -> tuple[ProteinTargetProposal, bool]:
        now = self._clock.now()
        target = self._targets.latest_approved(user_id)
        if target is None:
            raise ProteinProposalUnavailable("approved target policy required")
        start = now - timedelta(days=self._policy.max_evidence_age_days)
        samples = self._body_mass.list_active(user_id, start, now + timedelta(microseconds=1))
        if not samples:
            raise ProteinProposalUnavailable("recent persisted body-mass evidence required")
        latest = max(samples, key=lambda item: (item.measured_at, item.sample_uuid))
        proposal = build_protein_target_proposal(
            proposal_id=self._ids.new_id(),
            user_id=user_id,
            prior_target=target,
            body_mass=latest,
            generated_at=now,
            policy=self._policy,
        )
        matching_protein_goal = next(
            (goal for goal in target.goals_jsonb if goal["nutrient"] == "protein_g"),
            None,
        )
        if (
            matching_protein_goal is not None
            and matching_protein_goal["kind"] == proposal.target_kind.value
            and Decimal(str(matching_protein_goal["value"])) == proposal.proposed_protein_g
        ):
            raise ProteinProposalUnavailable(
                "approved protein target already matches the current proposal"
            )
        return self._proposals.save(proposal)


class DecideProteinTargetProposalUseCase:
    def __init__(
        self,
        *,
        targets: TargetPolicyRepository,
        proposals: ProteinTargetProposalRepository,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._targets = targets
        self._proposals = proposals
        self._clock = clock
        self._ids = ids

    def execute(
        self,
        *,
        user_id: UUID,
        proposal_id: UUID,
        decision: ProteinProposalDecisionValue,
        client_event_id: UUID,
        rationale: str,
    ) -> DecideProteinProposalOutcome:
        if not rationale.strip():
            raise ValueError("rationale is required")
        proposal = self._proposals.find_by_id(user_id, proposal_id)
        if proposal is None:
            raise ProteinProposalUnavailable("protein target proposal not found")
        now = self._clock.now()
        resulting: TargetPolicyVersion | None = None
        decision_log: DecisionLogEntry | None = None
        resulting_id: UUID | None = None
        if decision is ProteinProposalDecisionValue.APPROVED:
            current = self._targets.latest_approved(user_id)
            if current is None or current.version_id != proposal.prior_target_policy_version_id:
                raise ProteinProposalStale("approved target policy changed after proposal")
            goals = [dict(goal) for goal in current.goals_jsonb if goal["nutrient"] != "protein_g"]
            existing_weight = next(
                (goal["weight"] for goal in current.goals_jsonb if goal["nutrient"] == "protein_g"),
                "1",
            )
            goals.append(
                {
                    "nutrient": "protein_g",
                    "kind": proposal.target_kind.value,
                    "value": str(proposal.proposed_protein_g),
                    "weight": existing_weight,
                }
            )
            goals.sort(key=lambda goal: goal["nutrient"])
            resulting_id = self._ids.new_id()
            resulting = TargetPolicyVersion(
                version_id=resulting_id,
                user_id=user_id,
                policy_version=f"protein-proposal-{proposal.evidence_digest[:24]}",
                goals_jsonb=goals,
                payload_sha256=target_policy_payload_sha256(goals),
                created_at=now,
            )
            decision_log = DecisionLogEntry(
                decision_id=self._ids.new_id(),
                user_id=user_id,
                subject="target_policy",
                decision="approved",
                rationale=rationale.strip(),
                policy_version_id=resulting_id,
                decided_at=now,
            )
        terminal = ProteinTargetProposalDecision(
            decision_id=self._ids.new_id(),
            user_id=user_id,
            proposal_id=proposal_id,
            decision=decision,
            rationale=rationale.strip(),
            client_event_id=client_event_id,
            resulting_target_policy_version_id=resulting_id,
            decided_at=now,
        )
        try:
            return self._proposals.decide(proposal, terminal, resulting, decision_log)
        except ProteinProposalDecisionConflictError as exc:
            raise ProteinProposalConflict(str(exc)) from exc
        except StaleProteinProposalError as exc:
            raise ProteinProposalStale(str(exc)) from exc


__all__ = [
    "CreateProteinTargetProposalUseCase",
    "DecideProteinTargetProposalUseCase",
    "ProteinProposalConflict",
    "ProteinProposalStale",
    "ProteinProposalUnavailable",
]
