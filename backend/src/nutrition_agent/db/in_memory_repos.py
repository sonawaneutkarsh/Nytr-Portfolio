"""In-memory repository implementations mirroring the SQL constraints.

Used by unit/integration tests and the fixture-only CLI dry run. Uniqueness
semantics match migrations/0001_stacks_ingestion.sql so tests exercise the
same idempotency behavior the database enforces.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from uuid import NAMESPACE_OID, UUID, uuid4, uuid5

from nutrition_agent.application.ports import (
    BodyGoalsRepository,
    BodyMassHistoryRepository,
    ConsumptionRepository,
    CustomFoodRepository,
    DetailedTrainingAnalyticsRepository,
    DetailedTrainingRepository,
    DetailedTrainingRevisionConflictError,
    DetailedTrainingSyncRepository,
    DuplicateConsumptionError,
    DuplicateLogicalPlanError,
    DuplicateManualFoodError,
    DuplicateNextMealConsumptionError,
    DuplicateNextMealRecommendationError,
    DuplicateTargetReviewError,
    FoodRepository,
    GoalPolicyRepository,
    GoalPolicyVersionExistsError,
    HealthBodyMassRepository,
    MenuDayReadRepository,
    MenuPageOfferingPin,
    MenuPageValidationState,
    MenuPageVersionRepository,
    NextMealConsumptionRepository,
    NextMealRecommendationRepository,
    NutritionAuthorityKey,
    OfferingRepository,
    PageLabelAuthorityKey,
    PersistedPlanItemReference,
    PersistedPlanView,
    PersistMenuPageOutcome,
    PlanRunRepository,
    ProfileRepository,
    ProteinProposalDecisionConflictError,
    ProteinTargetProposalRepository,
    QuarantineRepository,
    ResolvedMenuDay,
    ReusableNutritionAuthority,
    ReusablePageLabelAuthority,
    RunRepository,
    SnapshotRepository,
    StaleProteinProposalError,
    StaleTargetReviewError,
    TargetPolicyRepository,
    TargetPolicyVersionExistsError,
    TargetReviewDecisionConflictError,
    TargetReviewDecisionRepository,
    TargetReviewRepository,
    TrainingSessionRepository,
    ValidatedMenuLabelObservation,
    ValidatedMenuPage,
)
from nutrition_agent.domain.body_goals import (
    BodyGoalProfileVersion,
    StartingCalorieProposal,
    StartingTargetDecision,
    WaistMeasurement,
)
from nutrition_agent.domain.consumption import (
    ConsumptionEntry,
    ConsumptionState,
    RecordConsumptionOutcome,
)
from nutrition_agent.domain.health.entities import (
    BatchOutcome,
    HealthSyncStatus,
    LatestSample,
    StoredSample,
    SyncBatch,
)
from nutrition_agent.domain.health.trend import BodyMassObservation
from nutrition_agent.domain.next_meal import (
    NextMealRecommendation,
    PersistNextMealOutcome,
)
from nutrition_agent.domain.next_meal_consumption import (
    NextMealConsumptionEntry,
    RecordNextMealConsumptionOutcome,
)
from nutrition_agent.domain.nutrition.custom_foods import (
    AdjustManualFoodOutcome,
    CustomFoodVersion,
    ManualFoodAdjustmentKind,
    ManualFoodConsumptionAdjustment,
    ManualFoodConsumptionEntry,
    RecordManualFoodOutcome,
)
from nutrition_agent.domain.planning.artifacts import (
    DecisionLogEntry,
    PlanItem,
    PlanRun,
    PlanVersion,
    TargetPolicyVersion,
)
from nutrition_agent.domain.protein_target import (
    DecideProteinProposalOutcome,
    ProteinTargetProposal,
    ProteinTargetProposalDecision,
)
from nutrition_agent.domain.stacks.entities import (
    MealPeriod,
    MenuOffering,
    NutritionProfile,
    NutritionSourceState,
)
from nutrition_agent.domain.stacks.ingestion import IngestionRun, QuarantineRecord
from nutrition_agent.domain.target_review import (
    DecideTargetReviewOutcome,
    GoalPolicyVersion,
    PersistTargetReviewOutcome,
    TargetReview,
    TargetReviewDecision,
    TargetReviewDecisionValue,
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
    DetailedTrainingSessionPage,
    DetailedTrainingSessionSummary,
    DetailedTrainingSourceSystem,
    DetailedTrainingSyncCheckpoint,
    DetailedTrainingSyncMode,
    StoredDetailedTrainingSession,
)
from nutrition_agent.infrastructure.snapshot_store import SnapshotRef


class DuplicateKeyError(Exception):
    pass


@dataclass
class InMemoryRunRepository(RunRepository):
    runs: dict[UUID, IngestionRun] = field(default_factory=dict)

    def create(self, run: IngestionRun) -> None:
        self.runs[run.run_id] = run

    def update(self, run: IngestionRun) -> None:
        self.runs[run.run_id] = run


@dataclass
class InMemorySnapshotRepository(SnapshotRepository):
    recorded: list[tuple[SnapshotRef, str]] = field(default_factory=list)
    _by_content_sha256: dict[str, tuple[SnapshotRef, str]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def record(self, ref: SnapshotRef, parser_version: str) -> SnapshotRef:
        existing = self._by_content_sha256.get(ref.content_sha256)
        if existing is not None:
            return existing[0]
        self.recorded.append((ref, parser_version))
        self._by_content_sha256[ref.content_sha256] = (ref, parser_version)
        return ref

    def find_latest_by_request(
        self, source_url: str, method: str, request_params: dict[str, str]
    ) -> tuple[SnapshotRef, str] | None:
        matches = [
            (ref, version)
            for ref, version in self.recorded
            if ref.source_url == source_url
            and ref.method == method
            and ref.request_params == request_params
        ]
        return matches[-1] if matches else None


@dataclass
class InMemoryFoodRepository(FoodRepository):
    foods: dict[UUID, dict[str, object]] = field(default_factory=dict)
    _by_key: dict[tuple[int, str], UUID] = field(default_factory=dict)

    def upsert_by_name(
        self, campus_id: int, name_raw: str, name_normalized: str
    ) -> tuple[UUID, bool]:
        key = (campus_id, name_normalized)
        existing = self._by_key.get(key)
        if existing is not None:
            row = self.foods[existing]
            row["name_raw"] = name_raw
            row["last_seen_at"] = datetime.now()
            return existing, False
        food_id = UUID(int=len(self.foods) + 1)
        self._by_key[key] = food_id
        self.foods[food_id] = {
            "campus_id": campus_id,
            "name_raw": name_raw,
            "name_normalized": name_normalized,
        }
        return food_id, True

    def get_by_name(self, campus_id: int, name_normalized: str) -> UUID | None:
        return self._by_key.get((campus_id, name_normalized))


@dataclass
class InMemoryOfferingRepository(OfferingRepository):
    offerings: dict[UUID, MenuOffering] = field(default_factory=dict)
    _by_natural: dict[tuple[date, MealPeriod, int, UUID, int], UUID] = field(default_factory=dict)

    def upsert(self, offering: MenuOffering) -> tuple[UUID, bool]:
        key = (
            offering.service_date,
            offering.meal_period,
            offering.campus_id,
            offering.food_id,
            offering.occurrence_ordinal,
        )
        existing_id = self._by_natural.get(key)
        if existing_id is not None:
            stored = self.offerings[existing_id]
            updated = MenuOffering(
                offering_id=existing_id,
                service_date=stored.service_date,
                meal_period=stored.meal_period,
                campus_id=stored.campus_id,
                food_id=stored.food_id,
                occurrence_ordinal=stored.occurrence_ordinal,
                category_name=offering.category_name,
                category_position=offering.category_position,
                item_position=offering.item_position,
                source_mid=offering.source_mid,
                dietary_tags=offering.dietary_tags,
                profile_id=stored.profile_id,
                snapshot_id=offering.snapshot_id,
            )
            self.offerings[existing_id] = updated
            return existing_id, False
        self._by_natural[key] = offering.offering_id
        self.offerings[offering.offering_id] = offering
        return offering.offering_id, True

    def link_profile(self, offering_id: UUID, profile_id: UUID) -> None:
        stored = self.offerings[offering_id]
        self.offerings[offering_id] = MenuOffering(
            offering_id=stored.offering_id,
            service_date=stored.service_date,
            meal_period=stored.meal_period,
            campus_id=stored.campus_id,
            food_id=stored.food_id,
            occurrence_ordinal=stored.occurrence_ordinal,
            category_name=stored.category_name,
            category_position=stored.category_position,
            item_position=stored.item_position,
            source_mid=stored.source_mid,
            dietary_tags=stored.dietary_tags,
            profile_id=profile_id,
            snapshot_id=stored.snapshot_id,
        )

    def count_for_page(self, service_date: date, meal_period: MealPeriod) -> int:
        return sum(
            1
            for o in self.offerings.values()
            if o.service_date == service_date and o.meal_period == meal_period
        )


@dataclass
class InMemoryMenuDayReadRepository(MenuDayReadRepository):
    """Fixture/test implementation of the resolved menu-day read boundary."""

    days: dict[date, ResolvedMenuDay] = field(default_factory=dict)

    def get_for_date(self, service_date: date) -> ResolvedMenuDay | None:
        return self.days.get(service_date)


@dataclass
class InMemoryProfileRepository(ProfileRepository):
    profiles: dict[UUID, NutritionProfile] = field(default_factory=dict)
    _refs: dict[tuple[UUID, str], UUID] = field(default_factory=dict)

    def insert_version(self, profile: NutritionProfile, snapshot: SnapshotRef) -> UUID:
        key = (profile.food_id, snapshot.content_sha256)
        existing = self._refs.get(key)
        if existing is not None:
            return existing
        profile_id = UUID(uuid5(NAMESPACE_OID, f"{profile.food_id}:{snapshot.content_sha256}").hex)
        self._refs[key] = profile_id
        self.profiles[profile_id] = profile
        return profile_id

    def latest_for_food(self, food_id: UUID) -> NutritionProfile | None:
        matches = [p for p in self.profiles.values() if p.food_id == food_id]
        return matches[-1] if matches else None


@dataclass
class InMemoryMenuPageVersionRepository(MenuPageVersionRepository):
    """In-memory mirror of accepted pages and resumable label evidence."""

    foods: InMemoryFoodRepository
    offerings: InMemoryOfferingRepository
    profiles: InMemoryProfileRepository
    page_versions: dict[UUID, ValidatedMenuPage] = field(default_factory=dict)
    memberships: dict[UUID, tuple[MenuPageOfferingPin, ...]] = field(default_factory=dict)
    label_observations: dict[UUID, ValidatedMenuLabelObservation] = field(default_factory=dict)
    _label_observation_keys: dict[tuple[object, ...], UUID] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )

    def has_accepted_observation(
        self,
        ingestion_run_id: UUID,
        service_date: date,
        meal_period: MealPeriod,
        campus_id: int,
    ) -> bool:
        return any(
            page.ingestion_run_id == ingestion_run_id
            and page.service_date == service_date
            and page.meal_period is meal_period
            and page.campus_id == campus_id
            for page in self.page_versions.values()
        )

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
        found: dict[NutritionAuthorityKey, ReusableNutritionAuthority] = {}
        seen: set[NutritionAuthorityKey] = set()
        pages = sorted(
            self.page_versions.values(),
            key=lambda page: (page.accepted_at, page.page_version_id),
            reverse=True,
        )
        for page in pages:
            pins = self.memberships.get(page.page_version_id, ())
            if len(pins) != len(page.offerings):
                raise ValueError("accepted page membership count mismatch")
            for prepared, pin in zip(page.offerings, pins, strict=True):
                key = NutritionAuthorityKey(
                    campus_id=page.campus_id,
                    name_normalized=pin.name_normalized,
                    source_mid=pin.source_mid,
                )
                if key not in requested or key in seen:
                    continue
                # A newer accepted non-profile/stale pin must prevent fallback
                # to an older profile for the same exact source identity.
                seen.add(key)
                if (
                    page.parser_version != parser_version
                    or pin.nutrition_source_state is not NutritionSourceState.PROFILE_AVAILABLE
                    or pin.profile_id is None
                    or prepared.profile_snapshot is None
                ):
                    continue
                profile = self.profiles.profiles.get(pin.profile_id)
                snapshot = prepared.profile_snapshot
                if (
                    profile is None
                    or profile.food_id != pin.food_id
                    or profile.provenance.parser_version != parser_version
                    or snapshot.snapshot_id != pin.nutrition_snapshot_id
                    or snapshot.fetched_at < fetched_not_before
                ):
                    continue
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
        found: dict[PageLabelAuthorityKey, ReusablePageLabelAuthority] = {}
        seen: set[PageLabelAuthorityKey] = set()
        observations = sorted(
            self.label_observations.values(),
            key=lambda observation: (observation.recorded_at, observation.observation_id),
            reverse=True,
        )
        for observation in observations:
            key = observation.key
            if key not in requested or key in seen:
                continue
            seen.add(key)
            if (
                observation.parser_version != parser_version
                or observation.nutrition_snapshot.fetched_at < fetched_not_before
                or (
                    observation.profile is not None
                    and observation.profile.provenance.parser_version != parser_version
                )
            ):
                continue
            try:
                found[key] = ReusablePageLabelAuthority(
                    key=key,
                    nutrition_source_state=observation.nutrition_source_state,
                    profile=observation.profile,
                    nutrition_snapshot=observation.nutrition_snapshot,
                )
            except ValueError:
                continue
        return tuple(found[key] for key in sorted(found))

    def persist_label_observation(
        self, observation: ValidatedMenuLabelObservation
    ) -> ReusablePageLabelAuthority:
        food_id, _ = self.foods.upsert_by_name(
            observation.key.campus_id,
            observation.food_name_raw,
            observation.key.name_normalized,
        )
        profile: NutritionProfile | None = None
        profile_id: UUID | None = None
        if observation.profile is not None:
            profile = replace(observation.profile, food_id=food_id)
            profile_id = self.profiles.insert_version(
                profile,
                observation.nutrition_snapshot,
            )
            profile = self.profiles.profiles[profile_id]
        stored = replace(observation, food_id=food_id, profile=profile)
        unique_key = self._label_observation_unique_key(stored)
        existing_id = self._label_observation_keys.get(unique_key)
        if existing_id is not None:
            existing = self.label_observations[existing_id]
            if (
                existing.food_id != stored.food_id
                or existing.nutrition_source_state is not stored.nutrition_source_state
                or existing.profile != stored.profile
                or existing.nutrition_snapshot.snapshot_id != stored.nutrition_snapshot.snapshot_id
            ):
                raise DuplicateKeyError("label-observation evidence conflicts")
            stored = existing
        else:
            self._label_observation_keys[unique_key] = stored.observation_id
            self.label_observations[stored.observation_id] = stored
        return ReusablePageLabelAuthority(
            key=stored.key,
            nutrition_source_state=stored.nutrition_source_state,
            profile=stored.profile,
            nutrition_snapshot=stored.nutrition_snapshot,
        )

    @staticmethod
    def _label_observation_unique_key(
        observation: ValidatedMenuLabelObservation,
    ) -> tuple[object, ...]:
        key = observation.key
        return (
            key.service_date,
            key.meal_period,
            key.campus_id,
            key.name_normalized,
            key.occurrence_ordinal,
            key.source_mid,
            observation.parser_version,
            observation.nutrition_snapshot.snapshot_id,
        )

    def persist_validated_page(self, page: ValidatedMenuPage) -> PersistMenuPageOutcome:
        if self.has_accepted_observation(
            page.ingestion_run_id,
            page.service_date,
            page.meal_period,
            page.campus_id,
        ):
            raise DuplicateKeyError("accepted menu page observation already exists for run/page")

        backup = deepcopy(
            (
                self.foods.foods,
                self.foods._by_key,
                self.offerings.offerings,
                self.offerings._by_natural,
                self.profiles.profiles,
                self.profiles._refs,
                self.page_versions,
                self.memberships,
            )
        )
        inserted = updated = profiles_persisted = 0
        membership: list[MenuPageOfferingPin] = []
        try:
            for prepared in page.offerings:
                candidate = prepared.offering
                food_id, _ = self.foods.upsert_by_name(
                    candidate.campus_id,
                    prepared.food_name_raw,
                    prepared.food_name_normalized,
                )
                profile_id: UUID | None = None
                if prepared.profile is not None and prepared.profile_snapshot is not None:
                    profile = replace(prepared.profile, food_id=food_id)
                    profile_id = self.profiles.insert_version(
                        profile,
                        prepared.profile_snapshot,
                    )
                    profiles_persisted += 1
                stored_id, was_inserted = self.offerings.upsert(replace(candidate, food_id=food_id))
                if profile_id is not None:
                    self.offerings.link_profile(stored_id, profile_id)
                source_state = prepared.nutrition_source_state
                nutrition_snapshot = prepared.nutrition_snapshot
                if source_state is None or nutrition_snapshot is None:
                    raise ValueError("accepted offering requires classified nutrition provenance")
                membership.append(
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

            if page.validation_state is MenuPageValidationState.VALIDATED_EMPTY:
                assert membership == []
            else:
                assert len(membership) == page.offering_count
            self.memberships[page.page_version_id] = tuple(membership)
            self.page_versions[page.page_version_id] = page
        except Exception:
            (
                self.foods.foods,
                self.foods._by_key,
                self.offerings.offerings,
                self.offerings._by_natural,
                self.profiles.profiles,
                self.profiles._refs,
                self.page_versions,
                self.memberships,
            ) = backup
            raise

        return PersistMenuPageOutcome(
            inserted_offerings=inserted,
            updated_offerings=updated,
            profiles_persisted=profiles_persisted,
        )


@dataclass
class InMemoryQuarantineRepository(QuarantineRepository):
    records: list[QuarantineRecord] = field(default_factory=list)

    def add(self, record: QuarantineRecord) -> None:
        self.records.append(record)

    def list_by_run(self, run_id: UUID) -> list[QuarantineRecord]:
        return [r for r in self.records if r.run_id == run_id]


@dataclass
class InMemoryHealthBodyMassRepository(HealthBodyMassRepository, BodyMassHistoryRepository):
    """Mirrors migration 0002 semantics exactly (no DELETE, tombstones).

    Semantics under test here are the same the SQL implementation enforces:
    - insert-if-absent on (user_id, sample_uuid): duplicates are no-ops;
    - adds never overwrite an existing row's measurement (first add wins) and
      never resurrect a tombstone;
    - deletions insert tombstone-only rows when the sample is unknown and
      COALESCE tombstoned_at otherwise (first deletion timestamp wins);
    - status counts: record_count counts rows carrying a real measurement
      (including later-tombstoned ones), tombstone_count counts tombstones.
    """

    rows: dict[tuple[UUID, UUID], StoredSample] = field(default_factory=dict)
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def apply_batch(self, user_id: UUID, batch: SyncBatch) -> BatchOutcome:
        accepted = duplicates = applied = dup_deletions = 0
        for sample in batch.added:
            key = (user_id, sample.sample_uuid)
            if key in self.rows:
                duplicates += 1
                continue
            self.rows[key] = StoredSample(
                user_id=user_id,
                sample_uuid=sample.sample_uuid,
                value_kg=sample.value_kg,
                sample_start=sample.sample_start,
                sample_end=sample.sample_end,
                source_name=sample.source_name,
                source_bundle_id=sample.source_bundle_id,
                ingested_at=self.clock(),
                tombstoned_at=None,
            )
            accepted += 1
        for deletion in batch.deletions:
            key = (user_id, deletion.sample_uuid)
            existing = self.rows.get(key)
            if existing is None:
                self.rows[key] = StoredSample(
                    user_id=user_id,
                    sample_uuid=deletion.sample_uuid,
                    value_kg=None,
                    sample_start=None,
                    sample_end=None,
                    source_name=None,
                    source_bundle_id=None,
                    ingested_at=self.clock(),
                    tombstoned_at=self.clock(),
                )
                applied += 1
            elif existing.tombstoned_at is None:
                self.rows[key] = replace(
                    existing, tombstoned_at=self.clock(), ingested_at=existing.ingested_at
                )
                applied += 1
            else:
                dup_deletions += 1
        return BatchOutcome(
            accepted_added=accepted,
            duplicate_added=duplicates,
            applied_deletions=applied,
            duplicate_deletions=dup_deletions,
        )

    def status_summary(self, user_id: UUID) -> HealthSyncStatus:
        mine = [row for (uid, _), row in self.rows.items() if uid == user_id]
        measured = [r for r in mine if r.value_kg is not None]
        tombstones = [r for r in mine if r.tombstoned_at is not None]
        candidates: list[LatestSample] = []
        for row in measured:
            if row.tombstoned_at is None and row.sample_start is not None:
                value_kg = row.value_kg
                if value_kg is not None:
                    candidates.append(
                        LatestSample(
                            sample_uuid=row.sample_uuid,
                            sample_start=row.sample_start,
                            value_kg=value_kg,
                        )
                    )
        latest = max(candidates, key=lambda s: s.sample_start) if candidates else None
        last_ingested = max((r.ingested_at for r in mine), default=None)
        return HealthSyncStatus(
            record_count=len(measured),
            tombstone_count=len(tombstones),
            latest_sample=latest,
            last_ingested_at=last_ingested,
        )

    def list_active(
        self,
        user_id: UUID,
        start_inclusive: datetime,
        end_exclusive: datetime,
    ) -> tuple[BodyMassObservation, ...]:
        observations = [
            BodyMassObservation(
                sample_uuid=row.sample_uuid,
                value_kg=row.value_kg,
                measured_at=row.sample_start,
            )
            for (owner_id, _), row in self.rows.items()
            if owner_id == user_id
            and row.tombstoned_at is None
            and row.value_kg is not None
            and row.sample_start is not None
            and start_inclusive <= row.sample_start < end_exclusive
        ]
        return tuple(
            sorted(
                observations,
                key=lambda item: (item.measured_at.astimezone(UTC), str(item.sample_uuid)),
            )
        )


@dataclass
class InMemoryBodyGoalsRepository(BodyGoalsRepository):
    target_repository: TargetPolicyRepository | None = None
    profiles: dict[UUID, BodyGoalProfileVersion] = field(default_factory=dict)
    waist: dict[UUID, WaistMeasurement] = field(default_factory=dict)
    proposals: dict[UUID, StartingCalorieProposal] = field(default_factory=dict)
    decisions: dict[UUID, StartingTargetDecision] = field(default_factory=dict)
    _decision_events: dict[tuple[UUID, UUID], UUID] = field(default_factory=dict)

    def save_profile(self, profile: BodyGoalProfileVersion) -> BodyGoalProfileVersion:
        if profile.profile_id in self.profiles:
            raise ValueError("profile version already exists")
        for existing in self.profiles.values():
            if (
                existing.user_id == profile.user_id
                and existing.payload_sha256 == profile.payload_sha256
            ):
                return existing
        self.profiles[profile.profile_id] = profile
        return profile

    def latest_profile(self, user_id: UUID) -> BodyGoalProfileVersion | None:
        values = [item for item in self.profiles.values() if item.user_id == user_id]
        return max(values, key=lambda item: (item.created_at, item.profile_id)) if values else None

    def save_waist(self, measurement: WaistMeasurement) -> None:
        if measurement.measurement_id in self.waist:
            raise ValueError("waist measurement already exists")
        if measurement.corrects_measurement_id is not None:
            prior = self.waist.get(measurement.corrects_measurement_id)
            if prior is None or prior.user_id != measurement.user_id:
                raise ValueError("invalid waist correction")
            if any(
                item.corrects_measurement_id == prior.measurement_id for item in self.waist.values()
            ):
                raise ValueError("waist measurement already corrected")
        self.waist[measurement.measurement_id] = measurement

    def list_active_waist(self, user_id: UUID) -> tuple[WaistMeasurement, ...]:
        corrected = {
            item.corrects_measurement_id
            for item in self.waist.values()
            if item.corrects_measurement_id
        }
        return tuple(
            sorted(
                (
                    item
                    for item in self.waist.values()
                    if item.user_id == user_id and item.measurement_id not in corrected
                ),
                key=lambda item: (item.measured_at, item.measurement_id),
            )
        )

    def save_starting_proposal(
        self, proposal: StartingCalorieProposal
    ) -> tuple[StartingCalorieProposal, bool]:
        for existing in self.proposals.values():
            if (
                existing.user_id == proposal.user_id
                and existing.evidence_sha256 == proposal.evidence_sha256
            ):
                return existing, False
        self.proposals[proposal.proposal_id] = proposal
        return proposal, True

    def latest_starting_proposal(self, user_id: UUID) -> StartingCalorieProposal | None:
        values = [item for item in self.proposals.values() if item.user_id == user_id]
        return max(values, key=lambda item: (item.created_at, item.proposal_id)) if values else None

    def find_starting_proposal(
        self, user_id: UUID, proposal_id: UUID
    ) -> StartingCalorieProposal | None:
        value = self.proposals.get(proposal_id)
        return value if value is not None and value.user_id == user_id else None

    def find_starting_decision(
        self, user_id: UUID, proposal_id: UUID
    ) -> StartingTargetDecision | None:
        value = self.decisions.get(proposal_id)
        return value if value is not None and value.user_id == user_id else None

    def decide_starting_proposal(
        self,
        proposal: StartingCalorieProposal,
        decision: StartingTargetDecision,
        resulting_policy: TargetPolicyVersion | None,
        target_decision_log: DecisionLogEntry | None,
    ) -> tuple[StartingTargetDecision, bool]:
        existing = self.decisions.get(proposal.proposal_id)
        event = self._decision_events.get((proposal.user_id, decision.client_event_id))
        if existing is not None:
            if (
                existing.decision == decision.decision
                and existing.client_event_id == decision.client_event_id
            ):
                return existing, False
            raise ValueError("starting target decision conflict")
        if event is not None:
            raise ValueError("starting target decision event conflict")
        if resulting_policy is not None:
            if self.target_repository is None or target_decision_log is None:
                raise ValueError("target repository required for approval")
            self.target_repository.save_approved(
                resulting_policy,
                target_decision_log.rationale,
                target_decision_log.decided_at or decision.decided_at,
            )
        self.decisions[proposal.proposal_id] = decision
        self._decision_events[(proposal.user_id, decision.client_event_id)] = proposal.proposal_id
        return decision, True


_TrainingIdentity = tuple[UUID, TrainingSourceSystem, str]


@dataclass
class InMemoryTrainingSessionRepository(TrainingSessionRepository):
    """In-memory mirror of migration 0010's immutable/tombstone semantics."""

    rows: dict[_TrainingIdentity, StoredTrainingSession] = field(default_factory=dict)
    tombstones: dict[_TrainingIdentity, datetime] = field(default_factory=dict)
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ids: Callable[[], UUID] = uuid4

    def apply_batch(self, user_id: UUID, batch: TrainingSyncBatch) -> TrainingBatchOutcome:
        accepted = duplicates = applied = duplicate_deletions = 0
        for observation in batch.added:
            key = (user_id, observation.source_system, observation.source_record_id)
            if key in self.rows or key in self.tombstones:
                duplicates += 1
                continue
            self.rows[key] = StoredTrainingSession(
                session_id=self.ids(),
                user_id=user_id,
                source_system=observation.source_system,
                source_record_id=observation.source_record_id,
                activity_type=observation.activity_type,
                started_at=observation.started_at,
                ended_at=observation.ended_at,
                active_duration_seconds=observation.active_duration_seconds,
                active_energy_kcal=observation.active_energy_kcal,
                timezone_identifier=observation.timezone_identifier,
                source_name=observation.source_name,
                source_bundle_id=observation.source_bundle_id,
                source_revision=observation.source_revision,
                ingested_at=self.clock(),
                tombstoned_at=None,
            )
            accepted += 1

        for deletion in batch.deletions:
            key = (user_id, deletion.source_system, deletion.source_record_id)
            if key in self.tombstones:
                duplicate_deletions += 1
                continue
            tombstoned_at = self.clock()
            self.tombstones[key] = tombstoned_at
            existing = self.rows.get(key)
            if existing is not None:
                self.rows[key] = replace(existing, tombstoned_at=tombstoned_at)
            applied += 1

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
        return tuple(
            sorted(
                (
                    row
                    for (owner_id, _, _), row in self.rows.items()
                    if owner_id == user_id
                    and row.tombstoned_at is None
                    and start_inclusive <= row.started_at < end_exclusive
                ),
                key=lambda row: (
                    row.started_at.astimezone(UTC),
                    row.source_system.value,
                    row.source_record_id,
                ),
            )
        )


