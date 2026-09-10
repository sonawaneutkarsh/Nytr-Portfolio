"""Deterministic daily-plan generation use case (M6, ADR-017).

Orchestrates existing frozen capabilities only: schedule resolution +
deterministic planning live in domain/planning (M4); byte-stable serialization
lives in domain/nutrition (M3). This module adds hashing/fingerprinting,
persistence, and nothing else — no arithmetic, no ranking, no LLM.

Idempotency design (ADR-017 §2): ``plan_run`` carries a UNIQUE
(user_id, requested_for_date, inputs_fingerprint) key. The fingerprint binds
ONLY decision-relevant identity and EXCLUDES all clock reads — staleness is an
outcome property that monotonically tightens with time, so an earlier attempt
can only ever be more permissive, keeping replay-by-fingerprint safe in every
reachable ordering.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from nutrition_agent.application.ports import (
    Clock,
    IdGenerator,
    PersistedPlanView,
    PlanRunRepository,
)
from nutrition_agent.domain.configurable_meals import ConfigurableMealDefinition
from nutrition_agent.domain.nutrition.serialization import to_json_bytes
from nutrition_agent.domain.nutrition.targets import TargetSet
from nutrition_agent.domain.planning.artifacts import (
    PlanItem,
    PlanRun,
    PlanRunStatus,
    PlanVersion,
    plan_document,
    plan_reason_codes,
    plan_run_status_of,
)
from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.planning.menu_view import MenuDayView
from nutrition_agent.domain.planning.planner import PlannerResult, generate_daily_plan
from nutrition_agent.domain.planning.policy import PlannerPolicy, SlotPolicy
from nutrition_agent.domain.planning.schedule import ScheduleException, WeeklySchedule
from nutrition_agent.domain.stacks.entities import NutrientKey


@dataclass(frozen=True)
class DailyPlanInputs:
    """Everything that identically determines one day's plan (ADR-017 §2)."""

    user_id: UUID
    requested_for_date: date
    timezone: str
    schedule: WeeklySchedule
    exceptions: Sequence[ScheduleException]
    menu: MenuDayView
    policy: PlannerPolicy
    slot_policies: Mapping[MealContext, SlotPolicy]
    targets: TargetSet
    target_policy_version_id: UUID | None = None
    """Offering id -> immutable nutrition_profile row id captured at read time.

    Content identity (sha256) lives inside the planning read models; row-id
    pins for ``plan_item`` come from the repository link and are therefore an
    explicit parallel map rather than hidden state on the view."""
    offering_profile_ids: Mapping[str, UUID] | None = None
    configurable_meal_definitions: Sequence[ConfigurableMealDefinition] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "configurable_meal_definitions",
            tuple(self.configurable_meal_definitions),
        )


@dataclass(frozen=True)
class DailyPlanOutcome:
    replayed: bool
    run: PlanRun | None = None
    version: PlanVersion | None = None
    items: tuple[PlanItem, ...] = ()
    replay_view: PersistedPlanView | None = None


