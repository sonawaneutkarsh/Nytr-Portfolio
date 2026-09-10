"""Deterministic, evidence-pinned protein target proposal policy (M16A)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from uuid import UUID

from nutrition_agent.domain.health.trend import BodyMassObservation
from nutrition_agent.domain.nutrition.targets import GoalKind
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion


@dataclass(frozen=True)
class ProteinTargetProposalPolicy:
    policy_version: str
    grams_per_pound: Decimal
    kilograms_per_pound: Decimal
    rounding_quantum_g: Decimal
    target_kind: GoalKind
    max_evidence_age_days: int


# Versioned owner policy, based on the documented trainer working reference.
# It is data passed to the evaluator; the arithmetic contains no implicit
# health constant and changing this record produces different evidence.
OWNER_PROTEIN_TARGET_POLICY_V1 = ProteinTargetProposalPolicy(
    policy_version="owner-protein-target.v1",
    grams_per_pound=Decimal("0.8"),
    kilograms_per_pound=Decimal("0.45359237"),
    rounding_quantum_g=Decimal("1"),
    target_kind=GoalKind.FLOOR,
    max_evidence_age_days=7,
)


class ProteinProposalDecisionValue(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ProteinTargetProposal:
    proposal_id: UUID
    user_id: UUID
    prior_target_policy_version_id: UUID
    body_mass_sample_uuid: UUID
    policy_version: str
    target_kind: GoalKind
    body_mass_kg: Decimal
    grams_per_pound: Decimal
    proposed_protein_g: Decimal
    evidence_digest: str
    calculation_payload: dict[str, object]
    rationale: str
    provenance: str
    generated_at: datetime


@dataclass(frozen=True)
class ProteinTargetProposalDecision:
    decision_id: UUID
    user_id: UUID
    proposal_id: UUID
    decision: ProteinProposalDecisionValue
    rationale: str
    client_event_id: UUID
    resulting_target_policy_version_id: UUID | None
    decided_at: datetime


@dataclass(frozen=True)
class DecideProteinProposalOutcome:
    decision: ProteinTargetProposalDecision
    created: bool


def build_protein_target_proposal(
    *,
    proposal_id: UUID,
    user_id: UUID,
    prior_target: TargetPolicyVersion,
    body_mass: BodyMassObservation,
    generated_at: datetime,
    policy: ProteinTargetProposalPolicy,
) -> ProteinTargetProposal:
    pounds = body_mass.value_kg / policy.kilograms_per_pound
    proposed = (pounds * policy.grams_per_pound).quantize(
        policy.rounding_quantum_g, rounding=ROUND_HALF_EVEN
    )
    calculation: dict[str, object] = {
        "body_mass": {
            "measured_at": body_mass.measured_at.isoformat(),
            "sample_uuid": str(body_mass.sample_uuid),
            "value_kg": str(body_mass.value_kg),
        },
        "formula": "body_mass_kg / kilograms_per_pound * grams_per_pound",
        "grams_per_pound": str(policy.grams_per_pound),
        "kilograms_per_pound": str(policy.kilograms_per_pound),
        "max_evidence_age_days": policy.max_evidence_age_days,
        "prior_target_payload_sha256": prior_target.payload_sha256,
        "prior_target_policy_version_id": str(prior_target.version_id),
        "proposed_protein_g": str(proposed),
        "rounding": "ROUND_HALF_EVEN",
        "rounding_quantum_g": str(policy.rounding_quantum_g),
        "target_kind": policy.target_kind.value,
        "target_policy": policy.policy_version,
    }
    canonical = json.dumps(calculation, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    return ProteinTargetProposal(
        proposal_id=proposal_id,
        user_id=user_id,
        prior_target_policy_version_id=prior_target.version_id,
        body_mass_sample_uuid=body_mass.sample_uuid,
        policy_version=policy.policy_version,
        target_kind=policy.target_kind,
        body_mass_kg=body_mass.value_kg,
        grams_per_pound=policy.grams_per_pound,
        proposed_protein_g=proposed,
        evidence_digest=digest,
        calculation_payload=calculation,
        rationale=(
            "Body-mass-linked protein floor calculated by a versioned owner nutrition "
            "proposal policy; it is not a medical diagnosis and approval is required "
            "before it becomes an active target."
        ),
        provenance="HealthKit body-mass sample persisted by the authenticated iOS companion",
        generated_at=generated_at,
    )


__all__ = [
    "DecideProteinProposalOutcome",
    "OWNER_PROTEIN_TARGET_POLICY_V1",
    "ProteinProposalDecisionValue",
    "ProteinTargetProposal",
    "ProteinTargetProposalDecision",
    "ProteinTargetProposalPolicy",
    "build_protein_target_proposal",
]
