from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from nutrition_agent.application.body_goals import (
    AddWaistMeasurementUseCase,
    BodyGoalsUnavailable,
    CreateStartingCalorieProposalUseCase,
    DecideStartingCalorieProposalUseCase,
    GetBodyGoalsUseCase,
    SaveBodyGoalProfileUseCase,
)
from nutrition_agent.application.body_mass_trend import BodyMassTrendUseCase
from nutrition_agent.application.target_review import CreateGoalPolicyUseCase
from nutrition_agent.db.in_memory_repos import (
    InMemoryBodyGoalsRepository,
    InMemoryGoalPolicyRepository,
    InMemoryHealthBodyMassRepository,
    InMemoryTargetPolicyRepository,
)
from nutrition_agent.domain.body_goals import (
    ActivityLevel,
    FormulaSex,
    PhaseAssessmentStatus,
    StartingTargetDecisionValue,
    WaistTrendStatus,
    WaistUnit,
    assess_phase,
    calculate_waist_trend,
    starting_calorie_estimate,
)
from nutrition_agent.domain.health.entities import BodyMassSample, SyncBatch
from nutrition_agent.domain.health.trend import BodyMassTrendStatus
from nutrition_agent.domain.target_review import GoalDirection


class Clock:
    def __init__(self, now: datetime) -> None:
        self.value = now

    def now(self) -> datetime:
        return self.value


class IDs:
    def new_id(self) -> UUID:
        return uuid4()


def test_mifflin_policy_is_deterministic_and_has_no_exercise_input() -> None:
    first = starting_calorie_estimate(
        weight_kg=Decimal("70"),
        height_cm=Decimal("175"),
        age_years=30,
        formula_sex=FormulaSex.MALE,
        activity_level=ActivityLevel.MODERATELY_ACTIVE,
        goal_direction=GoalDirection.GAIN,
    )
    second = starting_calorie_estimate(
        weight_kg=Decimal("70"),
        height_cm=Decimal("175"),
        age_years=30,
        formula_sex=FormulaSex.MALE,
        activity_level=ActivityLevel.MODERATELY_ACTIVE,
        goal_direction=GoalDirection.GAIN,
    )
    assert first == second
    assert first == (Decimal("1648.75"), Decimal("1.55"), Decimal("2555.5625"), Decimal("2750"))
    assert starting_calorie_estimate(
        weight_kg=Decimal("70"),
        height_cm=Decimal("175"),
        age_years=30,
        formula_sex=FormulaSex.MALE,
        activity_level=ActivityLevel.MODERATELY_ACTIVE,
        goal_direction=GoalDirection.MAINTAIN,
    )[3] == Decimal("2550")
    assert starting_calorie_estimate(
        weight_kg=Decimal("70"),
        height_cm=Decimal("175"),
        age_years=30,
        formula_sex=FormulaSex.MALE,
        activity_level=ActivityLevel.MODERATELY_ACTIVE,
        goal_direction=GoalDirection.LOSE,
    )[3] == Decimal("2250")


def test_profile_validation_and_missing_weight_fail_closed() -> None:
    now = datetime(2026, 9, 12, 12, tzinfo=UTC)
    repo = InMemoryBodyGoalsRepository()
    profiles = SaveBodyGoalProfileUseCase(repo, Clock(now), IDs())
    with pytest.raises(ValueError):
        profiles.execute(
            user_id=uuid4(),
            height_cm=Decimal("0"),
            date_of_birth=date(1995, 1, 1),
            formula_sex=FormulaSex.FEMALE,
            activity_level=ActivityLevel.SEDENTARY,
            target_weight_kg=None,
        )
    with pytest.raises(BodyGoalsUnavailable, match="profile"):
        CreateStartingCalorieProposalUseCase(
            repository=repo,
            health=InMemoryHealthBodyMassRepository(),
            goals=InMemoryGoalPolicyRepository(),
            targets=InMemoryTargetPolicyRepository(),
            clock=Clock(now),
            ids=IDs(),
        ).execute(user_id=uuid4(), as_of_date=now.date(), timezone="America/New_York")


def test_identical_profile_save_is_idempotent_without_rewriting_history() -> None:
    now = datetime(2026, 9, 12, 12, tzinfo=UTC)
    owner = uuid4()
    repository = InMemoryBodyGoalsRepository()
    use_case = SaveBodyGoalProfileUseCase(repository, Clock(now), IDs())
    fields = {
        "user_id": owner,
        "height_cm": Decimal("175"),
        "date_of_birth": date(1995, 6, 1),
        "formula_sex": FormulaSex.MALE,
        "activity_level": ActivityLevel.LIGHTLY_ACTIVE,
        "target_weight_kg": None,
    }
    first = use_case.execute(**fields)  # type: ignore[arg-type]
    replay = use_case.execute(**fields)  # type: ignore[arg-type]

    assert replay == first
    assert tuple(repository.profiles.values()) == (first,)


