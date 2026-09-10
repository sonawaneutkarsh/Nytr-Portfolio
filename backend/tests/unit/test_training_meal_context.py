"""M12C deterministic training-aware Lunch context selection."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from nutrition_agent.api.app import HealthApiDeps, create_health_app
from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.daily_plan import compute_inputs_fingerprint
from nutrition_agent.application.health_sync import HealthBodyMassSyncUseCase, HealthSyncDeps
from nutrition_agent.application.ports import ResolvedMenuDay
from nutrition_agent.application.server_inputs import (
    CANONICAL_M6_DEMO_CONFIGURATION,
    CANONICAL_M6_DEMO_TARGETS,
    PRODUCTION_SERVER_CONFIGURATION,
    DefaultServerInputsProvider,
)
from nutrition_agent.application.training_context import TrainingDayContextUseCase
from nutrition_agent.application.training_meal_context import (
    TrainingAwareServerInputsProvider,
    apply_training_aware_meal_context,
)
from nutrition_agent.db.in_memory_repos import (
    InMemoryHealthBodyMassRepository,
    InMemoryMenuDayReadRepository,
    InMemoryTrainingSessionRepository,
)
from nutrition_agent.domain.nutrition.targets import GoalKind, NutrientGoal, TargetSet
from nutrition_agent.domain.planning.artifacts import plan_document
from nutrition_agent.domain.planning.context import MealContext
from nutrition_agent.domain.planning.menu_view import MenuDayView, PeriodMenu
from nutrition_agent.domain.planning.planner import generate_daily_plan
from nutrition_agent.domain.planning.schedule import resolve_day
from nutrition_agent.domain.stacks.entities import MealPeriod, NutrientKey
from nutrition_agent.domain.training import (
    StoredTrainingSession,
    TrainingSourceSystem,
)
from nutrition_agent.domain.training.context import (
    OWNER_STRENGTH_TRAINING_DAY_POLICY_V1,
    TrainingDayContext,
    TrainingDayReasonCode,
    evaluate_training_day_context,
)
from nutrition_agent.domain.training.meal_context import (
    OWNER_TRAINING_AWARE_MEAL_CONTEXT_POLICY_V1,
    MealContextSelectionReason,
    TrainingEvidenceState,
    select_lunch_meal_context,
)

USER = UUID("00000000-0000-0000-0000-0000000000c1")
FRIDAY = date(2026, 9, 4)
THURSDAY = date(2026, 9, 3)
TIMEZONE = "America/New_York"
ZONE = ZoneInfo(TIMEZONE)
TRADITIONAL_STRENGTH = "50"
FUNCTIONAL_STRENGTH = "20"
WALKING = "52"


class _Clock:
    def now(self) -> datetime:
        return _at(FRIDAY, 20)


def _at(day: date, hour: int, minute: int = 0, *, zone: ZoneInfo = ZONE) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=zone)


def _session(
    number: int,
    *,
    day: date = FRIDAY,
    start: time = time(9),
    end: time = time(10),
    activity_type: str | None = TRADITIONAL_STRENGTH,
    duration: Decimal | None = Decimal("7200"),
    energy: Decimal | None = None,
    source_name: str | None = "Hevy",
    tombstoned: bool = False,
    source_record_id: str | None = None,
    timezone: ZoneInfo = ZONE,
) -> StoredTrainingSession:
    started_at = datetime.combine(day, start, tzinfo=timezone)
    ended_at = datetime.combine(day, end, tzinfo=timezone)
    if end < start:
        ended_at += timedelta(days=1)
    return StoredTrainingSession(
        session_id=UUID(int=number),
        user_id=USER,
        source_system=TrainingSourceSystem.HEALTHKIT,
        source_record_id=source_record_id or f"workout-{number}",
        activity_type=activity_type,
        started_at=started_at,
        ended_at=ended_at,
        active_duration_seconds=duration,
        active_energy_kcal=energy,
        timezone_identifier=TIMEZONE,
        source_name=source_name,
        source_bundle_id="com.hevyapp.hevy" if source_name == "Hevy" else "com.apple.Health",
        source_revision=None,
        ingested_at=_at(day, 20),
        tombstoned_at=_at(day, 21) if tombstoned else None,
    )


def _context(
    *sessions: StoredTrainingSession,
    day: date = FRIDAY,
    timezone: str = TIMEZONE,
) -> TrainingDayContext:
    return evaluate_training_day_context(
        local_date=day,
        timezone=timezone,
        training_sessions=tuple(sessions),
    )


def _selection(
    context: TrainingDayContext | None,
    *,
    day: date = FRIDAY,
    lunch_start: time = time(12, 30),
    timezone: str = TIMEZONE,
):
    zone = ZoneInfo(timezone)
    return select_lunch_meal_context(
        local_date=day,
        timezone=timezone,
        lunch_starts_at=datetime.combine(day, lunch_start, tzinfo=zone),
        training_context=context,
    )


def _targets() -> TargetSet:
    return TargetSet(
        policy_version="m12c-targets.v1",
        goals={
            NutrientKey.CALORIES_KCAL: NutrientGoal(
                kind=GoalKind.TARGET,
                value=Decimal("3000"),
                weight=Decimal("1"),
            ),
            NutrientKey.PROTEIN_G: NutrientGoal(
                kind=GoalKind.FLOOR,
                value=Decimal("150"),
                weight=Decimal("1"),
            ),
        },
    )


def _base_inputs(day: date = FRIDAY):
    menu = ResolvedMenuDay(
        menu=MenuDayView(
            service_date=day,
            periods={
                MealPeriod.LUNCH: PeriodMenu(offerings=(), explicitly_empty=True),
                MealPeriod.DINNER: PeriodMenu(offerings=(), explicitly_empty=True),
            },
            fetched_at=datetime.combine(day, time(6), tzinfo=ZONE),
            snapshot_sha256=f"menu-{day.isoformat()}",
        ),
        offering_profile_ids={},
    )
    provider = DefaultServerInputsProvider(
        menu_days=InMemoryMenuDayReadRepository(days={day: menu})
    )
    return provider.build(
        user_id=USER,
        requested_for_date=day,
        timezone=TIMEZONE,
        targets=_targets(),
        target_policy_version_id=UUID(int=900),
    )


def _lunch_context(inputs) -> MealContext:
    slots = resolve_day(
        inputs.schedule,
        inputs.exceptions,
        inputs.requested_for_date,
        inputs.policy.context_period,
    )
    return next(slot.context for slot in slots if slot.menu_period is MealPeriod.LUNCH)


def test_policies_are_explicit_and_versioned() -> None:
    assert OWNER_TRAINING_AWARE_MEAL_CONTEXT_POLICY_V1.version == (
        "owner-training-aware-meal-context.v1"
    )
    assert OWNER_STRENGTH_TRAINING_DAY_POLICY_V1.version == "owner-strength-training-day.v1"


def test_hevy_strength_before_lunch_selects_post_workout() -> None:
    selection = _selection(_context(_session(1)))
    assert selection.selected_context is MealContext.POST_WORKOUT_LUNCH
    assert selection.reason_code is MealContextSelectionReason.QUALIFYING_WORKOUT_BEFORE_LUNCH
    assert selection.qualifying_prior_session_ids == (UUID(int=1),)
    assert selection.training_policy_version == "owner-strength-training-day.v1"


def test_qualifying_strength_after_lunch_selects_generic() -> None:
    selection = _selection(
        _context(_session(1, start=time(18), end=time(19, 30), duration=Decimal("5400")))
    )
    assert selection.selected_context is MealContext.LUNCH
    assert selection.reason_code is MealContextSelectionReason.QUALIFYING_WORKOUT_ONLY_AFTER_LUNCH


@pytest.mark.parametrize(
    ("sessions", "training_reason"),
    [
        ((), TrainingDayReasonCode.NO_SESSIONS),
        ((_session(1, activity_type=WALKING),), TrainingDayReasonCode.UNSUPPORTED_ACTIVITY_TYPE),
        (
            (_session(1, end=time(10, 34), duration=Decimal("1140")),),
            TrainingDayReasonCode.SESSIONS_BELOW_DURATION,
        ),
    ],
)
def test_no_qualifying_workout_selects_generic(
    sessions: tuple[StoredTrainingSession, ...],
    training_reason: TrainingDayReasonCode,
) -> None:
    selection = _selection(_context(*sessions))
    assert selection.selected_context is MealContext.LUNCH
    assert selection.reason_code is MealContextSelectionReason.NO_QUALIFYING_WORKOUT
    assert training_reason in selection.training_reason_codes


def test_apple_watch_functional_strength_at_exact_boundary_qualifies() -> None:
    session = _session(
        1,
        start=time(11),
        end=time(11, 20),
        activity_type=FUNCTIONAL_STRENGTH,
        duration=Decimal("1200"),
        source_name="Apple Watch",
    )
    assert _selection(_context(session)).selected_context is MealContext.POST_WORKOUT_LUNCH


def test_active_energy_and_source_name_do_not_change_selection_or_fingerprint() -> None:
    baseline_session = _session(1, energy=None, source_name="Hevy")
    changed_session = replace(
        baseline_session,
        active_energy_kcal=Decimal("99999"),
        source_name="Different Source App",
        source_bundle_id="example.changed",
    )
    baseline = _selection(_context(baseline_session))
    changed = _selection(_context(changed_session))
    assert baseline == changed

    inputs = _base_inputs()
    baseline_inputs = apply_training_aware_meal_context(inputs, _context(baseline_session)).inputs
    changed_inputs = apply_training_aware_meal_context(inputs, _context(changed_session)).inputs
    assert compute_inputs_fingerprint(baseline_inputs) == compute_inputs_fingerprint(changed_inputs)


def test_two_workouts_select_post_workout_when_one_completed_before_lunch() -> None:
    context = _context(
        _session(1),
        _session(2, start=time(18), end=time(19), duration=Decimal("3600")),
    )
    selection = _selection(context)
    assert selection.selected_context is MealContext.POST_WORKOUT_LUNCH
    assert selection.qualifying_prior_session_ids == (UUID(int=1),)


def test_missing_or_mismatched_evidence_is_neutral_and_explicitly_unknown() -> None:
    missing = _selection(None)
    mismatched = _selection(_context(_session(1), day=THURSDAY))
    for selection in (missing, mismatched):
        assert selection.selected_context is MealContext.LUNCH
        assert selection.evidence_state is TrainingEvidenceState.UNAVAILABLE
        assert selection.reason_code is MealContextSelectionReason.TRAINING_EVIDENCE_UNAVAILABLE


def test_timezone_boundary_is_deterministic() -> None:
    los_angeles = ZoneInfo("America/Los_Angeles")
    session = _session(
        1,
        day=THURSDAY,
        start=time(10),
        end=time(12),
        timezone=los_angeles,
        source_name="Apple Watch",
    )
    context = _context(session, day=THURSDAY, timezone="America/Los_Angeles")
    selection = _selection(
        context,
        day=THURSDAY,
        lunch_start=time(13),
        timezone="America/Los_Angeles",
    )
    assert selection.selected_context is MealContext.POST_WORKOUT_LUNCH


def test_midnight_crossing_workout_uses_start_date_but_actual_end_time() -> None:
    session = _session(
        1,
        day=THURSDAY,
        start=time(23, 50),
        end=time(0, 20),
        duration=Decimal("1800"),
    )
    thursday_context = _context(session, day=THURSDAY)
    assert thursday_context.is_training_day is True
    selection = _selection(thursday_context, day=THURSDAY, lunch_start=time(13))
    assert selection.selected_context is MealContext.LUNCH
    assert selection.reason_code is MealContextSelectionReason.QUALIFYING_WORKOUT_ONLY_AFTER_LUNCH
    assert _context(session, day=FRIDAY).is_training_day is False


def test_tombstone_and_duplicate_identity_cannot_create_extra_effect() -> None:
    tombstoned = _session(1, tombstoned=True)
    first = _session(2, source_record_id="same-source-record")
    duplicate = _session(3, source_record_id="same-source-record")
    context = _context(tombstoned, first, duplicate)
    selection = _selection(context)
    assert context.qualifying_session_count == 1
    assert selection.selected_context is MealContext.POST_WORKOUT_LUNCH
    assert len(selection.qualifying_prior_session_ids) == 1


def test_workout_ending_exactly_at_lunch_is_not_claimed_as_post_workout() -> None:
    context = _context(_session(1, start=time(11, 30), end=time(12, 30), duration=Decimal("3600")))
    assert _selection(context).selected_context is MealContext.LUNCH


def test_projection_changes_only_dated_context_and_preserves_targets_and_policy() -> None:
    original = _base_inputs()
    projected = apply_training_aware_meal_context(original, _context(_session(1))).inputs
    assert _lunch_context(projected) is MealContext.POST_WORKOUT_LUNCH
    assert projected.targets is original.targets
    assert projected.target_policy_version_id == original.target_policy_version_id
    assert projected.policy is original.policy
    assert projected.policy.slot_shares == original.policy.slot_shares
    assert projected.policy.version == "psh-fall-2026-planner.v5"
    assert "owner-training-aware-meal-context.v1" in projected.schedule.version
    assert "owner-strength-training-day.v1" in projected.schedule.version

    result = generate_daily_plan(
        plan_date=projected.requested_for_date,
        plan_at=_at(FRIDAY, 14),
        schedule=projected.schedule,
        exceptions=projected.exceptions,
        menu=projected.menu,
        policy=projected.policy,
        slot_policies=projected.slot_policies,
        targets=projected.targets,
        configurable_meal_definitions=projected.configurable_meal_definitions,
    )
    document = plan_document(result)
    assert document["policy_versions"]["schedule"] == projected.schedule.version  # type: ignore[index]
    assert document["slots"][0]["context"] == "post_workout_lunch"  # type: ignore[index]


def test_static_training_day_without_observed_workout_becomes_generic() -> None:
    original = _base_inputs()
    assert _lunch_context(original) is MealContext.POST_WORKOUT_LUNCH
    projected = apply_training_aware_meal_context(original, _context()).inputs
    assert _lunch_context(projected) is MealContext.LUNCH
    assert "no_qualifying_workout" in projected.schedule.version


def test_flexible_day_with_prior_workout_becomes_post_workout() -> None:
    original = _base_inputs(THURSDAY)
    assert _lunch_context(original) is MealContext.LUNCH
    context = _context(
        _session(1, day=THURSDAY, start=time(10), end=time(12)),
        day=THURSDAY,
    )
    projected = apply_training_aware_meal_context(original, context).inputs
    assert _lunch_context(projected) is MealContext.POST_WORKOUT_LUNCH


class _Resolver:
    def __init__(self, context: TrainingDayContext) -> None:
        self.context = context
        self.calls: list[tuple[UUID, date, str]] = []

    def execute(self, *, user_id: UUID, local_date: date, timezone: str) -> TrainingDayContext:
        self.calls.append((user_id, local_date, timezone))
        return self.context


class _UnavailableResolver:
    def execute(self, *, user_id: UUID, local_date: date, timezone: str) -> TrainingDayContext:
        del user_id, local_date, timezone
        raise RuntimeError("simulated unavailable training storage")


def test_decorated_provider_uses_server_owned_user_evidence() -> None:
    original = _base_inputs()
    base = DefaultServerInputsProvider(
        menu_days=InMemoryMenuDayReadRepository(
            days={
                FRIDAY: ResolvedMenuDay(
                    menu=original.menu,
                    offering_profile_ids=original.offering_profile_ids or {},
                )
            }
        )
    )
    resolver = _Resolver(_context(_session(1)))
    provider = TrainingAwareServerInputsProvider(base=base, training_contexts=resolver)
    built = provider.build(
        user_id=USER,
        requested_for_date=FRIDAY,
        timezone=TIMEZONE,
        targets=original.targets,
        target_policy_version_id=original.target_policy_version_id,
    )
    assert resolver.calls == [(USER, FRIDAY, TIMEZONE)]
    assert _lunch_context(built) is MealContext.POST_WORKOUT_LUNCH


@pytest.mark.parametrize("resolver", [None, _UnavailableResolver()])
def test_decorated_provider_fails_closed_to_generic_when_evidence_is_unavailable(
    resolver,
) -> None:
    original = _base_inputs()
    base = DefaultServerInputsProvider(
        menu_days=InMemoryMenuDayReadRepository(
            days={
                FRIDAY: ResolvedMenuDay(
                    menu=original.menu,
                    offering_profile_ids=original.offering_profile_ids or {},
                )
            }
        )
    )
    provider = TrainingAwareServerInputsProvider(base=base, training_contexts=resolver)
    built = provider.build(
        user_id=USER,
        requested_for_date=FRIDAY,
        timezone=TIMEZONE,
        targets=original.targets,
        target_policy_version_id=original.target_policy_version_id,
    )
    assert _lunch_context(built) is MealContext.LUNCH
    assert "training_evidence_unavailable" in built.schedule.version


def test_application_use_case_and_provider_are_deterministic() -> None:
    repository = InMemoryTrainingSessionRepository()
    repository.rows[(USER, TrainingSourceSystem.HEALTHKIT, "workout-1")] = _session(1)
    resolver = TrainingDayContextUseCase(training=repository)
    original = _base_inputs()
    base = DefaultServerInputsProvider(
        menu_days=InMemoryMenuDayReadRepository(
            days={
                FRIDAY: ResolvedMenuDay(
                    menu=original.menu,
                    offering_profile_ids=original.offering_profile_ids or {},
                )
            }
        )
    )
    provider = TrainingAwareServerInputsProvider(base=base, training_contexts=resolver)
    first = provider.build(
        user_id=USER,
        requested_for_date=FRIDAY,
        timezone=TIMEZONE,
        targets=original.targets,
    )
    second = provider.build(
        user_id=USER,
        requested_for_date=FRIDAY,
        timezone=TIMEZONE,
        targets=original.targets,
    )
    assert first == second
    assert compute_inputs_fingerprint(first) == compute_inputs_fingerprint(second)


def test_application_factory_wires_training_context_into_production_inputs() -> None:
    repository = InMemoryTrainingSessionRepository()
    context_use_case = TrainingDayContextUseCase(training=repository)
    settings = HealthApiSettings(
        database_url=None,
        jwt_secret="m12c-offline-test-secret",
        supabase_url=None,
        jwt_audience="authenticated",
    )
    app = create_health_app(
        HealthApiDeps(
            settings=settings,
            verifier=TokenVerifier(settings),
            use_case=HealthBodyMassSyncUseCase(
                HealthSyncDeps(InMemoryHealthBodyMassRepository(), _Clock())
            ),
            training_context_use_case=context_use_case,
        )
    )
    provider = app.state.planning_inputs_provider
    assert isinstance(provider, TrainingAwareServerInputsProvider)
    assert provider.training_contexts is context_use_case


def test_historical_inputs_remain_unchanged_when_training_projection_is_not_used() -> None:
    original = _base_inputs()
    assert original.schedule is PRODUCTION_SERVER_CONFIGURATION.schedule
    assert original.schedule.version == "psh-fall-2026.v1"
    assert _lunch_context(original) is MealContext.POST_WORKOUT_LUNCH
    assert CANONICAL_M6_DEMO_CONFIGURATION.schedule.version == "m6-demo.v1"
    assert CANONICAL_M6_DEMO_TARGETS.policy_version == "m6-demo-targets.v1"