_DetailedRevisionKey = tuple[UUID, DetailedTrainingSourceSystem, str, str]
_DetailedSessionKey = tuple[UUID, DetailedTrainingSourceSystem, str]


@dataclass
class InMemoryDetailedTrainingRepository(
    DetailedTrainingRepository,
    DetailedTrainingSyncRepository,
    DetailedTrainingAnalyticsRepository,
):
    """Immutable detailed revisions; source deletions permanently hide a session."""

    revisions: dict[_DetailedRevisionKey, StoredDetailedTrainingSession] = field(
        default_factory=dict
    )
    tombstones: dict[_DetailedSessionKey, datetime] = field(default_factory=dict)
    sync_checkpoints: list[DetailedTrainingSyncCheckpoint] = field(default_factory=list)
    ids: Callable[[], UUID] = uuid4

    def apply_import(
        self,
        user_id: UUID,
        batch: DetailedTrainingImportBatch,
        ingested_at: datetime,
    ) -> DetailedTrainingImportOutcome:
        if not batch.complete:
            raise ValueError("incomplete detailed training import cannot be persisted")
        staged_revisions = dict(self.revisions)
        staged_tombstones = dict(self.tombstones)
        accepted = duplicates = applied = duplicate_deletions = blocked = 0
        sessions_created = revisions_appended = 0
        for session in batch.sessions:
            logical = (user_id, session.source_system, session.source_session_id)
            if logical in staged_tombstones:
                blocked += 1
                continue
            revision_key = (*logical, session.source_revision)
            existing = staged_revisions.get(revision_key)
            if existing is not None:
                if existing.session != session:
                    raise DetailedTrainingRevisionConflictError(
                        "detailed source revision was reused with conflicting facts"
                    )
                duplicates += 1
                continue
            has_prior_revision = any(key[:3] == logical for key in staged_revisions)
            staged_revisions[revision_key] = StoredDetailedTrainingSession(
                revision_id=self.ids(),
                user_id=user_id,
                session=session,
                ingested_at=ingested_at,
            )
            accepted += 1
            if has_prior_revision:
                revisions_appended += 1
            else:
                sessions_created += 1

        for deletion in batch.deletions:
            logical = (user_id, deletion.source_system, deletion.source_session_id)
            if logical in staged_tombstones:
                duplicate_deletions += 1
                continue
            staged_tombstones[logical] = deletion.removed_at
            applied += 1

        self.revisions = staged_revisions
        self.tombstones = staged_tombstones

        return DetailedTrainingImportOutcome(
            accepted_revisions=accepted,
            duplicate_revisions=duplicates,
            applied_deletions=applied,
            duplicate_deletions=duplicate_deletions,
            blocked_by_tombstone=blocked,
            sessions_created=sessions_created,
            revisions_appended=revisions_appended,
        )

    def latest_sync_checkpoint(
        self,
        user_id: UUID,
        source_system: DetailedTrainingSourceSystem,
    ) -> DetailedTrainingSyncCheckpoint | None:
        matches = [
            item
            for item in self.sync_checkpoints
            if item.user_id == user_id and item.source_system is source_system
        ]
        if not matches:
            return None
        return deepcopy(
            max(
                matches,
                key=lambda item: (
                    item.source_event_watermark,
                    item.completed_at,
                    item.checkpoint_id,
                ),
            )
        )

    def apply_sync(
        self,
        user_id: UUID,
        batch: DetailedTrainingImportBatch,
        ingested_at: datetime,
        checkpoint: DetailedTrainingSyncCheckpoint,
    ) -> DetailedTrainingImportOutcome:
        if checkpoint.user_id != user_id:
            raise ValueError("sync checkpoint owner does not match import owner")
        if any(item.checkpoint_id == checkpoint.checkpoint_id for item in self.sync_checkpoints):
            raise ValueError("sync checkpoint identity already exists")
        if any(
            item.source_system is not checkpoint.source_system for item in batch.sessions
        ) or any(item.source_system is not checkpoint.source_system for item in batch.deletions):
            raise ValueError("sync checkpoint source does not match imported source")
        if any(item.parser_version != checkpoint.parser_version for item in batch.sessions) or any(
            item.parser_version != checkpoint.parser_version for item in batch.deletions
        ):
            raise ValueError("sync checkpoint parser does not match imported source")
        revisions_before = dict(self.revisions)
        tombstones_before = dict(self.tombstones)
        checkpoints_before = list(self.sync_checkpoints)
        try:
            prior = self.latest_sync_checkpoint(user_id, checkpoint.source_system)
            if prior is None and checkpoint.sync_mode is not DetailedTrainingSyncMode.BOOTSTRAP:
                raise ValueError("first detailed training sync checkpoint must be bootstrap")
            if (
                prior is not None
                and checkpoint.sync_mode is not DetailedTrainingSyncMode.INCREMENTAL
            ):
                raise ValueError("later detailed training sync checkpoints must be incremental")
            if prior is not None and (
                checkpoint.source_event_watermark < prior.source_event_watermark
                or (prior.bootstrap_completed and not checkpoint.bootstrap_completed)
            ):
                raise ValueError("detailed training sync checkpoint cannot regress")
            outcome = self.apply_import(user_id, batch, ingested_at)
            self.sync_checkpoints.append(deepcopy(checkpoint))
            return outcome
        except Exception:
            self.revisions = revisions_before
            self.tombstones = tombstones_before
            self.sync_checkpoints = checkpoints_before
            raise

    def list_latest(self, user_id: UUID, limit: int) -> tuple[DetailedTrainingSessionSummary, ...]:
        latest: dict[_DetailedSessionKey, StoredDetailedTrainingSession] = {}
        for (owner, source, source_id, _), stored in self.revisions.items():
            logical = (owner, source, source_id)
            if owner != user_id or logical in self.tombstones:
                continue
            prior = latest.get(logical)
            if prior is None or _revision_sort_key(stored) > _revision_sort_key(prior):
                latest[logical] = stored
        summaries = tuple(
            DetailedTrainingSessionSummary(
                revision_id=stored.revision_id,
                source_system=stored.session.source_system,
                source_session_id=stored.session.source_session_id,
                source_revision=stored.session.source_revision,
                title=stored.session.title,
                started_at=stored.session.started_at,
                ended_at=stored.session.ended_at,
                exercise_count=len(stored.session.exercises),
                set_count=sum(len(item.sets) for item in stored.session.exercises),
            )
            for stored in latest.values()
        )
        return tuple(
            sorted(
                summaries,
                key=lambda item: (
                    item.started_at.astimezone(UTC),
                    item.source_system.value,
                    item.source_session_id,
                ),
                reverse=True,
            )[:limit]
        )

    def get_by_revision_id(
        self, user_id: UUID, revision_id: UUID
    ) -> StoredDetailedTrainingSession | None:
        for (owner, source, source_id, _), stored in self.revisions.items():
            if stored.revision_id != revision_id or owner != user_id:
                continue
            if (owner, source, source_id) in self.tombstones:
                return None
            return deepcopy(stored)
        return None

    def list_latest_full(
        self,
        user_id: UUID,
        limit: int,
        *,
        source_system: DetailedTrainingSourceSystem | None = None,
        source_exercise_id: str | None = None,
    ) -> DetailedTrainingSessionPage:
        latest: dict[_DetailedSessionKey, StoredDetailedTrainingSession] = {}
        for (owner, source, source_id, _), stored in self.revisions.items():
            logical = (owner, source, source_id)
            if owner != user_id or logical in self.tombstones:
                continue
            if source_system is not None and source is not source_system:
                continue
            prior = latest.get(logical)
            if prior is None or _revision_sort_key(stored) > _revision_sort_key(prior):
                latest[logical] = stored
        selected = tuple(
            item
            for item in latest.values()
            if source_exercise_id is None
            or any(
                exercise.source_exercise_id == source_exercise_id
                for exercise in item.session.exercises
            )
        )
        ordered = tuple(
            sorted(
                selected,
                key=lambda item: (
                    item.session.started_at.astimezone(UTC),
                    item.session.source_system.value,
                    item.session.source_session_id,
                    item.revision_id,
                ),
                reverse=True,
            )
        )
        return DetailedTrainingSessionPage(
            sessions=deepcopy(ordered[:limit]),
            has_more=len(ordered) > limit,
        )


