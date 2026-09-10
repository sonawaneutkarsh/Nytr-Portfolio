"""PostgreSQL repository implementations (lazy psycopg import).

No DELETE statements exist here by design (ADR-013 pillar 4); replacement is
insert-new-version plus supersede pointers. These implementations are exercised
by migration tests only when STACKS_TEST_DATABASE_URL is configured.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import NAMESPACE_OID, UUID, uuid4, uuid5

from nutrition_agent.application.ports import (
    AcceptedMenuPageKey,
    AcceptedMenuPageObservation,
    BodyProfileRepository,
    ConsumptionRepository,
    CustomFoodRepository,
    DailyNutritionLedgerRepository,
    DetailedTrainingAnalyticsRepository,
    DetailedTrainingRepository,
    DetailedTrainingRevisionConflictError,
    DetailedTrainingSyncRepository,
    DuplicateConsumptionError,
    DuplicateLogicalPlanError,
    DuplicateManualFoodError,
    DuplicateNextMealConsumptionError,
    DuplicateNextMealRecommendationError,
    DuplicateProteinProposalError,
    DuplicateTargetReviewError,
    GoalPolicyRepository,
    GoalPolicyVersionExistsError,
    MenuDayReadRepository,
    MenuPageCoverageRepository,
    MenuPageOfferingPin,
    MenuPageVersionRepository,
    NextMealConsumptionRepository,
    NextMealRecommendationRepository,
    NutritionAuthorityKey,
    PageLabelAuthorityKey,
    PersistedPlanItemReference,
    PersistedPlanView,
    PersistMenuPageOutcome,
    ProteinProposalDecisionConflictError,
    ResolvedMenuDay,
    ReusableNutritionAuthority,
    ReusablePageLabelAuthority,
    StaleProteinProposalError,
    StaleTargetReviewError,
    TargetPolicyVersionExistsError,
    TargetReviewDecisionConflictError,
    TargetReviewDecisionRepository,
    TargetReviewRepository,
    TrainingSessionRepository,
    ValidatedMenuLabelObservation,
    ValidatedMenuPage,
    WaistMeasurementRepository,
)
from nutrition_agent.domain.body_goals import OwnerBodyProfile, WaistMeasurement
from nutrition_agent.domain.consumption import (
    ConsumptionEntry,
    ConsumptionState,
    RecordConsumptionOutcome,
)
from nutrition_agent.domain.health.entities import (
    BatchOutcome,
    HealthSyncStatus,
    LatestSample,
    SyncBatch,
)
from nutrition_agent.domain.health.trend import BodyMassObservation
from nutrition_agent.domain.next_meal import (
    NextMealRecommendation,
    NextMealStatus,
    PersistNextMealOutcome,
)
from nutrition_agent.domain.next_meal_consumption import (
    NextMealConsumptionEntry,
    RecordNextMealConsumptionOutcome,
)
from nutrition_agent.domain.nutrition.custom_foods import (
    CustomFoodAuthority,
    CustomFoodProvenance,
    CustomFoodVersion,
    ManualFoodConsumptionEntry,
    ManualMealPeriod,
    ManualNutritionFacts,
    RecordManualFoodOutcome,
)
from nutrition_agent.domain.nutrition.ledger import (
    ConsumedNutritionEvidence,
    NutritionAuthority,
)
from nutrition_agent.domain.nutrition.targets import GoalKind
from nutrition_agent.domain.planning.artifacts import (
    DecisionLogEntry,
    PlanItem,
    PlanRun,
    PlanVersion,
    TargetPolicyVersion,
)
from nutrition_agent.domain.planning.menu_view import MenuDayView, OfferingView, PeriodMenu
from nutrition_agent.domain.protein_target import (
    DecideProteinProposalOutcome,
    ProteinProposalDecisionValue,
    ProteinTargetProposal,
    ProteinTargetProposalDecision,
)
from nutrition_agent.domain.stacks.entities import (
    ComponentStatement,
    Confidence,
    DietaryTag,
    MealPeriod,
    MenuOffering,
    NutrientKey,
    NutrientValue,
    NutritionProfile,
    NutritionSourceState,
    Provenance,
    ServingBasisKind,
)
from nutrition_agent.domain.stacks.ingestion import (
    ErrorCode,
    IngestionRun,
    QuarantineRecord,
    Severity,
    SubjectType,
)
from nutrition_agent.domain.target_review import (
    DecideTargetReviewOutcome,
    GoalDirection,
    GoalPolicyVersion,
    PersistTargetReviewOutcome,
    TargetReview,
    TargetReviewDecision,
    TargetReviewDecisionValue,
    TargetReviewStatus,
    goal_policy_payload_sha256,
    target_review_evaluation_document,
    target_review_evaluation_from_document,
)
from nutrition_agent.domain.training import (
    StoredTrainingSession,
    TrainingBatchOutcome,
    TrainingSourceSystem,
    TrainingSyncBatch,
)
from nutrition_agent.domain.training.detail import (
    DetailedTrainingImportBatch,
    DetailedTrainingImportOutcome,
    DetailedTrainingSession,
    DetailedTrainingSessionPage,
    DetailedTrainingSessionSummary,
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
from nutrition_agent.infrastructure.snapshot_store import SnapshotRef
from nutrition_agent.infrastructure.stacks_source.constants import STACKS_CAMPUS_ID


def _connect(dsn: str) -> Any:
    import psycopg

    return psycopg.connect(dsn)


class SqlHealthBodyMassRepository:
    """PostgreSQL store for body-mass samples (M5, ADR-016).

    Every public method runs one transaction as the restricted `authenticated`
    role with `request.jwt.claim.sub` set to the caller's subject, so Row Level
    Security (migration 0002) enforces owner isolation even if a query bug
    tried to cross users. No DELETE exists here; deletions are tombstones.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _authenticated_cursor(self, user_sub: str) -> Any:
        import psycopg

        conn = psycopg.connect(self._dsn)
        cur = conn.cursor()
        # Transaction-scoped: role + JWT-claim setting revert at commit/rollback.
        cur.execute("SET LOCAL ROLE authenticated")
        cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (user_sub,))
        return conn, cur

    def apply_batch(self, user_id: UUID, batch: SyncBatch) -> BatchOutcome:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            accepted = duplicates = applied = dup_deletions = 0
            for sample in batch.added:
                cur.execute(
                    """
                    INSERT INTO health_body_mass_sample (
                        id, user_id, hk_sample_uuid, value_kg,
                        sample_start, sample_end, source_name, source_bundle_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, hk_sample_uuid) DO NOTHING
                    """,
                    (
                        str(uuid4()),
                        str(user_id),
                        str(sample.sample_uuid),
                        str(sample.value_kg),
                        sample.sample_start,
                        sample.sample_end,
                        sample.source_name,
                        sample.source_bundle_id,
                    ),
                )
                if cur.rowcount == 1:
                    accepted += 1
                else:
                    duplicates += 1
            for deletion in batch.deletions:
                cur.execute(
                    """
                    SELECT tombstoned_at FROM health_body_mass_sample
                    WHERE user_id=%s AND hk_sample_uuid=%s
                    """,
                    (str(user_id), str(deletion.sample_uuid)),
                )
                existing = cur.fetchone()
                if existing is None:
                    # Unknown sample: record a tombstone-only row so a later
                    # add cannot resurrect the deleted HealthKit sample.
                    cur.execute(
                        """
                        INSERT INTO health_body_mass_sample (
                            id, user_id, hk_sample_uuid, tombstoned_at)
                        VALUES (%s, %s, %s, now())
                        """,
                        (str(uuid4()), str(user_id), str(deletion.sample_uuid)),
                    )
                    applied += 1
                elif existing[0] is None:
                    # Active measurement: tombstone it, retain history.
                    cur.execute(
                        """
                        UPDATE health_body_mass_sample
                        SET tombstoned_at = now()
                        WHERE user_id=%s AND hk_sample_uuid=%s
                          AND tombstoned_at IS NULL
                        """,
                        (str(user_id), str(deletion.sample_uuid)),
                    )
                    applied += 1
                else:
                    # Already tombstoned: idempotent repeat, first timestamp wins.
                    dup_deletions += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        return BatchOutcome(
            accepted_added=accepted,
            duplicate_added=duplicates,
            applied_deletions=applied,
            duplicate_deletions=dup_deletions,
        )

    def status_summary(self, user_id: UUID) -> HealthSyncStatus:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE value_kg IS NOT NULL) AS record_count,
                    COUNT(*) FILTER (WHERE tombstoned_at IS NOT NULL) AS tombstone_count,
                    MAX(ingested_at) AS last_ingested_at
                FROM health_body_mass_sample
                """
            )
            record_count, tombstone_count, last_ingested = cur.fetchone()
            cur.execute(
                """
                SELECT hk_sample_uuid, sample_start, value_kg
                FROM health_body_mass_sample
                WHERE tombstoned_at IS NULL AND value_kg IS NOT NULL
                ORDER BY sample_start DESC
                LIMIT 1
                """
            )
            latest = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        latest_sample = (
            LatestSample(
                sample_uuid=UUID(str(latest[0])),
                sample_start=latest[1],
                value_kg=latest[2],
            )
            if latest is not None
            else None
        )
        return HealthSyncStatus(
            record_count=int(record_count),
            tombstone_count=int(tombstone_count),
            latest_sample=latest_sample,
            last_ingested_at=last_ingested,
        )

    def list_active(
        self,
        user_id: UUID,
        start_inclusive: datetime,
        end_exclusive: datetime,
    ) -> tuple[BodyMassObservation, ...]:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                """
                SELECT hk_sample_uuid, value_kg, sample_start
                FROM health_body_mass_sample
                WHERE user_id=%s
                  AND tombstoned_at IS NULL
                  AND value_kg IS NOT NULL
                  AND sample_start IS NOT NULL
                  AND sample_start >= %s
                  AND sample_start < %s
                ORDER BY sample_start, hk_sample_uuid
                """,
                (str(user_id), start_inclusive, end_exclusive),
            )
            observations = tuple(
                BodyMassObservation(
                    sample_uuid=UUID(str(row[0])),
                    value_kg=row[1],
                    measured_at=row[2],
                )
                for row in cur.fetchall()
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return observations


class SqlBodyProfileRepository(BodyProfileRepository):
    """Owner profile row for explicit height and optional target weight."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _authenticated_cursor(self, user_sub: str) -> Any:
        import psycopg

        conn = psycopg.connect(self._dsn)
        cur = conn.cursor()
        cur.execute("SET LOCAL ROLE authenticated")
        cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (user_sub,))
        return conn, cur

    def get(self, user_id: UUID) -> OwnerBodyProfile | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                "SELECT user_id, height_cm, target_weight_kg, updated_at "
                "FROM owner_body_profile WHERE user_id=%s",
                (str(user_id),),
            )
            row = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if row is None:
            return None
        return OwnerBodyProfile(
            user_id=UUID(str(row[0])),
            height_cm=Decimal(row[1]),
            target_weight_kg=Decimal(row[2]) if row[2] is not None else None,
            updated_at=row[3],
        )

    def save(self, profile: OwnerBodyProfile) -> None:
        conn, cur = self._authenticated_cursor(str(profile.user_id))
        try:
            cur.execute(
                """
                INSERT INTO owner_body_profile (user_id, height_cm, target_weight_kg, updated_at)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (user_id) DO UPDATE SET
                    height_cm=EXCLUDED.height_cm,
                    target_weight_kg=EXCLUDED.target_weight_kg,
                    updated_at=EXCLUDED.updated_at
                """,
                (
                    str(profile.user_id),
                    profile.height_cm,
                    profile.target_weight_kg,
                    profile.updated_at,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


class SqlWaistMeasurementRepository(WaistMeasurementRepository):
    """Append-only owner-entered waist evidence under RLS."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _authenticated_cursor(self, user_sub: str) -> Any:
        import psycopg

        conn = psycopg.connect(self._dsn)
        cur = conn.cursor()
        cur.execute("SET LOCAL ROLE authenticated")
        cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (user_sub,))
        return conn, cur

    def append(self, measurement: WaistMeasurement) -> None:
        conn, cur = self._authenticated_cursor(str(measurement.user_id))
        try:
            cur.execute(
                """
                INSERT INTO waist_measurement
                    (measurement_id, user_id, waist_cm, measured_at, recorded_at, source)
                VALUES (%s,%s,%s,%s,%s,%s)
                """,
                (
                    str(measurement.measurement_id),
                    str(measurement.user_id),
                    measurement.waist_cm,
                    measurement.measured_at,
                    measurement.recorded_at,
                    measurement.source,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_recent(self, user_id: UUID, limit: int = 30) -> tuple[WaistMeasurement, ...]:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                """
                SELECT measurement_id, user_id, waist_cm, measured_at, recorded_at, source
                FROM waist_measurement
                WHERE user_id=%s
                ORDER BY measured_at DESC, measurement_id DESC
                LIMIT %s
                """,
                (str(user_id), limit),
            )
            rows = cur.fetchall()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return tuple(
            WaistMeasurement(
                measurement_id=UUID(str(row[0])),
                user_id=UUID(str(row[1])),
                waist_cm=Decimal(row[2]),
                measured_at=row[3],
                recorded_at=row[4],
                source=str(row[5]),
            )
            for row in rows
        )


class SqlTrainingSessionRepository(TrainingSessionRepository):
    """Owner-RLS PostgreSQL store for immutable workout observations."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _authenticated_cursor(self, user_sub: str) -> Any:
        import psycopg

        conn = psycopg.connect(self._dsn)
        cur = conn.cursor()
        cur.execute("SET LOCAL ROLE authenticated")
        cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (user_sub,))
        return conn, cur

    def apply_batch(self, user_id: UUID, batch: TrainingSyncBatch) -> TrainingBatchOutcome:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            accepted = duplicates = applied = duplicate_deletions = 0
            for observation in batch.added:
                identity = (
                    str(user_id),
                    observation.source_system.value,
                    observation.source_record_id,
                )
                cur.execute(
                    """
                    SELECT 1
                    FROM training_session_tombstone
                    WHERE user_id=%s AND source_system=%s AND source_record_id=%s
                    """,
                    identity,
                )
                if cur.fetchone() is not None:
                    duplicates += 1
                    continue
                cur.execute(
                    """
                    INSERT INTO training_session (
                        session_id, user_id, source_system, source_record_id,
                        activity_type, started_at, ended_at,
                        active_duration_seconds, active_energy_kcal,
                        timezone_identifier, source_name, source_bundle_id,
                        source_revision)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (user_id, source_system, source_record_id) DO NOTHING
                    """,
                    (
                        str(uuid4()),
                        *identity,
                        observation.activity_type,
                        observation.started_at,
                        observation.ended_at,
                        (
                            str(observation.active_duration_seconds)
                            if observation.active_duration_seconds is not None
                            else None
                        ),
                        (
                            str(observation.active_energy_kcal)
                            if observation.active_energy_kcal is not None
                            else None
                        ),
                        observation.timezone_identifier,
                        observation.source_name,
                        observation.source_bundle_id,
                        observation.source_revision,
                    ),
                )
                if cur.rowcount == 1:
                    accepted += 1
                else:
                    duplicates += 1

            for deletion in batch.deletions:
                identity = (
                    str(user_id),
                    deletion.source_system.value,
                    deletion.source_record_id,
                )
                cur.execute(
                    """
                    INSERT INTO training_session_tombstone (
                        tombstone_id, user_id, source_system, source_record_id)
                    VALUES (%s,%s,%s,%s)
                    ON CONFLICT (user_id, source_system, source_record_id) DO NOTHING
                    RETURNING tombstoned_at
                    """,
                    (str(uuid4()), *identity),
                )
                inserted = cur.fetchone()
                if inserted is None:
                    duplicate_deletions += 1
                    continue
                tombstoned_at = inserted[0]
                cur.execute(
                    """
                    UPDATE training_session
                    SET tombstoned_at=%s
                    WHERE user_id=%s AND source_system=%s AND source_record_id=%s
                      AND tombstoned_at IS NULL
                    """,
                    (tombstoned_at, *identity),
                )
                applied += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return TrainingBatchOutcome(
            accepted_added=accepted,
            duplicate_added=duplicates,
            applied_deletions=applied,
            duplicate_deletions=duplicate_deletions,
        )

    def list_active(
        self,
        user_id: UUID,
        start_inclusive: datetime,
        end_exclusive: datetime,
    ) -> tuple[StoredTrainingSession, ...]:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                """
                SELECT session_id, user_id, source_system, source_record_id,
                       activity_type, started_at, ended_at,
                       active_duration_seconds, active_energy_kcal,
                       timezone_identifier, source_name, source_bundle_id,
                       source_revision, ingested_at, tombstoned_at
                FROM training_session
                WHERE user_id=%s
                  AND tombstoned_at IS NULL
                  AND started_at >= %s
                  AND started_at < %s
                ORDER BY started_at, source_system, source_record_id
                """,
                (str(user_id), start_inclusive, end_exclusive),
            )
            rows = tuple(
                StoredTrainingSession(
                    session_id=UUID(str(row[0])),
                    user_id=UUID(str(row[1])),
                    source_system=TrainingSourceSystem(str(row[2])),
                    source_record_id=str(row[3]),
                    activity_type=row[4],
                    started_at=row[5],
                    ended_at=row[6],
                    active_duration_seconds=row[7],
                    active_energy_kcal=row[8],
                    timezone_identifier=row[9],
                    source_name=row[10],
                    source_bundle_id=row[11],
                    source_revision=row[12],
                    ingested_at=row[13],
                    tombstoned_at=row[14],
                )
                for row in cur.fetchall()
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return rows


class SqlDetailedTrainingRepository(
    DetailedTrainingRepository,
    DetailedTrainingSyncRepository,
    DetailedTrainingAnalyticsRepository,
):
    """Owner-RLS repository for immutable detailed-session source revisions."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _authenticated_cursor(self, user_sub: str) -> Any:
        import psycopg

        conn = psycopg.connect(self._dsn)
        cur = conn.cursor()
        cur.execute("SET LOCAL ROLE authenticated")
        cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (user_sub,))
        return conn, cur

    def apply_import(
        self,
        user_id: UUID,
        batch: DetailedTrainingImportBatch,
        ingested_at: datetime,
    ) -> DetailedTrainingImportOutcome:
        if not batch.complete:
            raise ValueError("incomplete detailed training import cannot be persisted")
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            outcome = self._apply_import_with_cursor(cur, user_id, batch, ingested_at)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return outcome

    def latest_sync_checkpoint(
        self,
        user_id: UUID,
        source_system: DetailedTrainingSourceSystem,
    ) -> DetailedTrainingSyncCheckpoint | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                """
                SELECT checkpoint_id, user_id, source_system, sync_mode,
                       bootstrap_completed, source_event_watermark, parser_version,
                       provider_version, pages_fetched, logical_requests,
                       attempts_made, retries, completed_at
                FROM training_detail_sync_checkpoint
                WHERE user_id=%s AND source_system=%s
                ORDER BY source_event_watermark DESC, completed_at DESC,
                         checkpoint_id DESC
                LIMIT 1
                """,
                (str(user_id), source_system.value),
            )
            row = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if row is None:
            return None
        return DetailedTrainingSyncCheckpoint(
            checkpoint_id=UUID(str(row[0])),
            user_id=UUID(str(row[1])),
            source_system=DetailedTrainingSourceSystem(str(row[2])),
            sync_mode=DetailedTrainingSyncMode(str(row[3])),
            bootstrap_completed=bool(row[4]),
            source_event_watermark=row[5],
            parser_version=str(row[6]),
            provider_version=str(row[7]),
            pages_fetched=int(row[8]),
            logical_requests=int(row[9]),
            attempts_made=int(row[10]),
            retries=int(row[11]),
            completed_at=row[12],
        )

    def apply_sync(
        self,
        user_id: UUID,
        batch: DetailedTrainingImportBatch,
        ingested_at: datetime,
        checkpoint: DetailedTrainingSyncCheckpoint,
    ) -> DetailedTrainingImportOutcome:
        if not batch.complete:
            raise ValueError("incomplete detailed training sync cannot be persisted")
        if checkpoint.user_id != user_id:
            raise ValueError("sync checkpoint owner does not match import owner")
        session_source_mismatch = any(
            item.source_system is not checkpoint.source_system for item in batch.sessions
        )
        deletion_source_mismatch = any(
            item.source_system is not checkpoint.source_system for item in batch.deletions
        )
        if session_source_mismatch or deletion_source_mismatch:
            raise ValueError("sync checkpoint source does not match imported source")
        session_parser_mismatch = any(
            item.parser_version != checkpoint.parser_version for item in batch.sessions
        )
        deletion_parser_mismatch = any(
            item.parser_version != checkpoint.parser_version for item in batch.deletions
        )
        if session_parser_mismatch or deletion_parser_mismatch:
            raise ValueError("sync checkpoint parser does not match imported source")
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            outcome = self._apply_import_with_cursor(cur, user_id, batch, ingested_at)
            cur.execute(
                """
                INSERT INTO training_detail_sync_checkpoint (
                    checkpoint_id, user_id, source_system, sync_mode,
                    bootstrap_completed, source_event_watermark, parser_version,
                    provider_version, pages_fetched, logical_requests,
                    attempts_made, retries, completed_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    str(checkpoint.checkpoint_id),
                    str(user_id),
                    checkpoint.source_system.value,
                    checkpoint.sync_mode.value,
                    checkpoint.bootstrap_completed,
                    checkpoint.source_event_watermark,
                    checkpoint.parser_version,
                    checkpoint.provider_version,
                    checkpoint.pages_fetched,
                    checkpoint.logical_requests,
                    checkpoint.attempts_made,
                    checkpoint.retries,
                    checkpoint.completed_at,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return outcome

    def _apply_import_with_cursor(
        self,
        cur: Any,
        user_id: UUID,
        batch: DetailedTrainingImportBatch,
        ingested_at: datetime,
    ) -> DetailedTrainingImportOutcome:
        accepted = duplicates = applied = duplicate_deletions = blocked = 0
        sessions_created = revisions_appended = 0
        for session in batch.sessions:
            identity = (
                str(user_id),
                session.source_system.value,
                session.source_session_id,
            )
            cur.execute(
                """
                SELECT 1 FROM training_detail_session_tombstone
                WHERE user_id=%s AND source_system=%s AND source_session_id=%s
                """,
                identity,
            )
            if cur.fetchone() is not None:
                blocked += 1
                continue
            revision_identity = (*identity, session.source_revision)
            cur.execute(
                """
                SELECT source_payload_sha256
                FROM training_detail_session_revision
                WHERE user_id=%s AND source_system=%s
                  AND source_session_id=%s AND source_revision=%s
                """,
                revision_identity,
            )
            existing = cur.fetchone()
            if existing is not None:
                if str(existing[0]) != session.source_payload_sha256:
                    raise DetailedTrainingRevisionConflictError(
                        "detailed source revision was reused with conflicting facts"
                    )
                duplicates += 1
                continue
            cur.execute(
                """
                SELECT 1 FROM training_detail_session_revision
                WHERE user_id=%s AND source_system=%s AND source_session_id=%s
                LIMIT 1
                """,
                identity,
            )
            has_prior_revision = cur.fetchone() is not None
            self._insert_revision(cur, user_id, session, ingested_at)
            accepted += 1
            if has_prior_revision:
                revisions_appended += 1
            else:
                sessions_created += 1

        for deletion in batch.deletions:
            cur.execute(
                """
                INSERT INTO training_detail_session_tombstone (
                    tombstone_id, user_id, source_system, source_session_id,
                    deleted_at, parser_version, source_payload_sha256, observed_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (user_id, source_system, source_session_id) DO NOTHING
                """,
                (
                    str(uuid4()),
                    str(user_id),
                    deletion.source_system.value,
                    deletion.source_session_id,
                    deletion.removed_at,
                    deletion.parser_version,
                    deletion.source_payload_sha256,
                    ingested_at,
                ),
            )
            if cur.rowcount == 1:
                applied += 1
            else:
                duplicate_deletions += 1
        return DetailedTrainingImportOutcome(
            accepted_revisions=accepted,
            duplicate_revisions=duplicates,
            applied_deletions=applied,
            duplicate_deletions=duplicate_deletions,
            blocked_by_tombstone=blocked,
            sessions_created=sessions_created,
            revisions_appended=revisions_appended,
        )

    @staticmethod
    def _insert_revision(
        cur: Any,
        user_id: UUID,
        session: DetailedTrainingSession,
        ingested_at: datetime,
    ) -> None:
        revision_id = uuid4()
        cur.execute(
            """
            INSERT INTO training_detail_session_revision (
                revision_id, user_id, source_system, source_session_id,
                source_revision, title, description, routine_id, started_at,
                ended_at, source_created_at, source_updated_at, parser_version,
                source_payload_sha256, ingested_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                str(revision_id),
                str(user_id),
                session.source_system.value,
                session.source_session_id,
                session.source_revision,
                session.title,
                session.description,
                session.routine_id,
                session.started_at,
                session.ended_at,
                session.source_created_at,
                session.source_updated_at,
                session.parser_version,
                session.source_payload_sha256,
                ingested_at,
            ),
        )
        for exercise in session.exercises:
            exercise_id = uuid4()
            cur.execute(
                """
                INSERT INTO training_detail_exercise (
                    exercise_id, user_id, revision_id, occurrence_identity,
                    source_exercise_id, display_name, exercise_order, notes, superset_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    str(exercise_id),
                    str(user_id),
                    str(revision_id),
                    exercise.occurrence_identity,
                    exercise.source_exercise_id,
                    exercise.display_name,
                    exercise.exercise_order,
                    exercise.notes,
                    exercise.superset_id,
                ),
            )
            for training_set in exercise.sets:
                cur.execute(
                    """
                    INSERT INTO training_detail_set (
                        set_id, user_id, exercise_id, set_identity, source_set_id,
                        set_index, set_type, reps, load_value, load_unit,
                        distance_value, distance_unit, duration_seconds, rpe,
                        custom_metric)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        str(uuid4()),
                        str(user_id),
                        str(exercise_id),
                        training_set.set_identity,
                        training_set.source_set_id,
                        training_set.set_index,
                        training_set.set_type.value,
                        training_set.reps,
                        str(training_set.load.value) if training_set.load else None,
                        training_set.load.unit.value if training_set.load else None,
                        str(training_set.distance.value) if training_set.distance else None,
                        training_set.distance.unit.value if training_set.distance else None,
                        (
                            str(training_set.duration_seconds)
                            if training_set.duration_seconds is not None
                            else None
                        ),
                        str(training_set.rpe) if training_set.rpe is not None else None,
                        (
                            str(training_set.custom_metric)
                            if training_set.custom_metric is not None
                            else None
                        ),
                    ),
                )

    def list_latest(self, user_id: UUID, limit: int) -> tuple[DetailedTrainingSessionSummary, ...]:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                """
                WITH ranked AS (
                    SELECT r.*,
                           row_number() OVER (
                               PARTITION BY r.source_system, r.source_session_id
                               ORDER BY COALESCE(r.source_updated_at, r.ingested_at) DESC,
                                        r.source_revision DESC, r.revision_id DESC
                           ) AS newest
                    FROM training_detail_session_revision r
                    WHERE r.user_id=%s
                      AND NOT EXISTS (
                          SELECT 1 FROM training_detail_session_tombstone t
                          WHERE t.user_id=r.user_id
                            AND t.source_system=r.source_system
                            AND t.source_session_id=r.source_session_id
                      )
                )
                SELECT r.revision_id, r.source_system, r.source_session_id,
                       r.source_revision, r.title, r.started_at, r.ended_at,
                       COUNT(DISTINCT e.exercise_id), COUNT(s.set_id)
                FROM ranked r
                LEFT JOIN training_detail_exercise e ON e.revision_id=r.revision_id
                LEFT JOIN training_detail_set s ON s.exercise_id=e.exercise_id
                WHERE r.newest=1
                GROUP BY r.revision_id, r.source_system, r.source_session_id,
                         r.source_revision, r.title, r.started_at, r.ended_at
                ORDER BY r.started_at DESC, r.source_system, r.source_session_id
                LIMIT %s
                """,
                (str(user_id), limit),
            )
            result = tuple(
                DetailedTrainingSessionSummary(
                    revision_id=UUID(str(row[0])),
                    source_system=DetailedTrainingSourceSystem(str(row[1])),
                    source_session_id=str(row[2]),
                    source_revision=str(row[3]),
                    title=str(row[4]),
                    started_at=row[5],
                    ended_at=row[6],
                    exercise_count=int(row[7]),
                    set_count=int(row[8]),
                )
                for row in cur.fetchall()
            )
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_latest_full(
        self,
        user_id: UUID,
        limit: int,
        *,
        source_system: DetailedTrainingSourceSystem | None = None,
        source_exercise_id: str | None = None,
    ) -> DetailedTrainingSessionPage:
        if limit < 1:
            raise ValueError("limit must be positive")
        parameters: list[object] = [str(user_id)]
        source_clause = ""
        if source_system is not None:
            source_clause = "AND r.source_system=%s"
            parameters.append(source_system.value)
        exercise_clause = ""
        if source_exercise_id is not None:
            exercise_clause = """
              AND EXISTS (
                  SELECT 1 FROM training_detail_exercise matching
                  WHERE matching.user_id=r.user_id
                    AND matching.revision_id=r.revision_id
                    AND matching.source_exercise_id=%s
              )
            """
            parameters.append(source_exercise_id)
        parameters.append(limit + 1)
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                f"""
                WITH ranked AS (
                    SELECT r.*,
                           row_number() OVER (
                               PARTITION BY r.source_system, r.source_session_id
                               ORDER BY COALESCE(r.source_updated_at, r.ingested_at) DESC,
                                        r.source_revision DESC, r.revision_id DESC
                           ) AS newest
                    FROM training_detail_session_revision r
                    WHERE r.user_id=%s
                      {source_clause}
                      AND NOT EXISTS (
                          SELECT 1 FROM training_detail_session_tombstone t
                          WHERE t.user_id=r.user_id
                            AND t.source_system=r.source_system
                            AND t.source_session_id=r.source_session_id
                      )
                )
                SELECT r.revision_id, r.user_id, r.source_system,
                       r.source_session_id, r.source_revision, r.title,
                       r.description, r.routine_id, r.started_at, r.ended_at,
                       r.source_created_at, r.source_updated_at, r.parser_version,
                       r.source_payload_sha256, r.ingested_at
                FROM ranked r
                WHERE r.newest=1
                  {exercise_clause}
                ORDER BY r.started_at DESC, r.source_system,
                         r.source_session_id, r.revision_id DESC
                LIMIT %s
                """,
                tuple(parameters),
            )
            all_parents = cur.fetchall()
            has_more = len(all_parents) > limit
            parents = all_parents[:limit]
            sessions = self._hydrate_detailed_sessions(cur, user_id, parents)
            conn.commit()
            return DetailedTrainingSessionPage(sessions=sessions, has_more=has_more)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _hydrate_detailed_sessions(
        self,
        cur: Any,
        user_id: UUID,
        parent_rows: Sequence[Sequence[Any]],
    ) -> tuple[StoredDetailedTrainingSession, ...]:
        if not parent_rows:
            return ()
        revision_ids = [str(row[0]) for row in parent_rows]
        cur.execute(
            """
            SELECT revision_id, exercise_id, occurrence_identity,
                   source_exercise_id, display_name, exercise_order, notes, superset_id
            FROM training_detail_exercise
            WHERE user_id=%s AND revision_id=ANY(%s::uuid[])
            ORDER BY revision_id, exercise_order
            """,
            (str(user_id), revision_ids),
        )
        exercise_rows = cur.fetchall()
        exercise_ids = [str(row[1]) for row in exercise_rows]
        set_rows: list[Sequence[Any]] = []
        if exercise_ids:
            cur.execute(
                """
                SELECT exercise_id, set_identity, source_set_id, set_index,
                       set_type, reps, load_value, load_unit, distance_value,
                       distance_unit, duration_seconds, rpe, custom_metric
                FROM training_detail_set
                WHERE user_id=%s AND exercise_id=ANY(%s::uuid[])
                ORDER BY exercise_id, set_index
                """,
                (str(user_id), exercise_ids),
            )
            set_rows = cur.fetchall()
        sets_by_exercise: dict[str, list[DetailedTrainingSet]] = {
            exercise_id: [] for exercise_id in exercise_ids
        }
        for row in set_rows:
            sets_by_exercise[str(row[0])].append(self._set_from_row(row))
        exercises_by_revision: dict[str, list[ExercisePerformance]] = {
            revision_id: [] for revision_id in revision_ids
        }
        for row in exercise_rows:
            exercises_by_revision[str(row[0])].append(
                ExercisePerformance(
                    occurrence_identity=str(row[2]),
                    source_exercise_id=str(row[3]),
                    display_name=str(row[4]),
                    exercise_order=int(row[5]),
                    notes=row[6],
                    superset_id=row[7],
                    sets=tuple(sets_by_exercise[str(row[1])]),
                )
            )
        return tuple(
            StoredDetailedTrainingSession(
                revision_id=UUID(str(parent[0])),
                user_id=UUID(str(parent[1])),
                session=DetailedTrainingSession(
                    source_system=DetailedTrainingSourceSystem(str(parent[2])),
                    source_session_id=str(parent[3]),
                    source_revision=str(parent[4]),
                    title=str(parent[5]),
                    description=parent[6],
                    routine_id=parent[7],
                    started_at=parent[8],
                    ended_at=parent[9],
                    source_created_at=parent[10],
                    source_updated_at=parent[11],
                    parser_version=str(parent[12]),
                    source_payload_sha256=str(parent[13]),
                    exercises=tuple(exercises_by_revision[str(parent[0])]),
                ),
                ingested_at=parent[14],
            )
            for parent in parent_rows
        )

    def get_by_revision_id(
        self, user_id: UUID, revision_id: UUID
    ) -> StoredDetailedTrainingSession | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                """
                SELECT r.revision_id, r.user_id, r.source_system,
                       r.source_session_id, r.source_revision, r.title,
                       r.description, r.routine_id, r.started_at, r.ended_at,
                       r.source_created_at, r.source_updated_at, r.parser_version,
                       r.source_payload_sha256, r.ingested_at
                FROM training_detail_session_revision r
                WHERE r.user_id=%s AND r.revision_id=%s
                  AND NOT EXISTS (
                      SELECT 1 FROM training_detail_session_tombstone t
                      WHERE t.user_id=r.user_id
                        AND t.source_system=r.source_system
                        AND t.source_session_id=r.source_session_id
                  )
                """,
                (str(user_id), str(revision_id)),
            )
            parent = cur.fetchone()
            if parent is None:
                conn.commit()
                return None
            cur.execute(
                """
                SELECT exercise_id, occurrence_identity, source_exercise_id,
                       display_name, exercise_order, notes, superset_id
                FROM training_detail_exercise
                WHERE user_id=%s AND revision_id=%s
                ORDER BY exercise_order
                """,
                (str(user_id), str(revision_id)),
            )
            exercise_rows = cur.fetchall()
            exercise_ids = [str(row[0]) for row in exercise_rows]
            set_rows: list[Sequence[Any]] = []
            if exercise_ids:
                cur.execute(
                    """
                    SELECT exercise_id, set_identity, source_set_id, set_index,
                           set_type, reps, load_value, load_unit, distance_value,
                           distance_unit, duration_seconds, rpe, custom_metric
                    FROM training_detail_set
                    WHERE user_id=%s AND exercise_id=ANY(%s::uuid[])
                    ORDER BY exercise_id, set_index
                    """,
                    (str(user_id), exercise_ids),
                )
                set_rows = cur.fetchall()
            by_exercise: dict[str, list[DetailedTrainingSet]] = {
                exercise_id: [] for exercise_id in exercise_ids
            }
            for row in set_rows:
                by_exercise[str(row[0])].append(self._set_from_row(row))
            exercises = tuple(
                ExercisePerformance(
                    occurrence_identity=str(row[1]),
                    source_exercise_id=str(row[2]),
                    display_name=str(row[3]),
                    exercise_order=int(row[4]),
                    notes=row[5],
                    superset_id=row[6],
                    sets=tuple(by_exercise[str(row[0])]),
                )
                for row in exercise_rows
            )
            session = DetailedTrainingSession(
                source_system=DetailedTrainingSourceSystem(str(parent[2])),
                source_session_id=str(parent[3]),
                source_revision=str(parent[4]),
                title=str(parent[5]),
                description=parent[6],
                routine_id=parent[7],
                started_at=parent[8],
                ended_at=parent[9],
                source_created_at=parent[10],
                source_updated_at=parent[11],
                parser_version=str(parent[12]),
                source_payload_sha256=str(parent[13]),
                exercises=exercises,
            )
            stored = StoredDetailedTrainingSession(
                revision_id=UUID(str(parent[0])),
                user_id=UUID(str(parent[1])),
                session=session,
                ingested_at=parent[14],
            )
            conn.commit()
            return stored
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _set_from_row(row: Sequence[Any]) -> DetailedTrainingSet:
        load = TrainingLoad(row[6], TrainingLoadUnit(str(row[7]))) if row[6] is not None else None
        distance = (
            TrainingDistance(row[8], TrainingDistanceUnit(str(row[9])))
            if row[8] is not None
            else None
        )
        return DetailedTrainingSet(
            set_identity=str(row[1]),
            source_set_id=row[2],
            set_index=int(row[3]),
            set_type=TrainingSetType(str(row[4])),
            reps=row[5],
            load=load,
            distance=distance,
            duration_seconds=row[10],
            rpe=row[11],
            custom_metric=row[12],
        )


class SqlRunRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def create(self, run: IngestionRun) -> None:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO stacks_ingestion_run (run_id, status, mode, params,"
                " config_fingerprint, started_at, finished_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    str(run.run_id),
                    run.status.value,
                    run.mode,
                    json.dumps(run.params),
                    run.config_fingerprint,
                    run.started_at,
                    run.finished_at,
                ),
            )

    def update(self, run: IngestionRun) -> None:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE stacks_ingestion_run SET status=%s, finished_at=%s WHERE run_id=%s",
                (run.status.value, run.finished_at, str(run.run_id)),
            )


class SqlSnapshotRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def record(self, ref: SnapshotRef, parser_version: str) -> SnapshotRef:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO source_snapshot (
                    snapshot_id, source_system, source_url, http_method,
                    request_params, http_status, content_sha256, byte_size,
                    content_type, fetched_at, parser_version, ingestion_run_id,
                    storage_path)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (source_system, content_sha256) DO NOTHING
                RETURNING snapshot_id, content_sha256, storage_path, source_url,
                          http_method, request_params, http_status, fetched_at,
                          byte_size, ingestion_run_id
                """,
                (
                    str(ref.snapshot_id),
                    "institutional_menu",
                    ref.source_url,
                    ref.method,
                    json.dumps(ref.request_params),
                    ref.http_status,
                    ref.content_sha256,
                    ref.byte_size,
                    "text/html",
                    ref.fetched_at,
                    parser_version,
                    str(ref.run_id) if ref.run_id else None,
                    ref.storage_path,
                ),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    """
                    SELECT snapshot_id, content_sha256, storage_path, source_url,
                           http_method, request_params, http_status, fetched_at,
                           byte_size, ingestion_run_id
                    FROM source_snapshot
                    WHERE source_system='institutional_menu' AND content_sha256=%s
                    """,
                    (ref.content_sha256,),
                )
                row = cur.fetchone()
            assert row is not None
        return SnapshotRef(
            snapshot_id=UUID(str(row[0])),
            content_sha256=str(row[1]),
            storage_path=str(row[2]),
            source_url=str(row[3]),
            method=str(row[4]),
            request_params=dict(row[5] or {}),
            http_status=int(row[6]),
            fetched_at=row[7],
            byte_size=int(row[8]),
            run_id=UUID(str(row[9])) if row[9] else None,
        )

    def find_latest_by_request(
        self, source_url: str, method: str, request_params: dict[str, str]
    ) -> tuple[SnapshotRef, str] | None:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT snapshot_id, content_sha256, http_status, fetched_at,
                       parser_version
                FROM source_snapshot
                WHERE source_url=%s AND http_method=%s AND request_params=%s
                ORDER BY fetched_at DESC LIMIT 1
                """,
                (source_url, method, json.dumps(request_params)),
            )
            row = cur.fetchone()
        if row is None:
            return None
        ref = SnapshotRef(
            snapshot_id=UUID(str(row[0])),
            content_sha256=str(row[1]),
            storage_path="",
            source_url=source_url,
            method=method,
            request_params=dict(request_params),
            http_status=int(row[2]),
            fetched_at=row[3],
        )
        return ref, str(row[4])


class SqlFoodRepository:
    """Trusted global food identity store used by unaccepted diagnostic writes."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def upsert_by_name(
        self, campus_id: int, name_raw: str, name_normalized: str
    ) -> tuple[UUID, bool]:
        food_id = uuid5(NAMESPACE_OID, f"stacks-food:{campus_id}:{name_normalized}")
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO stacks_food (
                    food_id, campus_id, name_raw, name_normalized)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (campus_id, name_normalized)
                DO UPDATE SET name_raw=EXCLUDED.name_raw,
                              last_seen_at=now()
                RETURNING food_id, (xmax = 0) AS inserted
                """,
                (str(food_id), campus_id, name_raw, name_normalized),
            )
            stored_id, inserted = cur.fetchone()
        return UUID(str(stored_id)), bool(inserted)

    def get_by_name(self, campus_id: int, name_normalized: str) -> UUID | None:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT food_id FROM stacks_food
                WHERE campus_id=%s AND name_normalized=%s
                """,
                (campus_id, name_normalized),
            )
            row = cur.fetchone()
        return UUID(str(row[0])) if row is not None else None


