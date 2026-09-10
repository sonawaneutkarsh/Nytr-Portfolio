"""Persistence-shaped artifacts of the daily-plan subsystem (M6, ADR-017/018).

Pure domain records + the deterministic plan-document builder. No I/O imports
(hashlib usage lives in the application layer); serialization of these
documents via ``nutrition.serialization.to_json_bytes`` IS the canonical
artifact contract.

Determinism rules (ADR-017 §3):
- The document contains ONLY decision-relevant state.
- Execution timing (``plan_at``, clock reads) and ``menu_fetched_at`` are
  deliberately EXCLUDED: staleness is an outcome property that monotonically
  tightens with time, so an earlier attempt can only ever be MORE permissive,
  making replay-by-fingerprint safe in every reachable ordering.
- Everything included is deterministic given identical resolved inputs and
  policy versions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from nutrition_agent.domain.nutrition.serialization import facts_to_dict
from nutrition_agent.domain.planning.planner import (
    PlannerResult,
    PlannerStatus,
    RankedCandidate,
    SlotResult,
)
from nutrition_agent.domain.stacks.entities import NutrientKey


class PlanRunStatus(StrEnum):
    COMPLETED = "completed"
    NO_PLAN = "no_plan"


@dataclass(frozen=True)
class PlanRun:
    run_id: UUID
    user_id: UUID
    requested_for_date: date
    timezone: str
    inputs_fingerprint: str
    status: PlanRunStatus
    reason_codes: tuple[str, ...]
    started_at: datetime
    finished_at: datetime
    target_policy_version_id: UUID | None = None


@dataclass(frozen=True)
class PlanVersion:
    version_id: UUID
    run_id: UUID
    plan_jsonb: dict[str, object]
    plan_canonical: str
    plan_sha256: str


@dataclass(frozen=True)
class PlanItem:
    """Queryable per-candidate metadata; provenance pins copied AT PLAN TIME.

    ``profile_row_ids`` pins the immutable nutrition_profile rows referenced;
    ``profile_content_sha256s`` pins their content identity (the sha the
    artifact itself records). Either alone would suffice for reproducibility —
    carrying both makes SQL-level joins and artifact-level replay independent.
    """

    item_id: UUID
    version_id: UUID
    slot_index: int
    context: str
    rank: int
    candidate_id: str
    menu_period: str
    food_ids: tuple[UUID, ...]
    offering_ids: tuple[UUID, ...]
    profile_row_ids: tuple[UUID, ...]
    profile_content_sha256s: tuple[str, ...]
    score_total: str
    calories_kcal: str


@dataclass(frozen=True)
class TargetPolicyVersion:
    """One immutable approved target-policy version (ADR-018)."""

    version_id: UUID
    user_id: UUID
    policy_version: str
    goals_jsonb: list[dict[str, str]]
    payload_sha256: str
    created_at: datetime | None = None


@dataclass(frozen=True)
class DecisionLogEntry:
    decision_id: UUID
    user_id: UUID
    subject: str
    decision: str
    rationale: str
    policy_version_id: UUID
    decided_at: datetime | None = None


def plan_run_status_of(result: PlannerResult) -> PlanRunStatus:
    if result.status is PlannerStatus.OK:
        return PlanRunStatus.COMPLETED
    return PlanRunStatus.NO_PLAN


def plan_reason_codes(result: PlannerResult) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for reason in result.failure_reasons:
        seen.setdefault(reason.value)
    for slot in result.slots:
        for reason in slot.failure_reasons:
            seen.setdefault(reason.value)
    return tuple(sorted(seen))


def candidate_document(candidate: RankedCandidate) -> dict[str, object]:
    if candidate.estimated_details is not None:
        details = candidate.estimated_details
        availability = details.availability
        totals_document = facts_to_dict(candidate.meal.totals)
        quantities = totals_document["quantities"]
        assert isinstance(quantities, dict)
        adjustments = {
            key: str(value)
            for key, value in candidate.score_breakdown.items()
            if key.startswith("uncertainty:") or key.startswith("preference:")
        }
        nutritional_breakdown = {
            key: str(value)
            for key, value in candidate.score_breakdown.items()
            if key not in adjustments
        }
        return {
            "candidate_id": candidate.candidate_id,
            "candidate_kind": candidate.candidate_kind.value,
            "calories_kcal": quantities.get(NutrientKey.CALORIES_KCAL.value),
            "category_names": list(candidate.category_names),
            "configurable_estimate": {
                "availability": {
                    "campus_id": details.definition.campus_id,
                    "category_name": availability.category_name,
                    "food_id": str(availability.food_id),
                    "menu_period": details.menu_period.value,
                    "menu_snapshot_sha256": details.menu_snapshot_sha256,
                    "nutrition_snapshot_sha256": availability.nutrition_snapshot_sha256,
                    "nutrition_source_state": (
                        availability.nutrition_source_state.value
                        if availability.nutrition_source_state
                        else None
                    ),
                    "occurrence_ordinal": availability.occurrence_ordinal,
                    "offering_id": str(availability.offering_id),
                    "service_date": details.service_date.isoformat(),
                    "source_page_snapshot_sha256": availability.snapshot_sha256,
                    "source_mid": availability.source_mid,
                },
                "definition": details.definition.decision_document(),
                "evidence_digest": details.definition.evidence_digest,
                "nutritional_score": {
                    "breakdown": nutritional_breakdown,
                    "total": str(
                        sum(
                            (Decimal(value) for value in nutritional_breakdown.values()),
                            Decimal(0),
                        )
                    ),
                },
                "score_adjustments": adjustments,
            },
            "dietary_tags": sorted(set(candidate.dietary_tags)),
            "lines": [],
            "provenance": {
                "food_ids": [str(availability.food_id)],
                "offering_ids": [str(availability.offering_id)],
                "profile_content_sha256s": [],
            },
            "score": {
                "breakdown": {key: str(value) for key, value in candidate.score_breakdown.items()},
                "total": str(candidate.score_total),
            },
            "totals": totals_document,
        }
    meal = candidate.meal
    lines: list[dict[str, object]] = []
    profile_pins: list[str] = []
    food_ids: list[str] = []
    offering_ids: list[str] = []
    for ref in candidate.line_refs:
        lines.append(
            {
                "category_name": ref.category_name,
                "food_id": str(ref.food_id),
                "name_normalized": ref.name_normalized,
                "occurrence_ordinal": ref.occurrence_ordinal,
                "offering_id": str(ref.offering_id),
                "parser_version": ref.parser_version,
                "profile_content_sha256": ref.profile_sha256,
                "servings": str(ref.line.servings),
                "source_mid": ref.source_mid,
            }
        )
        profile_pins.append(ref.profile_sha256)
        food_ids.append(str(ref.food_id))
        offering_ids.append(str(ref.offering_id))
    totals_document = facts_to_dict(meal.totals)
    quantities = totals_document["quantities"]
    assert isinstance(quantities, dict)
    return {
        "candidate_id": candidate.candidate_id,
        "calories_kcal": quantities.get(NutrientKey.CALORIES_KCAL.value),
        "category_names": list(candidate.category_names),
        "dietary_tags": sorted(set(candidate.dietary_tags)),
        "lines": lines,
        "provenance": {
            "offering_ids": offering_ids,
            "food_ids": food_ids,
            "profile_content_sha256s": profile_pins,
        },
        "score": {
            "breakdown": {key: str(value) for key, value in candidate.score_breakdown.items()},
            "total": str(candidate.score_total),
        },
        "totals": totals_document,
    }


def slot_document(slot: SlotResult) -> dict[str, object]:
    return {
        "context": slot.context.value,
        "menu_period": slot.menu_period.value,
        "status": slot.status.value,
        "window": [slot.window[0].isoformat(), slot.window[1].isoformat()],
        # Planner-emission order preserved; rejection counts stable-keyed.
        "failure_reasons": [reason.value for reason in slot.failure_reasons],
        "rejection_counts": dict(sorted(slot.rejection_counts.items())),
        "rejection_details": list(slot.rejection_details),
        "candidates": [candidate_document(c) for c in slot.candidates],
    }


def plan_document(result: PlannerResult) -> dict[str, object]:
    """The deterministic daily-plan artifact (the hash input; ADR-017).

    Excluded by design: menu_fetched_at / any clock read / user id / any
    database or environment ordering.
    """
    document: dict[str, object] = {
        "artifact_kind": "daily_plan",
        "plan_date": result.plan_date.isoformat(),
        "policy_versions": {
            "engine": result.engine_policy_version,
            "planner": result.planner_policy_version,
            "schedule": result.schedule_version,
            "target": result.target_policy_version,
        },
        "menu_snapshot_sha256": result.menu_snapshot_sha256,
        "slots": [slot_document(slot) for slot in result.slots],
        "status": result.status.value,
    }
    if result.dietary_policy_version is not None:
        policy_versions = document["policy_versions"]
        assert isinstance(policy_versions, dict)
        policy_versions["dietary"] = result.dietary_policy_version
    if result.failure_reasons:
        document["failure_reasons"] = [reason.value for reason in result.failure_reasons]
    return document