def _revision_sort_key(stored: StoredDetailedTrainingSession) -> tuple[datetime, str, UUID]:
    source_time = stored.session.source_updated_at or stored.ingested_at
    return source_time.astimezone(UTC), stored.session.source_revision, stored.revision_id


_ConsumptionKey = tuple[UUID, UUID, UUID, UUID]
_ConsumptionRequest = tuple[UUID, UUID, UUID, UUID, ConsumptionState, UUID]


@dataclass
class InMemoryConsumptionRepository(ConsumptionRepository):
    """Append-only mirror of migration 0004 idempotency and owner reads."""

    _entries: dict[_ConsumptionKey, ConsumptionEntry] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _entry_keys: dict[UUID, _ConsumptionKey] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    @staticmethod
    def _key(entry: ConsumptionEntry) -> _ConsumptionKey:
        return (
            entry.user_id,
            entry.plan_version_id,
            entry.item_id,
            entry.client_event_id,
        )

    @staticmethod
    def _request(entry: ConsumptionEntry) -> _ConsumptionRequest:
        return (
            entry.user_id,
            entry.plan_run_id,
            entry.plan_version_id,
            entry.item_id,
            entry.state,
            entry.client_event_id,
        )

    def save(self, entry: ConsumptionEntry) -> RecordConsumptionOutcome:
        key = self._key(entry)
        original = self._entries.get(key)
        if original is not None:
            if self._request(original) != self._request(entry):
                raise DuplicateConsumptionError(
                    "consumption idempotency key was reused with conflicting data"
                )
            return RecordConsumptionOutcome(entry=original, created=False)

        if entry.entry_id in self._entry_keys:
            raise DuplicateConsumptionError("consumption entry_id already exists")

        self._entries[key] = entry
        self._entry_keys[entry.entry_id] = key
        return RecordConsumptionOutcome(entry=entry, created=True)

    def list_for_run(self, user_id: UUID, plan_run_id: UUID) -> tuple[ConsumptionEntry, ...]:
        matching = (
            entry
            for entry in self._entries.values()
            if entry.user_id == user_id and entry.plan_run_id == plan_run_id
        )
        return tuple(sorted(matching, key=lambda entry: (entry.recorded_at, entry.entry_id)))


