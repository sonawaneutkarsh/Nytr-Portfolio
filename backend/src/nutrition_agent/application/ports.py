"""Application-layer ports (driven/driving interfaces).

Implementations live in infrastructure; the use case depends only on these.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from nutrition_agent.domain.body_goals import OwnerBodyProfile, WaistMeasurement
from nutrition_agent.domain.consumption import (
    ConsumptionEntry,
    RecordConsumptionOutcome,
)
from nutrition_agent.domain.health.entities import (
    BatchOutcome,
    HealthSyncStatus,
    SyncBatch,
)
from nutrition_agent.domain.health.trend import BodyMassObservation
from nutrition_agent.domain.next_meal import NextMealRecommendation, PersistNextMealOutcome
from nutrition_agent.domain.next_meal_consumption import (
    NextMealConsumptionEntry,
    RecordNextMealConsumptionOutcome,
)
from nutrition_agent.domain.nutrition.barcodes import BarcodeProduct
from nutrition_agent.domain.nutrition.custom_foods import (
    CustomFoodVersion,
    ManualFoodConsumptionEntry,
    RecordManualFoodOutcome,
)
from nutrition_agent.domain.nutrition.ledger import ConsumedNutritionEvidence
from nutrition_agent.domain.planning.artifacts import (
    DecisionLogEntry,
    PlanItem,
    PlanRun,
    PlanVersion,
    TargetPolicyVersion,
)
from nutrition_agent.domain.planning.menu_view import MenuDayView
from nutrition_agent.domain.protein_target import (
    DecideProteinProposalOutcome,
    ProteinTargetProposal,
    ProteinTargetProposalDecision,
)
from nutrition_agent.domain.stacks.entities import (
    DietaryTag,
    MealPeriod,
    MenuOffering,
    NutritionProfile,
    NutritionSourceState,
)
from nutrition_agent.domain.stacks.ingestion import (
    IngestionRun,
    QuarantineRecord,
    RunStatus,
)
from nutrition_agent.domain.target_review import (
    DecideTargetReviewOutcome,
    GoalPolicyVersion,
    PersistTargetReviewOutcome,
    TargetReview,
    TargetReviewDecision,
)
from nutrition_agent.domain.training import (
    StoredTrainingSession,
    TrainingBatchOutcome,
    TrainingSyncBatch,
)
from nutrition_agent.domain.training.detail import (
    DetailedTrainingImportBatch,
    DetailedTrainingImportOutcome,
    DetailedTrainingSessionPage,
    DetailedTrainingSessionSummary,
    DetailedTrainingSourceFetch,
    DetailedTrainingSourceSystem,
    DetailedTrainingSyncCheckpoint,
    StoredDetailedTrainingSession,
)
from nutrition_agent.infrastructure.snapshot_store import SnapshotRef


@dataclass(frozen=True)
class IngestCommand:
    service_date: date
    meal_periods: Sequence[MealPeriod]


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new_id(self) -> UUID: ...


class RunRepository(Protocol):
    def create(self, run: IngestionRun) -> None: ...
    def update(self, run: IngestionRun) -> None: ...


class SnapshotRepository(Protocol):
    def record(self, ref: SnapshotRef, parser_version: str) -> SnapshotRef:
        """Persist or resolve one content-deduplicated canonical snapshot."""
        ...

    def find_latest_by_request(
        self, source_url: str, method: str, request_params: dict[str, str]
    ) -> tuple[SnapshotRef, str] | None: ...


class FoodRepository(Protocol):
    def upsert_by_name(
        self, campus_id: int, name_raw: str, name_normalized: str
    ) -> tuple[UUID, bool]: ...
    def get_by_name(self, campus_id: int, name_normalized: str) -> UUID | None: ...


class OfferingRepository(Protocol):
    def upsert(self, offering: MenuOffering) -> tuple[UUID, bool]:
        """Return (stored_offering_id, inserted). Existing natural key -> update."""

    def link_profile(self, offering_id: UUID, profile_id: UUID) -> None: ...
    def count_for_page(self, service_date: date, meal_period: MealPeriod) -> int: ...


class MenuPageValidationState(StrEnum):
    VALIDATED_NONEMPTY = "validated_nonempty"
    VALIDATED_EMPTY = "validated_empty"


@dataclass(frozen=True)
class PreparedMenuPageOffering:
    """One fully prepared offering awaiting an atomic accepted-page commit."""

    offering: MenuOffering
    food_name_raw: str
    food_name_normalized: str
    profile: NutritionProfile | None
    profile_snapshot: SnapshotRef | None
    nutrition_source_state: NutritionSourceState | None = NutritionSourceState.PROFILE_AVAILABLE
    nutrition_snapshot: SnapshotRef | None = None

    def __post_init__(self) -> None:
        if self.nutrition_snapshot is None and self.profile_snapshot is not None:
            object.__setattr__(self, "nutrition_snapshot", self.profile_snapshot)
        if (self.profile is None) != (self.profile_snapshot is None):
            raise ValueError("profile and canonical profile snapshot must be supplied together")
        if self.nutrition_source_state is NutritionSourceState.PROFILE_AVAILABLE and (
            self.profile is None or self.profile_snapshot is None
        ):
            raise ValueError("profile_available requires an exact profile")
        if self.nutrition_source_state not in {
            None,
            NutritionSourceState.PROFILE_AVAILABLE,
        } and (self.profile is not None or self.profile_snapshot is not None):
            raise ValueError("non-profile source state cannot carry a profile")
        if self.nutrition_source_state is not None and self.nutrition_snapshot is None:
            raise ValueError("classified nutrition source state requires an exact snapshot")
        if self.profile is not None and self.profile_snapshot is not None:
            provenance = self.profile.provenance
            if (
                provenance.snapshot_id != self.profile_snapshot.snapshot_id
                or provenance.content_sha256 != self.profile_snapshot.content_sha256
            ):
                raise ValueError("profile provenance must use the canonical profile snapshot")
            if self.nutrition_snapshot != self.profile_snapshot:
                raise ValueError("profile nutrition snapshot must match profile provenance")


@dataclass(frozen=True)
class MenuPageOfferingPin:
    """Immutable planning facts for one offering in one accepted page."""

    page_version_id: UUID
    offering_id: UUID
    food_id: UUID
    profile_id: UUID | None
    name_normalized: str
    occurrence_ordinal: int
    category_name: str
    category_position: int
    item_position: int
    source_mid: str
    dietary_tags: tuple[DietaryTag, ...]
    nutrition_source_state: NutritionSourceState
    nutrition_snapshot_id: UUID


@dataclass(frozen=True, order=True)
class NutritionAuthorityKey:
    """Exact Source-A identity required before an accepted profile may be reused."""

    campus_id: int
    name_normalized: str
    source_mid: str


@dataclass(frozen=True)
class ReusableNutritionAuthority:
    """An immutable accepted profile and its exact label-response provenance."""

    key: NutritionAuthorityKey
    profile: NutritionProfile
    nutrition_snapshot: SnapshotRef

    def __post_init__(self) -> None:
        provenance = self.profile.provenance
        if (
            provenance.snapshot_id != self.nutrition_snapshot.snapshot_id
            or provenance.content_sha256 != self.nutrition_snapshot.content_sha256
        ):
            raise ValueError("reusable profile provenance does not match its snapshot")
        if (
            self.nutrition_snapshot.method != "GET"
            or self.nutrition_snapshot.http_status != 200
            or not self.key.source_mid
        ):
            raise ValueError("reusable profile snapshot is not an accepted label response")


@dataclass(frozen=True, order=True)
class PageLabelAuthorityKey:
    """Exact occurrence identity for resumable evidence on one logical page."""

    service_date: date
    meal_period: MealPeriod
    campus_id: int
    name_normalized: str
    source_mid: str
    occurrence_ordinal: int

    def __post_init__(self) -> None:
        if not self.name_normalized or not self.source_mid:
            raise ValueError("page-label identity requires a normalized name and source mid")
        if self.occurrence_ordinal < 0:
            raise ValueError("page-label occurrence ordinal cannot be negative")


@dataclass(frozen=True)
class ReusablePageLabelAuthority:
    """Accepted label evidence reusable only for the exact logical page occurrence."""

    key: PageLabelAuthorityKey
    nutrition_source_state: NutritionSourceState
    profile: NutritionProfile | None
    nutrition_snapshot: SnapshotRef

    def __post_init__(self) -> None:
        if self.nutrition_source_state is NutritionSourceState.PROFILE_AVAILABLE:
            if self.profile is None:
                raise ValueError("profile_available label authority requires a profile")
            provenance = self.profile.provenance
            if (
                provenance.snapshot_id != self.nutrition_snapshot.snapshot_id
                or provenance.content_sha256 != self.nutrition_snapshot.content_sha256
            ):
                raise ValueError("label-authority profile provenance does not match its snapshot")
        elif self.profile is not None:
            raise ValueError("non-profile label authority cannot carry a profile")
        if self.nutrition_snapshot.method != "GET" or self.nutrition_snapshot.http_status != 200:
            raise ValueError("label authority requires a successful GET snapshot")


@dataclass(frozen=True)
class ValidatedMenuLabelObservation:
    """One immutable accepted label result; never whole-page authority."""

    observation_id: UUID
    key: PageLabelAuthorityKey
    menu_snapshot: SnapshotRef
    food_id: UUID
    food_name_raw: str
    nutrition_source_state: NutritionSourceState
    profile: NutritionProfile | None
    nutrition_snapshot: SnapshotRef
    parser_version: str
    ingestion_run_id: UUID
    recorded_at: datetime

    def __post_init__(self) -> None:
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise ValueError("label observation recorded_at must be timezone-aware")
        if not self.parser_version:
            raise ValueError("label observation requires a parser version")
        if self.menu_snapshot.method != "POST" or self.menu_snapshot.http_status != 200:
            raise ValueError("label observation requires a successful menu POST snapshot")
        ReusablePageLabelAuthority(
            key=self.key,
            nutrition_source_state=self.nutrition_source_state,
            profile=self.profile,
            nutrition_snapshot=self.nutrition_snapshot,
        )
        if self.profile is not None and self.profile.food_id != self.food_id:
            raise ValueError("label-observation profile does not belong to its food")


@dataclass(frozen=True)
class ValidatedMenuPage:
    """A parsed/validated page ready for one atomic persistence transaction."""

    page_version_id: UUID
    service_date: date
    meal_period: MealPeriod
    campus_id: int
    snapshot_id: UUID
    ingestion_run_id: UUID
    validation_state: MenuPageValidationState
    accepted_at: datetime
    parser_version: str
    offerings: Sequence[PreparedMenuPageOffering]

    def __post_init__(self) -> None:
        prepared = tuple(self.offerings)
        object.__setattr__(self, "offerings", prepared)
        if self.accepted_at.tzinfo is None or self.accepted_at.utcoffset() is None:
            raise ValueError("accepted_at must be timezone-aware")
        if self.validation_state is MenuPageValidationState.VALIDATED_EMPTY and prepared:
            raise ValueError("validated_empty page cannot carry offerings")
        if self.validation_state is MenuPageValidationState.VALIDATED_NONEMPTY and not prepared:
            raise ValueError("validated_nonempty page must carry offerings")
        for item in prepared:
            if self.validation_state is MenuPageValidationState.VALIDATED_NONEMPTY and (
                item.nutrition_source_state is None or item.nutrition_snapshot is None
            ):
                raise ValueError("validated_nonempty offering must carry an exact classification")
            offering = item.offering
            if (
                offering.service_date != self.service_date
                or offering.meal_period is not self.meal_period
                or offering.campus_id != self.campus_id
                or offering.snapshot_id != self.snapshot_id
            ):
                raise ValueError("prepared offering does not belong to the validated page")

    @property
    def offering_count(self) -> int:
        return len(self.offerings)


@dataclass(frozen=True)
class PersistMenuPageOutcome:
    inserted_offerings: int
    updated_offerings: int
    profiles_persisted: int


class MenuPageVersionRepository(Protocol):
    """Trusted global store for accepted pages and resumable label evidence."""

    def has_accepted_observation(
        self,
        ingestion_run_id: UUID,
        service_date: date,
        meal_period: MealPeriod,
        campus_id: int,
    ) -> bool: ...

    def persist_validated_page(self, page: ValidatedMenuPage) -> PersistMenuPageOutcome:
        """Atomically persist page data, membership, and acceptance marker."""
        ...

    def find_reusable_nutrition_authorities(
        self,
        keys: Sequence[NutritionAuthorityKey],
        *,
        parser_version: str,
        fetched_not_before: datetime,
    ) -> tuple[ReusableNutritionAuthority, ...]:
        """Resolve only a reusable newest accepted pin for each exact key.

        A newer non-profile, stale, or parser-incompatible pin blocks fallback
        to older authority for that key.
        """
        ...

    def find_reusable_page_label_authorities(
        self,
        keys: Sequence[PageLabelAuthorityKey],
        *,
        parser_version: str,
        fetched_not_before: datetime,
    ) -> tuple[ReusablePageLabelAuthority, ...]:
        """Resolve only current evidence for exact logical-page occurrences."""
        ...

    def persist_label_observation(
        self, observation: ValidatedMenuLabelObservation
    ) -> ReusablePageLabelAuthority:
        """Persist one immutable label result without accepting its menu page."""
        ...


@dataclass(frozen=True, order=True)
class AcceptedMenuPageKey:
    """One globally accepted Stacks page relevant to refresh selection."""

    service_date: date
    meal_period: MealPeriod
    campus_id: int


@dataclass(frozen=True)
class AcceptedMenuPageObservation:
    """Latest accepted observation time for one Stacks source page key."""

    key: AcceptedMenuPageKey
    accepted_at: datetime

    def __post_init__(self) -> None:
        if self.accepted_at.tzinfo is None or self.accepted_at.utcoffset() is None:
            raise ValueError("accepted_at must be timezone-aware")


class MenuPageCoverageRepository(Protocol):
    """Trusted read port for accepted-page coverage in one bounded window."""

    def accepted_pages(
        self,
        *,
        campus_id: int,
        start_date: date,
        end_date: date,
    ) -> tuple[AcceptedMenuPageObservation, ...]: ...


@dataclass(frozen=True)
class ResolvedMenuDay:
    """Resolved menu input and immutable offering-to-profile provenance pins."""

    menu: MenuDayView
    offering_profile_ids: Mapping[str, UUID]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "offering_profile_ids",
            MappingProxyType(dict(self.offering_profile_ids)),
        )


class MenuDayReadRepository(Protocol):
    """Narrow read port for one already-resolved persisted menu day."""

    def get_for_date(self, service_date: date) -> ResolvedMenuDay | None: ...


class ProfileRepository(Protocol):
    def insert_version(self, profile: NutritionProfile, snapshot: SnapshotRef) -> UUID:
        """Insert a new immutable version; return existing id when (food, sha) known."""

    def latest_for_food(self, food_id: UUID) -> NutritionProfile | None: ...


class QuarantineRepository(Protocol):
    def add(self, record: QuarantineRecord) -> None: ...
    def list_by_run(self, run_id: UUID) -> list[QuarantineRecord]: ...


class HealthBodyMassRepository(Protocol):
    """Durable store for HealthKit body-mass samples (M5, ADR-016).

    Implementations must mirror the semantics of migration 0002 exactly:
    idempotent upsert on (user_id, sample_uuid), tombstone-only rows for
    deletions arriving before their sample, no resurrection of tombstoned
    UUIDs, first tombstoned_at retained, and per-user isolation (RLS in SQL).
    """

    def apply_batch(self, user_id: UUID, batch: SyncBatch) -> BatchOutcome:
        """Apply one batch atomically; return idempotency observability counts."""
        ...

    def status_summary(self, user_id: UUID) -> HealthSyncStatus: ...


class BodyMassHistoryRepository(Protocol):
    """Owner-scoped active body-mass history for deterministic derived reads."""

    def list_active(
        self,
        user_id: UUID,
        start_inclusive: datetime,
        end_exclusive: datetime,
    ) -> tuple[BodyMassObservation, ...]:
        """Return active observations in a half-open absolute-time range."""
        ...


class BodyProfileRepository(Protocol):
    """Owner profile data; profile updates are explicit owner actions."""

    def get(self, user_id: UUID) -> OwnerBodyProfile | None: ...

    def save(self, profile: OwnerBodyProfile) -> None: ...


class WaistMeasurementRepository(Protocol):
    """Append-only owner-entered waist evidence."""

    def append(self, measurement: WaistMeasurement) -> None: ...

    def list_recent(self, user_id: UUID, limit: int = 30) -> tuple[WaistMeasurement, ...]: ...


class TrainingSessionRepository(Protocol):
    """Owner-scoped immutable training observations with identity tombstones."""

    def apply_batch(self, user_id: UUID, batch: TrainingSyncBatch) -> TrainingBatchOutcome:
        """Apply complete observations and deletions atomically and idempotently."""
        ...

    def list_active(
        self,
        user_id: UUID,
        start_inclusive: datetime,
        end_exclusive: datetime,
    ) -> tuple[StoredTrainingSession, ...]:
        """Return active sessions attributed by start time in a half-open range."""
        ...


class DetailedTrainingSource(Protocol):
    """External detailed-training boundary; no transport details leak inward."""

    def load_changes(self, since: datetime | None) -> DetailedTrainingImportBatch: ...


class DetailedTrainingRevisionConflictError(Exception):
    """One immutable source revision identity was reused with different facts."""


class DetailedTrainingSourceFailureKind(StrEnum):
    NOT_CONFIGURED = "not_configured"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    MALFORMED_RESPONSE = "malformed_response"
    INCOMPLETE_PAGINATION = "incomplete_pagination"


class DetailedTrainingSourceError(RuntimeError):
    """Secret-safe typed failure from a detailed-training provider."""

    def __init__(
        self,
        kind: DetailedTrainingSourceFailureKind,
        detail: str,
        *,
        attempts_made: int = 0,
        retries: int = 0,
        retry_causes: tuple[str, ...] = (),
    ) -> None:
        super().__init__(detail)
        self.kind = kind
        self.attempts_made = attempts_made
        self.retries = retries
        self.retry_causes = retry_causes


class DetailedTrainingRepository(Protocol):
    """Owner-scoped immutable detailed-session revisions and tombstones."""

    def apply_import(
        self,
        user_id: UUID,
        batch: DetailedTrainingImportBatch,
        ingested_at: datetime,
    ) -> DetailedTrainingImportOutcome: ...

    def list_latest(
        self, user_id: UUID, limit: int
    ) -> tuple[DetailedTrainingSessionSummary, ...]: ...

    def get_by_revision_id(
        self, user_id: UUID, revision_id: UUID
    ) -> StoredDetailedTrainingSession | None: ...


class DetailedTrainingSyncSource(Protocol):
    """Bounded production source used by explicit detailed-training sync."""

    @property
    def is_configured(self) -> bool: ...

    def load_initial(self) -> DetailedTrainingSourceFetch: ...

    def load_changes(self, since: datetime) -> DetailedTrainingSourceFetch: ...


class DetailedTrainingSyncRepository(Protocol):
    """Atomic detailed import plus immutable continuation persistence."""

    def latest_sync_checkpoint(
        self,
        user_id: UUID,
        source_system: DetailedTrainingSourceSystem,
    ) -> DetailedTrainingSyncCheckpoint | None: ...

    def apply_sync(
        self,
        user_id: UUID,
        batch: DetailedTrainingImportBatch,
        ingested_at: datetime,
        checkpoint: DetailedTrainingSyncCheckpoint,
    ) -> DetailedTrainingImportOutcome: ...


class DetailedTrainingAnalyticsRepository(Protocol):
    """Bounded latest-active detailed revisions for compute-on-read analytics."""

    def list_latest_full(
        self,
        user_id: UUID,
        limit: int,
        *,
        source_system: DetailedTrainingSourceSystem | None = None,
        source_exercise_id: str | None = None,
    ) -> DetailedTrainingSessionPage: ...

    def latest_sync_checkpoint(
        self,
        user_id: UUID,
        source_system: DetailedTrainingSourceSystem,
    ) -> DetailedTrainingSyncCheckpoint | None: ...


class DuplicateLogicalPlanError(Exception):
    """A run with the same (user, date, fingerprint) already exists."""


@dataclass(frozen=True)
class PersistedPlanItemReference:
    """Persisted identity projection for one canonical artifact candidate."""

    item_id: UUID
    version_id: UUID
    slot_index: int
    rank: int
    candidate_id: str


@dataclass(frozen=True)
class PersistedPlanView:
    """Replay/read projection of one persisted daily plan (ADR-017)."""

    run_id: UUID
    user_id: UUID
    requested_for_date: date
    timezone: str
    inputs_fingerprint: str
    status: str
    reason_codes: tuple[str, ...]
    started_at: datetime
    finished_at: datetime
    version_id: UUID | None
    plan_sha256: str | None
    plan_jsonb: dict[str, object] | None
    plan_canonical: str | None
    target_policy_version_id: UUID | None = None
    plan_items: tuple[PersistedPlanItemReference, ...] = ()


class PlanRunRepository(Protocol):
    """User-owned plan persistence; SQL impl enforces RLS defense-in-depth."""

    def find_replay(
        self, user_id: UUID, requested_date: date, inputs_fingerprint: str
    ) -> PersistedPlanView | None: ...

    def latest_for_user_date(
        self, user_id: UUID, requested_date: date
    ) -> PersistedPlanView | None: ...

    def save(
        self,
        run: PlanRun,
        version: PlanVersion,
        items: Sequence[PlanItem],
    ) -> None:
        """Atomic append. Raises DuplicateLogicalPlanError on fingerprint reuse."""
        ...


class DuplicateConsumptionError(Exception):
    """A consumption idempotency key or entry ID was reused inconsistently."""


class ConsumptionRepository(Protocol):
    """Append-only user-owned consumption persistence."""

    def save(self, entry: ConsumptionEntry) -> RecordConsumptionOutcome: ...

    def list_for_run(self, user_id: UUID, plan_run_id: UUID) -> tuple[ConsumptionEntry, ...]: ...


class DailyNutritionLedgerRepository(Protocol):
    """Read-only owner-scoped projection of frozen nutrition for eaten events."""

    def list_eaten_evidence(
        self,
        user_id: UUID,
        start_inclusive: datetime,
        end_exclusive: datetime,
    ) -> tuple[ConsumedNutritionEvidence, ...]: ...


class DuplicateManualFoodError(Exception):
    """A manual-food identity or event was reused inconsistently."""


class CustomFoodRepository(Protocol):
    """Immutable owner food versions and append-only manual consumption."""

    def save_version(self, version: CustomFoodVersion, *, create_identity: bool) -> None: ...

    def list_latest(self, user_id: UUID) -> tuple[CustomFoodVersion, ...]: ...

    def find_version(
        self, user_id: UUID, food_id: UUID, version_id: UUID
    ) -> CustomFoodVersion | None: ...

    def save_consumption(self, entry: ManualFoodConsumptionEntry) -> RecordManualFoodOutcome: ...

    def find_latest_by_source(
        self, user_id: UUID, source_system: str, source_key: str
    ) -> CustomFoodVersion | None: ...


class BarcodeProductProvider(Protocol):
    """One exact product-code lookup; never a fuzzy product search."""

    def lookup(self, barcode: str) -> BarcodeProduct: ...


class TargetPolicyVersionExistsError(Exception):
    """The user already approved a version with this label or payload hash."""


class TargetPolicyRepository(Protocol):
    def save_approved(
        self, policy: TargetPolicyVersion, rationale: str, decided_by_clock: datetime
    ) -> None:
        """Atomically persist the immutable version + its decision_log entry."""
        ...

    def latest_approved(self, user_id: UUID) -> TargetPolicyVersion | None: ...

    def list_approved_for_window(
        self,
        user_id: UUID,
        start_inclusive: datetime,
        end_exclusive: datetime,
    ) -> tuple[TargetPolicyVersion, ...]:
        """Return the boundary policy plus changes strictly inside the interval."""
        ...

    def find_by_version_id(self, user_id: UUID, version_id: UUID) -> TargetPolicyVersion | None: ...

    def find_by_version_label(self, user_id: UUID, policy_version: str) -> bool: ...


class DuplicateProteinProposalError(Exception):
    """Proposal evidence identity was reused with different content."""


class ProteinProposalDecisionConflictError(Exception):
    """A terminal proposal decision or event id conflicts."""


class StaleProteinProposalError(Exception):
    """The proposal no longer references the latest approved target."""


class ProteinTargetProposalRepository(Protocol):
    def save(self, proposal: ProteinTargetProposal) -> tuple[ProteinTargetProposal, bool]: ...
    def latest(self, user_id: UUID) -> ProteinTargetProposal | None: ...
    def find_by_id(self, user_id: UUID, proposal_id: UUID) -> ProteinTargetProposal | None: ...
    def find_decision(
        self, user_id: UUID, proposal_id: UUID
    ) -> ProteinTargetProposalDecision | None: ...
    def decide(
        self,
        proposal: ProteinTargetProposal,
        decision: ProteinTargetProposalDecision,
        resulting_policy: TargetPolicyVersion | None,
        target_decision_log: DecisionLogEntry | None,
    ) -> DecideProteinProposalOutcome: ...


class DuplicateNextMealRecommendationError(Exception):
    """A client request id was replayed with different client-supplied inputs."""


class NextMealRecommendationRepository(Protocol):
    def save(self, recommendation: NextMealRecommendation) -> PersistNextMealOutcome: ...
    def find_by_client_request_id(
        self, user_id: UUID, client_request_id: UUID
    ) -> NextMealRecommendation | None: ...
    def latest(self, user_id: UUID) -> NextMealRecommendation | None: ...

    def find_by_id(
        self, user_id: UUID, recommendation_id: UUID
    ) -> NextMealRecommendation | None: ...


class DuplicateNextMealConsumptionError(Exception):
    """A recommendation or client event was consumed inconsistently."""


class NextMealConsumptionRepository(Protocol):
    """Append-only factual snapshots of selected next-meal recommendations."""

    def save(self, entry: NextMealConsumptionEntry) -> RecordNextMealConsumptionOutcome: ...

    def find_for_recommendation(
        self, user_id: UUID, recommendation_id: UUID
    ) -> NextMealConsumptionEntry | None: ...


class GoalPolicyVersionExistsError(Exception):
    """A goal-policy version label or semantic payload already exists."""


class GoalPolicyRepository(Protocol):
    """Append-only owner-scoped goal intent, separate from nutrient targets."""

    def save(self, policy: GoalPolicyVersion) -> None: ...

    def latest(self, user_id: UUID) -> GoalPolicyVersion | None: ...

    def find_by_version_id(self, user_id: UUID, version_id: UUID) -> GoalPolicyVersion | None: ...


class DuplicateTargetReviewError(Exception):
    """A review digest or row identity was reused with conflicting evidence."""


class TargetReviewRepository(Protocol):
    """Immutable target-review evidence with digest-based replay semantics."""

    def save(self, review: TargetReview) -> PersistTargetReviewOutcome: ...

    def find_by_digest(self, user_id: UUID, recommendation_digest: str) -> TargetReview | None: ...

    def find_by_id(self, user_id: UUID, review_id: UUID) -> TargetReview | None: ...


class TargetReviewDecisionConflictError(Exception):
    """A terminal decision or idempotency identity was reused inconsistently."""


class StaleTargetReviewError(Exception):
    """The latest goal or target policy no longer matches the reviewed inputs."""


class TargetReviewDecisionRepository(Protocol):
    """Atomic terminal lifecycle for one immutable target review."""

    def find_for_review(
        self, user_id: UUID, review_id: UUID
    ) -> DecideTargetReviewOutcome | None: ...

    def decide(
        self,
        review: TargetReview,
        decision: TargetReviewDecision,
        resulting_policy: TargetPolicyVersion | None,
        target_decision_log: DecisionLogEntry | None,
    ) -> DecideTargetReviewOutcome: ...


def terminal_states() -> frozenset[RunStatus]:
    return frozenset(
        {
            RunStatus.PERSISTED,
            RunStatus.PARTIAL_FAILURE,
            RunStatus.QUARANTINED,
            RunStatus.FAILED,
            RunStatus.BLOCKED_BY_POLICY,
        }
    )