def _offering_row(offering: MenuOffering) -> tuple[Any, ...]:
    return (
        str(offering.offering_id),
        offering.service_date,
        offering.meal_period.value,
        offering.campus_id,
        str(offering.food_id),
        offering.occurrence_ordinal,
        offering.category_name,
        offering.category_position,
        offering.item_position,
        offering.source_mid,
        [tag.value for tag in offering.dietary_tags],
        str(offering.profile_id) if offering.profile_id else None,
        str(offering.snapshot_id) if offering.snapshot_id else None,
    )


def _upsert_offering(cur: Any, offering: MenuOffering) -> tuple[UUID, bool]:
    cur.execute(
        """
        INSERT INTO menu_offering (
            offering_id, service_date, meal_period, campus_id, food_id,
            occurrence_ordinal, category_name, category_position,
            item_position, source_mid, dietary_tags, profile_id, snapshot_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (service_date, meal_period, campus_id, food_id,
                     occurrence_ordinal)
        DO UPDATE SET category_name=EXCLUDED.category_name,
                      category_position=EXCLUDED.category_position,
                      item_position=EXCLUDED.item_position,
                      source_mid=EXCLUDED.source_mid,
                      dietary_tags=EXCLUDED.dietary_tags,
                      snapshot_id=EXCLUDED.snapshot_id
        RETURNING offering_id, (xmax = 0) AS inserted
        """,
        _offering_row(offering),
    )
    returned_id, inserted = cur.fetchone()
    return UUID(str(returned_id)), bool(inserted)


