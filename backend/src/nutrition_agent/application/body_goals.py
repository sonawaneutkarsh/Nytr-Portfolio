"""Body & Goals setup orchestration, separate from target-review decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from nutrition_agent.application.body_mass_trend import BodyMassTrendUseCase
from nutrition_agent.application.ports import (
    BodyMassHistoryRepository,
    BodyProfileRepository,
    Clock,
    GoalPolicyRepository,
    IdGenerator,
    TargetPolicyRepository,
    WaistMeasurementRepository,
)
from nutrition_agent.domain.body_goals import (
    OwnerBodyProfile,
    PhaseProgress,
    StartingCalorieEstimate,
    WaistMeasurement,
    estimate_starting_calories,
    evaluate_phase_progress,
)
from nutrition_agent.domain.planning.artifacts import TargetPolicyVersion
from nutrition_agent.domain.target_review import GoalPolicyVersion


@dataclass(frozen=True)
class BodyGoalsSnapshot:
    profile: OwnerBodyProfile | None
    latest_weight_kg: Decimal | None
    latest_weight_at: datetime | None
    weight_freshness_days: int | None
    latest_waist: WaistMeasurement | None
    waist_history: tuple[WaistMeasurement, ...]
    goal: GoalPolicyVersion | None
    target: TargetPolicyVersion | None
    progress: PhaseProgress | None


class BodyGoalsUseCase:
    def __init__(
        self,
        *,
        profiles: BodyProfileRepository,
        waists: WaistMeasurementRepository,
        body_mass: BodyMassHistoryRepository,
        goals: GoalPolicyRepository,
        targets: TargetPolicyRepository,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._profiles = profiles
        self._waists = waists
        self._body_mass = body_mass
        self._goals = goals
        self._targets = targets
        self._clock = clock
        self._ids = ids

    def snapshot(self, *, user_id: UUID, as_of_date: date, timezone: str) -> BodyGoalsSnapshot:
        trend = BodyMassTrendUseCase(self._body_mass).execute(
            user_id=user_id, as_of_date=as_of_date, timezone=timezone
        )
        try:
            zone = ZoneInfo(timezone)
        except Exception as exc:  # pragma: no cover - trend validates this too
            raise ValueError("timezone is not recognized") from exc
        start = datetime.combine(
            as_of_date - timedelta(days=90), datetime.min.time(), tzinfo=zone
        ).astimezone(UTC)
        end = datetime.combine(
            as_of_date + timedelta(days=1), datetime.min.time(), tzinfo=zone
        ).astimezone(UTC)
        observations = self._body_mass.list_active(user_id, start, end)
        latest = max(observations, key=lambda item: item.measured_at) if observations else None
        latest_waist = self._waists.list_recent(user_id, limit=30)
        goal = self._goals.latest(user_id)
        target = self._targets.latest_approved(user_id)
        progress = (
            evaluate_phase_progress(goal=goal, trend=trend, waist_measurements=latest_waist)
            if goal is not None
            else None
        )
        age = (
            max(0, (as_of_date - latest.measured_at.astimezone(zone).date()).days)
            if latest is not None
            else None
        )
        return BodyGoalsSnapshot(
            profile=self._profiles.get(user_id),
            latest_weight_kg=latest.value_kg if latest else None,
            latest_weight_at=latest.measured_at if latest else None,
            weight_freshness_days=age,
            latest_waist=latest_waist[0] if latest_waist else None,
            waist_history=latest_waist,
            goal=goal,
            target=target,
            progress=progress,
        )

    def save_profile(
        self, *, user_id: UUID, height_cm: Decimal, target_weight_kg: Decimal | None
    ) -> OwnerBodyProfile:
        profile = OwnerBodyProfile(
            user_id=user_id,
            height_cm=height_cm,
            target_weight_kg=target_weight_kg,
            updated_at=self._clock.now(),
        )
        self._profiles.save(profile)
        return profile

    def record_waist(
        self, *, user_id: UUID, waist_cm: Decimal, measured_at: datetime
    ) -> WaistMeasurement:
        measurement = WaistMeasurement(
            measurement_id=self._ids.new_id(),
            user_id=user_id,
            waist_cm=waist_cm,
            measured_at=measured_at,
            recorded_at=self._clock.now(),
        )
        self._waists.append(measurement)
        return measurement

    def starting_estimate(
        self, *, user_id: UUID, as_of_date: date, timezone: str
    ) -> StartingCalorieEstimate:
        snapshot = self.snapshot(user_id=user_id, as_of_date=as_of_date, timezone=timezone)
        if snapshot.latest_weight_kg is None:
            raise ValueError("a fresh HealthKit body-weight sample is required")
        return estimate_starting_calories(snapshot.latest_weight_kg)


__all__ = ["BodyGoalsSnapshot", "BodyGoalsUseCase"]
