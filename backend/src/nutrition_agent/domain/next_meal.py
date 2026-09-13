"""Pure deterministic next-meal allocation and recommendation (M16A)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo

from nutrition_agent.domain.configurable_meals import ConfigurableMealDefinition
from nutrition_agent.domain.nutrition.ledger import DailyNutritionLedger
from nutrition_agent.domain.nutrition.targets import GoalKind, NutrientGoal, TargetSet
from nutrition_agent.domain.planning.artifacts import candidate_document
from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.planning.eligibility import ReasonCode, freshness_gate
from nutrition_agent.domain.planning.menu_view import MenuDayView
from nutrition_agent.domain.planning.planner import PlannerStatus, rank_candidates_for_slot
from nutrition_agent.domain.planning.policy import PlannerPolicy, SlotPolicy
from nutrition_agent.domain.planning.schedule import ScheduleException, WeeklySchedule, resolve_day
from nutrition_agent.domain.stacks.entities import NutrientKey


class NextMealStatus(StrEnum):
    RECOMMENDED = "recommended"
    NO_APPROVED_TARGET_POLICY = "no_approved_target_policy"
    NO_APPROVED_CALORIE_TARGET = "no_approved_calorie_target"
    NO_APPROVED_PROTEIN_TARGET = "no_approved_protein_target"
    UNSUPPORTED_TARGET_SEMANTICS = "unsupported_target_semantics"
    INCOMPLETE_LEDGER_NUTRITION = "incomplete_ledger_nutrition"
    NO_REMAINING_MEAL_OPPORTUNITY = "no_remaining_meal_opportunity"
    DAILY_CALORIE_TARGET_MET = "daily_calorie_target_met"
    MENU_DATA_UNAVAILABLE = "menu_data_unavailable"
    STALE_MENU_DATA = "stale_menu_data"
    NO_ELIGIBLE_CANDIDATE = "no_eligible_candidate"


@dataclass(frozen=True)
class NextMealPolicy:
    policy_version: str
    calorie_quantum: Decimal
    protein_quantum: Decimal
    alternative_limit: int


NEXT_MEAL_POLICY_V2 = NextMealPolicy(
    policy_version="next-meal.remaining-opportunities.v2",
    calorie_quantum=Decimal("1"),
    protein_quantum=Decimal("0.1"),
    alternative_limit=3,
)


@dataclass(frozen=True)
class NextMealRecommendation:
    recommendation_id: UUID
    user_id: UUID
    client_request_id: UUID
    local_date: date
    timezone: str
    decision_at: datetime
    target_policy_version_id: UUID | None
    status: NextMealStatus
    reason_codes: tuple[str, ...]
    inputs_digest: str
    artifact_jsonb: dict[str, object]
    artifact_sha256: str
    created_at: datetime


@dataclass(frozen=True)
class PersistNextMealOutcome:
    recommendation: NextMealRecommendation
    created: bool


def _digest(document: dict[str, object]) -> str:
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def ledger_evidence_document(ledger: DailyNutritionLedger) -> dict[str, object]:
    """Freeze the factual ledger inputs used by a recommendation or failure."""

    target = ledger.target
    return {
        "authorities": [value.value for value in ledger.authorities],
        "consumed_item_count": ledger.consumed_item_count,
        "consumed_entry_ids": [str(item.entry_id) for item in ledger.consumed_items],
        "consumed_items": [
            {
                "authority": item.authority.value,
                "calories_kcal": (
                    str(item.calories_kcal) if item.calories_kcal is not None else None
                ),
                "candidate_id": item.candidate_id,
                "confidence": item.confidence,
                "custom_food_id": str(item.custom_food_id) if item.custom_food_id else None,
                "custom_food_version_id": (
                    str(item.custom_food_version_id) if item.custom_food_version_id else None
                ),
                "entry_id": str(item.entry_id),
                "item_name": item.item_name,
                "meal_context": item.meal_context,
                "plan_item_id": str(item.plan_item_id) if item.plan_item_id else None,
                "plan_run_id": str(item.plan_run_id) if item.plan_run_id else None,
                "plan_version_id": str(item.plan_version_id) if item.plan_version_id else None,
                "protein_g": str(item.protein_g) if item.protein_g is not None else None,
                "provenance_summary": item.provenance_summary,
                "recorded_at": item.recorded_at.isoformat(),
                "source_system": item.source_system,
                "unknown_nutrients": list(item.unknown_nutrients),
            }
            for item in ledger.consumed_items
        ],
        "known_calories_consumed": (
            str(ledger.known_calories_consumed)
            if ledger.known_calories_consumed is not None
            else None
        ),
        "known_protein_g_consumed": (
            str(ledger.known_protein_g_consumed)
            if ledger.known_protein_g_consumed is not None
            else None
        ),
        "nutrition_completeness": ledger.nutrition_completeness.value,
        "reason_codes": list(ledger.reason_codes),
        "remaining_calories": (
            str(ledger.remaining_known_calories)
            if ledger.remaining_known_calories is not None
            else None
        ),
        "remaining_protein_g": (
            str(ledger.remaining_known_protein_g)
            if ledger.remaining_known_protein_g is not None
            else None
        ),
        "target": (
            {
                "calories_goal_kind": target.calories_goal_kind,
                "calories_kcal": (
                    str(target.calories_kcal) if target.calories_kcal is not None else None
                ),
                "policy_version": target.policy_version,
                "policy_version_id": str(target.policy_version_id),
                "protein_g": str(target.protein_g) if target.protein_g is not None else None,
                "protein_goal_kind": target.protein_goal_kind,
            }
            if target is not None
            else None
        ),
        "unknown_nutrients": list(ledger.unknown_nutrients),
    }


def failure_artifact(
    *,
    local_date: date,
    timezone: str,
    decision_at: datetime,
    status: NextMealStatus,
    reason_codes: tuple[str, ...],
    ledger: DailyNutritionLedger | None = None,
    policy: NextMealPolicy = NEXT_MEAL_POLICY_V2,
) -> dict[str, object]:
    artifact: dict[str, object] = {
        "artifact_kind": "next_meal_recommendation",
        "artifact_version": "m16a.v1",
        "decision_at": decision_at.isoformat(),
        "local_date": local_date.isoformat(),
        "next_meal_policy_version": policy.policy_version,
        "reason_codes": list(reason_codes),
        "status": status.value,
        "timezone": timezone,
    }
    if ledger is not None:
        artifact["ledger"] = ledger_evidence_document(ledger)
    return artifact


def allocate_remaining_targets(
    *,
    remaining_calories: Decimal,
    remaining_protein_g: Decimal,
    protein_kind: GoalKind,
    opportunity_count: int,
    policy: NextMealPolicy = NEXT_MEAL_POLICY_V2,
) -> TargetSet:
    """Allocate an equal share, with the final opportunity taking all remainder."""

    if opportunity_count <= 0:
        raise ValueError("opportunity_count must be positive")
    divisor = Decimal(opportunity_count)
    calories = (
        remaining_calories
        if opportunity_count == 1
        else (remaining_calories / divisor).quantize(
            policy.calorie_quantum, rounding=ROUND_HALF_EVEN
        )
    )
    protein_need_active = remaining_protein_g > 0
    protein = (
        remaining_protein_g
        if opportunity_count == 1
        else (remaining_protein_g / divisor).quantize(
            policy.protein_quantum, rounding=ROUND_HALF_EVEN
        )
    )
    # Positive goals are required by the established target evaluator. A
    # satisfied/overshot protein goal needs no additional chase; the harmless
    # positive placeholder has zero weight and therefore cannot affect ranking.
    calories = max(calories, policy.calorie_quantum)
    protein = max(protein, policy.protein_quantum)
    return TargetSet(
        policy_version=policy.policy_version,
        goals={
            NutrientKey.CALORIES_KCAL: NutrientGoal(
                kind=GoalKind.TARGET, value=calories, weight=Decimal("1")
            ),
            NutrientKey.PROTEIN_G: NutrientGoal(
                kind=protein_kind,
                value=protein,
                weight=Decimal("1") if protein_need_active else Decimal(0),
            ),
        },
    )


def build_next_meal_artifact(
    *,
    local_date: date,
    timezone: str,
    decision_at: datetime,
    ledger: DailyNutritionLedger,
    schedule: WeeklySchedule,
    exceptions: tuple[ScheduleException, ...],
    menu: MenuDayView,
    planner_policy: PlannerPolicy,
    slot_policies: dict[MealContext, SlotPolicy],
    configurable_meal_definitions: tuple[ConfigurableMealDefinition, ...],
    target_policy_version_id: UUID,
    target_policy_version: str,
    policy: NextMealPolicy = NEXT_MEAL_POLICY_V2,
) -> tuple[NextMealStatus, tuple[str, ...], dict[str, object]]:
    """Return a decision document; never mutates targets, ledger, menu, or plans."""

    missing_recorded_macros = any(
        item.calories_kcal is None or item.protein_g is None for item in ledger.consumed_items
    )
    if missing_recorded_macros:
        status = NextMealStatus.INCOMPLETE_LEDGER_NUTRITION
        reasons: tuple[str, ...] = ("recorded_calories_or_protein_missing",)
        return (
            status,
            reasons,
            failure_artifact(
                local_date=local_date,
                timezone=timezone,
                decision_at=decision_at,
                status=status,
                reason_codes=reasons,
                ledger=ledger,
                policy=policy,
            ),
        )
    target = ledger.target
    if target is None:
        status = NextMealStatus.NO_APPROVED_TARGET_POLICY
        reasons = ("approved_target_required",)
        return (
            status,
            reasons,
            failure_artifact(
                local_date=local_date,
                timezone=timezone,
                decision_at=decision_at,
                status=status,
                reason_codes=reasons,
                ledger=ledger,
                policy=policy,
            ),
        )
    if target.calories_kcal is None or target.calories_goal_kind is None:
        status = NextMealStatus.NO_APPROVED_CALORIE_TARGET
        reasons = ("approved_calorie_target_required",)
        return (
            status,
            reasons,
            failure_artifact(
                local_date=local_date,
                timezone=timezone,
                decision_at=decision_at,
                status=status,
                reason_codes=reasons,
                ledger=ledger,
                policy=policy,
            ),
        )
    if target.protein_g is None or target.protein_goal_kind is None:
        status = NextMealStatus.NO_APPROVED_PROTEIN_TARGET
        reasons = ("approved_protein_target_required",)
        return (
            status,
            reasons,
            failure_artifact(
                local_date=local_date,
                timezone=timezone,
                decision_at=decision_at,
                status=status,
                reason_codes=reasons,
                ledger=ledger,
                policy=policy,
            ),
        )
    try:
        calorie_kind = GoalKind(target.calories_goal_kind or "")
        protein_kind = GoalKind(target.protein_goal_kind or "")
    except ValueError:
        calorie_kind = protein_kind = GoalKind.ADVISORY
    remaining_calories = (
        ledger.remaining_known_calories if ledger.consumed_item_count > 0 else target.calories_kcal
    )
    remaining_protein = (
        ledger.remaining_known_protein_g if ledger.consumed_item_count > 0 else target.protein_g
    )
    if (
        calorie_kind is not GoalKind.TARGET
        or protein_kind not in {GoalKind.TARGET, GoalKind.FLOOR}
        or target.calories_kcal is None
        or target.protein_g is None
        or remaining_calories is None
        or remaining_protein is None
    ):
        status = NextMealStatus.UNSUPPORTED_TARGET_SEMANTICS
        reasons = ("calorie_target_and_protein_target_or_floor_required",)
        return (
            status,
            reasons,
            failure_artifact(
                local_date=local_date,
                timezone=timezone,
                decision_at=decision_at,
                status=status,
                reason_codes=reasons,
                ledger=ledger,
                policy=policy,
            ),
        )
    if remaining_calories <= 0:
        status = NextMealStatus.DAILY_CALORIE_TARGET_MET
        reasons = ("no_positive_calorie_remainder",)
        return (
            status,
            reasons,
            failure_artifact(
                local_date=local_date,
                timezone=timezone,
                decision_at=decision_at,
                status=status,
                reason_codes=reasons,
                ledger=ledger,
                policy=policy,
            ),
        )

    zone = ZoneInfo(timezone)
    local_now = decision_at.astimezone(zone)
    slots = resolve_day(schedule, exceptions, local_date, planner_policy.context_period)
    remaining_slots = tuple(
        slot for slot in slots if local_now.date() < local_date or local_now.time() < slot.window[1]
    )
    if not remaining_slots:
        status = NextMealStatus.NO_REMAINING_MEAL_OPPORTUNITY
        reasons = ("all_stacks_windows_elapsed",)
        return (
            status,
            reasons,
            failure_artifact(
                local_date=local_date,
                timezone=timezone,
                decision_at=decision_at,
                status=status,
                reason_codes=reasons,
                ledger=ledger,
                policy=policy,
            ),
        )

    if menu.service_date != local_date:
        status = NextMealStatus.MENU_DATA_UNAVAILABLE
        reasons = ("menu_service_date_mismatch",)
        artifact = failure_artifact(
            local_date=local_date,
            timezone=timezone,
            decision_at=decision_at,
            status=status,
            reason_codes=reasons,
            ledger=ledger,
            policy=policy,
        )
        artifact["menu_snapshot_sha256"] = menu.snapshot_sha256
        artifact["menu_service_date"] = menu.service_date.isoformat()
        artifact["schedule_version"] = schedule.version
        artifact["target_policy_version_id"] = str(target_policy_version_id)
        return status, reasons, artifact

    stale_menu = freshness_gate(
        menu.fetched_at, decision_at, planner_policy.max_menu_age.total_seconds()
    )
    if stale_menu is not None:
        status = NextMealStatus.STALE_MENU_DATA
        reasons = ("menu_snapshot_stale",)
        artifact = failure_artifact(
            local_date=local_date,
            timezone=timezone,
            decision_at=decision_at,
            status=status,
            reason_codes=reasons,
            ledger=ledger,
            policy=policy,
        )
        artifact.update(
            {
                "menu_snapshot_sha256": menu.snapshot_sha256,
                "schedule_version": schedule.version,
                "target_policy_version_id": str(target_policy_version_id),
            }
        )
        return status, reasons, artifact

    # Allocation has already happened above; prevent daily share slicing.
    effective_policy = replace(
        planner_policy,
        version=f"{planner_policy.version}+{policy.policy_version}",
        slot_shares={},
    )
    eligibility_targets = allocate_remaining_targets(
        remaining_calories=remaining_calories,
        remaining_protein_g=max(remaining_protein, Decimal(0)),
        protein_kind=protein_kind,
        opportunity_count=1,
        policy=policy,
    )
    eligible_slots = []
    rejected_reasons: list[ReasonCode] = []
    for candidate_slot in remaining_slots:
        preflight = rank_candidates_for_slot(
            slot=candidate_slot,
            plan_date=local_date,
            plan_at=decision_at,
            menu=menu,
            policy=effective_policy,
            slot_policies=slot_policies,
            targets=eligibility_targets,
            configurable_meal_definitions=configurable_meal_definitions,
        )
        if preflight.status is PlannerStatus.OK and preflight.candidates:
            eligible_slots.append(candidate_slot)
        else:
            rejected_reasons.extend(preflight.failure_reasons)
    if not eligible_slots:
        status = NextMealStatus.NO_ELIGIBLE_CANDIDATE
        reasons = tuple(dict.fromkeys(reason.value for reason in rejected_reasons)) or (
            "no_ranked_candidate",
        )
        artifact = failure_artifact(
            local_date=local_date,
            timezone=timezone,
            decision_at=decision_at,
            status=status,
            reason_codes=reasons,
            ledger=ledger,
            policy=policy,
        )
        artifact.update(
            {
                "menu_snapshot_sha256": menu.snapshot_sha256,
                "schedule_version": schedule.version,
                "target_policy_version_id": str(target_policy_version_id),
            }
        )
        return status, reasons, artifact

    allocated = allocate_remaining_targets(
        remaining_calories=remaining_calories,
        remaining_protein_g=max(remaining_protein, Decimal(0)),
        protein_kind=protein_kind,
        opportunity_count=len(eligible_slots),
        policy=policy,
    )
    slot = eligible_slots[0]
    ranked = rank_candidates_for_slot(
        slot=slot,
        plan_date=local_date,
        plan_at=decision_at,
        menu=menu,
        policy=effective_policy,
        slot_policies=slot_policies,
        targets=allocated,
        configurable_meal_definitions=configurable_meal_definitions,
    )
    if ranked.status is not PlannerStatus.OK or not ranked.candidates:  # pragma: no cover
        raise AssertionError("eligible next-meal slot became ineligible after target allocation")

    candidates = ranked.candidates[: 1 + policy.alternative_limit]
    allocation = allocated.goals
    effective_ledger = ledger_evidence_document(ledger)
    effective_ledger["remaining_calories"] = str(remaining_calories)
    effective_ledger["remaining_protein_g"] = str(remaining_protein)
    effective_ledger["macro_evidence_basis"] = (
        "no_recorded_items_zero_consumed"
        if ledger.consumed_item_count == 0
        else "recorded_calories_and_protein"
    )
    artifact = {
        "artifact_kind": "next_meal_recommendation",
        "artifact_version": "m16a.v1",
        "status": NextMealStatus.RECOMMENDED.value,
        "reason_codes": [],
        "decision_at": decision_at.isoformat(),
        "local_date": local_date.isoformat(),
        "timezone": timezone,
        "next_meal_policy_version": policy.policy_version,
        "planner_policy_version": planner_policy.version,
        "schedule_version": schedule.version,
        "target_policy_version": target_policy_version,
        "target_policy_version_id": str(target_policy_version_id),
        "menu_snapshot_sha256": menu.snapshot_sha256,
        "nutrition_authorities": [value.value for value in ledger.authorities],
        "ledger": effective_ledger,
        "remaining_opportunities": [
            {
                "context": entry.context.value,
                "menu_period": entry.menu_period.value,
                "window": [entry.window[0].isoformat(), entry.window[1].isoformat()],
            }
            for entry in eligible_slots
        ],
        "selected_opportunity": {
            "context": slot.context.value,
            "menu_period": slot.menu_period.value,
            "window": [slot.window[0].isoformat(), slot.window[1].isoformat()],
        },
        "allocated_targets": {
            "calories_kcal": str(allocation[NutrientKey.CALORIES_KCAL].value),
            "calories_goal_kind": GoalKind.TARGET.value,
            "protein_g": str(allocation[NutrientKey.PROTEIN_G].value),
            "protein_goal_kind": protein_kind.value,
            "protein_scoring_active": allocation[NutrientKey.PROTEIN_G].weight != 0,
            "opportunity_count": len(eligible_slots),
            "rule": "equal_share_across_remaining_opportunities_final_takes_remainder",
        },
        "selected": candidate_document(candidates[0]),
        "alternatives": [candidate_document(candidate) for candidate in candidates[1:]],
    }
    return NextMealStatus.RECOMMENDED, (), artifact


def recommendation_from_artifact(
    *,
    recommendation_id: UUID,
    user_id: UUID,
    client_request_id: UUID,
    local_date: date,
    timezone: str,
    decision_at: datetime,
    target_policy_version_id: UUID | None,
    status: NextMealStatus,
    reason_codes: tuple[str, ...],
    artifact: dict[str, object],
) -> NextMealRecommendation:
    inputs = {
        key: artifact[key]
        for key in sorted(artifact)
        if key not in {"selected", "alternatives", "status", "reason_codes"}
    }
    return NextMealRecommendation(
        recommendation_id=recommendation_id,
        user_id=user_id,
        client_request_id=client_request_id,
        local_date=local_date,
        timezone=timezone,
        decision_at=decision_at,
        target_policy_version_id=target_policy_version_id,
        status=status,
        reason_codes=reason_codes,
        inputs_digest=_digest(inputs),
        artifact_jsonb=artifact,
        artifact_sha256=_digest(artifact),
        created_at=decision_at,
    )


__all__ = [
    "NEXT_MEAL_POLICY_V2",
    "NextMealPolicy",
    "NextMealRecommendation",
    "NextMealStatus",
    "PersistNextMealOutcome",
    "allocate_remaining_targets",
    "build_next_meal_artifact",
    "failure_artifact",
    "ledger_evidence_document",
    "recommendation_from_artifact",
]