class SqlOfferingRepository:
    """Upsert-only offering persistence keyed by the natural uniqueness constraint."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def upsert(self, offering: MenuOffering) -> tuple[UUID, bool]:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            return _upsert_offering(cur, offering)

    def link_profile(self, offering_id: UUID, profile_id: UUID) -> None:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE menu_offering SET profile_id=%s WHERE offering_id=%s",
                (str(profile_id), str(offering_id)),
            )

    def count_for_page(self, service_date: date, meal_period: MealPeriod) -> int:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM menu_offering WHERE service_date=%s AND meal_period=%s",
                (service_date, meal_period.value),
            )
            count = cur.fetchone()
        return int(count[0]) if count else 0


def _profile_json(profile: NutritionProfile) -> dict[str, Any]:
    return {
        key.value: {
            "value": str(nv.value) if nv.value is not None else None,
            "unit": nv.unit,
            "dv_percent": str(nv.dv_percent) if nv.dv_percent is not None else None,
        }
        for key, nv in profile.nutrients.items()
    }


def _insert_profile_version(cur: Any, profile: NutritionProfile) -> UUID:
    profile_id = UUID(
        uuid5(
            NAMESPACE_OID,
            f"{profile.food_id}:{profile.provenance.content_sha256}",
        ).hex
    )
    cur.execute(
        """
        INSERT INTO nutrition_profile (
            profile_id, food_id, snapshot_id, parser_version,
            serving_basis_raw, serving_basis_kind, nutrients,
            unavailable_fields, extra_fields, ingredients_raw,
            ingredient_components, allergens, confidence, captured_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (food_id, snapshot_id) DO NOTHING
        RETURNING profile_id
        """,
        (
            str(profile_id),
            str(profile.food_id),
            str(profile.provenance.snapshot_id),
            profile.provenance.parser_version,
            profile.serving_basis_raw,
            profile.serving_basis_kind.value,
            json.dumps(_profile_json(profile)),
            [key.value for key in profile.unavailable_fields],
            json.dumps(profile.extra_fields),
            profile.ingredients_raw,
            json.dumps(
                [
                    {"component_name": component.component_name, "text": component.text}
                    for component in profile.ingredient_components or []
                ]
            ),
            list(profile.allergens),
            profile.confidence.value,
            profile.provenance.fetched_at,
        ),
    )
    inserted = cur.fetchone()
    if inserted is not None:
        return UUID(str(inserted[0]))
    cur.execute(
        "SELECT profile_id FROM nutrition_profile WHERE food_id=%s AND snapshot_id=%s",
        (str(profile.food_id), str(profile.provenance.snapshot_id)),
    )
    existing = cur.fetchone()
    assert existing is not None
    return UUID(str(existing[0]))


class SqlProfileRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def insert_version(self, profile: NutritionProfile, snapshot: SnapshotRef) -> UUID:
        canonical_profile = replace(
            profile,
            provenance=replace(
                profile.provenance,
                snapshot_id=snapshot.snapshot_id,
                content_sha256=snapshot.content_sha256,
                source_url=snapshot.source_url,
                fetched_at=snapshot.fetched_at,
            ),
        )
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            return _insert_profile_version(cur, canonical_profile)

    def latest_for_food(self, food_id: UUID) -> NutritionProfile | None:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.profile_id, p.parser_version, p.serving_basis_raw,
                       p.serving_basis_kind, p.nutrients, p.unavailable_fields,
                       p.extra_fields, p.ingredients_raw, p.ingredient_components,
                       p.allergens, p.confidence, p.captured_at,
                       s.content_sha256, s.source_url
                FROM nutrition_profile p
                JOIN source_snapshot s ON s.snapshot_id = p.snapshot_id
                WHERE p.food_id=%s AND p.superseded_by IS NULL
                ORDER BY p.captured_at DESC, p.profile_id DESC
                LIMIT 1
                """,
                (str(food_id),),
            )
            row = cur.fetchone()
        if row is None:
            return None
        (
            profile_id,
            parser_version,
            basis_raw,
            basis_kind,
            nutrients,
            unavailable,
            extra_fields,
            ingredients_raw,
            components,
            allergens,
            confidence,
            captured_at,
            content_sha256,
            source_url,
        ) = row

        nutrient_map: dict[NutrientKey, NutrientValue] = {}
        for key_text, value in dict(nutrients).items():
            value_map = dict(value)
            raw_value = value_map.get("value")
            dv_percent = value_map.get("dv_percent")
            nutrient_map[NutrientKey(str(key_text))] = NutrientValue(
                value=Decimal(str(raw_value)) if raw_value is not None else None,
                unit=str(value_map.get("unit", "")),
                dv_percent=(Decimal(str(dv_percent)) if dv_percent is not None else None),
            )
        provenance = Provenance(
            snapshot_id=UUID(str(profile_id)),  # placeholder id; full snapshot row pinned upstream
            content_sha256=str(content_sha256),
            source_url=str(source_url),
            parser_version=str(parser_version),
            fetched_at=captured_at if isinstance(captured_at, datetime) else datetime.now(),
        )
        return NutritionProfile(
            food_id=food_id,
            serving_basis_raw=str(basis_raw),
            serving_basis_kind=ServingBasisKind(str(basis_kind)),
            nutrients=nutrient_map,
            unavailable_fields=tuple(NutrientKey(str(k)) for k in (unavailable or [])),
            extra_fields=dict(extra_fields or {}),
            ingredients_raw=str(ingredients_raw),
            ingredient_components=tuple(
                ComponentStatement(
                    component_name=str(entry.get("component_name", "")),
                    text=str(entry.get("text", "")),
                )
                for entry in (components or [])
            )
            or None,
            allergens=tuple(str(a) for a in (allergens or [])),
            confidence=Confidence(str(confidence)),
            provenance=provenance,
        )


class SqlMenuPageCoverageRepository(MenuPageCoverageRepository):
    """Trusted global read model for bounded automatic refresh selection."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def accepted_pages(
        self,
        *,
        campus_id: int,
        start_date: date,
        end_date: date,
    ) -> tuple[AcceptedMenuPageObservation, ...]:
        if end_date < start_date:
            raise ValueError("end_date must not precede start_date")
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT service_date, meal_period, campus_id, MAX(accepted_at)
                FROM menu_page_version
                WHERE campus_id=%s
                  AND service_date BETWEEN %s AND %s
                  AND validation_state IN (
                      'validated_nonempty','validated_empty')
                GROUP BY service_date, meal_period, campus_id
                ORDER BY service_date, meal_period, campus_id
                """,
                (campus_id, start_date, end_date),
            )
            return tuple(
                AcceptedMenuPageObservation(
                    key=AcceptedMenuPageKey(
                        service_date=row[0],
                        meal_period=MealPeriod(str(row[1])),
                        campus_id=int(row[2]),
                    ),
                    accepted_at=row[3],
                )
                for row in cur.fetchall()
            )


class SqlStacksRefreshLease:
    """Session-level PostgreSQL advisory lease for one global refresh runner."""

    _LOCK_NAME = "nutrition-agent:stacks-refresh:institutional"

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: Any | None = None
        self._acquired = False

    @property
    def acquired(self) -> bool:
        return self._acquired

    def __enter__(self) -> SqlStacksRefreshLease:
        if self._conn is not None:
            raise RuntimeError("refresh lease cannot be entered twice")
        conn = _connect(self._dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
                    (self._LOCK_NAME,),
                )
                self._acquired = bool(cur.fetchone()[0])
        except BaseException:
            conn.close()
            raise
        if self._acquired:
            self._conn = conn
        else:
            conn.close()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        conn = self._conn
        self._conn = None
        try:
            if conn is not None and self._acquired:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                        (self._LOCK_NAME,),
                    )
                conn.commit()
        finally:
            self._acquired = False
            if conn is not None:
                conn.close()


