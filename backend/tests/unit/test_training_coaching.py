"""Deterministic post-v1 coaching policy over immutable Hevy evidence."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from nutrition_agent.application.training_analytics import GetExerciseTrainingHistoryUseCase
from nutrition_agent.db.in_memory_repos import InMemoryDetailedTrainingRepository
from nutrition_agent.domain.training.analytics import (
    ExerciseCoachingGuidance,
    TrainingCoachingAction,
    TrainingCoachingStatus,
)
from nutrition_agent.domain.training.detail import (
    DetailedTrainingSession,
    DetailedTrainingSet,
    DetailedTrainingSourceSystem,
    DetailedTrainingSyncCheckpoint,
    DetailedTrainingSyncMode,
    ExercisePerformance,
    StoredDetailedTrainingSession,
    TrainingLoad,
    TrainingLoadUnit,
    TrainingSetType,
)

USER = UUID(int=1)
BASE = datetime(2026, 9, 1, 14, tzinfo=UTC)
EXERCISE_ID = "coaching-exercise"


def _set(
    index: int,
    *,
    reps: int | None,
    load: Decimal | None,
    kind: TrainingSetType = TrainingSetType.NORMAL,
    rpe: Decimal | None = None,
) -> DetailedTrainingSet:
    return DetailedTrainingSet(
        set_identity=f"set:{index}",
        set_index=index,
        set_type=kind,
        reps=reps,
        load=(TrainingLoad(load, TrainingLoadUnit.KILOGRAM) if load is not None else None),
        rpe=rpe,
    )


def _exercise(
    *,
    name: str = "Cable Row",
    sets: tuple[DetailedTrainingSet, ...],
    order: int = 0,
) -> ExercisePerformance:
    return ExercisePerformance(
        occurrence_identity=f"{EXERCISE_ID}:{order}",
        source_exercise_id=EXERCISE_ID,
        display_name=name,
        exercise_order=order,
        sets=sets,
    )


def _stored(
    *,
    revision_id: int,
    day: int,
    exercises: tuple[ExercisePerformance, ...],
) -> StoredDetailedTrainingSession:
    started_at = BASE + timedelta(days=day)
    session = DetailedTrainingSession(
        source_system=DetailedTrainingSourceSystem.HEVY,
        source_session_id=f"session-{revision_id}",
        source_revision=f"revision-{revision_id}",
        title="Training",
        started_at=started_at,
        ended_at=started_at + timedelta(hours=1),
        exercises=exercises,
        parser_version="hevy-public-api.v1",
        source_payload_sha256=f"{revision_id:064x}",
        source_updated_at=started_at,
    )
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


def _guidance(
    previous: tuple[ExercisePerformance, ...],
    latest: tuple[ExercisePerformance, ...],
    *,
    include_checkpoint: bool = True,
    as_of_date: date = date(2026, 9, 9),
) -> ExerciseCoachingGuidance:
    repo = InMemoryDetailedTrainingRepository()
    stored = (
        _stored(revision_id=1, day=0, exercises=previous),
        _stored(revision_id=2, day=7, exercises=latest),
    )
    repo.revisions = {
        (
            USER,
            item.session.source_system,
            item.session.source_session_id,
            item.session.source_revision,
        ): item
        for item in stored
    }
    if include_checkpoint:
        repo.sync_checkpoints.append(_checkpoint())
    history = GetExerciseTrainingHistoryUseCase(repo).execute(
        user_id=USER,
        source_system=DetailedTrainingSourceSystem.HEVY,
        source_exercise_id=EXERCISE_ID,
        timezone="UTC",
        as_of_date=as_of_date,
    )
    assert history is not None
    return history.coaching


def test_weighted_progress_adds_one_rep_at_exact_same_load() -> None:
    guidance = _guidance(
        (_exercise(sets=(_set(0, reps=8, load=Decimal("40")),)),),
        (_exercise(sets=(_set(0, reps=9, load=Decimal("40")),)),),
    )

    assert guidance.policy_version == "owner-training-coaching.v1"
    assert guidance.status is TrainingCoachingStatus.PROGRESS
    assert guidance.action is TrainingCoachingAction.ADD_ONE_TOP_SET_REP_SAME_LOAD
    assert guidance.target is not None
    assert guidance.target.top_load_kg == Decimal("40")
    assert guidance.target.top_set_reps == 10
    assert guidance.target.total_reps is None


def test_weighted_load_change_failure_and_high_effort_hold_exact_latest_target() -> None:
    increased = _guidance(
        (_exercise(sets=(_set(0, reps=8, load=Decimal("40")),)),),
        (_exercise(sets=(_set(0, reps=6, load=Decimal("42.5")),)),),
    )
    failure = _guidance(
        (_exercise(sets=(_set(0, reps=8, load=Decimal("40")),)),),
        (
            _exercise(
                sets=(
                    _set(
                        0,
                        reps=9,
                        load=Decimal("40"),
                        kind=TrainingSetType.FAILURE,
                        rpe=Decimal("9.5"),
                    ),
                )
            ),
        ),
    )
    previous_failure = _guidance(
        (
            _exercise(
                sets=(
                    _set(
                        0,
                        reps=8,
                        load=Decimal("40"),
                        kind=TrainingSetType.FAILURE,
                    ),
                )
            ),
        ),
        (_exercise(sets=(_set(0, reps=9, load=Decimal("40")),)),),
    )

    assert increased.status is TrainingCoachingStatus.HOLD
    assert increased.action is TrainingCoachingAction.REPEAT_LATEST_TOP_SET
    assert increased.target is not None
    assert increased.target.top_load_kg == Decimal("42.5")
    assert increased.target.top_set_reps == 6
    assert "load_increased_consolidate" in increased.reason_codes
    assert failure.status is TrainingCoachingStatus.HOLD
    assert "failure_set_present" in failure.reason_codes
    assert "high_effort_recorded" in failure.reason_codes
    assert previous_failure.status is TrainingCoachingStatus.HOLD
    assert "failure_set_present" in previous_failure.reason_codes


def test_bodyweight_progresses_total_reps_without_zero_load_volume() -> None:
    guidance = _guidance(
        (_exercise(name="Pull Up", sets=(_set(0, reps=8, load=None),)),),
        (_exercise(name="Pull Up", sets=(_set(0, reps=9, load=None),)),),
    )

    assert guidance.status is TrainingCoachingStatus.PROGRESS
    assert guidance.action is TrainingCoachingAction.ADD_ONE_TOTAL_REP_BODYWEIGHT
    assert guidance.target is not None
    assert guidance.target.top_load_kg is None
    assert guidance.target.total_reps == 10
    assert not guidance.target.keep_assistance_constant


def test_assisted_progress_requires_identical_assistance_configuration() -> None:
    same = _guidance(
        (
            _exercise(
                name="Pull Up (Assisted)",
                sets=(
                    _set(0, reps=8, load=Decimal("20")),
                    _set(1, reps=7, load=Decimal("20")),
                ),
            ),
        ),
        (
            _exercise(
                name="Pull Up (Assisted)",
                sets=(
                    _set(0, reps=9, load=Decimal("20")),
                    _set(1, reps=7, load=Decimal("20")),
                ),
            ),
        ),
    )
    changed = _guidance(
        (_exercise(name="Pull Up (Assisted)", sets=(_set(0, reps=8, load=Decimal("20")),)),),
        (_exercise(name="Pull Up (Assisted)", sets=(_set(0, reps=9, load=Decimal("15")),)),),
    )

    assert same.status is TrainingCoachingStatus.PROGRESS
    assert same.action is TrainingCoachingAction.ADD_ONE_TOTAL_REP_SAME_ASSISTANCE
    assert same.target is not None
    assert same.target.total_reps == 17
    assert same.target.keep_assistance_constant
    assert changed.status is TrainingCoachingStatus.UNAVAILABLE
    assert changed.target is None
    assert changed.reason_codes == ("assistance_configuration_changed",)


def test_incomplete_stale_and_repeated_evidence_fail_closed() -> None:
    ordinary = (_exercise(sets=(_set(0, reps=8, load=Decimal("40")),)),)
    no_checkpoint = _guidance(ordinary, ordinary, include_checkpoint=False)
    stale = _guidance(ordinary, ordinary, as_of_date=date(2026, 11, 1))
    repeated = _guidance(
        ordinary,
        (
            _exercise(sets=(_set(0, reps=8, load=Decimal("40")),), order=0),
            _exercise(sets=(_set(0, reps=8, load=Decimal("40")),), order=1),
        ),
    )

    assert no_checkpoint.status is TrainingCoachingStatus.UNAVAILABLE
    assert no_checkpoint.reason_codes == ("history_incomplete",)
    assert stale.reason_codes == ("stale_latest_performance",)
    assert repeated.reason_codes == ("repeated_exercise_occurrence",)