@dataclass
class InMemoryCustomFoodRepository(CustomFoodRepository):
    versions: dict[UUID, CustomFoodVersion] = field(default_factory=dict)
    foods: dict[UUID, UUID] = field(default_factory=dict)
    consumptions: dict[tuple[UUID, UUID], ManualFoodConsumptionEntry] = field(default_factory=dict)
    adjustments: dict[tuple[UUID, UUID], ManualFoodConsumptionAdjustment] = field(
        default_factory=dict
    )
    sources: dict[tuple[UUID, str, str], UUID] = field(default_factory=dict)

    def save_version(self, version: CustomFoodVersion, *, create_identity: bool) -> None:
        owner = self.foods.get(version.food_id)
        if create_identity:
            if owner is not None:
                raise DuplicateManualFoodError("custom food already exists")
            self.foods[version.food_id] = version.user_id
            provenance = version.provenance
            if provenance.provider is not None and provenance.provider_code is not None:
                source_key = (version.user_id, provenance.provider, provenance.provider_code)
                if source_key in self.sources:
                    raise DuplicateManualFoodError("barcode food source already exists")
                self.sources[source_key] = version.food_id
        elif owner != version.user_id:
            raise LookupError("custom food not found")
        if version.version_id in self.versions:
            raise DuplicateManualFoodError("custom food version already exists")
        self.versions[version.version_id] = version

    def find_latest_by_source(
        self, user_id: UUID, source_system: str, source_key: str
    ) -> CustomFoodVersion | None:
        food_id = self.sources.get((user_id, source_system, source_key))
        if food_id is None:
            return None
        versions = [
            value
            for value in self.versions.values()
            if value.user_id == user_id and value.food_id == food_id
        ]
        return max(versions, key=lambda value: (value.created_at, value.version_id), default=None)

    def list_latest(self, user_id: UUID) -> tuple[CustomFoodVersion, ...]:
        latest: dict[UUID, CustomFoodVersion] = {}
        for value in self.versions.values():
            if value.user_id != user_id:
                continue
            current = latest.get(value.food_id)
            if current is None or (value.created_at, value.version_id) > (
                current.created_at,
                current.version_id,
            ):
                latest[value.food_id] = value
        return tuple(
            sorted(latest.values(), key=lambda value: (value.name.casefold(), value.food_id))
        )

    def find_version(
        self, user_id: UUID, food_id: UUID, version_id: UUID
    ) -> CustomFoodVersion | None:
        value = self.versions.get(version_id)
        return value if value and value.user_id == user_id and value.food_id == food_id else None

    @staticmethod
    def _request(entry: ManualFoodConsumptionEntry) -> tuple[object, ...]:
        return entry.request_facts()

    def save_consumption(self, entry: ManualFoodConsumptionEntry) -> RecordManualFoodOutcome:
        key = (entry.user_id, entry.client_event_id)
        original = self.consumptions.get(key)
        if original is not None:
            if self._request(original) != self._request(entry):
                raise DuplicateManualFoodError("manual consumption event conflicts")
            return RecordManualFoodOutcome(entry=original, created=False)
        self.consumptions[key] = entry
        return RecordManualFoodOutcome(entry=entry, created=True)

    def find_active_consumption(
        self, user_id: UUID, entry_id: UUID
    ) -> ManualFoodConsumptionEntry | None:
        superseded = {
            value.superseded_entry_id
            for value in self.adjustments.values()
            if value.user_id == user_id
        }
        if entry_id in superseded:
            return None
        return next(
            (
                value
                for value in self.consumptions.values()
                if value.user_id == user_id and value.entry_id == entry_id
            ),
            None,
        )

    def find_consumption(self, user_id: UUID, entry_id: UUID) -> ManualFoodConsumptionEntry | None:
        return next(
            (
                value
                for value in self.consumptions.values()
                if value.user_id == user_id and value.entry_id == entry_id
            ),
            None,
        )

    def save_adjustment(
        self,
        adjustment: ManualFoodConsumptionAdjustment,
        replacement: ManualFoodConsumptionEntry | None,
    ) -> AdjustManualFoodOutcome:
        key = (adjustment.user_id, adjustment.client_event_id)
        existing = self.adjustments.get(key)
        if existing is not None:
            existing_replacement = next(
                (
                    value
                    for value in self.consumptions.values()
                    if value.entry_id == existing.replacement_entry_id
                ),
                None,
            )
            same_request = (
                existing.superseded_entry_id == adjustment.superseded_entry_id
                and existing.kind is adjustment.kind
                and (
                    (existing_replacement is None and replacement is None)
                    or (
                        existing_replacement is not None
                        and replacement is not None
                        and existing_replacement.correction_facts()
                        == replacement.correction_facts()
                    )
                )
            )
            if not same_request:
                raise DuplicateManualFoodError("manual adjustment event conflicts")
            return AdjustManualFoodOutcome(existing, existing_replacement, False)
        if self.find_active_consumption(adjustment.user_id, adjustment.superseded_entry_id) is None:
            raise LookupError("active manual consumption not found")
        if adjustment.kind is ManualFoodAdjustmentKind.CORRECTION:
            if replacement is None or replacement.entry_id != adjustment.replacement_entry_id:
                raise ValueError("correction replacement mismatch")
            self.consumptions[(replacement.user_id, replacement.client_event_id)] = replacement
        elif replacement is not None:
            raise ValueError("void cannot contain replacement")
        self.adjustments[key] = adjustment
        return AdjustManualFoodOutcome(adjustment, replacement, True)