def test_proposal_does_not_activate_target_until_owner_approval() -> None:
    now = datetime(2026, 9, 12, 12, tzinfo=UTC)
    owner = uuid4()
    target_repo = InMemoryTargetPolicyRepository()
    repo = InMemoryBodyGoalsRepository(target_repository=target_repo)
    health = InMemoryHealthBodyMassRepository(clock=lambda: now)
    health.apply_batch(
        owner,
        SyncBatch(
            client_batch_id=uuid4(),
            added=(
                BodyMassSample(
                    uuid4(), Decimal("70"), now - timedelta(hours=2), now - timedelta(hours=2)
                ),
            ),
            deletions=(),
        ),
    )
    SaveBodyGoalProfileUseCase(repo, Clock(now), IDs()).execute(
        user_id=owner,
        height_cm=Decimal("175"),
        date_of_birth=date(1995, 6, 1),
        formula_sex=FormulaSex.MALE,
        activity_level=ActivityLevel.LIGHTLY_ACTIVE,
        target_weight_kg=Decimal("75"),
    )
    goals = InMemoryGoalPolicyRepository()
    CreateGoalPolicyUseCase(goals, Clock(now), IDs()).execute(
        user_id=owner,
        policy_version="owner-goal",
        direction=GoalDirection.GAIN,
        desired_rate_kg_per_week=Decimal("0.25"),
    )
    proposal, created = CreateStartingCalorieProposalUseCase(
        repository=repo,
        health=health,
        goals=goals,
        targets=target_repo,
        clock=Clock(now),
        ids=IDs(),
    ).execute(user_id=owner, as_of_date=now.date(), timezone="America/New_York")
    assert created is True
    assert target_repo.latest_approved(owner) is None
    decision_use_case = DecideStartingCalorieProposalUseCase(repo, target_repo, Clock(now), IDs())
    event = uuid4()
    decision, decision_created = decision_use_case.execute(
        user_id=owner,
        proposal_id=proposal.proposal_id,
        decision=StartingTargetDecisionValue.APPROVED,
        client_event_id=event,
    )
    assert decision_created is True
    assert decision.resulting_target_policy_version_id is not None
    assert target_repo.latest_approved(owner).goals_jsonb[0]["value"] == str(
        proposal.proposed_calorie_kcal
    )  # type: ignore[union-attr]
    replay, replay_created = decision_use_case.execute(
        user_id=owner,
        proposal_id=proposal.proposal_id,
        decision=StartingTargetDecisionValue.APPROVED,
        client_event_id=event,
    )
    assert replay == decision
    assert replay_created is False
    with pytest.raises(BodyGoalsUnavailable, match="Target Review"):
        CreateStartingCalorieProposalUseCase(
            repository=repo,
            health=health,
            goals=goals,
            targets=target_repo,
            clock=Clock(now),
            ids=IDs(),
        ).execute(user_id=owner, as_of_date=now.date(), timezone="America/New_York")


def test_waist_units_corrections_and_trend_preserve_history() -> None:
    now = datetime(2026, 9, 12, 12, tzinfo=UTC)
    owner = uuid4()
    repo = InMemoryBodyGoalsRepository()
    use_case = AddWaistMeasurementUseCase(repo, Clock(now), IDs())
    first = use_case.execute(
        user_id=owner,
        value=Decimal("32"),
        unit=WaistUnit.IN,
        measured_at=now - timedelta(days=21),
        corrects_measurement_id=None,
    )
    assert first.value_cm == Decimal("81.28")
    replacement = use_case.execute(
        user_id=owner,
        value=Decimal("81"),
        unit=WaistUnit.CM,
        measured_at=now - timedelta(days=21),
        corrects_measurement_id=first.measurement_id,
    )
    use_case.execute(
        user_id=owner,
        value=Decimal("80"),
        unit=WaistUnit.CM,
        measured_at=now - timedelta(days=7),
        corrects_measurement_id=None,
    )
    use_case.execute(
        user_id=owner,
        value=Decimal("79"),
        unit=WaistUnit.CM,
        measured_at=now,
        corrects_measurement_id=None,
    )
    assert first.measurement_id in repo.waist
    active = repo.list_active_waist(owner)
    assert replacement in active and first not in active
    trend = calculate_waist_trend(active, now.date())
    assert trend.status is WaistTrendStatus.READY
    assert trend.weekly_rate_cm is not None and trend.weekly_rate_cm < 0


def test_phase_assessment_never_changes_goal_and_fails_closed() -> None:
    unavailable = assess_phase(
        goal_direction=GoalDirection.GAIN,
        goal_band_status="within_band",
        weight_status=BodyMassTrendStatus.INSUFFICIENT,
        weight_rate_kg=None,
        waist=calculate_waist_trend((), date(2026, 9, 12)),
    )
    assert unavailable.status is PhaseAssessmentStatus.UNAVAILABLE
    assert unavailable.reason_codes == ("insufficient_weight_or_waist_evidence",)


def test_phase_assessment_does_not_infer_maintain_when_goal_is_missing() -> None:
    health = InMemoryHealthBodyMassRepository()
    summary = GetBodyGoalsUseCase(
        repository=InMemoryBodyGoalsRepository(),
        health=health,
        trends=BodyMassTrendUseCase(health),
        goals=InMemoryGoalPolicyRepository(),
        targets=InMemoryTargetPolicyRepository(),
    ).execute(user_id=uuid4(), as_of_date=date(2026, 9, 12), timezone="UTC")

    assert summary.goal is None
    assert summary.phase_assessment.status is PhaseAssessmentStatus.UNAVAILABLE
    assert summary.phase_assessment.reason_codes == ("goal_direction_unavailable",)
