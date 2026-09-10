"""Target-policy approval use case (M6, ADR-018).

Persists ONE immutable approved policy version plus its decision-log entry.
This is bookkeeping for externally authored targets — NOT nutrition science:
nothing here computes, adjusts, or generates goal values (ADR-007/ADR-018).
Validation reconstructs the domain value objects so malformed or
float-contaminated input fails closed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from nutrition_agent.application.ports import (
    Clock,
    IdGenerator,
    TargetPolicyRepository,
    TargetPolicyVersionExistsError,
)
from nutrition_agent.domain.nutrition.targets import GoalKind, NutrientGoal, TargetSet
from nutrition_agent.domain.planning.artifacts import (
    DecisionLogEntry,
    TargetPolicyVersion,
)
from nutrition_agent.domain.stacks.entities import NutrientKey

VALID_GOAL_KINDS = {kind.value for kind in GoalKind}
RATIONALE_MAX_LENGTH = 2000


class InvalidTargetPolicy(ValueError):
    """Payload cannot be reconstructed as a domain TargetSet; fail closed."""


class DuplicateTargetPolicyVersion(Exception):
    """The user already approved this version label (or identical payload)."""


@dataclass(frozen=True)
class ApprovalOutcome:
    policy: TargetPolicyVersion
    decision: DecisionLogEntry


def _canonical_goals_bytes(goals: list[dict[str, str]]) -> bytes:
    return json.dumps(
        {"goals": sorted(goals, key=lambda g: g["nutrient"])},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def target_policy_payload_sha256(goals: list[dict[str, str]]) -> str:
    """Return the existing ADR-018 semantic identity for normalized goals."""

    return hashlib.sha256(_canonical_goals_bytes(goals)).hexdigest()


def parse_goals(raw_goals: Any) -> list[dict[str, str]]:
    """Validate + normalize a raw goals list into the stored payload shape."""
    from decimal import Decimal, InvalidOperation

    if not isinstance(raw_goals, list) or not raw_goals:
        raise InvalidTargetPolicy("goals must be a non-empty list")
    normalized: dict[NutrientKey, NutrientGoal] = {}
    for entry in raw_goals:
        if not isinstance(entry, dict):
            raise InvalidTargetPolicy("each goal must be an object")
        nutrient_raw = entry.get("nutrient")
        kind_raw = entry.get("kind")
        value_raw = entry.get("value")
        weight_raw = entry.get("weight")
        if not (
            isinstance(nutrient_raw, str)
            and nutrient_raw
            and isinstance(kind_raw, str)
            and kind_raw
            and isinstance(value_raw, str)
            and value_raw
            and isinstance(weight_raw, str)
            and weight_raw
        ):
            raise InvalidTargetPolicy("goal fields nutrient/kind/value/weight are required strings")
        try:
            nutrient = NutrientKey(nutrient_raw)
        except ValueError as exc:
            raise InvalidTargetPolicy(f"unknown nutrient: {nutrient_raw}") from exc
        if kind_raw not in VALID_GOAL_KINDS:
            raise InvalidTargetPolicy(f"unknown goal kind: {kind_raw}")
        try:
            # Domain-object reconstruction IS the validation: TargetSet/NutrientGoal
            # enforce positive values and non-negative weights (Decimal-only).
            reconstructed = NutrientGoal(
                kind=GoalKind(kind_raw), value=Decimal(value_raw), weight=Decimal(weight_raw)
            )
        except (ValueError, ArithmeticError, InvalidOperation) as exc:
            raise InvalidTargetPolicy(f"invalid decimal value/weight for {nutrient_raw}") from exc
        if nutrient in normalized:
            raise InvalidTargetPolicy(f"duplicate nutrient entry: {nutrient_raw}")
        normalized[nutrient] = reconstructed
    return [
        {
            "kind": goal.kind.value,
            "nutrient": key.value,
            "value": str(goal.value),
            "weight": str(goal.weight),
        }
        for key, goal in sorted(normalized.items(), key=lambda item: item[0].value)
    ]


def build_domain_target_set(goals_jsonb: list[dict[str, str]]) -> TargetSet:
    """Reconstruct the frozen M3 TargetSet from a stored payload."""
    from decimal import Decimal

    goals = {
        NutrientKey(goal["nutrient"]): NutrientGoal(
            kind=GoalKind(goal["kind"]),
            value=Decimal(goal["value"]),
            weight=Decimal(goal["weight"]),
        )
        for goal in goals_jsonb
    }
    return TargetSet(policy_version="stored", goals=goals)


class ApproveTargetPolicyUseCase:
    def __init__(self, repository: TargetPolicyRepository, clock: Clock, ids: IdGenerator) -> None:
        self._repository = repository
        self._clock = clock
        self._ids = ids

    def execute(
        self,
        user_id: UUID,
        policy_version_label: str,
        raw_goals: Any,
        rationale: str,
    ) -> ApprovalOutcome:
        if not isinstance(policy_version_label, str) or not policy_version_label.strip():
            raise InvalidTargetPolicy("policy_version is required")
        if len(policy_version_label) > 200:
            raise InvalidTargetPolicy("policy_version too long")
        if not isinstance(rationale, str) or not rationale.strip():
            raise InvalidTargetPolicy("rationale is required")
        if len(rationale) > RATIONALE_MAX_LENGTH:
            raise InvalidTargetPolicy("rationale too long")

        goals = parse_goals(raw_goals)
        # Round-trip through the frozen domain object to guarantee the stored
        # payload always reconstructs (defense in depth beyond field checks).
        build_domain_target_set(goals)

        payload_sha256 = target_policy_payload_sha256(goals)
        now = self._clock.now()
        policy = TargetPolicyVersion(
            version_id=self._ids.new_id(),
            user_id=user_id,
            policy_version=policy_version_label.strip(),
            goals_jsonb=goals,
            payload_sha256=payload_sha256,
            created_at=now,
        )
        decision = DecisionLogEntry(
            decision_id=self._ids.new_id(),
            user_id=user_id,
            subject="target_policy",
            decision="approved",
            rationale=rationale.strip(),
            policy_version_id=policy.version_id,
            decided_at=now,
        )
        try:
            self._repository.save_approved(
                policy, rationale=decision.rationale, decided_by_clock=now
            )
        except TargetPolicyVersionExistsError as exc:
            raise DuplicateTargetPolicyVersion(str(exc)) from exc
        return ApprovalOutcome(policy=policy, decision=decision)


__all__ = [
    "ApprovalOutcome",
    "ApproveTargetPolicyUseCase",
    "DuplicateTargetPolicyVersion",
    "InvalidTargetPolicy",
    "build_domain_target_set",
    "target_policy_payload_sha256",
]