def _view_from_parts(
    run: PlanRun,
    version: PlanVersion | None,
    items: Sequence[PlanItem] = (),
) -> PersistedPlanView:
    return PersistedPlanView(
        run_id=run.run_id,
        user_id=run.user_id,
        requested_for_date=run.requested_for_date,
        timezone=run.timezone,
        inputs_fingerprint=run.inputs_fingerprint,
        status=run.status.value,
        reason_codes=run.reason_codes,
        started_at=run.started_at,
        finished_at=run.finished_at,
        version_id=version.version_id if version else None,
        plan_sha256=version.plan_sha256 if version else None,
        plan_jsonb=version.plan_jsonb if version else None,
        plan_canonical=version.plan_canonical if version else None,
        target_policy_version_id=run.target_policy_version_id,
        plan_items=tuple(
            PersistedPlanItemReference(
                item_id=item.item_id,
                version_id=item.version_id,
                slot_index=item.slot_index,
                rank=item.rank,
                candidate_id=item.candidate_id,
            )
            for item in sorted(items, key=lambda item: (item.slot_index, item.rank, item.item_id))
        ),
    )


@dataclass
class InMemoryPlanRunRepository(PlanRunRepository):
    """Mirrors migration 0003 semantics: UNIQUE (user, date, fingerprint)."""

    runs: dict[UUID, PlanRun] = field(default_factory=dict)
    versions: dict[UUID, PlanVersion] = field(default_factory=dict)
    items: dict[UUID, list[PlanItem]] = field(default_factory=dict)
    _by_fingerprint: dict[tuple[UUID, date, str], UUID] = field(default_factory=dict)

    def find_replay(
        self, user_id: UUID, requested_date: date, inputs_fingerprint: str
    ) -> PersistedPlanView | None:
        run_id = self._by_fingerprint.get((user_id, requested_date, inputs_fingerprint))
        if run_id is None:
            return None
        return _view_from_parts(
            self.runs[run_id], self.versions.get(run_id), self.items.get(run_id, ())
        )

    def latest_for_user_date(self, user_id: UUID, requested_date: date) -> PersistedPlanView | None:
        matching = [
            run
            for run in self.runs.values()
            if run.user_id == user_id and run.requested_for_date == requested_date
        ]
        if not matching:
            return None
        # Deterministic pick: last finished, tiebreak on run_id for stability.
        latest = max(matching, key=lambda run: (run.finished_at, run.run_id))
        return _view_from_parts(
            latest, self.versions.get(latest.run_id), self.items.get(latest.run_id, ())
        )

    def save(
        self,
        run: PlanRun,
        version: PlanVersion,
        items: Sequence[PlanItem],
    ) -> None:
        key = (run.user_id, run.requested_for_date, run.inputs_fingerprint)
        if key in self._by_fingerprint:
            raise DuplicateLogicalPlanError(
                "plan_run already exists for (user, date, inputs_fingerprint)"
            )
        assert version.run_id == run.run_id
        self._by_fingerprint[key] = run.run_id
        self.runs[run.run_id] = run
        self.versions[run.run_id] = version
        self.items[run.run_id] = list(items)