class SqlMenuPageVersionRepository(MenuPageVersionRepository):
    """Trusted global persistence for accepted pages and label evidence."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def has_accepted_observation(
        self,
        ingestion_run_id: UUID,
        service_date: date,
        meal_period: MealPeriod,
        campus_id: int,
    ) -> bool:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM menu_page_version
                WHERE ingestion_run_id=%s AND service_date=%s
                  AND meal_period=%s AND campus_id=%s
                """,
                (
                    str(ingestion_run_id),
                    service_date,
                    meal_period.value,
                    campus_id,
                ),
            )
            return cur.fetchone() is not None

    def find_reusable_nutrition_authorities(
        self,
        keys: Sequence[NutritionAuthorityKey],
        *,
        parser_version: str,
        fetched_not_before: datetime,
    ) -> tuple[ReusableNutritionAuthority, ...]:
        if fetched_not_before.tzinfo is None or fetched_not_before.utcoffset() is None:
            raise ValueError("fetched_not_before must be timezone-aware")
        requested = set(keys)
        if not requested:
            return ()
        source_mids = sorted({key.source_mid for key in requested})
        campus_ids = {key.campus_id for key in requested}
        if len(campus_ids) != 1:
            raise ValueError("one reusable-authority lookup must use one campus")
        campus_id = next(iter(campus_ids))
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (
                           v.campus_id, m.name_normalized, m.source_mid)
                       m.name_normalized, m.source_mid, m.food_id,
                       m.profile_id, m.nutrition_source_state,
                       m.nutrition_snapshot_id, v.parser_version,
                       p.food_id, p.snapshot_id, p.parser_version,
                       p.serving_basis_raw,
                       p.serving_basis_kind, p.nutrients, p.unavailable_fields,
                       p.extra_fields, p.ingredients_raw,
                       p.ingredient_components, p.allergens, p.confidence,
                       s.snapshot_id, s.parser_version,
                       s.content_sha256, s.source_url, s.fetched_at,
                       s.storage_path, s.http_method, s.request_params,
                       s.http_status, s.byte_size, s.ingestion_run_id
                FROM menu_page_offering m
                JOIN menu_page_version v ON v.page_version_id=m.page_version_id
                LEFT JOIN nutrition_profile p ON p.profile_id=m.profile_id
                LEFT JOIN source_snapshot s ON s.snapshot_id=m.nutrition_snapshot_id
                WHERE v.campus_id=%s
                  AND v.validation_state IN (
                      'validated_nonempty','validated_empty')
                  AND m.source_mid=ANY(%s)
                ORDER BY v.campus_id, m.name_normalized, m.source_mid,
                         v.accepted_at DESC, v.page_version_id DESC,
                         m.offering_id DESC
                """,
                (campus_id, source_mids),
            )
            rows = cur.fetchall()

        found: dict[NutritionAuthorityKey, ReusableNutritionAuthority] = {}
        for row in rows:
            (
                name_normalized,
                source_mid,
                food_id_raw,
                profile_id,
                nutrition_source_state,
                nutrition_snapshot_id,
                page_parser_version,
                profile_food_id,
                profile_snapshot_id,
                profile_parser_version,
                basis_raw,
                basis_kind,
                nutrients,
                unavailable,
                extra_fields,
                ingredients_raw,
                components,
                allergens,
                confidence,
                snapshot_id,
                snapshot_parser_version,
                content_sha256,
                source_url,
                fetched_at,
                storage_path,
                http_method,
                request_params,
                http_status,
                byte_size,
                ingestion_run_id,
            ) = row
            key = NutritionAuthorityKey(
                campus_id=campus_id,
                name_normalized=str(name_normalized),
                source_mid=str(source_mid),
            )
            if key not in requested or key in found:
                continue
            if (
                nutrition_source_state != NutritionSourceState.PROFILE_AVAILABLE.value
                or profile_id is None
                or nutrition_snapshot_id is None
                or profile_food_id != food_id_raw
                or profile_snapshot_id != nutrition_snapshot_id
                or snapshot_id != nutrition_snapshot_id
                or page_parser_version != parser_version
                or profile_parser_version != parser_version
                or snapshot_parser_version != parser_version
                or fetched_at < fetched_not_before
                or http_method != "GET"
                or http_status != 200
            ):
                continue
            food_id = UUID(str(food_id_raw))
            profile = _nutrition_profile_from_exact_row(
                food_id,
                (
                    profile_snapshot_id,
                    profile_parser_version,
                    basis_raw,
                    basis_kind,
                    nutrients,
                    unavailable,
                    extra_fields,
                    ingredients_raw,
                    components,
                    allergens,
                    confidence,
                    content_sha256,
                    source_url,
                    fetched_at,
                ),
            )
            snapshot = SnapshotRef(
                snapshot_id=UUID(str(snapshot_id)),
                content_sha256=str(content_sha256),
                storage_path=str(storage_path),
                source_url=str(source_url),
                method=str(http_method),
                request_params=dict(request_params or {}),
                http_status=int(http_status),
                fetched_at=fetched_at,
                byte_size=int(byte_size),
                run_id=(UUID(str(ingestion_run_id)) if ingestion_run_id is not None else None),
            )
            try:
                found[key] = ReusableNutritionAuthority(
                    key=key,
                    profile=profile,
                    nutrition_snapshot=snapshot,
                )
            except ValueError:
                continue
        return tuple(found[key] for key in sorted(found))

    def find_reusable_page_label_authorities(
        self,
        keys: Sequence[PageLabelAuthorityKey],
        *,
        parser_version: str,
        fetched_not_before: datetime,
    ) -> tuple[ReusablePageLabelAuthority, ...]:
        if fetched_not_before.tzinfo is None or fetched_not_before.utcoffset() is None:
            raise ValueError("fetched_not_before must be timezone-aware")
        requested = set(keys)
        if not requested:
            return ()
        page_keys = {(key.service_date, key.meal_period, key.campus_id) for key in requested}
        if len(page_keys) != 1:
            raise ValueError("one page-label lookup must use one logical page")
        service_date, meal_period, campus_id = next(iter(page_keys))
        source_mids = sorted({key.source_mid for key in requested})
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (
                           o.name_normalized, o.source_mid, o.occurrence_ordinal)
                       o.service_date, o.meal_period, o.campus_id,
                       o.name_normalized, o.source_mid, o.occurrence_ordinal,
                       o.nutrition_source_state, o.food_id, o.profile_id,
                       o.nutrition_snapshot_id, o.parser_version, o.recorded_at,
                       p.food_id, p.snapshot_id, p.parser_version,
                       p.serving_basis_raw, p.serving_basis_kind, p.nutrients,
                       p.unavailable_fields, p.extra_fields, p.ingredients_raw,
                       p.ingredient_components, p.allergens, p.confidence,
                       s.snapshot_id, s.parser_version, s.content_sha256,
                       s.source_url, s.fetched_at, s.storage_path,
                       s.http_method, s.request_params, s.http_status,
                       s.byte_size, s.ingestion_run_id,
                       ms.http_method, ms.http_status
                FROM menu_label_observation o
                LEFT JOIN nutrition_profile p ON p.profile_id=o.profile_id
                JOIN source_snapshot s ON s.snapshot_id=o.nutrition_snapshot_id
                JOIN source_snapshot ms ON ms.snapshot_id=o.menu_snapshot_id
                WHERE o.service_date=%s AND o.meal_period=%s
                  AND o.campus_id=%s AND o.source_mid=ANY(%s)
                ORDER BY o.name_normalized, o.source_mid, o.occurrence_ordinal,
                         o.recorded_at DESC, o.observation_id DESC
                """,
                (service_date, meal_period.value, campus_id, source_mids),
            )
            rows = cur.fetchall()

        found: dict[PageLabelAuthorityKey, ReusablePageLabelAuthority] = {}
        for row in rows:
            key = PageLabelAuthorityKey(
                service_date=row[0],
                meal_period=MealPeriod(str(row[1])),
                campus_id=int(row[2]),
                name_normalized=str(row[3]),
                source_mid=str(row[4]),
                occurrence_ordinal=int(row[5]),
            )
            if key not in requested or key in found:
                continue
            source_state = NutritionSourceState(str(row[6]))
            food_id_raw = row[7]
            profile_id = row[8]
            nutrition_snapshot_id = row[9]
            observation_parser_version = row[10]
            profile_food_id = row[12]
            profile_snapshot_id = row[13]
            profile_parser_version = row[14]
            snapshot_id = row[24]
            snapshot_parser_version = row[25]
            fetched_at = row[28]
            http_method = row[30]
            http_status = row[32]
            menu_http_method = row[35]
            menu_http_status = row[36]
            if (
                observation_parser_version != parser_version
                or nutrition_snapshot_id != snapshot_id
                or snapshot_parser_version != parser_version
                or fetched_at < fetched_not_before
                or http_method != "GET"
                or http_status != 200
                or menu_http_method != "POST"
                or menu_http_status != 200
            ):
                continue
            food_id = UUID(str(food_id_raw))
            profile: NutritionProfile | None = None
            if source_state is NutritionSourceState.PROFILE_AVAILABLE:
                if (
                    profile_id is None
                    or profile_food_id != food_id_raw
                    or profile_snapshot_id != nutrition_snapshot_id
                    or profile_parser_version != parser_version
                ):
                    continue
                profile = _nutrition_profile_from_exact_row(
                    food_id,
                    (
                        profile_snapshot_id,
                        profile_parser_version,
                        row[15],
                        row[16],
                        row[17],
                        row[18],
                        row[19],
                        row[20],
                        row[21],
                        row[22],
                        row[23],
                        row[26],
                        row[27],
                        fetched_at,
                    ),
                )
            elif profile_id is not None or profile_food_id is not None:
                continue
            snapshot = SnapshotRef(
                snapshot_id=UUID(str(snapshot_id)),
                content_sha256=str(row[26]),
                storage_path=str(row[29]),
                source_url=str(row[27]),
                method=str(http_method),
                request_params=dict(row[31] or {}),
                http_status=int(http_status),
                fetched_at=fetched_at,
                byte_size=int(row[33]),
                run_id=UUID(str(row[34])) if row[34] is not None else None,
            )
            try:
                found[key] = ReusablePageLabelAuthority(
                    key=key,
                    nutrition_source_state=source_state,
                    profile=profile,
                    nutrition_snapshot=snapshot,
                )
            except ValueError:
                continue
        return tuple(found[key] for key in sorted(found))

    def persist_label_observation(
        self, observation: ValidatedMenuLabelObservation
    ) -> ReusablePageLabelAuthority:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO stacks_food (
                    food_id, campus_id, name_raw, name_normalized)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (campus_id, name_normalized)
                DO UPDATE SET name_raw=EXCLUDED.name_raw,
                              last_seen_at=now()
                RETURNING food_id
                """,
                (
                    str(observation.food_id),
                    observation.key.campus_id,
                    observation.food_name_raw,
                    observation.key.name_normalized,
                ),
            )
            food_id = UUID(str(cur.fetchone()[0]))
            profile: NutritionProfile | None = None
            profile_id: UUID | None = None
            if observation.profile is not None:
                profile = replace(observation.profile, food_id=food_id)
                profile_id = _insert_profile_version(cur, profile)
            cur.execute(
                """
                INSERT INTO menu_label_observation (
                    observation_id, service_date, meal_period, campus_id,
                    menu_snapshot_id, name_normalized, occurrence_ordinal,
                    source_mid, food_id, nutrition_source_state, profile_id,
                    nutrition_snapshot_id, parser_version, ingestion_run_id,
                    recorded_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT ON CONSTRAINT uq_menu_label_observation_evidence
                DO NOTHING
                RETURNING observation_id
                """,
                (
                    str(observation.observation_id),
                    observation.key.service_date,
                    observation.key.meal_period.value,
                    observation.key.campus_id,
                    str(observation.menu_snapshot.snapshot_id),
                    observation.key.name_normalized,
                    observation.key.occurrence_ordinal,
                    observation.key.source_mid,
                    str(food_id),
                    observation.nutrition_source_state.value,
                    str(profile_id) if profile_id is not None else None,
                    str(observation.nutrition_snapshot.snapshot_id),
                    observation.parser_version,
                    str(observation.ingestion_run_id),
                    observation.recorded_at,
                ),
            )
            if cur.fetchone() is None:
                cur.execute(
                    """
                    SELECT food_id, nutrition_source_state, profile_id
                    FROM menu_label_observation
                    WHERE service_date=%s AND meal_period=%s AND campus_id=%s
                      AND name_normalized=%s AND occurrence_ordinal=%s
                      AND source_mid=%s AND parser_version=%s
                      AND nutrition_snapshot_id=%s
                    """,
                    (
                        observation.key.service_date,
                        observation.key.meal_period.value,
                        observation.key.campus_id,
                        observation.key.name_normalized,
                        observation.key.occurrence_ordinal,
                        observation.key.source_mid,
                        observation.parser_version,
                        str(observation.nutrition_snapshot.snapshot_id),
                    ),
                )
                existing = cur.fetchone()
                if existing is None or existing != (
                    food_id,
                    observation.nutrition_source_state.value,
                    profile_id,
                ):
                    raise ValueError("label-observation idempotency conflict")
        return ReusablePageLabelAuthority(
            key=observation.key,
            nutrition_source_state=observation.nutrition_source_state,
            profile=profile,
            nutrition_snapshot=observation.nutrition_snapshot,
        )

    def persist_validated_page(self, page: ValidatedMenuPage) -> PersistMenuPageOutcome:
        inserted = updated = profiles_persisted = 0
        accepted_pins: list[MenuPageOfferingPin] = []
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            for prepared in page.offerings:
                candidate = prepared.offering
                cur.execute(
                    """
                    INSERT INTO stacks_food (
                        food_id, campus_id, name_raw, name_normalized)
                    VALUES (%s,%s,%s,%s)
                    ON CONFLICT (campus_id, name_normalized)
                    DO UPDATE SET name_raw=EXCLUDED.name_raw,
                                  last_seen_at=now()
                    RETURNING food_id
                    """,
                    (
                        str(candidate.food_id),
                        candidate.campus_id,
                        prepared.food_name_raw,
                        prepared.food_name_normalized,
                    ),
                )
                food_id = UUID(str(cur.fetchone()[0]))

                profile_id: UUID | None = None
                if prepared.profile is not None and prepared.profile_snapshot is not None:
                    snapshot = prepared.profile_snapshot
                    canonical_profile = replace(
                        prepared.profile,
                        food_id=food_id,
                        provenance=replace(
                            prepared.profile.provenance,
                            snapshot_id=snapshot.snapshot_id,
                            content_sha256=snapshot.content_sha256,
                            source_url=snapshot.source_url,
                            fetched_at=snapshot.fetched_at,
                        ),
                    )
                    profile_id = _insert_profile_version(
                        cur,
                        canonical_profile,
                    )
                    profiles_persisted += 1

                stored_id, was_inserted = _upsert_offering(
                    cur,
                    replace(candidate, food_id=food_id),
                )
                if profile_id is not None:
                    cur.execute(
                        "UPDATE menu_offering SET profile_id=%s WHERE offering_id=%s",
                        (str(profile_id), str(stored_id)),
                    )
                source_state = prepared.nutrition_source_state
                nutrition_snapshot = prepared.nutrition_snapshot
                if source_state is None or nutrition_snapshot is None:
                    raise ValueError("accepted offering requires classified nutrition provenance")
                accepted_pins.append(
                    MenuPageOfferingPin(
                        page_version_id=page.page_version_id,
                        offering_id=stored_id,
                        food_id=food_id,
                        profile_id=profile_id,
                        name_normalized=prepared.food_name_normalized,
                        occurrence_ordinal=candidate.occurrence_ordinal,
                        category_name=candidate.category_name,
                        category_position=candidate.category_position,
                        item_position=candidate.item_position,
                        source_mid=candidate.source_mid,
                        dietary_tags=candidate.dietary_tags,
                        nutrition_source_state=source_state,
                        nutrition_snapshot_id=nutrition_snapshot.snapshot_id,
                    )
                )
                if was_inserted:
                    inserted += 1
                else:
                    updated += 1

            for pin in accepted_pins:
                cur.execute(
                    """
                    INSERT INTO menu_page_offering (
                        page_version_id, offering_id, food_id, profile_id,
                        name_normalized, occurrence_ordinal, category_name,
                        category_position, item_position, source_mid, dietary_tags,
                        nutrition_source_state, nutrition_snapshot_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        str(pin.page_version_id),
                        str(pin.offering_id),
                        str(pin.food_id),
                        str(pin.profile_id) if pin.profile_id is not None else None,
                        pin.name_normalized,
                        pin.occurrence_ordinal,
                        pin.category_name,
                        pin.category_position,
                        pin.item_position,
                        pin.source_mid,
                        [tag.value for tag in pin.dietary_tags],
                        pin.nutrition_source_state.value,
                        str(pin.nutrition_snapshot_id),
                    ),
                )

            cur.execute(
                "SELECT COUNT(*) FROM menu_page_offering WHERE page_version_id=%s",
                (str(page.page_version_id),),
            )
            membership_count = int(cur.fetchone()[0])
            if membership_count != page.offering_count:
                raise ValueError("accepted page membership count mismatch")

            # Acceptance marker is deliberately last. Its membership FK is
            # deferred, and the marker plus all normalized writes become
            # visible together only when this transaction commits.
            cur.execute(
                """
                INSERT INTO menu_page_version (
                    page_version_id, service_date, meal_period, campus_id,
                    snapshot_id, ingestion_run_id, validation_state,
                    offering_count, accepted_at, parser_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    str(page.page_version_id),
                    page.service_date,
                    page.meal_period.value,
                    page.campus_id,
                    str(page.snapshot_id),
                    str(page.ingestion_run_id),
                    page.validation_state.value,
                    page.offering_count,
                    page.accepted_at,
                    page.parser_version,
                ),
            )

        return PersistMenuPageOutcome(
            inserted_offerings=inserted,
            updated_offerings=updated,
            profiles_persisted=profiles_persisted,
        )


def _nutrition_profile_from_exact_row(food_id: UUID, row: Sequence[Any]) -> NutritionProfile:
    (
        snapshot_id,
        parser_version,
        basis_raw,
        basis_kind,
        nutrients,
        unavailable,
        extra_fields,
        ingredients_raw,
        components,
        allergens,
        confidence,
        content_sha256,
        source_url,
        fetched_at,
    ) = row
    nutrient_map: dict[NutrientKey, NutrientValue] = {}
    for key_text, value in dict(nutrients).items():
        value_map = dict(value)
        raw_value = value_map.get("value")
        dv_percent = value_map.get("dv_percent")
        nutrient_map[NutrientKey(str(key_text))] = NutrientValue(
            value=Decimal(str(raw_value)) if raw_value is not None else None,
            unit=str(value_map.get("unit", "")),
            dv_percent=Decimal(str(dv_percent)) if dv_percent is not None else None,
        )
    return NutritionProfile(
        food_id=food_id,
        serving_basis_raw=str(basis_raw),
        serving_basis_kind=ServingBasisKind(str(basis_kind)),
        nutrients=nutrient_map,
        unavailable_fields=tuple(NutrientKey(str(key)) for key in (unavailable or [])),
        extra_fields=dict(extra_fields or {}),
        ingredients_raw=str(ingredients_raw),
        ingredient_components=tuple(
            ComponentStatement(
                component_name=str(entry.get("component_name", "")),
                text=str(entry.get("text", "")),
            )
            for entry in (components or [])
        )
        or None,
        allergens=tuple(str(allergen) for allergen in (allergens or [])),
        confidence=Confidence(str(confidence)),
        provenance=Provenance(
            snapshot_id=UUID(str(snapshot_id)),
            content_sha256=str(content_sha256),
            source_url=str(source_url),
            parser_version=str(parser_version),
            fetched_at=fetched_at,
        ),
    )


class SqlMenuDayReadRepository(MenuDayReadRepository):
    """Trusted global reader over immutable accepted menu-page versions."""

    def __init__(
        self,
        dsn: str,
        *,
        campus_id: int = STACKS_CAMPUS_ID,
        required_periods: Sequence[MealPeriod] = tuple(MealPeriod),
    ) -> None:
        self._dsn = dsn
        self._campus_id = campus_id
        requested = frozenset(required_periods)
        self._required_periods = tuple(period for period in MealPeriod if period in requested)
        if not self._required_periods:
            raise ValueError("at least one required meal period is required")

    def get_for_date(self, service_date: date) -> ResolvedMenuDay | None:
        selected: list[tuple[MealPeriod, Sequence[Any]]] = []
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            for period in self._required_periods:
                cur.execute(
                    """
                    SELECT p.page_version_id, p.validation_state,
                           p.offering_count, s.snapshot_id,
                           s.content_sha256, p.accepted_at
                    FROM menu_page_version p
                    JOIN source_snapshot s ON s.snapshot_id=p.snapshot_id
                    WHERE p.service_date=%s AND p.meal_period=%s
                      AND p.campus_id=%s
                      AND p.validation_state IN (
                          'validated_nonempty','validated_empty')
                    ORDER BY p.accepted_at DESC, p.page_version_id DESC
                    LIMIT 1
                    """,
                    (service_date, period.value, self._campus_id),
                )
                page_row = cur.fetchone()
                if page_row is None:
                    return None
                selected.append((period, page_row))

            periods: dict[MealPeriod, PeriodMenu] = {}
            profile_ids: dict[str, UUID] = {}
            day_parts: list[dict[str, object]] = []
            fetched_times: list[datetime] = []
            for period, page_row in selected:
                page_version_id = UUID(str(page_row[0]))
                validation_state = str(page_row[1])
                offering_count = int(page_row[2])
                menu_snapshot_sha256 = str(page_row[4])
                accepted_at = page_row[5]
                fetched_times.append(accepted_at)
                if validation_state == "validated_empty":
                    if offering_count != 0:
                        raise ValueError("validated empty page has a nonzero offering count")
                    periods[period] = PeriodMenu(offerings=(), explicitly_empty=True)
                    day_parts.append(
                        {
                            "meal_period": period.value,
                            "snapshot_sha256": menu_snapshot_sha256,
                            "memberships": [],
                        }
                    )
                    continue

                cur.execute(
                    """
                    SELECT m.offering_id, m.food_id, m.profile_id,
                           m.name_normalized, m.occurrence_ordinal,
                           m.category_name, m.category_position,
                           m.item_position, m.source_mid, m.dietary_tags,
                           m.nutrition_source_state, m.nutrition_snapshot_id,
                           ns.content_sha256,
                           p.food_id, p.snapshot_id, p.parser_version,
                           p.serving_basis_raw, p.serving_basis_kind,
                           p.nutrients, p.unavailable_fields, p.extra_fields,
                           p.ingredients_raw, p.ingredient_components,
                           p.allergens, p.confidence,
                           ps.content_sha256, ps.source_url, ps.fetched_at
                    FROM menu_page_offering m
                    JOIN source_snapshot ns ON ns.snapshot_id=m.nutrition_snapshot_id
                    LEFT JOIN nutrition_profile p ON p.profile_id=m.profile_id
                    LEFT JOIN source_snapshot ps ON ps.snapshot_id=p.snapshot_id
                    WHERE m.page_version_id=%s
                    ORDER BY m.category_position ASC, m.item_position ASC,
                             m.occurrence_ordinal ASC, m.offering_id ASC
                    """,
                    (str(page_version_id),),
                )
                membership_rows = cur.fetchall()
                if len(membership_rows) != offering_count:
                    raise ValueError("accepted page membership count mismatch")

                views: list[OfferingView] = []
                membership_parts: list[dict[str, object]] = []
                for row in membership_rows:
                    offering_id = UUID(str(row[0]))
                    food_id = UUID(str(row[1]))
                    profile_id = UUID(str(row[2])) if row[2] is not None else None
                    source_state = NutritionSourceState(str(row[10]))
                    nutrition_snapshot_id = UUID(str(row[11]))
                    nutrition_snapshot_sha256 = str(row[12])
                    profile: NutritionProfile | None = None
                    profile_sha256: str | None = None
                    if source_state is NutritionSourceState.PROFILE_AVAILABLE:
                        if profile_id is None or row[13] is None or row[14] is None:
                            raise ValueError("profile_available membership has no exact profile")
                        profile_food_id = UUID(str(row[13]))
                        profile_snapshot_id = UUID(str(row[14]))
                        if profile_food_id != food_id:
                            raise ValueError("accepted page profile does not belong to pinned food")
                        if profile_snapshot_id != nutrition_snapshot_id:
                            raise ValueError(
                                "accepted profile snapshot does not match nutrition pin"
                            )
                        profile = _nutrition_profile_from_exact_row(
                            food_id,
                            (
                                row[14],
                                row[15],
                                row[16],
                                row[17],
                                row[18],
                                row[19],
                                row[20],
                                row[21],
                                row[22],
                                row[23],
                                row[24],
                                row[25],
                                row[26],
                                row[27],
                            ),
                        )
                        profile_sha256 = profile.provenance.content_sha256
                        profile_ids[str(offering_id)] = profile_id
                    elif profile_id is not None or row[13] is not None:
                        raise ValueError("non-profile membership resolved a profile")
                    views.append(
                        OfferingView(
                            offering_id=offering_id,
                            food_id=food_id,
                            name_normalized=str(row[3]),
                            source_mid=str(row[8]),
                            occurrence_ordinal=int(row[4]),
                            category_name=str(row[5]),
                            dietary_tags=tuple(DietaryTag(str(tag)) for tag in (row[9] or [])),
                            profile=profile,
                            profile_sha256=profile_sha256,
                            snapshot_sha256=menu_snapshot_sha256,
                            nutrition_source_state=source_state,
                            nutrition_snapshot_sha256=nutrition_snapshot_sha256,
                        )
                    )
                    membership_parts.append(
                        {
                            "category_position": int(row[6]),
                            "dietary_tags": sorted(str(tag) for tag in (row[9] or [])),
                            "food_id": str(food_id),
                            "item_position": int(row[7]),
                            "name_normalized": str(row[3]),
                            "nutrition_snapshot_sha256": nutrition_snapshot_sha256,
                            "nutrition_source_state": source_state.value,
                            "occurrence_ordinal": int(row[4]),
                            "profile_sha256": profile_sha256,
                            "source_mid": str(row[8]),
                        }
                    )
                periods[period] = PeriodMenu(offerings=tuple(views))
                day_parts.append(
                    {
                        "meal_period": period.value,
                        "snapshot_sha256": menu_snapshot_sha256,
                        "memberships": membership_parts,
                    }
                )

        canonical = json.dumps(
            {
                "schema": "menu-day-snapshot.v2",
                "service_date": service_date.isoformat(),
                "periods": day_parts,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return ResolvedMenuDay(
            menu=MenuDayView(
                service_date=service_date,
                periods=periods,
                # A mixed day is only as fresh as its oldest required page.
                fetched_at=min(fetched_times),
                snapshot_sha256=hashlib.sha256(canonical).hexdigest(),
                campus_id=self._campus_id,
            ),
            offering_profile_ids=profile_ids,
        )


class SqlQuarantineRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def add(self, record: QuarantineRecord) -> None:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO quarantine_record (
                    record_id, run_id, code, severity, subject_type,
                    natural_key, detail, parser_version, snapshot_ref_sha256, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    str(record.record_id),
                    str(record.run_id),
                    record.code.value,
                    record.severity.value,
                    record.subject_type.value,
                    json.dumps(record.natural_key),
                    record.detail,
                    record.parser_version,
                    record.snapshot_ref_sha256,
                    record.created_at or datetime.now(),
                ),
            )

    def list_by_run(self, run_id: UUID) -> list[QuarantineRecord]:
        with _connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT record_id, code, severity, subject_type, natural_key,
                       detail, parser_version, snapshot_ref_sha256, created_at
                FROM quarantine_record WHERE run_id=%s
                ORDER BY created_at ASC, record_id ASC
                """,
                (str(run_id),),
            )
            rows = cur.fetchall()
        return [
            QuarantineRecord(
                record_id=UUID(str(row[0])),
                run_id=run_id,
                code=ErrorCode(str(row[1])),
                severity=Severity(str(row[2])),
                subject_type=SubjectType(str(row[3])),
                natural_key=dict(row[4] or {}),
                detail=str(row[5]),
                parser_version=str(row[6]),
                snapshot_ref_sha256=row[7],
                created_at=row[8],
            )
            for row in rows
        ]


