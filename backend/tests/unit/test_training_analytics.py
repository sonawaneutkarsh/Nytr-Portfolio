"""Deterministic M14C training analytics over production-shaped observations."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from nutrition_agent.application.training_analytics import (
    GetExerciseIndexUseCase,
    GetExerciseTrainingHistoryUseCase,
    GetRecentTrainingAnalyticsUseCase,
)
from nutrition_agent.db.in_memory_repos import InMemoryDetailedTrainingRepository
from nutrition_agent.domain.training.analytics import (
    ExerciseMetricFamily,
    MetricCompleteness,
    TrainingPRType,
    analyze_exercise_occurrence,
    analyze_training_session,
)
from nutrition_agent.domain.training.detail import (
    DetailedTrainingDeletion,
    DetailedTrainingImportBatch,
    DetailedTrainingSession,
    DetailedTrainingSet,
    DetailedTrainingSourceSystem,
    DetailedTrainingSyncCheckpoint,
    DetailedTrainingSyncMode,
    ExercisePerformance,
    StoredDetailedTrainingSession,
    TrainingDistance,
    TrainingDistanceUnit,
    TrainingLoad,
    TrainingLoadUnit,
    TrainingSetType,
)

USER = UUID(int=1)
OTHER = UUID(int=2)
BASE = datetime(2026, 9, 1, 14, tzinfo=UTC)
LOAD_80_LB_KG = Decimal("36.28743275485118")
LOAD_90_LB_KG = Decimal("40.82336184920758")
ASSISTANCE_KG = Decimal("9.071858188712795")


def _set(
    index: int,
    *,
    kind: TrainingSetType = TrainingSetType.NORMAL,
    reps: int | None = 8,
    load: Decimal | None = LOAD_80_LB_KG,
    duration: Decimal | None = None,
    distance: Decimal | None = None,
    rpe: Decimal | None = None,
) -> DetailedTrainingSet:
    return DetailedTrainingSet(
        set_identity=f"0:{index}",
        set_index=index,
        set_type=kind,
        reps=reps,
        load=(TrainingLoad(load, TrainingLoadUnit.KILOGRAM) if load is not None else None),
        distance=(
            TrainingDistance(distance, TrainingDistanceUnit.METER) if distance is not None else None
        ),
        duration_seconds=duration,
        rpe=rpe,
    )


def _exercise(
    source_id: str = "fixture-row-template",
    name: str = "Cable Row",
    sets: tuple[DetailedTrainingSet, ...] | None = None,
    *,
    order: int = 0,
    occurrence: str | None = None,
) -> ExercisePerformance:
    return ExercisePerformance(
        occurrence_identity=occurrence or f"{source_id}:{order}",
        source_exercise_id=source_id,
        display_name=name,
        exercise_order=order,
        sets=sets or (_set(0),),
    )


def _session(
    source_session_id: str,
    started_at: datetime,
    exercises: tuple[ExercisePerformance, ...],
    *,
    revision: int = 1,
    title: str = "Pull 1",
) -> DetailedTrainingSession:
    return DetailedTrainingSession(
        source_system=DetailedTrainingSourceSystem.HEVY,
        source_session_id=source_session_id,
        source_revision=f"revision-{revision}",
        title=title,
        started_at=started_at,
        ended_at=started_at + timedelta(hours=1),
        exercises=exercises,
        parser_version="hevy-public-api.v1",
        source_payload_sha256=f"{revision:064x}",
        source_updated_at=started_at + timedelta(minutes=revision),
    )


def _stored(session: DetailedTrainingSession, revision_id: int) -> StoredDetailedTrainingSession:
    return StoredDetailedTrainingSession(
        revision_id=UUID(int=revision_id),
        user_id=USER,
        session=session,
        ingested_at=BASE + timedelta(days=10),
    )


def _checkpoint() -> DetailedTrainingSyncCheckpoint:
    return DetailedTrainingSyncCheckpoint(
        checkpoint_id=UUID(int=999),
        user_id=USER,
        source_system=DetailedTrainingSourceSystem.HEVY,
        sync_mode=DetailedTrainingSyncMode.BOOTSTRAP,
        bootstrap_completed=True,
        source_event_watermark=BASE,
        parser_version="hevy-public-api.v1",
        provider_version="hevy-api-provider.v1",
        pages_fetched=1,
        logical_requests=1,
        attempts_made=1,
        retries=0,
        completed_at=BASE,
    )


def test_working_set_policy_excludes_warmup_and_includes_drop_and_failure() -> None:
    exercise = _exercise(
        sets=(
            _set(0, kind=TrainingSetType.WARMUP, reps=10, load=Decimal("20")),
            _set(1, kind=TrainingSetType.NORMAL, reps=10),
            _set(2, kind=TrainingSetType.DROPSET, reps=9),
            _set(3, kind=TrainingSetType.FAILURE, reps=8),
        )
    )

    result = analyze_exercise_occurrence(exercise)

    assert result.recorded_set_count == 4
    assert result.working_set_count == 3
    assert result.warmup_set_count == 1
    assert result.rep_total == 27
    assert result.volume_kg_reps == LOAD_80_LB_KG * 27


def test_top_load_is_lexicographic_and_decimal_exact() -> None:
    result = analyze_exercise_occurrence(
        _exercise(
            sets=(
                _set(0, reps=12, load=LOAD_80_LB_KG),
                _set(1, reps=9, load=LOAD_90_LB_KG, rpe=Decimal("8.5")),
                _set(2, reps=8, load=LOAD_90_LB_KG),
            )
        )
    )

    assert result.top_load_set is not None
    assert result.top_load_set.set_index == 1
    assert result.top_load_set.load_kg == LOAD_90_LB_KG
    assert result.top_load_set.reps == 9
    assert result.max_rpe == Decimal("8.5")


def test_missing_rep_or_load_makes_volume_unavailable_not_zero() -> None:
    missing_load = analyze_exercise_occurrence(_exercise(sets=(_set(0), _set(1, load=None))))
    missing_reps = analyze_exercise_occurrence(_exercise(sets=(_set(0), _set(1, reps=None))))

    assert missing_load.volume_kg_reps is None
    assert missing_load.metric_completeness is MetricCompleteness.PARTIAL
    assert missing_reps.rep_total is None
    assert missing_reps.volume_kg_reps is None


def test_bodyweight_assisted_duration_distance_and_zero_records_are_not_strength_volume() -> None:
    bodyweight = analyze_exercise_occurrence(
        _exercise("pullup", "Pull Up", (_set(0, reps=10, load=None),))
    )
    assisted = analyze_exercise_occurrence(
        _exercise(
            "dip-template",
            "Triceps Dip (Assisted)",
            (_set(0, reps=10, load=ASSISTANCE_KG),),
        )
    )
    signed_assistance = analyze_exercise_occurrence(
        _exercise("assisted-pullup", "Pull Up", (_set(0, reps=8, load=Decimal("-20")),))
    )
    treadmill = analyze_exercise_occurrence(
        _exercise(
            "treadmill",
            "Treadmill",
            (_set(0, reps=0, load=Decimal("0"), duration=Decimal("900")),),
        )
    )
    distance = analyze_exercise_occurrence(
        _exercise(
            "run",
            "Run",
            (_set(0, reps=0, load=Decimal("0"), distance=Decimal("1609.344")),),
        )
    )
    stretching = analyze_exercise_occurrence(
        _exercise("stretch", "Stretching", (_set(0, reps=0, load=Decimal("0")),))
    )

    assert bodyweight.metric_family is ExerciseMetricFamily.BODYWEIGHT_REP
    assert bodyweight.volume_kg_reps is None
    assert assisted.metric_family is ExerciseMetricFamily.ASSISTED_REP
    assert assisted.max_load_kg is None and assisted.volume_kg_reps is None
    assert signed_assistance.metric_family is ExerciseMetricFamily.ASSISTED_REP
    assert treadmill.metric_family is ExerciseMetricFamily.DURATION
    assert treadmill.volume_kg_reps is None
    assert distance.metric_family is ExerciseMetricFamily.DISTANCE
    assert stretching.metric_family is ExerciseMetricFamily.OTHER
    assert stretching.volume_kg_reps is None


def test_session_summary_keeps_repeated_occurrences_separate() -> None:
    first = _exercise(order=0)
    second = _exercise(
        order=1,
        occurrence="fixture-row-template:1",
        sets=(replace(_set(0), set_identity="1:0"),),
    )
    result = analyze_training_session(_stored(_session("one", BASE, (first, second)), 1))

    assert result.exercise_count == 2
    assert tuple(item.occurrence_identity for item in result.exercises) == (
        "fixture-row-template:0",
        "fixture-row-template:1",
    )
    assert result.session_duration_seconds == Decimal("3600")


def test_history_deltas_prs_frequency_and_partial_history_wording() -> None:
    repo = InMemoryDetailedTrainingRepository()
    sessions = (
        _stored(
            _session(
                "latest",
                BASE + timedelta(days=3),
                (_exercise(sets=(_set(0, reps=5, load=LOAD_90_LB_KG),)),),
            ),
            3,
        ),
        _stored(
            _session(
                "middle",
                BASE + timedelta(days=2),
                (_exercise(sets=(_set(0, reps=11, load=LOAD_80_LB_KG),)),),
            ),
            2,
        ),
        _stored(
            _session("oldest", BASE, (_exercise(sets=(_set(0, reps=9, load=LOAD_80_LB_KG),)),)), 1
        ),
    )
    repo.revisions = {
        (
            USER,
            item.session.source_system,
            item.session.source_session_id,
            item.session.source_revision,
        ): item
        for item in sessions
    }
    repo.sync_checkpoints.append(_checkpoint())

    history = GetExerciseTrainingHistoryUseCase(repo).execute(
        user_id=USER,
        source_system=DetailedTrainingSourceSystem.HEVY,
        source_exercise_id="fixture-row-template",
        timezone="America/New_York",
        as_of_date=date(2026, 9, 4),
    )

    assert history is not None
    assert tuple(item.source_session_id for item in history.history) == (
        "latest",
        "middle",
        "oldest",
    )
    assert history.comparison.top_load_delta_kg == LOAD_90_LB_KG - LOAD_80_LB_KG
    assert history.comparison.reps_at_same_top_load_delta is None
    assert history.frequency.sessions_last_7_days == 3
    assert history.frequency.sessions_last_28_days == 3
    assert history.frequency.days_since_last_performance == 0
    types = tuple(item.pr_type for item in history.pr_evidence)
    assert TrainingPRType.HIGHEST_LOAD in types
    assert TrainingPRType.MOST_REPS_AT_LOAD in types
    assert TrainingPRType.HIGHEST_VOLUME_SESSION in types
    assert history.completeness.lifetime_guaranteed is False
    assert "not guaranteed" in history.completeness.wording


def test_same_load_rep_delta_and_equal_performance_create_no_new_pr() -> None:
    repo = InMemoryDetailedTrainingRepository()
    points = (
        _stored(
            _session("new", BASE + timedelta(days=1), (_exercise(sets=(_set(0, reps=11),)),)), 2
        ),
        _stored(_session("old", BASE, (_exercise(sets=(_set(0, reps=9),)),)), 1),
    )
    repo.revisions = {
        (
            USER,
            item.session.source_system,
            item.session.source_session_id,
            item.session.source_revision,
        ): item
        for item in points
    }
    history = GetExerciseTrainingHistoryUseCase(repo).execute(
        user_id=USER,
        source_system=DetailedTrainingSourceSystem.HEVY,
        source_exercise_id="fixture-row-template",
        timezone="UTC",
        as_of_date=date(2026, 9, 2),
    )
    assert history is not None
    assert history.comparison.top_load_delta_kg == Decimal("0")
    assert history.comparison.reps_at_same_top_load_delta == 2
    rep_prs = [
        item for item in history.pr_evidence if item.pr_type is TrainingPRType.MOST_REPS_AT_LOAD
    ]
    assert len(rep_prs) == 2

    equal = replace(
        points[0],
        revision_id=UUID(int=3),
        session=_session("equal", BASE + timedelta(days=2), (_exercise(sets=(_set(0, reps=11),)),)),
    )
    repo.revisions[(USER, equal.session.source_system, "equal", equal.session.source_revision)] = (
        equal
    )
    replay = GetExerciseTrainingHistoryUseCase(repo).execute(
        user_id=USER,
        source_system=DetailedTrainingSourceSystem.HEVY,
        source_exercise_id="fixture-row-template",
        timezone="UTC",
        as_of_date=date(2026, 9, 3),
    )
    assert replay is not None
    assert (
        len(
            [
                item
                for item in replay.pr_evidence
                if item.pr_type is TrainingPRType.MOST_REPS_AT_LOAD
            ]
        )
        == 2
    )


def test_latest_revision_wins_and_tombstone_removes_analytics() -> None:
    ids = iter((UUID(int=1), UUID(int=2)))
    repo = InMemoryDetailedTrainingRepository(ids=lambda: next(ids))
    old = _session("logical", BASE, (_exercise(sets=(_set(0, reps=8),)),), revision=1)
    new = _session("logical", BASE, (_exercise(sets=(_set(0, reps=10),)),), revision=2)
    repo.apply_import(USER, DetailedTrainingImportBatch((old, new), (), True), BASE)

    recent = GetRecentTrainingAnalyticsUseCase(repo).execute(user_id=USER)
    assert len(recent.sessions) == 1
    assert recent.sessions[0].exercises[0].rep_total == 10
    assert len(repo.revisions) == 2

    deletion = DetailedTrainingDeletion(
        DetailedTrainingSourceSystem.HEVY,
        "logical",
        BASE + timedelta(days=1),
        "hevy-public-api.v1",
        "d" * 64,
    )
    repo.apply_import(USER, DetailedTrainingImportBatch((), (deletion,), True), BASE)
    assert GetRecentTrainingAnalyticsUseCase(repo).execute(user_id=USER).sessions == ()


def test_exercise_index_uses_source_identity_owner_scope_and_bounds() -> None:
    repo = InMemoryDetailedTrainingRepository()
    stored = _stored(_session("one", BASE, (_exercise(),)), 1)
    repo.revisions[(USER, stored.session.source_system, "one", "revision-1")] = stored

    index = GetExerciseIndexUseCase(repo).execute(
        user_id=USER,
        timezone="UTC",
        as_of_date=date(2026, 9, 1),
    )
    other = GetExerciseIndexUseCase(repo).execute(
        user_id=OTHER,
        timezone="UTC",
        as_of_date=date(2026, 9, 1),
    )

    assert len(index.exercises) == 1
    assert index.exercises[0].source_exercise_id == "fixture-row-template"
    assert other.exercises == ()
    with pytest.raises(ValueError):
        GetExerciseIndexUseCase(repo).execute(
            user_id=USER, timezone="UTC", as_of_date=date(2026, 9, 1), limit=201
        )


def test_history_rejects_bad_timezone_and_is_bounded() -> None:
    repo = InMemoryDetailedTrainingRepository()
    stored = _stored(_session("one", BASE, (_exercise(),)), 1)
    repo.revisions[(USER, stored.session.source_system, "one", "revision-1")] = stored
    use_case = GetExerciseTrainingHistoryUseCase(repo)
    with pytest.raises(ValueError, match="timezone"):
        use_case.execute(
            user_id=USER,
            source_system=DetailedTrainingSourceSystem.HEVY,
            source_exercise_id="fixture-row-template",
            timezone="not/a-zone",
            as_of_date=date(2026, 9, 1),
        )
    with pytest.raises(ValueError, match="limit"):
        use_case.execute(
            user_id=USER,
            source_system=DetailedTrainingSourceSystem.HEVY,
            source_exercise_id="fixture-row-template",
            timezone="UTC",
            as_of_date=date(2026, 9, 1),
            limit=51,
        )


def test_unknown_set_type_is_unsupported() -> None:
    exercise = _exercise(sets=(_set(0, kind=cast(TrainingSetType, "mystery")),))
    result = analyze_exercise_occurrence(exercise)
    assert result.unsupported_set_count == 1
    assert result.working_set_count == 0
    assert result.warmup_set_count == 0


def test_dropset_can_be_top_set_evidence() -> None:
    result = analyze_exercise_occurrence(
        _exercise(sets=(_set(0, kind=TrainingSetType.DROPSET, reps=10, load=LOAD_90_LB_KG),))
    )
    assert result.top_load_set is not None
    assert result.top_load_set.set_type is TrainingSetType.DROPSET
    assert result.top_load_set.load_kg == LOAD_90_LB_KG


def test_failure_set_can_be_top_set_evidence() -> None:
    result = analyze_exercise_occurrence(
        _exercise(sets=(_set(0, kind=TrainingSetType.FAILURE, reps=10, load=LOAD_90_LB_KG),))
    )
    assert result.top_load_set is not None
    assert result.top_load_set.set_type is TrainingSetType.FAILURE
    assert result.top_load_set.load_kg == LOAD_90_LB_KG


def test_exact_decimal_volume_calculation() -> None:
    result = analyze_exercise_occurrence(
        _exercise(sets=(_set(0, reps=8, load=Decimal("36.28743275485118")),))
    )
    assert result.volume_kg_reps == Decimal("290.29946203880944")


def test_tombstoned_session_removed_from_pr_evidence() -> None:
    repo = InMemoryDetailedTrainingRepository()
    sessions = (
        _stored(
            _session("one", BASE, (_exercise(sets=(_set(0, reps=10, load=LOAD_80_LB_KG),)),)), 1
        ),
        _stored(
            _session(
                "two",
                BASE + timedelta(days=1),
                (_exercise(sets=(_set(0, reps=12, load=LOAD_80_LB_KG),)),),
            ),
            2,
        ),
    )
    for item in sessions:
        repo.revisions[
            (
                USER,
                item.session.source_system,
                item.session.source_session_id,
                item.session.source_revision,
            )
        ] = item
    repo.sync_checkpoints.append(_checkpoint())

    history = GetExerciseTrainingHistoryUseCase(repo).execute(
        user_id=USER,
        source_system=DetailedTrainingSourceSystem.HEVY,
        source_exercise_id="fixture-row-template",
        timezone="UTC",
        as_of_date=date(2026, 9, 3),
    )
    assert (
        len([pr for pr in history.pr_evidence if pr.pr_type is TrainingPRType.MOST_REPS_AT_LOAD])
        == 2
    )

    deletion = DetailedTrainingDeletion(
        DetailedTrainingSourceSystem.HEVY,
        "two",
        BASE + timedelta(days=2),
        "hevy-public-api.v1",
        "d" * 64,
    )
    repo.apply_import(USER, DetailedTrainingImportBatch((), (deletion,), True), BASE)

    history_after = GetExerciseTrainingHistoryUseCase(repo).execute(
        user_id=USER,
        source_system=DetailedTrainingSourceSystem.HEVY,
        source_exercise_id="fixture-row-template",
        timezone="UTC",
        as_of_date=date(2026, 9, 3),
    )
    assert (
        len(
            [
                pr
                for pr in history_after.pr_evidence
                if pr.pr_type is TrainingPRType.MOST_REPS_AT_LOAD
            ]
        )
        == 1
    )


def test_frequency_boundary_tests() -> None:
    repo = InMemoryDetailedTrainingRepository()
    tz = ZoneInfo("America/New_York")
    as_of = date(2026, 9, 28)

    session_7 = datetime(2026, 9, 21, 12, tzinfo=tz)
    session_6 = datetime(2026, 9, 22, 12, tzinfo=tz)
    session_28 = datetime(2026, 8, 31, 12, tzinfo=tz)

    sessions = (
        _stored(_session("s7", session_7, (_exercise(),)), 1),
        _stored(_session("s6", session_6, (_exercise(),)), 2),
        _stored(_session("s28", session_28, (_exercise(),)), 3),
    )
    for item in sessions:
        repo.revisions[
            (
                USER,
                item.session.source_system,
                item.session.source_session_id,
                item.session.source_revision,
            )
        ] = item
    repo.sync_checkpoints.append(_checkpoint())

    history = GetExerciseTrainingHistoryUseCase(repo).execute(
        user_id=USER,
        source_system=DetailedTrainingSourceSystem.HEVY,
        source_exercise_id="fixture-row-template",
        timezone="America/New_York",
        as_of_date=as_of,
    )

    assert history.frequency.sessions_last_7_days == 1
    assert history.frequency.sessions_last_28_days == 2


def test_volume_delta_invalid_when_one_side_lacks_valid_volume() -> None:
    repo = InMemoryDetailedTrainingRepository()
    sessions = (
        _stored(_session("one", BASE, (_exercise(sets=(_set(0, reps=10, load=None),)),)), 1),
        _stored(
            _session(
                "two",
                BASE + timedelta(days=1),
                (_exercise(sets=(_set(0, reps=10, load=LOAD_80_LB_KG),)),),
            ),
            2,
        ),
    )
    for item in sessions:
        repo.revisions[
            (
                USER,
                item.session.source_system,
                item.session.source_session_id,
                item.session.source_revision,
            )
        ] = item
    repo.sync_checkpoints.append(_checkpoint())

    history = GetExerciseTrainingHistoryUseCase(repo).execute(
        user_id=USER,
        source_system=DetailedTrainingSourceSystem.HEVY,
        source_exercise_id="fixture-row-template",
        timezone="UTC",
        as_of_date=date(2026, 9, 3),
    )
    assert history.comparison.volume_delta_kg_reps is None


def test_higher_load_comparison_reason_code() -> None:
    repo = InMemoryDetailedTrainingRepository()
    sessions = (
        _stored(
            _session("one", BASE, (_exercise(sets=(_set(0, reps=10, load=LOAD_80_LB_KG),)),)), 1
        ),
        _stored(
            _session(
                "two",
                BASE + timedelta(days=1),
                (_exercise(sets=(_set(0, reps=10, load=LOAD_90_LB_KG),)),),
            ),
            2,
        ),
    )
    for item in sessions:
        repo.revisions[
            (
                USER,
                item.session.source_system,
                item.session.source_session_id,
                item.session.source_revision,
            )
        ] = item
    repo.sync_checkpoints.append(_checkpoint())

    history = GetExerciseTrainingHistoryUseCase(repo).execute(
        user_id=USER,
        source_system=DetailedTrainingSourceSystem.HEVY,
        source_exercise_id="fixture-row-template",
        timezone="UTC",
        as_of_date=date(2026, 9, 3),
    )
    assert "top_loads_differ_for_rep_comparison" in history.comparison.reason_codes


def test_replay_does_not_duplicate_analytics() -> None:
    ids = iter((UUID(int=1), UUID(int=2)))
    repo = InMemoryDetailedTrainingRepository(ids=lambda: next(ids))
    session = _session("logical", BASE, (_exercise(sets=(_set(0, reps=8),)),), revision=1)

    batch = DetailedTrainingImportBatch((session,), (), True)
    repo.apply_import(USER, batch, BASE)
    recent_first = GetRecentTrainingAnalyticsUseCase(repo).execute(user_id=USER)

    ids2 = iter((UUID(int=3), UUID(int=4)))
    repo.generate_id = lambda: next(ids2)
    repo.apply_import(USER, batch, BASE + timedelta(minutes=1))

    recent_second = GetRecentTrainingAnalyticsUseCase(repo).execute(user_id=USER)
    assert len(recent_first.sessions) == 1
    assert len(recent_second.sessions) == 1
    assert recent_first.sessions[0].exercises[0].rep_total == 8
    assert recent_second.sessions[0].exercises[0].rep_total == 8
