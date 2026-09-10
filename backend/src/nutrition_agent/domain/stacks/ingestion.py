"""Ingestion run states, error codes, and quarantine records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID


class RunStatus(StrEnum):
    STARTED = "started"
    FETCHING_MENU = "fetching_menu"
    MENU_FETCHED = "menu_fetched"
    MENU_PARSED = "menu_parsed"
    LABEL_FETCHING = "label_fetching"
    LABELS_FETCHED = "labels_fetched"
    NORMALIZED = "normalized"
    VALIDATING = "validating"
    VALIDATED = "validated"
    PERSISTING = "persisting"
    PERSISTED = "persisted"
    PARTIAL_FAILURE = "partial_failure"
    QUARANTINED = "quarantined"
    FAILED = "failed"
    BLOCKED_BY_POLICY = "blocked_by_policy"


class ErrorCode(StrEnum):
    EMPTY_MENU_PERIOD = "empty_menu_period"
    DATE_OUT_OF_WINDOW = "date_out_of_window"
    ITEM_LIMIT_EXCEEDED = "item_limit_exceeded"
    LABEL_FETCH_LIMIT_EXCEEDED = "label_fetch_limit_exceeded"
    SELECTION_ECHO_MISMATCH = "selection_echo_mismatch"
    INVALID_MID_LABEL_UNAVAILABLE = "invalid_mid_label_unavailable"
    PLACEHOLDER_LABEL = "placeholder_label"
    PARSER_MARKUP_MISMATCH = "parser_markup_mismatch"
    NUTRITION_MALFORMED = "nutrition_malformed"
    TRANSPORT_FAILURE = "transport_failure"
    TRANSPORT_BLOCKED_BY_POLICY = "transport_blocked_by_policy"
    SNAPSHOT_STORE_FAILURE = "snapshot_store_failure"
    DB_CONSTRAINT_FAILURE = "db_constraint_failure"


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class SubjectType(StrEnum):
    RUN = "run"
    PAGE = "page"
    ITEM = "item"
    LABEL = "label"


@dataclass(frozen=True)
class QuarantineRecord:
    record_id: UUID
    run_id: UUID
    code: ErrorCode
    severity: Severity
    subject_type: SubjectType
    natural_key: dict[str, str]
    detail: str
    parser_version: str
    snapshot_ref_sha256: str | None = None
    created_at: datetime | None = None


@dataclass
class RunStats:
    menu_requests: int = 0
    pages_fetched: int = 0
    pages_skipped_by_hash: int = 0
    pages_quarantined: int = 0
    empty_periods: int = 0
    offerings_seen: int = 0
    labels_fetched: int = 0
    labels_reused: int = 0
    unique_label_requests: int = 0
    label_fetch_cap_reached: int = 0
    profiles_persisted: int = 0
    profile_linked_offerings: int = 0
    non_profile_offerings: int = 0
    source_placeholder: int = 0
    source_incomplete: int = 0
    source_unavailable: int = 0
    items_quarantined: int = 0
    inserted_offerings: int = 0
    updated_offerings: int = 0
    superseded_profiles: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "menu_requests": self.menu_requests,
            "pages_fetched": self.pages_fetched,
            "pages_skipped_by_hash": self.pages_skipped_by_hash,
            "pages_quarantined": self.pages_quarantined,
            "empty_periods": self.empty_periods,
            "offerings_seen": self.offerings_seen,
            "labels_fetched": self.labels_fetched,
            "labels_reused": self.labels_reused,
            "unique_label_requests": self.unique_label_requests,
            "label_fetch_cap_reached": self.label_fetch_cap_reached,
            "profiles_persisted": self.profiles_persisted,
            "profile_linked_offerings": self.profile_linked_offerings,
            "non_profile_offerings": self.non_profile_offerings,
            "source_placeholder": self.source_placeholder,
            "source_incomplete": self.source_incomplete,
            "source_unavailable": self.source_unavailable,
            "items_quarantined": self.items_quarantined,
            "inserted_offerings": self.inserted_offerings,
            "updated_offerings": self.updated_offerings,
            "superseded_profiles": self.superseded_profiles,
        }


@dataclass
class IngestionRun:
    run_id: UUID
    status: RunStatus
    mode: str
    params: dict[str, str]
    config_fingerprint: str
    started_at: datetime
    finished_at: datetime | None = None
    stats: RunStats = field(default_factory=RunStats)


@dataclass(frozen=True)
class IngestionRunReport:
    run_id: UUID
    status: RunStatus
    stats: dict[str, int]
    quarantines_by_code: dict[str, int]
    advertised_dates: tuple[date, ...] = ()

    def summary(self) -> str:
        lines = [f"run={self.run_id} status={self.status.value}"]
        lines += [f"  {k}={v}" for k, v in self.stats.items()]
        lines += [f"  quarantine[{k}]={v}" for k, v in sorted(self.quarantines_by_code.items())]
        return "\n".join(lines)