@dataclass
class InMemoryTargetPolicyRepository(TargetPolicyRepository):
    """Mirrors migration 0003 semantics: UNIQUE (user, version) payloads."""

    policies: dict[UUID, TargetPolicyVersion] = field(default_factory=dict)
    rationales: dict[UUID, str] = field(default_factory=dict)
    _by_label: dict[tuple[UUID, str], UUID] = field(default_factory=dict)
    _by_payload: dict[tuple[UUID, str], UUID] = field(default_factory=dict)

    def save_approved(
        self,
        policy: TargetPolicyVersion,
        rationale: str,
        decided_by_clock: datetime,
    ) -> None:
        del decided_by_clock  # timestamps carried on the policy record itself
        label_key = (policy.user_id, policy.policy_version)
        payload_key = (policy.user_id, policy.payload_sha256)
        existing = self._by_label.get(label_key)
        if existing is not None or self._by_payload.get(payload_key) is not None:
            raise TargetPolicyVersionExistsError(
                "target_policy_version already exists for this user"
            )
        self._by_label[label_key] = policy.version_id
        self._by_payload[payload_key] = policy.version_id
        self.policies[policy.version_id] = policy
        self.rationales[policy.version_id] = rationale

    def latest_approved(self, user_id: UUID) -> TargetPolicyVersion | None:
        mine = [p for p in self.policies.values() if p.user_id == user_id]
        if not mine:
            return None
        key = lambda p: (p.created_at or datetime.min.replace(tzinfo=UTC), p.version_id)  # noqa: E731
        return max(mine, key=key)

    def list_approved_for_window(
        self,
        user_id: UUID,
        start_inclusive: datetime,
        end_exclusive: datetime,
    ) -> tuple[TargetPolicyVersion, ...]:
        mine = [policy for policy in self.policies.values() if policy.user_id == user_id]
        if any(policy.created_at is None for policy in mine):
            raise ValueError("target policy approval timestamp is unavailable")

        def approved_at(policy: TargetPolicyVersion) -> datetime:
            if policy.created_at is None:  # guarded above; explicit for the type checker
                raise ValueError("target policy approval timestamp is unavailable")
            return policy.created_at

        boundary = [policy for policy in mine if approved_at(policy) <= start_inclusive]
        result: list[TargetPolicyVersion] = []
        if boundary:
            result.append(max(boundary, key=lambda p: (approved_at(p), p.version_id)))
        result.extend(
            policy for policy in mine if start_inclusive < approved_at(policy) < end_exclusive
        )
        return tuple(sorted(result, key=lambda p: (approved_at(p), p.version_id)))

    def find_by_version_id(self, user_id: UUID, version_id: UUID) -> TargetPolicyVersion | None:
        policy = self.policies.get(version_id)
        return policy if policy is not None and policy.user_id == user_id else None

    def find_by_version_label(self, user_id: UUID, policy_version: str) -> bool:
        return (user_id, policy_version) in self._by_label

    def rationale_for(self, version_id: UUID) -> str:
        return self.rationales.get(version_id, "")


