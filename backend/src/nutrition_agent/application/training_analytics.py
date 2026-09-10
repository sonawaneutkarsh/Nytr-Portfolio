"""Compute-on-read use cases for factual detailed-training analytics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from nutrition_agent.application.ports import DetailedTrainingAnalyticsRepository
from nutrition_agent.domain.training.analytics import (
    OWNER_TRAINING_ANALYTICS_V1,
    ExerciseIndexEntry,
    ExerciseTrainingHistory,
    TrainingAnalyticsPolicy,
    TrainingHistoryCompleteness,
    TrainingSessionAnalytics,
    analyze_training_session,
    build_exercise_history,
    build_exercise_index,
)
from nutrition_agent.domain.training.detail import DetailedTrainingSourceSystem

MAX_ANALYTICS_SCAN = 500


@dataclass(frozen=True)
class RecentTrainingAnalytics:
    policy_version: str
    sessions: tuple[TrainingSessionAnalytics, ...]


@dataclass(frozen=True)
class ExerciseIndex:
    policy_version: str
    exercises: tuple[ExerciseIndexEntry, ...]
    completeness: TrainingHistoryCompleteness


@dataclass(frozen=True)
class GetRecentTrainingAnalyticsUseCase:
    repository: DetailedTrainingAnalyticsRepository
    policy: TrainingAnalyticsPolicy = OWNER_TRAINING_ANALYTICS_V1

    def execute(self, *, user_id: UUID, limit: int = 20) -> RecentTrainingAnalytics:
        if limit < 1 or limit > 50:
            raise ValueError("limit must be between 1 and 50")
        page = self.repository.list_latest_full(
            user_id,
            limit,
            source_system=DetailedTrainingSourceSystem.HEVY,
        )
        return RecentTrainingAnalytics(
            policy_version=self.policy.version,
            sessions=tuple(analyze_training_session(item, self.policy) for item in page.sessions),
        )


@dataclass(frozen=True)
class GetExerciseIndexUseCase:
    repository: DetailedTrainingAnalyticsRepository
    policy: TrainingAnalyticsPolicy = OWNER_TRAINING_ANALYTICS_V1
    scan_limit: int = MAX_ANALYTICS_SCAN

    def execute(
        self,
        *,
        user_id: UUID,
        timezone: str,
        as_of_date: date,
        limit: int = 100,
    ) -> ExerciseIndex:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        page = self.repository.list_latest_full(
            user_id,
            self.scan_limit,
            source_system=DetailedTrainingSourceSystem.HEVY,
        )
        checkpoint = self.repository.latest_sync_checkpoint(
            user_id,
            DetailedTrainingSourceSystem.HEVY,
        )
        complete = _completeness(checkpoint is not None, not page.has_more)
        return ExerciseIndex(
            policy_version=self.policy.version,
            exercises=build_exercise_index(
                sessions=page.sessions,
                timezone=timezone,
                as_of_date=as_of_date,
                result_limit=limit,
                policy=self.policy,
            ),
            completeness=complete,
        )


@dataclass(frozen=True)
class GetExerciseTrainingHistoryUseCase:
    repository: DetailedTrainingAnalyticsRepository
    policy: TrainingAnalyticsPolicy = OWNER_TRAINING_ANALYTICS_V1
    scan_limit: int = MAX_ANALYTICS_SCAN

    def execute(
        self,
        *,
        user_id: UUID,
        source_system: DetailedTrainingSourceSystem,
        source_exercise_id: str,
        timezone: str,
        as_of_date: date,
        limit: int = 50,
    ) -> ExerciseTrainingHistory | None:
        if not source_exercise_id.strip():
            raise ValueError("source_exercise_id must not be empty")
        if limit < 1 or limit > 50:
            raise ValueError("limit must be between 1 and 50")
        page = self.repository.list_latest_full(
            user_id,
            self.scan_limit,
            source_system=source_system,
            source_exercise_id=source_exercise_id,
        )
        checkpoint = self.repository.latest_sync_checkpoint(user_id, source_system)
        return build_exercise_history(
            sessions=page.sessions,
            source_system=source_system,
            source_exercise_id=source_exercise_id,
            timezone=timezone,
            as_of_date=as_of_date,
            result_limit=limit,
            source_bootstrap_complete=checkpoint is not None,
            query_complete=not page.has_more,
            policy=self.policy,
        )


def _completeness(
    source_bootstrap_complete: bool,
    query_complete: bool,
) -> TrainingHistoryCompleteness:
    return TrainingHistoryCompleteness(
        source_bootstrap_complete=source_bootstrap_complete,
        query_complete=query_complete,
        lifetime_guaranteed=False,
        wording=(
            "Analytics cover synced Hevy history; Hevy lifetime completeness is not guaranteed."
            if source_bootstrap_complete and query_complete
            else "Analytics cover a bounded subset of synced Hevy history."
        ),
    )


__all__ = [
    "ExerciseIndex",
    "GetExerciseIndexUseCase",
    "GetExerciseTrainingHistoryUseCase",
    "GetRecentTrainingAnalyticsUseCase",
    "RecentTrainingAnalytics",
]