class _OwnedRepositoryBase:
    """Shared RLS discipline: every transaction runs AS the restricted
    ``authenticated`` role with the JWT subject set, mirroring migration 0002 /
    ADR-017 §5, so row-level security is enforced even against repository bugs."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _authenticated_cursor(self, user_sub: str) -> Any:
        import psycopg

        conn = psycopg.connect(self._dsn)
        cur = conn.cursor()
        cur.execute("SET LOCAL ROLE authenticated")
        cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (user_sub,))
        return conn, cur

    def _serializable_authenticated_cursor(self, user_sub: str) -> Any:
        """Open the one serializable transaction used by M10B approval."""

        import psycopg

        conn = psycopg.connect(self._dsn)
        cur = conn.cursor()
        cur.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        cur.execute("SET LOCAL ROLE authenticated")
        cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (user_sub,))
        return conn, cur


def _uuid_list(values: Sequence[UUID]) -> list[str]:
    return [str(value) for value in values]


class SqlConsumptionRepository(
    _OwnedRepositoryBase,
    ConsumptionRepository,
    DailyNutritionLedgerRepository,
):
    """Append-only consumption persistence under authenticated RLS scope."""

    @staticmethod
    def _row_to_entry(row: Sequence[Any]) -> ConsumptionEntry:
        return ConsumptionEntry(
            entry_id=UUID(str(row[0])),
            user_id=UUID(str(row[1])),
            plan_run_id=UUID(str(row[2])),
            plan_version_id=UUID(str(row[3])),
            item_id=UUID(str(row[4])),
            state=ConsumptionState(str(row[5])),
            client_event_id=UUID(str(row[6])),
            recorded_at=row[7],
        )

    @staticmethod
    def _logical_request(entry: ConsumptionEntry) -> tuple[object, ...]:
        return (
            entry.user_id,
            entry.plan_run_id,
            entry.plan_version_id,
            entry.item_id,
            entry.state,
            entry.client_event_id,
        )

    def save(self, entry: ConsumptionEntry) -> RecordConsumptionOutcome:
        conn, cur = self._authenticated_cursor(str(entry.user_id))
        try:
            key_params = (
                str(entry.user_id),
                str(entry.plan_version_id),
                str(entry.item_id),
                str(entry.client_event_id),
            )
            cur.execute(
                """
                SELECT entry_id, user_id, plan_run_id, plan_version_id,
                       item_id, state, client_event_id, recorded_at
                FROM plan_consumption
                WHERE user_id=%s AND plan_version_id=%s AND item_id=%s
                  AND client_event_id=%s
                """,
                key_params,
            )
            original_row = cur.fetchone()
            if original_row is not None:
                original = self._row_to_entry(original_row)
                if self._logical_request(original) != self._logical_request(entry):
                    raise DuplicateConsumptionError(
                        "consumption idempotency key was reused with conflicting data"
                    )
                conn.commit()
                return RecordConsumptionOutcome(entry=original, created=False)

            cur.execute(
                """
                INSERT INTO plan_consumption (
                    entry_id, user_id, plan_run_id, plan_version_id, item_id,
                    state, client_event_id, recorded_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
                RETURNING entry_id, user_id, plan_run_id, plan_version_id,
                          item_id, state, client_event_id, recorded_at
                """,
                (
                    str(entry.entry_id),
                    str(entry.user_id),
                    str(entry.plan_run_id),
                    str(entry.plan_version_id),
                    str(entry.item_id),
                    entry.state.value,
                    str(entry.client_event_id),
                    entry.recorded_at,
                ),
            )
            inserted = cur.fetchone()
            if inserted is not None:
                persisted = self._row_to_entry(inserted)
                conn.commit()
                return RecordConsumptionOutcome(entry=persisted, created=True)

            cur.execute(
                """
                SELECT entry_id, user_id, plan_run_id, plan_version_id,
                       item_id, state, client_event_id, recorded_at
                FROM plan_consumption
                WHERE user_id=%s AND plan_version_id=%s AND item_id=%s
                  AND client_event_id=%s
                """,
                key_params,
            )
            existing_row = cur.fetchone()
            if existing_row is None:
                raise DuplicateConsumptionError("consumption entry_id already exists")
            original = self._row_to_entry(existing_row)
            if self._logical_request(original) != self._logical_request(entry):
                raise DuplicateConsumptionError(
                    "consumption idempotency key was reused with conflicting data"
                )
            conn.commit()
            return RecordConsumptionOutcome(entry=original, created=False)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_for_run(self, user_id: UUID, plan_run_id: UUID) -> tuple[ConsumptionEntry, ...]:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                """
                SELECT entry_id, user_id, plan_run_id, plan_version_id,
                       item_id, state, client_event_id, recorded_at
                FROM plan_consumption
                WHERE user_id=%s AND plan_run_id=%s
                ORDER BY recorded_at ASC, entry_id ASC
                """,
                (str(user_id), str(plan_run_id)),
            )
            entries = tuple(self._row_to_entry(row) for row in cur.fetchall())
            conn.commit()
            return entries
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _ledger_evidence_from_row(row: Sequence[Any]) -> ConsumedNutritionEvidence:
        entry_id = UUID(str(row[0]))
        recorded_at = row[1]
        run_id = UUID(str(row[2]))
        version_id = UUID(str(row[3]))
        item_id = UUID(str(row[4]))
        slot_index = int(row[5])
        meal_context = str(row[6])
        rank = int(row[7])
        candidate_id = str(row[8])
        plan_jsonb = row[9]
        if not isinstance(plan_jsonb, dict):
            raise ValueError("consumed plan artifact is not an object")
        slots = plan_jsonb.get("slots")
        if not isinstance(slots, list) or not 0 <= slot_index < len(slots):
            raise ValueError("consumed plan slot cannot be reconstructed")
        slot = slots[slot_index]
        if not isinstance(slot, dict):
            raise ValueError("consumed plan slot is malformed")
        candidates = slot.get("candidates")
        if not isinstance(candidates, list) or not 1 <= rank <= len(candidates):
            raise ValueError("consumed plan candidate cannot be reconstructed")
        candidate = candidates[rank - 1]
        if not isinstance(candidate, dict) or candidate.get("candidate_id") != candidate_id:
            raise ValueError("consumed plan item does not match its immutable artifact")
        totals = candidate.get("totals")
        if not isinstance(totals, dict):
            raise ValueError("consumed candidate totals are malformed")
        quantities_raw = totals.get("quantities")
        if not isinstance(quantities_raw, dict):
            raise ValueError("consumed candidate quantities are malformed")
        quantities: dict[str, Decimal] = {}
        for key, value in quantities_raw.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("consumed candidate quantities must be decimal strings")
            quantities[key] = Decimal(value)

        confidence_raw = totals.get("confidence")
        confidence = confidence_raw if isinstance(confidence_raw, str) else None
        candidate_kind = candidate.get("candidate_kind")
        estimate = candidate.get("configurable_estimate")
        configuration_summary = None
        unknown: set[str] = set()
        lines = candidate.get("lines")
        if candidate_kind == "configurable_estimate":
            if not isinstance(estimate, dict):
                raise ValueError("configurable candidate is missing frozen estimate details")
            definition = estimate.get("definition")
            if not isinstance(definition, dict):
                raise ValueError("configurable candidate definition is malformed")
            name_raw = definition.get("display_name")
            summary_raw = definition.get("configuration_summary")
            if not isinstance(name_raw, str) or not isinstance(summary_raw, str):
                raise ValueError("configurable candidate identity is malformed")
            item_name = name_raw
            configuration_summary = summary_raw
            estimate_payload = definition.get("estimate")
            if not isinstance(estimate_payload, dict):
                raise ValueError("configurable candidate estimate is malformed")
            estimate_state = estimate_payload.get("state")
            if estimate_state not in {"complete_estimate", "partial_estimate"}:
                raise ValueError("configurable candidate estimate state is unsupported")
            unknown_raw = estimate_payload.get("unknown_nutrients", [])
            if not isinstance(unknown_raw, list) or not all(
                isinstance(value, str) for value in unknown_raw
            ):
                raise ValueError("configurable candidate unknown nutrients are malformed")
            unknown.update(cast(list[str], unknown_raw))
            authority = (
                NutritionAuthority.PARTIAL
                if estimate_state == "partial_estimate"
                else NutritionAuthority.ESTIMATED
            )
            provenance_summary = "Owner-observed configuration with external reference nutrition"
        else:
            if candidate_kind is not None:
                raise ValueError("consumed candidate kind is unsupported")
            if not isinstance(lines, list) or not lines:
                raise ValueError("strict consumed candidate has no frozen meal lines")
            names: list[str] = []
            for line in lines:
                if not isinstance(line, dict) or not isinstance(line.get("name_normalized"), str):
                    raise ValueError("strict consumed candidate line is malformed")
                names.append(str(line["name_normalized"]))
            item_name = " + ".join(names)
            authority = (
                NutritionAuthority.OFFICIAL
                if confidence in {"official_published", "official_component_sum"}
                else NutritionAuthority.PARTIAL
            )
            provenance_summary = "Stacks nutrition frozen with the immutable plan"

        unavailable_raw = totals.get("declared_unavailable", [])
        if not isinstance(unavailable_raw, list) or not all(
            isinstance(value, str) for value in unavailable_raw
        ):
            raise ValueError("consumed candidate unavailable nutrients are malformed")
        unknown.update(cast(list[str], unavailable_raw))
        for nutrient in NutrientKey:
            if nutrient.value not in quantities:
                unknown.add(nutrient.value)

        return ConsumedNutritionEvidence(
            entry_id=entry_id,
            recorded_at=recorded_at,
            plan_run_id=run_id,
            plan_version_id=version_id,
            plan_item_id=item_id,
            meal_context=meal_context,
            candidate_id=candidate_id,
            item_name=item_name,
            configuration_summary=configuration_summary,
            authority=authority,
            confidence=confidence,
            calories_kcal=quantities.get(NutrientKey.CALORIES_KCAL.value),
            protein_g=quantities.get(NutrientKey.PROTEIN_G.value),
            unknown_nutrients=tuple(unknown),
            provenance_summary=provenance_summary,
        )

    def list_eaten_evidence(
        self,
        user_id: UUID,
        start_inclusive: datetime,
        end_exclusive: datetime,
    ) -> tuple[ConsumedNutritionEvidence, ...]:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                """
                SELECT c.entry_id, c.recorded_at, c.plan_run_id,
                       c.plan_version_id, c.item_id,
                       i.slot_index, i.context, i.rank, i.candidate_id,
                       v.plan_jsonb
                FROM plan_consumption c
                JOIN plan_item i
                  ON i.item_id=c.item_id AND i.version_id=c.plan_version_id
                JOIN plan_version v ON v.version_id=c.plan_version_id
                JOIN plan_run r
                  ON r.run_id=c.plan_run_id AND r.user_id=c.user_id
                 AND v.run_id=r.run_id
                WHERE c.user_id=%s AND c.state='eaten'
                  AND c.recorded_at >= %s AND c.recorded_at < %s
                ORDER BY c.recorded_at ASC, c.entry_id ASC
                """,
                (str(user_id), start_inclusive, end_exclusive),
            )
            entries = [self._ledger_evidence_from_row(row) for row in cur.fetchall()]
            cur.execute(
                """
                SELECT entry_id, recorded_at, food_id, food_version_id,
                       meal_period, food_name, brand, serving_description,
                       consumed_amount, consumed_unit,
                       calories_kcal, protein_g, carbohydrate_g, total_fat_g,
                       fiber_g, sodium_mg, source_system, nutrition_authority,
                       nutrition_confidence, provenance_summary
                FROM manual_food_consumption
                WHERE user_id=%s AND recorded_at >= %s AND recorded_at < %s
                ORDER BY recorded_at ASC, entry_id ASC
                """,
                (str(user_id), start_inclusive, end_exclusive),
            )
            for row in cur.fetchall():
                unknown_names = (
                    "calories_kcal",
                    "protein_g",
                    "carbohydrate_g",
                    "total_fat_g",
                    "fiber_g",
                    "sodium_mg",
                )
                unknown = tuple(
                    name
                    for name, value in zip(unknown_names, row[10:16], strict=True)
                    if value is None
                )
                entries.append(
                    ConsumedNutritionEvidence(
                        entry_id=UUID(str(row[0])),
                        recorded_at=row[1],
                        plan_run_id=None,
                        plan_version_id=None,
                        plan_item_id=None,
                        meal_context=str(row[4]),
                        candidate_id=f"manual:{row[0]}",
                        item_name=str(row[5]),
                        configuration_summary=str(row[7]),
                        authority=NutritionAuthority(str(row[17])),
                        confidence=str(row[18]),
                        calories_kcal=row[10],
                        protein_g=row[11],
                        unknown_nutrients=unknown,
                        provenance_summary=str(row[19]),
                        source_system=str(row[16]),
                        custom_food_id=UUID(str(row[2])),
                        custom_food_version_id=UUID(str(row[3])),
                        consumed_amount=row[8],
                        consumed_unit=str(row[9]),
                    )
                )
            cur.execute(
                """
                SELECT entry_id, recorded_at, meal_context, candidate_id,
                       item_name, configuration_summary, nutrition_authority,
                       nutrition_confidence, calories_kcal, protein_g,
                       unknown_nutrients, recommendation_id,
                       recommendation_artifact_sha256
                FROM next_meal_consumption
                WHERE user_id=%s AND state='eaten'
                  AND recorded_at >= %s AND recorded_at < %s
                ORDER BY recorded_at ASC, entry_id ASC
                """,
                (str(user_id), start_inclusive, end_exclusive),
            )
            for row in cur.fetchall():
                entries.append(
                    ConsumedNutritionEvidence(
                        entry_id=UUID(str(row[0])),
                        recorded_at=row[1],
                        plan_run_id=None,
                        plan_version_id=None,
                        plan_item_id=None,
                        meal_context=str(row[2]),
                        candidate_id=str(row[3]),
                        item_name=str(row[4]),
                        configuration_summary=(str(row[5]) if row[5] is not None else None),
                        authority=NutritionAuthority(str(row[6])),
                        confidence=str(row[7]) if row[7] is not None else None,
                        calories_kcal=row[8],
                        protein_g=row[9],
                        unknown_nutrients=tuple(row[10] or ()),
                        provenance_summary=("Next Meal recommendation snapshot " + str(row[12])),
                        source_system="next_meal",
                    )
                )
            entries.sort(key=lambda item: (item.recorded_at, item.entry_id))
            conn.commit()
            return tuple(entries)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


class SqlCustomFoodRepository(_OwnedRepositoryBase, CustomFoodRepository):
    """Owner-scoped immutable custom foods and manual consumption."""

    @staticmethod
    def _version(row: Sequence[Any]) -> CustomFoodVersion:
        raw_provenance = row[15]
        if not isinstance(raw_provenance, dict):
            raise ValueError("custom food provenance is not an object")
        authority = CustomFoodAuthority(str(raw_provenance.get("authority")))
        fetched_at_raw = raw_provenance.get("fetched_at")
        fetched_at = (
            datetime.fromisoformat(str(fetched_at_raw)) if fetched_at_raw is not None else None
        )
        return CustomFoodVersion(
            food_id=UUID(str(row[0])),
            version_id=UUID(str(row[1])),
            user_id=UUID(str(row[2])),
            name=str(row[3]),
            brand=row[4],
            serving_description=str(row[5]),
            serving_amount=row[6],
            serving_unit=str(row[7]),
            nutrition=ManualNutritionFacts(
                calories_kcal=row[8],
                protein_g=row[9],
                carbohydrate_g=row[10],
                total_fat_g=row[11],
                fiber_g=row[12],
                sodium_mg=row[13],
            ),
            created_at=row[14],
            provenance=CustomFoodProvenance(
                authority=authority,
                provider=raw_provenance.get("provider"),
                scanned_barcode=raw_provenance.get("scanned_barcode"),
                provider_code=raw_provenance.get("provider_code"),
                product_url=raw_provenance.get("product_url"),
                fetched_at=fetched_at,
                payload_sha256=raw_provenance.get("payload_sha256"),
                data_license=raw_provenance.get("data_license"),
                nutrition_basis=raw_provenance.get("nutrition_basis"),
            ),
        )

    @staticmethod
    def _provenance(version: CustomFoodVersion) -> dict[str, object | None]:
        value = version.provenance
        return {
            "authority": value.authority.value,
            "provider": value.provider,
            "scanned_barcode": value.scanned_barcode,
            "provider_code": value.provider_code,
            "product_url": value.product_url,
            "fetched_at": value.fetched_at.isoformat() if value.fetched_at is not None else None,
            "payload_sha256": value.payload_sha256,
            "data_license": value.data_license,
            "nutrition_basis": value.nutrition_basis,
        }

    def save_version(self, version: CustomFoodVersion, *, create_identity: bool) -> None:
        from psycopg import errors as psycopg_errors

        conn, cur = self._authenticated_cursor(str(version.user_id))
        try:
            if create_identity:
                provenance = version.provenance
                cur.execute(
                    """INSERT INTO custom_food (
                           food_id,user_id,created_at,source_system,source_key)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (
                        str(version.food_id),
                        str(version.user_id),
                        version.created_at,
                        provenance.provider,
                        provenance.provider_code,
                    ),
                )
            else:
                cur.execute(
                    "SELECT 1 FROM custom_food WHERE food_id=%s AND user_id=%s",
                    (str(version.food_id), str(version.user_id)),
                )
                if cur.fetchone() is None:
                    raise LookupError("custom food not found")
            n = version.nutrition
            cur.execute(
                """
                INSERT INTO custom_food_version (
                    version_id,food_id,user_id,name,brand,serving_description,
                    serving_amount,serving_unit,calories_kcal,protein_g,
                    carbohydrate_g,total_fat_g,fiber_g,sodium_mg,created_at,
                    provenance_jsonb)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    str(version.version_id),
                    str(version.food_id),
                    str(version.user_id),
                    version.name,
                    version.brand,
                    version.serving_description,
                    version.serving_amount,
                    version.serving_unit,
                    n.calories_kcal,
                    n.protein_g,
                    n.carbohydrate_g,
                    n.total_fat_g,
                    n.fiber_g,
                    n.sodium_mg,
                    version.created_at,
                    json.dumps(self._provenance(version), sort_keys=True),
                ),
            )
            conn.commit()
        except psycopg_errors.UniqueViolation as exc:
            conn.rollback()
            raise DuplicateManualFoodError("custom food identity or version conflicts") from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_latest(self, user_id: UUID) -> tuple[CustomFoodVersion, ...]:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                """
                SELECT food_id,version_id,user_id,name,brand,serving_description,
                       serving_amount,serving_unit,calories_kcal,protein_g,
                       carbohydrate_g,total_fat_g,fiber_g,sodium_mg,created_at,
                       provenance_jsonb
                FROM (SELECT v.*, row_number() OVER (
                    PARTITION BY food_id ORDER BY created_at DESC, version_id DESC) AS ordinal
                    FROM custom_food_version v WHERE user_id=%s) ranked
                WHERE ordinal=1 ORDER BY lower(name), food_id
                """,
                (str(user_id),),
            )
            result = tuple(self._version(row) for row in cur.fetchall())
            conn.commit()
            return result
        finally:
            conn.close()

    def find_version(
        self, user_id: UUID, food_id: UUID, version_id: UUID
    ) -> CustomFoodVersion | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                """SELECT food_id,version_id,user_id,name,brand,serving_description,
                          serving_amount,serving_unit,calories_kcal,protein_g,
                          carbohydrate_g,total_fat_g,fiber_g,sodium_mg,created_at,
                          provenance_jsonb
                   FROM custom_food_version WHERE user_id=%s AND food_id=%s AND version_id=%s""",
                (str(user_id), str(food_id), str(version_id)),
            )
            row = cur.fetchone()
            conn.commit()
            return self._version(row) if row is not None else None
        finally:
            conn.close()

    def find_latest_by_source(
        self, user_id: UUID, source_system: str, source_key: str
    ) -> CustomFoodVersion | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                """SELECT v.food_id,v.version_id,v.user_id,v.name,v.brand,
                          v.serving_description,v.serving_amount,v.serving_unit,
                          v.calories_kcal,v.protein_g,v.carbohydrate_g,v.total_fat_g,
                          v.fiber_g,v.sodium_mg,v.created_at,v.provenance_jsonb
                   FROM custom_food f JOIN custom_food_version v
                     ON v.food_id=f.food_id AND v.user_id=f.user_id
                   WHERE f.user_id=%s AND f.source_system=%s AND f.source_key=%s
                   ORDER BY v.created_at DESC,v.version_id DESC LIMIT 1""",
                (str(user_id), source_system, source_key),
            )
            row = cur.fetchone()
            conn.commit()
            return self._version(row) if row is not None else None
        finally:
            conn.close()

    @staticmethod
    def _consumption(row: Sequence[Any]) -> ManualFoodConsumptionEntry:
        return ManualFoodConsumptionEntry(
            entry_id=UUID(str(row[0])),
            user_id=UUID(str(row[1])),
            food_id=UUID(str(row[2])),
            food_version_id=UUID(str(row[3])),
            client_event_id=UUID(str(row[4])),
            meal_period=ManualMealPeriod(str(row[5])),
            consumed_amount=row[6],
            consumed_unit=str(row[7]),
            portion_factor=row[8],
            food_name=str(row[9]),
            brand=row[10],
            serving_description=str(row[11]),
            serving_amount=row[12],
            serving_unit=str(row[13]),
            nutrition=ManualNutritionFacts(
                calories_kcal=row[14],
                protein_g=row[15],
                carbohydrate_g=row[16],
                total_fat_g=row[17],
                fiber_g=row[18],
                sodium_mg=row[19],
            ),
            recorded_at=row[20],
            source_system=str(row[21]),
            nutrition_authority=str(row[22]),
            nutrition_confidence=str(row[23]),
            provenance_summary=str(row[24]),
        )

    def save_consumption(self, entry: ManualFoodConsumptionEntry) -> RecordManualFoodOutcome:
        conn, cur = self._authenticated_cursor(str(entry.user_id))
        columns = (
            "entry_id,user_id,food_id,food_version_id,client_event_id,meal_period,"
            "consumed_amount,consumed_unit,portion_factor,food_name,brand,"
            "serving_description,serving_amount,serving_unit,calories_kcal,protein_g,"
            "carbohydrate_g,total_fat_g,fiber_g,sodium_mg,recorded_at"
            ",source_system,nutrition_authority,nutrition_confidence,provenance_summary"
        )
        try:
            cur.execute(
                f"SELECT {columns} FROM manual_food_consumption "
                "WHERE user_id=%s AND client_event_id=%s",
                (str(entry.user_id), str(entry.client_event_id)),
            )
            row = cur.fetchone()
            if row is not None:
                original = self._consumption(row)
                if original.request_facts() != entry.request_facts():
                    raise DuplicateManualFoodError("manual consumption event conflicts")
                conn.commit()
                return RecordManualFoodOutcome(original, False)
            n = entry.nutrition
            cur.execute(
                f"INSERT INTO manual_food_consumption ({columns}) "
                f"VALUES ({','.join(['%s'] * 25)}) "
                f"ON CONFLICT DO NOTHING RETURNING {columns}",
                (
                    str(entry.entry_id),
                    str(entry.user_id),
                    str(entry.food_id),
                    str(entry.food_version_id),
                    str(entry.client_event_id),
                    entry.meal_period.value,
                    entry.consumed_amount,
                    entry.consumed_unit,
                    entry.portion_factor,
                    entry.food_name,
                    entry.brand,
                    entry.serving_description,
                    entry.serving_amount,
                    entry.serving_unit,
                    n.calories_kcal,
                    n.protein_g,
                    n.carbohydrate_g,
                    n.total_fat_g,
                    n.fiber_g,
                    n.sodium_mg,
                    entry.recorded_at,
                    entry.source_system,
                    entry.nutrition_authority,
                    entry.nutrition_confidence,
                    entry.provenance_summary,
                ),
            )
            inserted = cur.fetchone()
            if inserted is not None:
                persisted = self._consumption(inserted)
                conn.commit()
                return RecordManualFoodOutcome(persisted, True)
            cur.execute(
                f"SELECT {columns} FROM manual_food_consumption "
                "WHERE user_id=%s AND client_event_id=%s",
                (str(entry.user_id), str(entry.client_event_id)),
            )
            existing = cur.fetchone()
            if existing is None:
                raise DuplicateManualFoodError("manual consumption entry ID conflicts")
            original = self._consumption(existing)
            if original.request_facts() != entry.request_facts():
                raise DuplicateManualFoodError("manual consumption event conflicts")
            conn.commit()
            return RecordManualFoodOutcome(original, False)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


class SqlPlanRunRepository(_OwnedRepositoryBase):
    """Daily-plan persistence (M6, ADR-017). Append-only; no DELETE.

    save() is atomic: plan_run (fingerprint-unique), its immutable
    plan_version, and every plan_item commit together or not at all.
    """

    def find_replay(
        self, user_id: UUID, requested_date: date, inputs_fingerprint: str
    ) -> PersistedPlanView | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                """
                SELECT r.run_id, r.user_id, r.requested_for_date, r.timezone,
                       r.inputs_fingerprint, r.status, r.reason_codes,
                       r.started_at, r.finished_at, r.target_policy_version_id,
                       v.version_id, v.plan_sha256, v.plan_jsonb, v.plan_canonical
                FROM plan_run r
                LEFT JOIN plan_version v ON v.run_id = r.run_id
                WHERE r.user_id=%s AND r.requested_for_date=%s
                  AND r.inputs_fingerprint=%s
                """,
                (str(user_id), requested_date, inputs_fingerprint),
            )
            row = cur.fetchone()
            items = self._items_for_version(cur, row[10] if row is not None else None)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self._row_to_view(row, items)

    def latest_for_user_date(self, user_id: UUID, requested_date: date) -> PersistedPlanView | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                """
                SELECT r.run_id, r.user_id, r.requested_for_date, r.timezone,
                       r.inputs_fingerprint, r.status, r.reason_codes,
                       r.started_at, r.finished_at, r.target_policy_version_id,
                       v.version_id, v.plan_sha256, v.plan_jsonb, v.plan_canonical
                FROM plan_run r
                LEFT JOIN plan_version v ON v.run_id = r.run_id
                WHERE r.user_id=%s AND r.requested_for_date=%s
                ORDER BY r.started_at DESC, r.run_id DESC
                LIMIT 1
                """,
                (str(user_id), requested_date),
            )
            row = cur.fetchone()
            items = self._items_for_version(cur, row[10] if row is not None else None)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self._row_to_view(row, items)

    def save(
        self,
        run: PlanRun,
        version: PlanVersion,
        items: Sequence[PlanItem],
    ) -> None:
        conn, cur = self._authenticated_cursor(str(run.user_id))
        try:
            cur.execute(
                """
                INSERT INTO plan_run (
                    run_id, user_id, requested_for_date, timezone,
                    inputs_fingerprint, status, reason_codes,
                    started_at, finished_at, target_policy_version_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (user_id, requested_for_date, inputs_fingerprint)
                DO NOTHING
                """,
                (
                    str(run.run_id),
                    str(run.user_id),
                    run.requested_for_date,
                    run.timezone,
                    run.inputs_fingerprint,
                    run.status.value,
                    list(run.reason_codes),
                    run.started_at,
                    run.finished_at,
                    (
                        str(run.target_policy_version_id)
                        if run.target_policy_version_id is not None
                        else None
                    ),
                ),
            )
            if cur.rowcount == 0:
                raise DuplicateLogicalPlanError(
                    "plan_run already exists for (user, date, inputs_fingerprint)"
                )
            cur.execute(
                """
                INSERT INTO plan_version (
                    version_id, run_id, plan_jsonb, plan_canonical, plan_sha256)
                VALUES (%s,%s,%s::jsonb,%s,%s)
                ON CONFLICT (run_id, plan_sha256) DO NOTHING
                """,
                (
                    str(version.version_id),
                    str(version.run_id),
                    json.dumps(version.plan_jsonb),
                    version.plan_canonical,
                    version.plan_sha256,
                ),
            )
            for item in items:
                cur.execute(
                    """
                    INSERT INTO plan_item (
                        item_id, version_id, slot_index, context, rank,
                        candidate_id, menu_period, food_ids, offering_ids,
                        profile_ids, score_total, calories_kcal)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s::uuid[],%s::uuid[],%s::uuid[],%s,%s)
                    """,
                    (
                        str(item.item_id),
                        str(item.version_id),
                        item.slot_index,
                        item.context,
                        item.rank,
                        item.candidate_id,
                        item.menu_period,
                        _uuid_list(item.food_ids),
                        _uuid_list(item.offering_ids),
                        _uuid_list(item.profile_row_ids),
                        item.score_total,
                        item.calories_kcal,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _items_for_version(
        cur: Any, version_id: object | None
    ) -> tuple[PersistedPlanItemReference, ...]:
        if version_id is None:
            return ()
        cur.execute(
            """
            SELECT item_id, version_id, slot_index, rank, candidate_id
            FROM plan_item
            WHERE version_id=%s
            ORDER BY slot_index ASC, rank ASC, item_id ASC
            """,
            (str(version_id),),
        )
        return tuple(
            PersistedPlanItemReference(
                item_id=UUID(str(item[0])),
                version_id=UUID(str(item[1])),
                slot_index=int(item[2]),
                rank=int(item[3]),
                candidate_id=str(item[4]),
            )
            for item in cur.fetchall()
        )

    @staticmethod
    def _row_to_view(
        row: Any,
        items: tuple[PersistedPlanItemReference, ...] = (),
    ) -> PersistedPlanView | None:
        if row is None:
            return None
        return PersistedPlanView(
            run_id=UUID(str(row[0])),
            user_id=UUID(str(row[1])),
            requested_for_date=row[2],
            timezone=str(row[3]),
            inputs_fingerprint=str(row[4]),
            status=str(row[5]),
            reason_codes=tuple(row[6] or []),
            started_at=row[7],
            finished_at=row[8],
            target_policy_version_id=UUID(str(row[9])) if row[9] else None,
            version_id=UUID(str(row[10])) if row[10] else None,
            plan_sha256=str(row[11]) if row[11] else None,
            plan_jsonb=dict(row[12]) if row[12] else None,
            plan_canonical=str(row[13]) if row[13] else None,
            plan_items=items,
        )


class SqlTargetPolicyRepository(_OwnedRepositoryBase):
    """Immutable approved target-policy versions + decision log (ADR-018)."""

    def save_approved(
        self,
        policy: TargetPolicyVersion,
        rationale: str,
        decided_by_clock: datetime,
    ) -> None:
        from psycopg import errors as psycopg_errors

        conn, cur = self._authenticated_cursor(str(policy.user_id))
        try:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (str(policy.user_id),),
            )
            cur.execute(
                """
                INSERT INTO target_policy_version (
                    version_id, user_id, policy_version, goals,
                    payload_sha256, created_at)
                VALUES (%s,%s,%s,%s,%s,%s)
                """,
                (
                    str(policy.version_id),
                    str(policy.user_id),
                    policy.policy_version,
                    json.dumps(policy.goals_jsonb),
                    policy.payload_sha256,
                    decided_by_clock,
                ),
            )
            decision_id = str(uuid4())
            cur.execute(
                """
                INSERT INTO decision_log (
                    decision_id, user_id, subject, decision, rationale,
                    policy_version_id, decided_at)
                VALUES (%s,%s,'target_policy','approved',%s,%s,%s)
                """,
                (
                    decision_id,
                    str(policy.user_id),
                    rationale,
                    str(policy.version_id),
                    decided_by_clock,
                ),
            )
            conn.commit()
        except psycopg_errors.UniqueViolation as exc:
            conn.rollback()
            raise TargetPolicyVersionExistsError(
                "target_policy_version already exists for this user"
            ) from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def latest_approved(self, user_id: UUID) -> TargetPolicyVersion | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                """
                SELECT version_id, user_id, policy_version, goals,
                       payload_sha256, created_at
                FROM target_policy_version
                WHERE user_id=%s
                ORDER BY created_at DESC, version_id DESC
                LIMIT 1
                """,
                (str(user_id),),
            )
            row = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if row is None:
            return None
        return TargetPolicyVersion(
            version_id=UUID(str(row[0])),
            user_id=UUID(str(row[1])),
            policy_version=str(row[2]),
            goals_jsonb=[dict(entry) for entry in (row[3] or [])],
            payload_sha256=str(row[4]),
            created_at=row[5],
        )

    def list_approved_for_window(
        self,
        user_id: UUID,
        start_inclusive: datetime,
        end_exclusive: datetime,
    ) -> tuple[TargetPolicyVersion, ...]:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                """
                WITH boundary AS (
                    SELECT version_id, user_id, policy_version, goals,
                           payload_sha256, created_at
                    FROM target_policy_version
                    WHERE user_id=%s AND created_at <= %s
                    ORDER BY created_at DESC, version_id DESC
                    LIMIT 1
                ), changes AS (
                    SELECT version_id, user_id, policy_version, goals,
                           payload_sha256, created_at
                    FROM target_policy_version
                    WHERE user_id=%s AND created_at > %s AND created_at < %s
                )
                SELECT * FROM boundary
                UNION ALL
                SELECT * FROM changes
                ORDER BY created_at ASC, version_id ASC
                """,
                (
                    str(user_id),
                    start_inclusive,
                    str(user_id),
                    start_inclusive,
                    end_exclusive,
                ),
            )
            rows = cur.fetchall()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return tuple(
            TargetPolicyVersion(
                version_id=UUID(str(row[0])),
                user_id=UUID(str(row[1])),
                policy_version=str(row[2]),
                goals_jsonb=[dict(entry) for entry in (row[3] or [])],
                payload_sha256=str(row[4]),
                created_at=row[5],
            )
            for row in rows
        )

    def find_by_version_id(self, user_id: UUID, version_id: UUID) -> TargetPolicyVersion | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                """
                SELECT version_id, user_id, policy_version, goals,
                       payload_sha256, created_at
                FROM target_policy_version
                WHERE user_id=%s AND version_id=%s
                """,
                (str(user_id), str(version_id)),
            )
            row = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if row is None:
            return None
        return TargetPolicyVersion(
            version_id=UUID(str(row[0])),
            user_id=UUID(str(row[1])),
            policy_version=str(row[2]),
            goals_jsonb=[dict(entry) for entry in (row[3] or [])],
            payload_sha256=str(row[4]),
            created_at=row[5],
        )

    def find_by_version_label(self, user_id: UUID, policy_version: str) -> bool:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                "SELECT 1 FROM target_policy_version WHERE user_id=%s AND policy_version=%s",
                (str(user_id), policy_version),
            )
            found = cur.fetchone() is not None
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return found


class SqlGoalPolicyRepository(_OwnedRepositoryBase, GoalPolicyRepository):
    """Immutable per-owner goal intent under the authenticated RLS boundary."""

    @staticmethod
    def _row_to_policy(row: Sequence[Any]) -> GoalPolicyVersion:
        policy = GoalPolicyVersion(
            version_id=UUID(str(row[0])),
            user_id=UUID(str(row[1])),
            policy_version=str(row[2]),
            direction=GoalDirection(str(row[3])),
            desired_rate_kg_per_week=Decimal(row[4]),
            payload_sha256=str(row[5]),
            created_at=row[6],
        )
        expected = goal_policy_payload_sha256(policy.direction, policy.desired_rate_kg_per_week)
        if expected != policy.payload_sha256:
            raise ValueError("stored goal policy payload hash is inconsistent")
        return policy

    def save(self, policy: GoalPolicyVersion) -> None:
        from psycopg import errors as psycopg_errors

        conn, cur = self._authenticated_cursor(str(policy.user_id))
        try:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (str(policy.user_id),),
            )
            cur.execute(
                """
                INSERT INTO goal_policy_version (
                    version_id, user_id, policy_version, direction,
                    desired_rate_kg_per_week, payload_sha256, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    str(policy.version_id),
                    str(policy.user_id),
                    policy.policy_version,
                    policy.direction.value,
                    policy.desired_rate_kg_per_week,
                    policy.payload_sha256,
                    policy.created_at,
                ),
            )
            conn.commit()
        except psycopg_errors.UniqueViolation as exc:
            conn.rollback()
            raise GoalPolicyVersionExistsError(
                "goal_policy_version already exists for this user"
            ) from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def latest(self, user_id: UUID) -> GoalPolicyVersion | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                """
                SELECT version_id, user_id, policy_version, direction,
                       desired_rate_kg_per_week, payload_sha256, created_at
                FROM goal_policy_version
                WHERE user_id=%s
                ORDER BY created_at DESC, version_id DESC
                LIMIT 1
                """,
                (str(user_id),),
            )
            row = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self._row_to_policy(row) if row is not None else None

    def find_by_version_id(self, user_id: UUID, version_id: UUID) -> GoalPolicyVersion | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                """
                SELECT version_id, user_id, policy_version, direction,
                       desired_rate_kg_per_week, payload_sha256, created_at
                FROM goal_policy_version
                WHERE user_id=%s AND version_id=%s
                """,
                (str(user_id), str(version_id)),
            )
            row = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self._row_to_policy(row) if row is not None else None