@dataclass
class InMemoryProteinTargetProposalRepository(ProteinTargetProposalRepository):
    targets: InMemoryTargetPolicyRepository | None = None
    proposals: dict[UUID, ProteinTargetProposal] = field(default_factory=dict)
    decisions: dict[UUID, ProteinTargetProposalDecision] = field(default_factory=dict)
    resulting_policies: dict[UUID, TargetPolicyVersion] = field(default_factory=dict)
    _by_digest: dict[tuple[UUID, str], UUID] = field(default_factory=dict)
    _by_event: dict[tuple[UUID, UUID], UUID] = field(default_factory=dict)

    def save(self, proposal: ProteinTargetProposal) -> tuple[ProteinTargetProposal, bool]:
        existing_id = self._by_digest.get((proposal.user_id, proposal.evidence_digest))
        if existing_id is not None:
            return deepcopy(self.proposals[existing_id]), False
        self.proposals[proposal.proposal_id] = deepcopy(proposal)
        self._by_digest[(proposal.user_id, proposal.evidence_digest)] = proposal.proposal_id
        return proposal, True

    def latest(self, user_id: UUID) -> ProteinTargetProposal | None:
        mine = [value for value in self.proposals.values() if value.user_id == user_id]
        return (
            max(mine, key=lambda value: (value.generated_at, value.proposal_id)) if mine else None
        )

    def find_by_id(self, user_id: UUID, proposal_id: UUID) -> ProteinTargetProposal | None:
        value = self.proposals.get(proposal_id)
        return value if value is not None and value.user_id == user_id else None

    def find_decision(
        self, user_id: UUID, proposal_id: UUID
    ) -> ProteinTargetProposalDecision | None:
        value = self.decisions.get(proposal_id)
        return deepcopy(value) if value is not None and value.user_id == user_id else None

    def decide(
        self,
        proposal: ProteinTargetProposal,
        decision: ProteinTargetProposalDecision,
        resulting_policy: TargetPolicyVersion | None,
        target_decision_log: DecisionLogEntry | None,
    ) -> DecideProteinProposalOutcome:
        del target_decision_log
        existing = self.decisions.get(proposal.proposal_id)
        if existing is not None:
            if (
                existing.decision == decision.decision
                and existing.client_event_id == decision.client_event_id
                and existing.rationale == decision.rationale
            ):
                return DecideProteinProposalOutcome(existing, False)
            raise ProteinProposalDecisionConflictError("proposal already decided")
        if (proposal.user_id, decision.client_event_id) in self._by_event:
            raise ProteinProposalDecisionConflictError("client event already used")
        if resulting_policy is not None:
            if self.targets is not None:
                latest = self.targets.latest_approved(proposal.user_id)
                if latest is None or latest.version_id != proposal.prior_target_policy_version_id:
                    raise StaleProteinProposalError("proposal target reference is stale")
                self.targets.save_approved(
                    resulting_policy,
                    decision.rationale,
                    decision.decided_at,
                )
            self.resulting_policies[resulting_policy.version_id] = deepcopy(resulting_policy)
        self.decisions[proposal.proposal_id] = deepcopy(decision)
        self._by_event[(proposal.user_id, decision.client_event_id)] = proposal.proposal_id
        return DecideProteinProposalOutcome(decision, True)


@dataclass
class InMemoryNextMealRecommendationRepository(NextMealRecommendationRepository):
    recommendations: dict[UUID, NextMealRecommendation] = field(default_factory=dict)
    _by_request: dict[tuple[UUID, UUID], UUID] = field(default_factory=dict)

    def save(self, recommendation: NextMealRecommendation) -> PersistNextMealOutcome:
        key = (recommendation.user_id, recommendation.client_request_id)
        existing_id = self._by_request.get(key)
        if existing_id is not None:
            existing = self.recommendations[existing_id]
            if (
                existing.local_date != recommendation.local_date
                or existing.timezone != recommendation.timezone
            ):
                raise DuplicateNextMealRecommendationError("client request input conflict")
            return PersistNextMealOutcome(deepcopy(existing), False)
        self.recommendations[recommendation.recommendation_id] = deepcopy(recommendation)
        self._by_request[key] = recommendation.recommendation_id
        return PersistNextMealOutcome(recommendation, True)

    def find_by_client_request_id(
        self, user_id: UUID, client_request_id: UUID
    ) -> NextMealRecommendation | None:
        recommendation_id = self._by_request.get((user_id, client_request_id))
        if recommendation_id is None:
            return None
        return deepcopy(self.recommendations[recommendation_id])

    def latest(self, user_id: UUID) -> NextMealRecommendation | None:
        mine = [value for value in self.recommendations.values() if value.user_id == user_id]
        return (
            max(mine, key=lambda value: (value.created_at, value.recommendation_id))
            if mine
            else None
        )

    def find_by_id(self, user_id: UUID, recommendation_id: UUID) -> NextMealRecommendation | None:
        value = self.recommendations.get(recommendation_id)
        return deepcopy(value) if value is not None and value.user_id == user_id else None


