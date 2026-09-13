"""Owner-scoped Body & Goals orchestration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from nutrition_agent.application.body_mass_trend import BodyMassTrendUseCase
from nutrition_agent.application.ports import (
    BodyGoalsRepository,
    Clock,
    GoalPolicyRepository,
    HealthBodyMassRepository,
    IdGenerator,
    TargetPolicyRepository,
)
from nutrition_agent.application.target_policy import target_policy_payload_sha256
from nutrition_agent.domain.body_goals import (
    GOAL_ADJUSTMENTS_KCAL,
    PHASE_ASSESSMENT_POLICY_VERSION,
    PROFILE_POLICY_VERSION,
    STARTING_CALORIE_POLICY_VERSION,
    ActivityLevel,
    BodyGoalProfileVersion,
    FormulaSex,
    PhaseAssessment,
    PhaseAssessmentStatus,
    StartingCalorieProposal,
    StartingTargetDecision,
    StartingTargetDecisionValue,
    WaistMeasurement,
    WaistTrend,
    WaistUnit,
    age_on,
    assess_phase,
    calculate_waist_trend,
    profile_digest,
    starting_calorie_estimate,
    starting_proposal_digest,
    waist_value_cm,
)
from nutrition_agent.domain.health.entities import LatestSample
from nutrition_agent.domain.health.trend import BodyMassTrendSummary
from nutrition_agent.domain.planning.artifacts import DecisionLogEntry, TargetPolicyVersion
from nutrition_agent.domain.target_review import (
    GoalPolicyVersion,
    interpret_goal_band,
)


class BodyGoalsUnavailable(LookupError):
    pass


class BodyGoalsConflict(Exception):
    pass


@dataclass(frozen=True)
class BodyGoalsSummary:
    as_of_date: date
    timezone: str
    latest_weight: LatestSample | None
    weight_age_days: int | None
    profile: BodyGoalProfileVersion | None
    waist_history: tuple[WaistMeasurement, ...]
    waist_trend: WaistTrend
    goal: GoalPolicyVersion | None
    target: TargetPolicyVersion | None
    latest_starting_proposal: StartingCalorieProposal | None
    latest_starting_decision: StartingTargetDecision | None
    body_trend: BodyMassTrendSummary
    phase_assessment: PhaseAssessment


class SaveBodyGoalProfileUseCase:
    def __init__(self, repository: BodyGoalsRepository, clock: Clock, ids: IdGenerator) -> None:
        self._repository, self._clock, self._ids = repository, clock, ids

    def execute(
        self,
        *,
        user_id: UUID,
        height_cm: Decimal,
        date_of_birth: date,
        formula_sex: FormulaSex,
        activity_level: ActivityLevel,
        target_weight_kg: Decimal | None,
    ) -> BodyGoalProfileVersion:
        age_on(date_of_birth, self._clock.now().date())
        profile = BodyGoalProfileVersion(
            profile_id=self._ids.new_id(),
            user_id=user_id,
            policy_version=PROFILE_POLICY_VERSION,
            height_cm=height_cm,
            date_of_birth=date_of_birth,
            formula_sex=formula_sex,
            activity_level=activity_level,
            target_weight_kg=target_weight_kg,
            payload_sha256=profile_digest(
                height_cm, date_of_birth, formula_sex, activity_level, target_weight_kg
            ),
            created_at=self._clock.now(),
        )
        return self._repository.save_profile(profile)


class AddWaistMeasurementUseCase:
    def __init__(self, repository: BodyGoalsRepository, clock: Clock, ids: IdGenerator) -> None:
        self._repository, self._clock, self._ids = repository, clock, ids

    def execute(
        self,
        *,
        user_id: UUID,
        value: Decimal,
        unit: WaistUnit,
        measured_at: datetime,
        corrects_measurement_id: UUID | None,
    ) -> WaistMeasurement:
        if measured_at > self._clock.now():
            raise ValueError("waist measurement cannot be in the future")
        if corrects_measurement_id is not None and all(
            item.measurement_id != corrects_measurement_id
            for item in self._repository.list_active_waist(user_id)
        ):
            raise BodyGoalsConflict("waist correction target is not an active owner record")
        measurement = WaistMeasurement(
            measurement_id=self._ids.new_id(),
            user_id=user_id,
            measured_at=measured_at,
            value_cm=waist_value_cm(value, unit),
            entered_value=value,
            entered_unit=unit,
            provenance="owner_entered",
            corrects_measurement_id=corrects_measurement_id,
            recorded_at=self._clock.now(),
        )
        self._repository.save_waist(measurement)
        return measurement


class CreateStartingCalorieProposalUseCase:
    def __init__(
        self,
        *,
        repository: BodyGoalsRepository,
        health: HealthBodyMassRepository,
        goals: GoalPolicyRepository,
        targets: TargetPolicyRepository,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._repository, self._health, self._goals, self._targets = (
            repository,
            health,
            goals,
            targets,
        )
        self._clock, self._ids = clock, ids

    def execute(
        self, *, user_id: UUID, as_of_date: date, timezone: str
    ) -> tuple[StartingCalorieProposal, bool]:
        zone = ZoneInfo(timezone)
        if self._targets.latest_approved(user_id) is not None:
            raise BodyGoalsUnavailable(
                "an approved calorie target already exists; use Target Review"
            )
        profile = self._repository.latest_profile(user_id)
        goal = self._goals.latest(user_id)
        latest = self._health.status_summary(user_id).latest_sample
        if profile is None:
            raise BodyGoalsUnavailable("owner profile evidence is required")
        if goal is None:
            raise BodyGoalsUnavailable("goal direction is required")
        if latest is None:
            raise BodyGoalsUnavailable("fresh HealthKit weight is required")
        age_days = (as_of_date - latest.sample_start.astimezone(zone).date()).days
        if age_days < 0 or age_days > 7:
            raise BodyGoalsUnavailable("fresh HealthKit weight is required")
        age = age_on(profile.date_of_birth, as_of_date)
        bmr, multiplier, maintenance, proposed = starting_calorie_estimate(
            weight_kg=latest.value_kg,
            height_cm=profile.height_cm,
            age_years=age,
            formula_sex=profile.formula_sex,
            activity_level=profile.activity_level,
            goal_direction=goal.direction,
        )
        value = StartingCalorieProposal(
            proposal_id=self._ids.new_id(),
            user_id=user_id,
            profile_id=profile.profile_id,
            goal_policy_version_id=goal.version_id,
            body_mass_sample_uuid=latest.sample_uuid,
            policy_version=STARTING_CALORIE_POLICY_VERSION,
            as_of_date=as_of_date,
            timezone=timezone,
            age_years=age,
            body_mass_kg=latest.value_kg,
            bmr_kcal=bmr,
            activity_multiplier=multiplier,
            maintenance_kcal=maintenance,
            goal_adjustment_kcal=GOAL_ADJUSTMENTS_KCAL[goal.direction],
            proposed_calorie_kcal=proposed,
            evidence_sha256="",
            created_at=self._clock.now(),
        )
        value = replace(value, evidence_sha256=starting_proposal_digest(value))
        return self._repository.save_starting_proposal(value)


class DecideStartingCalorieProposalUseCase:
    def __init__(
        self,
        repository: BodyGoalsRepository,
        targets: TargetPolicyRepository,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._repository, self._targets, self._clock, self._ids = repository, targets, clock, ids

    def execute(
        self,
        *,
        user_id: UUID,
        proposal_id: UUID,
        decision: StartingTargetDecisionValue,
        client_event_id: UUID,
    ) -> tuple[StartingTargetDecision, bool]:
        proposal = self._repository.find_starting_proposal(user_id, proposal_id)
        if proposal is None:
            raise BodyGoalsUnavailable("starting target proposal not found")
        existing = self._repository.find_starting_decision(user_id, proposal_id)
        if existing is not None:
            if existing.decision is decision and existing.client_event_id == client_event_id:
                return existing, False
            raise BodyGoalsConflict("starting target proposal already has a different decision")
        resulting: TargetPolicyVersion | None = None
        log: DecisionLogEntry | None = None
        resulting_id: UUID | None = None
        now = self._clock.now()
        if decision is StartingTargetDecisionValue.APPROVED:
            if self._targets.latest_approved(user_id) is not None:
                raise BodyGoalsConflict("an approved target was created after this proposal")
            resulting_id = self._ids.new_id()
            goals = [
                {
                    "nutrient": "calories_kcal",
                    "kind": "target",
                    "value": str(proposal.proposed_calorie_kcal),
                    "weight": "1",
                }
            ]
            resulting = TargetPolicyVersion(
                version_id=resulting_id,
                user_id=user_id,
                policy_version=f"starting-target-{proposal.evidence_sha256[:24]}",
                goals_jsonb=goals,
                payload_sha256=target_policy_payload_sha256(goals),
                created_at=now,
            )
            log = DecisionLogEntry(
                decision_id=self._ids.new_id(),
                user_id=user_id,
                subject="target_policy",
                decision="approved",
                rationale="Owner approved deterministic starting calorie estimate",
                policy_version_id=resulting_id,
                decided_at=now,
            )
        terminal = StartingTargetDecision(
            decision_id=self._ids.new_id(),
            user_id=user_id,
            proposal_id=proposal_id,
            decision=decision,
            client_event_id=client_event_id,
            resulting_target_policy_version_id=resulting_id,
            decided_at=now,
        )
        return self._repository.decide_starting_proposal(proposal, terminal, resulting, log)


class GetBodyGoalsUseCase:
    def __init__(
        self,
        *,
        repository: BodyGoalsRepository,
        health: HealthBodyMassRepository,
        trends: BodyMassTrendUseCase,
        goals: GoalPolicyRepository,
        targets: TargetPolicyRepository,
    ) -> None:
        self._repository, self._health, self._trends = repository, health, trends
        self._goals, self._targets = goals, targets

    def execute(self, *, user_id: UUID, as_of_date: date, timezone: str) -> BodyGoalsSummary:
        zone = ZoneInfo(timezone)
        latest = self._health.status_summary(user_id).latest_sample
        weight_age = (
            (as_of_date - latest.sample_start.astimezone(zone).date()).days if latest else None
        )
        waist_history = self._repository.list_active_waist(user_id)
        waist = calculate_waist_trend(waist_history, as_of_date, timezone)
        goal = self._goals.latest(user_id)
        body = self._trends.execute(user_id=user_id, as_of_date=as_of_date, timezone=timezone)
        band = interpret_goal_band(trend=body, goal_policy=goal) if goal else None
        phase = (
            assess_phase(
                goal_direction=goal.direction,
                goal_band_status=band.status.value if band else "unavailable",
                weight_status=body.status,
                weight_rate_kg=body.weekly_rate_kg,
                waist=waist,
            )
            if goal is not None
            else PhaseAssessment(
                PHASE_ASSESSMENT_POLICY_VERSION,
                PhaseAssessmentStatus.UNAVAILABLE,
                ("goal_direction_unavailable",),
            )
        )
        proposal = self._repository.latest_starting_proposal(user_id)
        return BodyGoalsSummary(
            as_of_date,
            timezone,
            latest,
            weight_age,
            self._repository.latest_profile(user_id),
            waist_history,
            waist,
            goal,
            self._targets.latest_approved(user_id),
            proposal,
            self._repository.find_starting_decision(user_id, proposal.proposal_id)
            if proposal
            else None,
            body,
            phase,
        )


__all__ = [name for name in globals() if name.endswith("UseCase") or name.startswith("BodyGoals")]