def _target_payload_sha256(targets: TargetSet) -> str:
    goals = [
        {
            "kind": goal.kind.value,
            "nutrient": key.value,
            "value": str(goal.value),
            "weight": str(goal.weight),
        }
        for key, goal in sorted(targets.goals.items(), key=lambda item: item[0].value)
    ]
    canonical = json.dumps(
        {"goals": goals, "policy_version": targets.policy_version},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _exception_digest(exceptions: Sequence[ScheduleException]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for exc in sorted(exceptions, key=lambda e: (e.date.isoformat(), e.version)):
        blocks: list[dict[str, object]] | None = None
        if exc.day is not None:
            blocks = [
                {
                    "context": block.meal_context.value if block.meal_context else None,
                    "end": block.end.isoformat(),
                    "kind": block.kind.value,
                    "label": block.label,
                    "start": block.start.isoformat(),
                }
                for block in exc.day.blocks
            ]
        entries.append({"blocks": blocks, "date": exc.date.isoformat(), "version": exc.version})
    return entries


def _slot_policy_digest(
    slot_policies: Mapping[MealContext, SlotPolicy],
) -> list[list[str]]:
    return [
        [
            context.value,
            ",".join(sorted(tag.value for tag in slot_policy.exclude_tags)),
            str(slot_policy.calorie_min),
            str(slot_policy.calorie_max),
        ]
        for context, slot_policy in sorted(slot_policies.items(), key=lambda item: item[0].value)
    ]


def compute_inputs_fingerprint(inputs: DailyPlanInputs) -> str:
    payload: dict[str, object] = {
        "exceptions": _exception_digest(inputs.exceptions),
        "menu_snapshot_sha256": inputs.menu.snapshot_sha256,
        "planner_policy_version": inputs.policy.version,
        "requested_for_date": inputs.requested_for_date.isoformat(),
        "schedule_version": inputs.schedule.version,
        "slot_policies": _slot_policy_digest(inputs.slot_policies),
        "target_payload_sha256": _target_payload_sha256(inputs.targets),
        "target_policy_version_id": (
            str(inputs.target_policy_version_id) if inputs.target_policy_version_id else None
        ),
        "timezone": inputs.timezone,
        "user_id": str(inputs.user_id),
    }
    if inputs.configurable_meal_definitions:
        payload["menu_campus_id"] = inputs.menu.campus_id
        payload["configurable_meal_definitions"] = [
            {
                "definition": definition.decision_document(),
                "evidence_digest": definition.evidence_digest,
            }
            for definition in sorted(
                inputs.configurable_meal_definitions,
                key=lambda item: (
                    item.definition_id,
                    item.definition_version,
                    item.configuration_version,
                    item.evidence_digest,
                ),
            )
        ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class GenerateDailyPlanUseCase:
    def __init__(self, runs: PlanRunRepository, clock: Clock, ids: IdGenerator) -> None:
        self._runs = runs
        self._clock = clock
        self._ids = ids

    def execute(self, inputs: DailyPlanInputs, plan_at: datetime) -> DailyPlanOutcome:
        fingerprint = compute_inputs_fingerprint(inputs)

        replay = self._runs.find_replay(inputs.user_id, inputs.requested_for_date, fingerprint)
        if replay is not None and replay.version_id is not None:
            return DailyPlanOutcome(replayed=True, replay_view=replay)

        result: PlannerResult = generate_daily_plan(
            plan_date=inputs.requested_for_date,
            plan_at=plan_at,
            schedule=inputs.schedule,
            exceptions=inputs.exceptions,
            menu=inputs.menu,
            policy=inputs.policy,
            slot_policies=dict(inputs.slot_policies),
            targets=inputs.targets,
            configurable_meal_definitions=inputs.configurable_meal_definitions,
        )

        status = plan_run_status_of(result)
        reason_codes = plan_reason_codes(result) if status is PlanRunStatus.NO_PLAN else ()
        document = plan_document(result)
        canonical_bytes = to_json_bytes(document)
        sha256 = hashlib.sha256(canonical_bytes).hexdigest()

        now = self._clock.now()
        run = PlanRun(
            run_id=self._ids.new_id(),
            user_id=inputs.user_id,
            requested_for_date=inputs.requested_for_date,
            timezone=inputs.timezone,
            inputs_fingerprint=fingerprint,
            status=status,
            reason_codes=reason_codes,
            started_at=now,
            finished_at=now,
            target_policy_version_id=inputs.target_policy_version_id,
        )
        version = PlanVersion(
            version_id=self._ids.new_id(),
            run_id=run.run_id,
            plan_jsonb=document,
            plan_canonical=canonical_bytes.decode("utf-8"),
            plan_sha256=sha256,
        )
        items = self._extract_items(version, result, inputs.offering_profile_ids or {})
        self._runs.save(run, version, items)
        return DailyPlanOutcome(replayed=False, run=run, version=version, items=tuple(items))

    def _extract_items(
        self,
        version: PlanVersion,
        result: PlannerResult,
        offering_profile_ids: Mapping[str, UUID],
    ) -> list[PlanItem]:
        items: list[PlanItem] = []
        for slot_index, slot in enumerate(result.slots):
            for rank, candidate in enumerate(slot.candidates, start=1):
                food_ids: list[UUID] = []
                offering_ids: list[UUID] = []
                shas: list[str] = []
                pinned_profiles: list[UUID] = []
                for ref in candidate.line_refs:
                    offering_ids.append(ref.offering_id)
                    food_ids.append(ref.food_id)
                    shas.append(ref.profile_sha256)
                    pinned = offering_profile_ids.get(str(ref.offering_id))
                    if pinned is not None:
                        pinned_profiles.append(pinned)
                if candidate.estimated_details is not None:
                    availability = candidate.estimated_details.availability
                    offering_ids.append(availability.offering_id)
                    food_ids.append(availability.food_id)
                calories = candidate.meal.totals.quantities[NutrientKey.CALORIES_KCAL]
                items.append(
                    PlanItem(
                        item_id=self._ids.new_id(),
                        version_id=version.version_id,
                        slot_index=slot_index,
                        context=candidate.slot_context.value,
                        rank=rank,
                        candidate_id=candidate.candidate_id,
                        menu_period=slot.menu_period.value,
                        food_ids=tuple(food_ids),
                        offering_ids=tuple(offering_ids),
                        profile_row_ids=tuple(pinned_profiles),
                        profile_content_sha256s=tuple(shas),
                        score_total=str(candidate.score_total),
                        calories_kcal=str(calories),
                    )
                )
        return items


__all__ = [
    "DailyPlanInputs",
    "DailyPlanOutcome",
    "GenerateDailyPlanUseCase",
    "compute_inputs_fingerprint",
]
