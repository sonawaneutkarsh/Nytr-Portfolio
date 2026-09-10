"""Explicit bounded synchronization of official Hevy detailed-training data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from nutrition_agent.application.ports import (
    Clock,
    DetailedTrainingSourceError,
    DetailedTrainingSourceFailureKind,
    DetailedTrainingSyncRepository,
    DetailedTrainingSyncSource,
    IdGenerator,
)
from nutrition_agent.domain.training.detail import (
    DetailedTrainingSourceSystem,
    DetailedTrainingSyncCheckpoint,
    DetailedTrainingSyncMode,
    DetailedTrainingSyncOutcome,
    DetailedTrainingSyncStatus,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_EVENT_REPLAY_OVERLAP = timedelta(seconds=1)


@dataclass(frozen=True)
class SyncHevyDetailedTrainingUseCase:
    source: DetailedTrainingSyncSource
    repository: DetailedTrainingSyncRepository
    clock: Clock
    ids: IdGenerator

    def execute(self, *, user_id: UUID) -> DetailedTrainingSyncOutcome:
        if not self.source.is_configured:
            raise DetailedTrainingSourceError(
                DetailedTrainingSourceFailureKind.NOT_CONFIGURED,
                "Hevy API access is not configured",
            )
        prior = self.repository.latest_sync_checkpoint(
            user_id,
            DetailedTrainingSourceSystem.HEVY,
        )
        if prior is None or not prior.bootstrap_completed:
            mode = DetailedTrainingSyncMode.BOOTSTRAP
            fetched = self.source.load_initial()
        else:
            mode = DetailedTrainingSyncMode.INCREMENTAL
            since = max(_EPOCH, prior.source_event_watermark - _EVENT_REPLAY_OVERLAP)
            fetched = self.source.load_changes(since)

        if not fetched.batch.complete or fetched.has_more:
            return DetailedTrainingSyncOutcome(
                status=DetailedTrainingSyncStatus.INCOMPLETE,
                mode=mode,
                sessions_created=0,
                revisions_appended=0,
                deletions_recorded=0,
                events_replayed=0,
                tombstone_blocked=0,
                pages_fetched=fetched.pages_fetched,
                has_more=True,
                source_event_watermark=fetched.source_event_watermark,
                logical_requests=fetched.logical_requests,
                attempts_made=fetched.attempts_made,
                retries=fetched.retries,
                checkpoint_advanced=False,
            )

        completed_at = self.clock.now()
        if completed_at.tzinfo is None or completed_at.utcoffset() is None:
            raise ValueError("Hevy sync clock must return a timezone-aware timestamp")
        watermark = fetched.source_event_watermark
        if prior is not None:
            watermark = max(watermark, prior.source_event_watermark)
        checkpoint = DetailedTrainingSyncCheckpoint(
            checkpoint_id=self.ids.new_id(),
            user_id=user_id,
            source_system=DetailedTrainingSourceSystem.HEVY,
            sync_mode=mode,
            bootstrap_completed=True,
            source_event_watermark=watermark,
            parser_version=fetched.parser_version,
            provider_version=fetched.provider_version,
            pages_fetched=fetched.pages_fetched,
            logical_requests=fetched.logical_requests,
            attempts_made=fetched.attempts_made,
            retries=fetched.retries,
            completed_at=completed_at,
        )
        imported = self.repository.apply_sync(
            user_id,
            fetched.batch,
            completed_at,
            checkpoint,
        )
        return DetailedTrainingSyncOutcome(
            status=DetailedTrainingSyncStatus.SYNCED,
            mode=mode,
            sessions_created=imported.sessions_created,
            revisions_appended=imported.revisions_appended,
            deletions_recorded=imported.applied_deletions,
            events_replayed=imported.duplicate_revisions + imported.duplicate_deletions,
            tombstone_blocked=imported.blocked_by_tombstone,
            pages_fetched=fetched.pages_fetched,
            has_more=False,
            source_event_watermark=watermark,
            logical_requests=fetched.logical_requests,
            attempts_made=fetched.attempts_made,
            retries=fetched.retries,
            checkpoint_advanced=True,
        )


__all__ = ["SyncHevyDetailedTrainingUseCase"]