class SqlTargetReviewRepository(_OwnedRepositoryBase, TargetReviewRepository):
    """Digest-idempotent immutable review evidence under owner RLS."""

    _SELECT = """
        SELECT review_id, user_id, as_of_date, timezone,
               trend_algorithm_version, trend_input_digest,
               goal_policy_version_id, prior_target_policy_version_id,
               review_policy_version, status, reason_codes,
               current_calorie_target, proposed_calorie_target, calorie_delta,
               recommendation_digest, evaluation_payload, created_at
        FROM target_review
    """

    @staticmethod
    def _row_to_review(row: Sequence[Any]) -> TargetReview:
        digest = str(row[14])
        evaluation = target_review_evaluation_from_document(dict(row[15] or {}), digest)
        expected = (
            evaluation.user_id,
            evaluation.as_of_date,
            evaluation.trend.timezone,
            evaluation.trend.algorithm_version,
            evaluation.trend.input_digest,
            evaluation.goal_policy.version_id,
            evaluation.prior_target_policy_version_id,
            evaluation.review_policy.policy_version,
            evaluation.status,
            tuple(reason.value for reason in evaluation.reason_codes),
            evaluation.current_calorie_target,
            evaluation.proposed_calorie_target,
            evaluation.calorie_delta,
        )
        stored = (
            UUID(str(row[1])),
            row[2],
            str(row[3]),
            str(row[4]),
            str(row[5]),
            UUID(str(row[6])),
            UUID(str(row[7])),
            str(row[8]),
            TargetReviewStatus(str(row[9])),
            tuple(str(reason) for reason in (row[10] or [])),
            Decimal(row[11]),
            Decimal(row[12]) if row[12] is not None else None,
            Decimal(row[13]) if row[13] is not None else None,
        )
        if expected != stored:
            raise ValueError("stored target review columns disagree with evidence payload")
        return TargetReview(review_id=UUID(str(row[0])), evaluation=evaluation, created_at=row[16])

    @staticmethod
    def _logical_request(review: TargetReview) -> object:
        return review.evaluation

    def _find_row(self, cur: Any, user_id: UUID, digest: str) -> Sequence[Any] | None:
        cur.execute(
            self._SELECT + " WHERE user_id=%s AND recommendation_digest=%s",
            (str(user_id), digest),
        )
        return cast(Sequence[Any] | None, cur.fetchone())

    def save(self, review: TargetReview) -> PersistTargetReviewOutcome:
        conn, cur = self._authenticated_cursor(str(review.user_id))
        try:
            original_row = self._find_row(cur, review.user_id, review.recommendation_digest)
            if original_row is not None:
                original = self._row_to_review(original_row)
                if self._logical_request(original) != self._logical_request(review):
                    raise DuplicateTargetReviewError(
                        "recommendation digest was reused with conflicting evidence"
                    )
                conn.commit()
                return PersistTargetReviewOutcome(review=original, created=False)

            evaluation = review.evaluation
            cur.execute(
                """
                INSERT INTO target_review (
                    review_id, user_id, as_of_date, timezone,
                    trend_algorithm_version, trend_input_digest,
                    goal_policy_version_id, prior_target_policy_version_id,
                    review_policy_version, status, reason_codes,
                    current_calorie_target, proposed_calorie_target, calorie_delta,
                    recommendation_digest, evaluation_payload, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
                RETURNING review_id, user_id, as_of_date, timezone,
                          trend_algorithm_version, trend_input_digest,
                          goal_policy_version_id, prior_target_policy_version_id,
                          review_policy_version, status, reason_codes,
                          current_calorie_target, proposed_calorie_target, calorie_delta,
                          recommendation_digest, evaluation_payload, created_at
                """,
                (
                    str(review.review_id),
                    str(review.user_id),
                    evaluation.as_of_date,
                    evaluation.trend.timezone,
                    evaluation.trend.algorithm_version,
                    evaluation.trend.input_digest,
                    str(evaluation.goal_policy.version_id),
                    str(evaluation.prior_target_policy_version_id),
                    evaluation.review_policy.policy_version,
                    evaluation.status.value,
                    [reason.value for reason in evaluation.reason_codes],
                    evaluation.current_calorie_target,
                    evaluation.proposed_calorie_target,
                    evaluation.calorie_delta,
                    evaluation.recommendation_digest,
                    json.dumps(target_review_evaluation_document(evaluation)),
                    review.created_at,
                ),
            )
            inserted = cur.fetchone()
            if inserted is not None:
                persisted = self._row_to_review(inserted)
                conn.commit()
                return PersistTargetReviewOutcome(review=persisted, created=True)

            existing_row = self._find_row(cur, review.user_id, review.recommendation_digest)
            if existing_row is None:
                raise DuplicateTargetReviewError("target review identity already exists")
            original = self._row_to_review(existing_row)
            if self._logical_request(original) != self._logical_request(review):
                raise DuplicateTargetReviewError(
                    "recommendation digest was reused with conflicting evidence"
                )
            conn.commit()
            return PersistTargetReviewOutcome(review=original, created=False)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def find_by_digest(self, user_id: UUID, recommendation_digest: str) -> TargetReview | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            row = self._find_row(cur, user_id, recommendation_digest)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self._row_to_review(row) if row is not None else None

    def find_by_id(self, user_id: UUID, review_id: UUID) -> TargetReview | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                self._SELECT + " WHERE user_id=%s AND review_id=%s",
                (str(user_id), str(review_id)),
            )
            row = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self._row_to_review(row) if row is not None else None