@dataclass
class InMemoryNextMealConsumptionRepository(NextMealConsumptionRepository):
    entries: dict[UUID, NextMealConsumptionEntry] = field(default_factory=dict)
    _by_event: dict[tuple[UUID, UUID], UUID] = field(default_factory=dict)
    _by_recommendation: dict[tuple[UUID, UUID], UUID] = field(default_factory=dict)

    def save(self, entry: NextMealConsumptionEntry) -> RecordNextMealConsumptionOutcome:
        event_key = (entry.user_id, entry.client_event_id)
        recommendation_key = (entry.user_id, entry.recommendation_id)
        existing_id = self._by_event.get(event_key) or self._by_recommendation.get(
            recommendation_key
        )
        if existing_id is not None:
            original = self.entries[existing_id]
            if original.request_facts() != entry.request_facts():
                raise DuplicateNextMealConsumptionError("next-meal consumption conflicts")
            return RecordNextMealConsumptionOutcome(original, False)
        if entry.entry_id in self.entries:
            raise DuplicateNextMealConsumptionError("next-meal consumption entry ID conflicts")
        self.entries[entry.entry_id] = deepcopy(entry)
        self._by_event[event_key] = entry.entry_id
        self._by_recommendation[recommendation_key] = entry.entry_id
        return RecordNextMealConsumptionOutcome(entry, True)

    def find_for_recommendation(
        self, user_id: UUID, recommendation_id: UUID
    ) -> NextMealConsumptionEntry | None:
        entry_id = self._by_recommendation.get((user_id, recommendation_id))
        return deepcopy(self.entries[entry_id]) if entry_id is not None else None


@dataclass
class InMemoryGoalPolicyRepository(GoalPolicyRepository):
    """Append-only goal intent with the same two uniqueness keys as SQL."""

    policies: dict[UUID, GoalPolicyVersion] = field(default_factory=dict)
    _by_label: dict[tuple[UUID, str], UUID] = field(default_factory=dict)
    _by_payload: dict[tuple[UUID, str], UUID] = field(default_factory=dict)

    def save(self, policy: GoalPolicyVersion) -> None:
        label_key = (policy.user_id, policy.policy_version)
        payload_key = (policy.user_id, policy.payload_sha256)
        if (
            policy.version_id in self.policies
            or label_key in self._by_label
            or payload_key in self._by_payload
        ):
            raise GoalPolicyVersionExistsError("goal_policy_version already exists for this user")
        self.policies[policy.version_id] = policy
        self._by_label[label_key] = policy.version_id
        self._by_payload[payload_key] = policy.version_id

    def latest(self, user_id: UUID) -> GoalPolicyVersion | None:
        mine = [policy for policy in self.policies.values() if policy.user_id == user_id]
        if not mine:
            return None
        return max(mine, key=lambda policy: (policy.created_at, policy.version_id))

    def find_by_version_id(self, user_id: UUID, version_id: UUID) -> GoalPolicyVersion | None:
        policy = self.policies.get(version_id)
        return policy if policy is not None and policy.user_id == user_id else None


@dataclass
class InMemoryTargetReviewRepository(TargetReviewRepository):
    """Digest-idempotent immutable target-review evidence."""

    reviews: dict[UUID, TargetReview] = field(default_factory=dict)
    _by_digest: dict[tuple[UUID, str], UUID] = field(default_factory=dict)

    def save(self, review: TargetReview) -> PersistTargetReviewOutcome:
        key = (review.user_id, review.recommendation_digest)
        existing_id = self._by_digest.get(key)
        if existing_id is not None:
            original = self.reviews[existing_id]
            if original.evaluation != review.evaluation:
                raise DuplicateTargetReviewError(
                    "recommendation digest was reused with conflicting evidence"
                )
            return PersistTargetReviewOutcome(review=original, created=False)
        if review.review_id in self.reviews:
            raise DuplicateTargetReviewError("target review identity already exists")
        self.reviews[review.review_id] = review
        self._by_digest[key] = review.review_id
        return PersistTargetReviewOutcome(review=review, created=True)

    def find_by_digest(self, user_id: UUID, recommendation_digest: str) -> TargetReview | None:
        review_id = self._by_digest.get((user_id, recommendation_digest))
        return self.reviews.get(review_id) if review_id is not None else None

    def find_by_id(self, user_id: UUID, review_id: UUID) -> TargetReview | None:
        review = self.reviews.get(review_id)
        return review if review is not None and review.user_id == user_id else None


@dataclass
class InMemoryTargetReviewDecisionRepository(TargetReviewDecisionRepository):
    """Atomic-in-process model of the SQL terminal decision transaction."""

    goals: InMemoryGoalPolicyRepository
    targets: InMemoryTargetPolicyRepository
    reviews: InMemoryTargetReviewRepository
    outcomes: dict[UUID, DecideTargetReviewOutcome] = field(default_factory=dict)
    _by_event: dict[tuple[UUID, UUID], UUID] = field(default_factory=dict)
    decision_logs: dict[UUID, DecisionLogEntry] = field(default_factory=dict)

    def find_for_review(self, user_id: UUID, review_id: UUID) -> DecideTargetReviewOutcome | None:
        outcome = self.outcomes.get(review_id)
        if outcome is None or outcome.review_decision.user_id != user_id:
            return None
        return outcome

    def decide(
        self,
        review: TargetReview,
        decision: TargetReviewDecision,
        resulting_policy: TargetPolicyVersion | None,
        target_decision_log: DecisionLogEntry | None,
    ) -> DecideTargetReviewOutcome:
        existing = self.find_for_review(decision.user_id, decision.review_id)
        if existing is not None:
            stored = existing.review_decision
            if (
                stored.decision is decision.decision
                and stored.client_event_id == decision.client_event_id
            ):
                return replace(existing, created=False)
            raise TargetReviewDecisionConflictError("review already has a terminal decision")
        event_key = (decision.user_id, decision.client_event_id)
        if event_key in self._by_event:
            raise TargetReviewDecisionConflictError("idempotency key was already used")
        persisted = self.reviews.find_by_id(decision.user_id, decision.review_id)
        if persisted is None or persisted != review:
            raise TargetReviewDecisionConflictError("review evidence is not persisted exactly")
        if decision.user_id != review.user_id:
            raise TargetReviewDecisionConflictError("decision owner does not match review")

        if decision.decision is TargetReviewDecisionValue.APPROVED:
            latest_goal = self.goals.latest(decision.user_id)
            latest_target = self.targets.latest_approved(decision.user_id)
            if (
                latest_goal is None
                or latest_target is None
                or latest_goal.version_id != review.evaluation.goal_policy.version_id
                or latest_target.version_id != review.evaluation.prior_target_policy_version_id
            ):
                raise StaleTargetReviewError("latest goal or target policy changed")
            if resulting_policy is None or target_decision_log is None:
                raise TargetReviewDecisionConflictError("approval records are incomplete")
            if resulting_policy.user_id != decision.user_id:
                raise TargetReviewDecisionConflictError("resulting target owner mismatch")
        elif resulting_policy is not None or target_decision_log is not None:
            raise TargetReviewDecisionConflictError("rejection cannot create target records")

        if resulting_policy is not None:
            try:
                self.targets.save_approved(
                    resulting_policy,
                    rationale=decision.rationale,
                    decided_by_clock=decision.decided_at,
                )
            except TargetPolicyVersionExistsError as exc:
                raise TargetReviewDecisionConflictError(
                    "resulting target policy already exists"
                ) from exc
            assert target_decision_log is not None
            self.decision_logs[target_decision_log.decision_id] = target_decision_log

        outcome = DecideTargetReviewOutcome(
            review_decision=decision,
            resulting_policy=resulting_policy,
            target_decision_log=target_decision_log,
            created=True,
        )
        self.outcomes[decision.review_id] = outcome
        self._by_event[event_key] = decision.review_id
        return outcome