class SqlTargetReviewDecisionRepository(_OwnedRepositoryBase, TargetReviewDecisionRepository):
    """One serializable transaction for the explicit M10B terminal decision."""

    _DECISION_SELECT = """
        SELECT d.decision_id, d.user_id, d.review_id, d.decision, d.rationale,
               d.resulting_target_policy_version_id, d.client_event_id, d.decided_at,
               p.version_id, p.user_id, p.policy_version, p.goals,
               p.payload_sha256, p.created_at,
               l.decision_id, l.user_id, l.subject, l.decision, l.rationale,
               l.policy_version_id, l.decided_at
        FROM target_review_decision d
        LEFT JOIN target_policy_version p
          ON p.version_id = d.resulting_target_policy_version_id
        LEFT JOIN decision_log l
          ON l.policy_version_id = d.resulting_target_policy_version_id
    """

    @staticmethod
    def _row_to_outcome(row: Sequence[Any], *, created: bool) -> DecideTargetReviewOutcome:
        terminal = TargetReviewDecision(
            decision_id=UUID(str(row[0])),
            user_id=UUID(str(row[1])),
            review_id=UUID(str(row[2])),
            decision=TargetReviewDecisionValue(str(row[3])),
            rationale=str(row[4]),
            resulting_target_policy_version_id=UUID(str(row[5])) if row[5] else None,
            client_event_id=UUID(str(row[6])),
            decided_at=row[7],
        )
        policy: TargetPolicyVersion | None = None
        target_log: DecisionLogEntry | None = None
        if terminal.decision is TargetReviewDecisionValue.APPROVED:
            if row[8] is None or row[14] is None:
                raise ValueError("approved review decision lacks target audit records")
            policy = TargetPolicyVersion(
                version_id=UUID(str(row[8])),
                user_id=UUID(str(row[9])),
                policy_version=str(row[10]),
                goals_jsonb=[dict(entry) for entry in (row[11] or [])],
                payload_sha256=str(row[12]),
                created_at=row[13],
            )
            target_log = DecisionLogEntry(
                decision_id=UUID(str(row[14])),
                user_id=UUID(str(row[15])),
                subject=str(row[16]),
                decision=str(row[17]),
                rationale=str(row[18]),
                policy_version_id=UUID(str(row[19])),
                decided_at=row[20],
            )
        return DecideTargetReviewOutcome(
            review_decision=terminal,
            resulting_policy=policy,
            target_decision_log=target_log,
            created=created,
        )

    def _find_outcome(
        self,
        cur: Any,
        *,
        user_id: UUID,
        review_id: UUID | None = None,
        client_event_id: UUID | None = None,
        created: bool = False,
    ) -> DecideTargetReviewOutcome | None:
        if (review_id is None) == (client_event_id is None):
            raise ValueError("exactly one decision lookup identity is required")
        if review_id is not None:
            clause = " WHERE d.user_id=%s AND d.review_id=%s"
            identity = review_id
        else:
            clause = " WHERE d.user_id=%s AND d.client_event_id=%s"
            assert client_event_id is not None
            identity = client_event_id
        cur.execute(self._DECISION_SELECT + clause, (str(user_id), str(identity)))
        rows = cur.fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise ValueError("target review decision has inconsistent decision-log cardinality")
        return self._row_to_outcome(rows[0], created=created)

    @staticmethod
    def _is_exact_replay(
        outcome: DecideTargetReviewOutcome, requested: TargetReviewDecision
    ) -> bool:
        stored = outcome.review_decision
        return (
            stored.user_id == requested.user_id
            and stored.review_id == requested.review_id
            and stored.decision is requested.decision
            and stored.rationale == requested.rationale
            and stored.client_event_id == requested.client_event_id
        )

    def find_for_review(self, user_id: UUID, review_id: UUID) -> DecideTargetReviewOutcome | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            outcome = self._find_outcome(cur, user_id=user_id, review_id=review_id)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return outcome

    def _replay_after_race(self, requested: TargetReviewDecision) -> DecideTargetReviewOutcome:
        outcome = self.find_for_review(requested.user_id, requested.review_id)
        if outcome is not None and self._is_exact_replay(outcome, requested):
            return replace(outcome, created=False)
        raise TargetReviewDecisionConflictError(
            "terminal decision, idempotency key, or resulting target already exists"
        )

    def decide(
        self,
        review: TargetReview,
        decision: TargetReviewDecision,
        resulting_policy: TargetPolicyVersion | None,
        target_decision_log: DecisionLogEntry | None,
    ) -> DecideTargetReviewOutcome:
        from psycopg import errors as psycopg_errors

        conn, cur = self._serializable_authenticated_cursor(str(decision.user_id))
        try:
            # The same per-owner lock is taken by ordinary goal/target-policy
            # creation, making the currentness check and approved write one
            # serial order without granting UPDATE locks on immutable tables.
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (str(decision.user_id),),
            )
            existing = self._find_outcome(
                cur,
                user_id=decision.user_id,
                review_id=decision.review_id,
            )
            if existing is not None:
                if self._is_exact_replay(existing, decision):
                    conn.commit()
                    return replace(existing, created=False)
                raise TargetReviewDecisionConflictError(
                    "review already has a different terminal decision"
                )
            event_collision = self._find_outcome(
                cur,
                user_id=decision.user_id,
                client_event_id=decision.client_event_id,
            )
            if event_collision is not None:
                raise TargetReviewDecisionConflictError("idempotency key was already used")

            cur.execute(
                SqlTargetReviewRepository._SELECT + " WHERE user_id=%s AND review_id=%s",
                (str(decision.user_id), str(decision.review_id)),
            )
            review_row = cur.fetchone()
            if review_row is None:
                raise TargetReviewDecisionConflictError("target review is not owner-visible")
            persisted_review = SqlTargetReviewRepository._row_to_review(review_row)
            if persisted_review != review:
                raise TargetReviewDecisionConflictError("persisted review evidence changed")

            if decision.decision is TargetReviewDecisionValue.APPROVED:
                cur.execute(
                    """
                    SELECT version_id
                    FROM goal_policy_version
                    WHERE user_id=%s
                    ORDER BY created_at DESC, version_id DESC
                    LIMIT 1
                    """,
                    (str(decision.user_id),),
                )
                latest_goal = cur.fetchone()
                cur.execute(
                    """
                    SELECT version_id
                    FROM target_policy_version
                    WHERE user_id=%s
                    ORDER BY created_at DESC, version_id DESC
                    LIMIT 1
                    """,
                    (str(decision.user_id),),
                )
                latest_target = cur.fetchone()
                if (
                    latest_goal is None
                    or latest_target is None
                    or UUID(str(latest_goal[0])) != review.evaluation.goal_policy.version_id
                    or UUID(str(latest_target[0]))
                    != review.evaluation.prior_target_policy_version_id
                ):
                    raise StaleTargetReviewError("latest goal or target policy changed")
                if resulting_policy is None or target_decision_log is None:
                    raise TargetReviewDecisionConflictError("approval records are incomplete")
                if (
                    resulting_policy.user_id != decision.user_id
                    or resulting_policy.version_id != decision.resulting_target_policy_version_id
                    or target_decision_log.user_id != decision.user_id
                    or target_decision_log.policy_version_id != resulting_policy.version_id
                ):
                    raise TargetReviewDecisionConflictError("approval records are inconsistent")
                cur.execute(
                    """
                    INSERT INTO target_policy_version (
                        version_id,user_id,policy_version,goals,payload_sha256,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        str(resulting_policy.version_id),
                        str(resulting_policy.user_id),
                        resulting_policy.policy_version,
                        json.dumps(resulting_policy.goals_jsonb),
                        resulting_policy.payload_sha256,
                        resulting_policy.created_at,
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO decision_log (
                        decision_id,user_id,subject,decision,rationale,
                        policy_version_id,decided_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        str(target_decision_log.decision_id),
                        str(target_decision_log.user_id),
                        target_decision_log.subject,
                        target_decision_log.decision,
                        target_decision_log.rationale,
                        str(target_decision_log.policy_version_id),
                        target_decision_log.decided_at,
                    ),
                )
            elif resulting_policy is not None or target_decision_log is not None:
                raise TargetReviewDecisionConflictError(
                    "rejection cannot create target-policy records"
                )

            cur.execute(
                """
                INSERT INTO target_review_decision (
                    decision_id,user_id,review_id,decision,rationale,
                    resulting_target_policy_version_id,client_event_id,decided_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    str(decision.decision_id),
                    str(decision.user_id),
                    str(decision.review_id),
                    decision.decision.value,
                    decision.rationale,
                    (
                        str(decision.resulting_target_policy_version_id)
                        if decision.resulting_target_policy_version_id is not None
                        else None
                    ),
                    str(decision.client_event_id),
                    decision.decided_at,
                ),
            )
            outcome = self._find_outcome(
                cur,
                user_id=decision.user_id,
                review_id=decision.review_id,
                created=True,
            )
            if outcome is None:
                raise ValueError("inserted target review decision could not be reconstructed")
            conn.commit()
            return outcome
        except (psycopg_errors.UniqueViolation, psycopg_errors.SerializationFailure):
            conn.rollback()
            return self._replay_after_race(decision)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# M16A repositories are intentionally co-located with the existing owned SQL
# adapters so they use the same transaction-scoped authenticated role/RLS seam.
def _protein_proposal_from_row(row: tuple[object, ...]) -> ProteinTargetProposal:
    return ProteinTargetProposal(
        proposal_id=UUID(str(row[0])),
        user_id=UUID(str(row[1])),
        prior_target_policy_version_id=UUID(str(row[2])),
        body_mass_sample_uuid=UUID(str(row[3])),
        policy_version=str(row[4]),
        target_kind=GoalKind(str(row[5])),
        body_mass_kg=Decimal(str(row[6])),
        grams_per_pound=Decimal(str(row[7])),
        proposed_protein_g=Decimal(str(row[8])),
        evidence_digest=str(row[9]),
        calculation_payload=cast(dict[str, object], row[10]),
        rationale=str(row[11]),
        provenance=str(row[12]),
        generated_at=cast(datetime, row[13]),
    )


_PROTEIN_PROPOSAL_COLUMNS = """
proposal_id,user_id,prior_target_policy_version_id,body_mass_sample_uuid,
policy_version,target_kind,body_mass_kg,grams_per_pound,proposed_protein_g,
evidence_digest,calculation_payload,rationale,provenance,generated_at
"""


class SqlProteinTargetProposalRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _authenticated_cursor(self, user_sub: str) -> Any:
        conn = _connect(self._dsn)
        cur = conn.cursor()
        cur.execute("SET LOCAL ROLE authenticated")
        cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (user_sub,))
        return conn, cur

    def save(self, proposal: ProteinTargetProposal) -> tuple[ProteinTargetProposal, bool]:
        conn, cur = self._authenticated_cursor(str(proposal.user_id))
        try:
            cur.execute(
                f"""INSERT INTO protein_target_proposal ({_PROTEIN_PROPOSAL_COLUMNS})
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (user_id,evidence_digest) DO NOTHING
                RETURNING {_PROTEIN_PROPOSAL_COLUMNS}""",
                (
                    str(proposal.proposal_id),
                    str(proposal.user_id),
                    str(proposal.prior_target_policy_version_id),
                    str(proposal.body_mass_sample_uuid),
                    proposal.policy_version,
                    proposal.target_kind.value,
                    str(proposal.body_mass_kg),
                    str(proposal.grams_per_pound),
                    str(proposal.proposed_protein_g),
                    proposal.evidence_digest,
                    json.dumps(proposal.calculation_payload),
                    proposal.rationale,
                    proposal.provenance,
                    proposal.generated_at,
                ),
            )
            row = cur.fetchone()
            created = row is not None
            if row is None:
                cur.execute(
                    f"SELECT {_PROTEIN_PROPOSAL_COLUMNS} FROM protein_target_proposal "
                    "WHERE user_id=%s AND evidence_digest=%s",
                    (str(proposal.user_id), proposal.evidence_digest),
                )
                row = cur.fetchone()
            if row is None:
                raise DuplicateProteinProposalError("proposal replay could not be read")
            stored = _protein_proposal_from_row(row)
            conn.commit()
            return stored, created
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def latest(self, user_id: UUID) -> ProteinTargetProposal | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                f"SELECT {_PROTEIN_PROPOSAL_COLUMNS} FROM protein_target_proposal "
                "WHERE user_id=%s ORDER BY generated_at DESC,proposal_id DESC LIMIT 1",
                (str(user_id),),
            )
            row = cur.fetchone()
            conn.commit()
            return _protein_proposal_from_row(row) if row else None
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def find_by_id(self, user_id: UUID, proposal_id: UUID) -> ProteinTargetProposal | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                f"SELECT {_PROTEIN_PROPOSAL_COLUMNS} FROM protein_target_proposal "
                "WHERE user_id=%s AND proposal_id=%s",
                (str(user_id), str(proposal_id)),
            )
            row = cur.fetchone()
            conn.commit()
            return _protein_proposal_from_row(row) if row else None
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def find_decision(
        self, user_id: UUID, proposal_id: UUID
    ) -> ProteinTargetProposalDecision | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            value = self._existing_decision(cur, user_id, proposal_id)
            conn.commit()
            return value
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _existing_decision(
        self, cur: Any, user_id: UUID, proposal_id: UUID
    ) -> ProteinTargetProposalDecision | None:
        cur.execute(
            """SELECT decision_id,user_id,proposal_id,decision,rationale,client_event_id,
                      resulting_target_policy_version_id,decided_at
               FROM protein_target_proposal_decision WHERE user_id=%s AND proposal_id=%s""",
            (str(user_id), str(proposal_id)),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return ProteinTargetProposalDecision(
            decision_id=UUID(str(row[0])),
            user_id=UUID(str(row[1])),
            proposal_id=UUID(str(row[2])),
            decision=ProteinProposalDecisionValue(str(row[3])),
            rationale=str(row[4]),
            client_event_id=UUID(str(row[5])),
            resulting_target_policy_version_id=UUID(str(row[6])) if row[6] else None,
            decided_at=cast(datetime, row[7]),
        )

    def decide(
        self,
        proposal: ProteinTargetProposal,
        decision: ProteinTargetProposalDecision,
        resulting_policy: TargetPolicyVersion | None,
        target_decision_log: DecisionLogEntry | None,
    ) -> DecideProteinProposalOutcome:
        from psycopg import errors as psycopg_errors

        conn, cur = self._authenticated_cursor(str(decision.user_id))
        try:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(decision.user_id),)
            )
            existing = self._existing_decision(cur, decision.user_id, decision.proposal_id)
            if existing is not None:
                if (
                    existing.decision is decision.decision
                    and existing.client_event_id == decision.client_event_id
                    and existing.rationale == decision.rationale
                ):
                    conn.commit()
                    return DecideProteinProposalOutcome(existing, False)
                raise ProteinProposalDecisionConflictError("proposal already decided")
            if resulting_policy is not None and target_decision_log is not None:
                cur.execute(
                    """SELECT version_id FROM target_policy_version
                       WHERE user_id=%s
                       ORDER BY created_at DESC,version_id DESC LIMIT 1""",
                    (str(decision.user_id),),
                )
                current = cur.fetchone()
                if (
                    current is None
                    or UUID(str(current[0])) != proposal.prior_target_policy_version_id
                ):
                    raise StaleProteinProposalError("approved target changed after proposal")
                cur.execute(
                    """INSERT INTO target_policy_version
                    (version_id,user_id,policy_version,goals,payload_sha256,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s)""",
                    (
                        str(resulting_policy.version_id),
                        str(resulting_policy.user_id),
                        resulting_policy.policy_version,
                        json.dumps(resulting_policy.goals_jsonb),
                        resulting_policy.payload_sha256,
                        resulting_policy.created_at,
                    ),
                )
                cur.execute(
                    """INSERT INTO decision_log
                    (decision_id,user_id,subject,decision,rationale,policy_version_id,decided_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        str(target_decision_log.decision_id),
                        str(target_decision_log.user_id),
                        target_decision_log.subject,
                        target_decision_log.decision,
                        target_decision_log.rationale,
                        str(target_decision_log.policy_version_id),
                        target_decision_log.decided_at,
                    ),
                )
            elif resulting_policy is not None or target_decision_log is not None:
                raise ProteinProposalDecisionConflictError("incomplete approval artifacts")
            cur.execute(
                """INSERT INTO protein_target_proposal_decision
                (decision_id,user_id,proposal_id,decision,rationale,
                 resulting_target_policy_version_id,client_event_id,decided_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    str(decision.decision_id),
                    str(decision.user_id),
                    str(decision.proposal_id),
                    decision.decision.value,
                    decision.rationale,
                    str(decision.resulting_target_policy_version_id)
                    if decision.resulting_target_policy_version_id
                    else None,
                    str(decision.client_event_id),
                    decision.decided_at,
                ),
            )
            conn.commit()
            return DecideProteinProposalOutcome(decision, True)
        except (psycopg_errors.UniqueViolation, psycopg_errors.SerializationFailure) as exc:
            conn.rollback()
            cur.execute("SET LOCAL ROLE authenticated")
            cur.execute(
                "SELECT set_config('request.jwt.claim.sub', %s, true)",
                (str(decision.user_id),),
            )
            existing = self._existing_decision(cur, decision.user_id, decision.proposal_id)
            if (
                existing is not None
                and existing.decision is decision.decision
                and existing.client_event_id == decision.client_event_id
                and existing.rationale == decision.rationale
            ):
                conn.commit()
                return DecideProteinProposalOutcome(existing, False)
            conn.rollback()
            raise ProteinProposalDecisionConflictError("proposal decision conflict") from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


_NEXT_MEAL_COLUMNS = """recommendation_id,user_id,client_request_id,local_date,timezone,
decision_at,target_policy_version_id,status,reason_codes,inputs_digest,artifact_jsonb,
artifact_sha256,created_at"""


def _next_meal_from_row(row: tuple[object, ...]) -> NextMealRecommendation:
    return NextMealRecommendation(
        recommendation_id=UUID(str(row[0])),
        user_id=UUID(str(row[1])),
        client_request_id=UUID(str(row[2])),
        local_date=cast(date, row[3]),
        timezone=str(row[4]),
        decision_at=cast(datetime, row[5]),
        target_policy_version_id=UUID(str(row[6])) if row[6] else None,
        status=NextMealStatus(str(row[7])),
        reason_codes=tuple(cast(Sequence[str], row[8] or ())),
        inputs_digest=str(row[9]),
        artifact_jsonb=cast(dict[str, object], row[10]),
        artifact_sha256=str(row[11]),
        created_at=cast(datetime, row[12]),
    )


class SqlNextMealRecommendationRepository(NextMealRecommendationRepository):
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _authenticated_cursor(self, user_sub: str) -> Any:
        conn = _connect(self._dsn)
        cur = conn.cursor()
        cur.execute("SET LOCAL ROLE authenticated")
        cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (user_sub,))
        return conn, cur

    def save(self, recommendation: NextMealRecommendation) -> PersistNextMealOutcome:
        conn, cur = self._authenticated_cursor(str(recommendation.user_id))
        try:
            cur.execute(
                f"""INSERT INTO next_meal_recommendation ({_NEXT_MEAL_COLUMNS})
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (user_id,client_request_id) DO NOTHING
                RETURNING {_NEXT_MEAL_COLUMNS}""",
                (
                    str(recommendation.recommendation_id),
                    str(recommendation.user_id),
                    str(recommendation.client_request_id),
                    recommendation.local_date,
                    recommendation.timezone,
                    recommendation.decision_at,
                    str(recommendation.target_policy_version_id)
                    if recommendation.target_policy_version_id
                    else None,
                    recommendation.status.value,
                    list(recommendation.reason_codes),
                    recommendation.inputs_digest,
                    json.dumps(recommendation.artifact_jsonb),
                    recommendation.artifact_sha256,
                    recommendation.created_at,
                ),
            )
            row = cur.fetchone()
            created = row is not None
            if row is None:
                cur.execute(
                    f"""SELECT {_NEXT_MEAL_COLUMNS}
                        FROM next_meal_recommendation
                        WHERE user_id=%s AND client_request_id=%s""",
                    (str(recommendation.user_id), str(recommendation.client_request_id)),
                )
                row = cur.fetchone()
            if row is None:
                raise DuplicateNextMealRecommendationError("recommendation replay unavailable")
            stored = _next_meal_from_row(row)
            if (
                stored.local_date != recommendation.local_date
                or stored.timezone != recommendation.timezone
            ):
                raise DuplicateNextMealRecommendationError("client request input conflict")
            conn.commit()
            return PersistNextMealOutcome(stored, created)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def find_by_client_request_id(
        self, user_id: UUID, client_request_id: UUID
    ) -> NextMealRecommendation | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                f"""SELECT {_NEXT_MEAL_COLUMNS}
                    FROM next_meal_recommendation
                    WHERE user_id=%s AND client_request_id=%s""",
                (str(user_id), str(client_request_id)),
            )
            row = cur.fetchone()
            conn.commit()
            return _next_meal_from_row(row) if row else None
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def latest(self, user_id: UUID) -> NextMealRecommendation | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                f"""SELECT {_NEXT_MEAL_COLUMNS}
                    FROM next_meal_recommendation WHERE user_id=%s
                    ORDER BY created_at DESC,recommendation_id DESC LIMIT 1""",
                (str(user_id),),
            )
            row = cur.fetchone()
            conn.commit()
            return _next_meal_from_row(row) if row else None
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def find_by_id(self, user_id: UUID, recommendation_id: UUID) -> NextMealRecommendation | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                f"""SELECT {_NEXT_MEAL_COLUMNS}
                    FROM next_meal_recommendation
                    WHERE user_id=%s AND recommendation_id=%s""",
                (str(user_id), str(recommendation_id)),
            )
            row = cur.fetchone()
            conn.commit()
            return _next_meal_from_row(row) if row else None
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


_NEXT_MEAL_CONSUMPTION_COLUMNS = """entry_id,user_id,recommendation_id,client_event_id,
local_date,timezone,recorded_at,recommendation_artifact_sha256,next_meal_policy_version,
meal_context,menu_period,candidate_id,item_name,serving_description,configuration_summary,
nutrition_authority,nutrition_confidence,calories_kcal,protein_g,unknown_nutrients,
selected_candidate_jsonb,selected_candidate_sha256"""


def _next_meal_consumption_from_row(row: Sequence[Any]) -> NextMealConsumptionEntry:
    return NextMealConsumptionEntry(
        entry_id=UUID(str(row[0])),
        user_id=UUID(str(row[1])),
        recommendation_id=UUID(str(row[2])),
        client_event_id=UUID(str(row[3])),
        local_date=cast(date, row[4]),
        timezone=str(row[5]),
        recorded_at=cast(datetime, row[6]),
        recommendation_artifact_sha256=str(row[7]),
        next_meal_policy_version=str(row[8]),
        meal_context=str(row[9]),
        menu_period=str(row[10]),
        candidate_id=str(row[11]),
        item_name=str(row[12]),
        serving_description=str(row[13]),
        configuration_summary=str(row[14]) if row[14] is not None else None,
        nutrition_authority=NutritionAuthority(str(row[15])),
        nutrition_confidence=str(row[16]) if row[16] is not None else None,
        calories_kcal=cast(Decimal | None, row[17]),
        protein_g=cast(Decimal | None, row[18]),
        unknown_nutrients=tuple(cast(Sequence[str], row[19] or ())),
        selected_candidate_jsonb=cast(dict[str, object], row[20]),
        selected_candidate_sha256=str(row[21]),
    )


class SqlNextMealConsumptionRepository(_OwnedRepositoryBase, NextMealConsumptionRepository):
    """Append-only M16B snapshots under the authenticated owner boundary."""

    @staticmethod
    def _parameters(entry: NextMealConsumptionEntry) -> tuple[object, ...]:
        return (
            str(entry.entry_id),
            str(entry.user_id),
            str(entry.recommendation_id),
            str(entry.client_event_id),
            entry.local_date,
            entry.timezone,
            entry.recorded_at,
            entry.recommendation_artifact_sha256,
            entry.next_meal_policy_version,
            entry.meal_context,
            entry.menu_period,
            entry.candidate_id,
            entry.item_name,
            entry.serving_description,
            entry.configuration_summary,
            entry.nutrition_authority.value,
            entry.nutrition_confidence,
            entry.calories_kcal,
            entry.protein_g,
            list(entry.unknown_nutrients),
            json.dumps(entry.selected_candidate_jsonb),
            entry.selected_candidate_sha256,
        )

    @staticmethod
    def _find_conflicts(cur: Any, entry: NextMealConsumptionEntry) -> list[Sequence[Any]]:
        cur.execute(
            f"""SELECT {_NEXT_MEAL_CONSUMPTION_COLUMNS}
                FROM next_meal_consumption
                WHERE user_id=%s AND (client_event_id=%s OR recommendation_id=%s)
                ORDER BY entry_id""",
            (
                str(entry.user_id),
                str(entry.client_event_id),
                str(entry.recommendation_id),
            ),
        )
        return list(cur.fetchall())

    @staticmethod
    def _replay_or_conflict(
        rows: Sequence[Sequence[Any]], entry: NextMealConsumptionEntry
    ) -> RecordNextMealConsumptionOutcome | None:
        if not rows:
            return None
        if len(rows) != 1:
            raise DuplicateNextMealConsumptionError("next-meal consumption keys conflict")
        original = _next_meal_consumption_from_row(rows[0])
        if original.request_facts() != entry.request_facts():
            raise DuplicateNextMealConsumptionError("next-meal consumption conflicts")
        return RecordNextMealConsumptionOutcome(original, False)

    def save(self, entry: NextMealConsumptionEntry) -> RecordNextMealConsumptionOutcome:
        conn, cur = self._authenticated_cursor(str(entry.user_id))
        try:
            replay = self._replay_or_conflict(self._find_conflicts(cur, entry), entry)
            if replay is not None:
                conn.commit()
                return replay
            cur.execute(
                f"""INSERT INTO next_meal_consumption (
                    {_NEXT_MEAL_CONSUMPTION_COLUMNS},state)
                    VALUES ({",".join(["%s"] * 22)},'eaten')
                    ON CONFLICT DO NOTHING
                    RETURNING {_NEXT_MEAL_CONSUMPTION_COLUMNS}""",
                self._parameters(entry),
            )
            row = cur.fetchone()
            if row is not None:
                persisted = _next_meal_consumption_from_row(row)
                conn.commit()
                return RecordNextMealConsumptionOutcome(persisted, True)
            replay = self._replay_or_conflict(self._find_conflicts(cur, entry), entry)
            if replay is None:
                raise DuplicateNextMealConsumptionError("next-meal consumption entry ID conflicts")
            conn.commit()
            return replay
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def find_for_recommendation(
        self, user_id: UUID, recommendation_id: UUID
    ) -> NextMealConsumptionEntry | None:
        conn, cur = self._authenticated_cursor(str(user_id))
        try:
            cur.execute(
                f"""SELECT {_NEXT_MEAL_CONSUMPTION_COLUMNS}
                    FROM next_meal_consumption
                    WHERE user_id=%s AND recommendation_id=%s""",
                (str(user_id), str(recommendation_id)),
            )
            row = cur.fetchone()
            conn.commit()
            return _next_meal_consumption_from_row(row) if row else None
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
