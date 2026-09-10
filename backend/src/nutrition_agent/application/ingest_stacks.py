"""Stacks ingestion use case: the Milestone 2 lifecycle state machine.

Implements ADR-013: snapshot-first, fail-closed, versioned profiles, no deletes.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Protocol
from uuid import NAMESPACE_OID, UUID, uuid5
from zoneinfo import ZoneInfo

from nutrition_agent.application.ports import (
    Clock,
    FoodRepository,
    IdGenerator,
    IngestCommand,
    MenuPageValidationState,
    MenuPageVersionRepository,
    NutritionAuthorityKey,
    OfferingRepository,
    PageLabelAuthorityKey,
    PreparedMenuPageOffering,
    ProfileRepository,
    QuarantineRepository,
    ReusableNutritionAuthority,
    ReusablePageLabelAuthority,
    RunRepository,
    SnapshotRepository,
    ValidatedMenuLabelObservation,
    ValidatedMenuPage,
)
from nutrition_agent.domain.stacks.entities import (
    MealPeriod,
    MenuOffering,
    NormalizedOfferingInput,
    NutritionProfile,
    NutritionSourceState,
    Provenance,
)
from nutrition_agent.domain.stacks.ingestion import (
    ErrorCode,
    IngestionRun,
    IngestionRunReport,
    QuarantineRecord,
    RunStatus,
    Severity,
    SubjectType,
)
from nutrition_agent.infrastructure.compliance_gate import BlockedByPolicy, ComplianceGate
from nutrition_agent.infrastructure.http_transport import TransportError
from nutrition_agent.infrastructure.snapshot_store import (
    RawPage,
    SnapshotRef,
    SnapshotStore,
)
from nutrition_agent.infrastructure.stacks_source.constants import (
    PARSER_VERSION,
    SOURCE_SYSTEM_INSTITUTIONAL,
    STACKS_CAMPUS_ID,
)
from nutrition_agent.infrastructure.stacks_source.label_parser import LabelPageParser
from nutrition_agent.infrastructure.stacks_source.menu_parser import MenuPageParser
from nutrition_agent.infrastructure.stacks_source.normalizer import (
    assign_occurrence_ordinals,
    menu_day_from_parsed,
    profile_from_parsed,
)

CAMPUS_TZ = ZoneInfo("America/New_York")
WINDOW_DAYS = 7


class StacksSource(Protocol):
    def fetch_menu_page(self, service_date: date, meal_period: MealPeriod) -> RawPage: ...
    def fetch_label(self, mid_instance: str) -> RawPage: ...


class PageOutcome(enum.Enum):
    OK = "ok"
    QUARANTINED_PAGE = "quarantined_page"
    TRANSPORT_FATAL = "transport_fatal"


class _LabelFetchCapReached(RuntimeError):
    """A bounded caller exhausted its unique label-request allowance."""


@dataclass(frozen=True)
class _CachedLabelFetch:
    name_normalized: str
    raw: RawPage | None = None
    transport_error: str | None = None


@dataclass(frozen=True)
class _ResolvedLabel:
    name_normalized: str
    profile: NutritionProfile | None
    nutrition_source_state: NutritionSourceState
    nutrition_snapshot: SnapshotRef


@dataclass(frozen=True)
class IngestionDeps:
    gate: ComplianceGate
    source: StacksSource
    snapshots: SnapshotStore
    runs: RunRepository
    snapshot_repo: SnapshotRepository
    pages: MenuPageVersionRepository
    foods: FoodRepository
    offerings: OfferingRepository
    profiles: ProfileRepository
    quarantine: QuarantineRepository
    clock: Clock
    ids: IdGenerator
    max_items_per_page: int = 125
    parser_version: str = PARSER_VERSION
    max_unique_label_fetches: int | None = None


class IngestStacksUseCase:
    def __init__(self, deps: IngestionDeps) -> None:
        if deps.max_unique_label_fetches is not None and deps.max_unique_label_fetches < 1:
            raise ValueError("max_unique_label_fetches must be positive when configured")
        self._d = deps
        self._observed_advertised_dates: tuple[date, ...] = ()
        # One use-case instance is one manual command or one scheduled batch.
        # Exact mids are never requested twice during that bounded lifetime.
        self._label_fetch_cache: dict[str, _CachedLabelFetch] = {}
        self._resolved_label_cache: dict[str, _ResolvedLabel] = {}
        self._unique_label_requests = 0

    def execute(self, command: IngestCommand) -> IngestionRunReport:
        self._observed_advertised_dates = ()
        run = self._start_run(command)
        try:
            self._d.gate.ensure_fetch_allowed(SOURCE_SYSTEM_INSTITUTIONAL)
        except BlockedByPolicy as exc:
            return self._finish_blocked(run, str(exc))

        allowed_dates = self._advertised_window()
        error_quarantines = 0

        for meal in command.meal_periods:
            if command.service_date not in allowed_dates:
                self._quarantine(
                    run.run_id,
                    ErrorCode.DATE_OUT_OF_WINDOW,
                    Severity.ERROR,
                    SubjectType.PAGE,
                    {"service_date": command.service_date.isoformat(), "meal": meal.value},
                    "requested date outside advertised window "
                    f"{self._window_bounds(allowed_dates)}",
                )
                error_quarantines += 1
                continue

            outcome = self._ingest_page(run, command.service_date, meal)
            if outcome is PageOutcome.TRANSPORT_FATAL:
                return self._finish(run, RunStatus.FAILED)
            if outcome is PageOutcome.QUARANTINED_PAGE:
                error_quarantines += 1

        saw_item_quarantines = run.stats.items_quarantined > 0
        final_status = (
            RunStatus.PERSISTED
            if error_quarantines == 0 and not saw_item_quarantines
            else RunStatus.PARTIAL_FAILURE
        )
        return self._finish(run, final_status)

    # ------------------------------------------------------------------ pages

    def _ingest_page(self, run: IngestionRun, service_date: date, meal: MealPeriod) -> PageOutcome:
        if self._d.pages.has_accepted_observation(
            run.run_id,
            service_date,
            meal,
            STACKS_CAMPUS_ID,
        ):
            run.stats.pages_skipped_by_hash += 1
            return PageOutcome.OK

        self._set_status(run, RunStatus.FETCHING_MENU)
        page_key = {"service_date": service_date.isoformat(), "meal": meal.value}
        run.stats.menu_requests += 1
        try:
            raw_menu = self._d.source.fetch_menu_page(service_date, meal)
        except TransportError as exc:
            self._quarantine(
                run.run_id,
                ErrorCode.TRANSPORT_FAILURE,
                Severity.ERROR,
                SubjectType.PAGE,
                dict(page_key),
                str(exc),
            )
            return PageOutcome.TRANSPORT_FATAL

        run.stats.pages_fetched += 1

        menu_ref = self._save_snapshot(run, raw_menu)
        if raw_menu.http_status != 200:
            self._quarantine(
                run.run_id,
                ErrorCode.TRANSPORT_FAILURE,
                Severity.ERROR,
                SubjectType.PAGE,
                dict(page_key),
                f"menu page returned HTTP {raw_menu.http_status}",
                snapshot_sha=menu_ref.content_sha256,
            )
            run.stats.pages_quarantined += 1
            return PageOutcome.TRANSPORT_FATAL

        self._set_status(run, RunStatus.MENU_PARSED)
        parsed = MenuPageParser().parse(
            raw_menu.body.decode("utf-8"), service_date, meal, STACKS_CAMPUS_ID
        )
        if not parsed.ok or parsed.value is None:
            detail = parsed.failure.detail if parsed.failure else "unknown parse failure"
            self._quarantine(
                run.run_id,
                ErrorCode.PARSER_MARKUP_MISMATCH,
                Severity.ERROR,
                SubjectType.PAGE,
                dict(page_key),
                detail,
                snapshot_sha=menu_ref.content_sha256,
            )
            run.stats.pages_quarantined += 1
            return PageOutcome.QUARANTINED_PAGE

        for failure in parsed.value.item_failures:
            self._quarantine(
                run.run_id,
                _error_code_from_parser(failure.code),
                Severity.ERROR,
                SubjectType.ITEM,
                {**page_key, "mid": _mid_from_detail(failure.detail)},
                failure.detail,
                snapshot_sha=menu_ref.content_sha256,
            )
        run.stats.items_quarantined += len(parsed.value.item_failures)
        total_parsed_items = sum(len(c.items) for c in parsed.value.categories)
        if parsed.value.item_failures and total_parsed_items == 0:
            run.stats.pages_quarantined += 1
            return PageOutcome.QUARANTINED_PAGE

        if not parsed.value.selection_echo_ok:
            self._quarantine(
                run.run_id,
                ErrorCode.SELECTION_ECHO_MISMATCH,
                Severity.ERROR,
                SubjectType.PAGE,
                dict(page_key),
                "rendered selection does not match request",
                snapshot_sha=menu_ref.content_sha256,
            )
            run.stats.pages_quarantined += 1
            return PageOutcome.QUARANTINED_PAGE

        if service_date not in parsed.value.date_window:
            self._quarantine(
                run.run_id,
                ErrorCode.DATE_OUT_OF_WINDOW,
                Severity.ERROR,
                SubjectType.PAGE,
                dict(page_key),
                "requested date is absent from the rendered advertised window",
                snapshot_sha=menu_ref.content_sha256,
            )
            run.stats.pages_quarantined += 1
            return PageOutcome.QUARANTINED_PAGE

        # This is source evidence from the rendered, selection-validated page,
        # not the scheduler's configured maximum window. Scheduled refresh uses
        # it to enumerate only dates the source currently advertises.
        self._observed_advertised_dates = tuple(parsed.value.date_window)

        if parsed.value.empty_period:
            self._d.pages.persist_validated_page(
                ValidatedMenuPage(
                    page_version_id=self._d.ids.new_id(),
                    service_date=service_date,
                    meal_period=meal,
                    campus_id=STACKS_CAMPUS_ID,
                    snapshot_id=menu_ref.snapshot_id,
                    ingestion_run_id=run.run_id,
                    validation_state=MenuPageValidationState.VALIDATED_EMPTY,
                    accepted_at=self._d.clock.now(),
                    parser_version=self._d.parser_version,
                    offerings=(),
                )
            )
            run.stats.empty_periods += 1
            self._quarantine(
                run.run_id,
                ErrorCode.EMPTY_MENU_PERIOD,
                Severity.INFO,
                SubjectType.PAGE,
                dict(page_key),
                "source published no items for this period",
                snapshot_sha=menu_ref.content_sha256,
            )
            return PageOutcome.OK

        menu_day = menu_day_from_parsed(parsed.value)
        if len(menu_day.offerings) > self._d.max_items_per_page:
            self._quarantine(
                run.run_id,
                ErrorCode.ITEM_LIMIT_EXCEEDED,
                Severity.ERROR,
                SubjectType.PAGE,
                dict(page_key),
                f"parsed {len(menu_day.offerings)} items; limit is {self._d.max_items_per_page}",
                snapshot_sha=menu_ref.content_sha256,
            )
            run.stats.pages_quarantined += 1
            return PageOutcome.QUARANTINED_PAGE
        ordinals = assign_occurrence_ordinals(menu_day.offerings)
        run.stats.offerings_seen += len(menu_day.offerings)

        authority_keys = tuple(
            NutritionAuthorityKey(
                campus_id=offering.campus_id,
                name_normalized=offering.name_normalized,
                source_mid=offering.source_mid,
            )
            for offering in menu_day.offerings
        )
        page_label_keys = tuple(
            PageLabelAuthorityKey(
                service_date=offering.service_date,
                meal_period=offering.meal_period,
                campus_id=offering.campus_id,
                name_normalized=offering.name_normalized,
                source_mid=offering.source_mid,
                occurrence_ordinal=ordinals[index],
            )
            for index, offering in enumerate(menu_day.offerings)
        )
        reusable_page_labels = {
            authority.key: authority
            for authority in self._d.pages.find_reusable_page_label_authorities(
                page_label_keys,
                parser_version=self._d.parser_version,
                fetched_not_before=self._d.clock.now() - timedelta(days=WINDOW_DAYS),
            )
        }
        reusable_authorities = {
            authority.key: authority
            for authority in self._d.pages.find_reusable_nutrition_authorities(
                authority_keys,
                parser_version=self._d.parser_version,
                fetched_not_before=self._d.clock.now() - timedelta(days=WINDOW_DAYS),
            )
        }

        self._set_status(run, RunStatus.LABEL_FETCHING)
        prepared: list[PreparedMenuPageOffering] = []
        try:
            for index, offering_input in enumerate(menu_day.offerings):
                page_label_key = page_label_keys[index]
                reusable_page_label = reusable_page_labels.get(page_label_key)
                food_id = uuid5(
                    NAMESPACE_OID,
                    f"stacks-food:{offering_input.campus_id}:{offering_input.name_normalized}",
                )
                offering = MenuOffering(
                    offering_id=self._d.ids.new_id(),
                    service_date=offering_input.service_date,
                    meal_period=offering_input.meal_period,
                    campus_id=offering_input.campus_id,
                    food_id=food_id,
                    occurrence_ordinal=ordinals[index],
                    category_name=offering_input.category_name,
                    category_position=offering_input.category_position,
                    item_position=offering_input.item_position,
                    source_mid=offering_input.source_mid,
                    dietary_tags=offering_input.dietary_tags,
                    profile_id=None,
                    snapshot_id=menu_ref.snapshot_id,
                )
                profile, profile_snapshot, source_state, nutrition_snapshot = self._prepare_profile(
                    run,
                    food_id,
                    offering_input,
                    reusable_page_label,
                    reusable_authorities.get(
                        NutritionAuthorityKey(
                            campus_id=offering_input.campus_id,
                            name_normalized=offering_input.name_normalized,
                            source_mid=offering_input.source_mid,
                        )
                    ),
                )
                if (
                    reusable_page_label is None
                    and source_state is not None
                    and nutrition_snapshot is not None
                ):
                    observed = self._d.pages.persist_label_observation(
                        ValidatedMenuLabelObservation(
                            observation_id=self._d.ids.new_id(),
                            key=page_label_key,
                            menu_snapshot=menu_ref,
                            food_id=profile.food_id if profile is not None else food_id,
                            food_name_raw=offering_input.name_raw,
                            nutrition_source_state=source_state,
                            profile=profile,
                            nutrition_snapshot=nutrition_snapshot,
                            parser_version=self._d.parser_version,
                            ingestion_run_id=run.run_id,
                            recorded_at=self._d.clock.now(),
                        )
                    )
                    profile = observed.profile
                    profile_snapshot = (
                        observed.nutrition_snapshot if observed.profile is not None else None
                    )
                    source_state = observed.nutrition_source_state
                    nutrition_snapshot = observed.nutrition_snapshot
                prepared.append(
                    PreparedMenuPageOffering(
                        offering=offering,
                        food_name_raw=offering_input.name_raw,
                        food_name_normalized=offering_input.name_normalized,
                        profile=profile,
                        profile_snapshot=profile_snapshot,
                        nutrition_source_state=source_state,
                        nutrition_snapshot=nutrition_snapshot,
                    )
                )
        except _LabelFetchCapReached:
            run.stats.label_fetch_cap_reached = 1
            run.stats.pages_quarantined += 1
            self._quarantine(
                run.run_id,
                ErrorCode.LABEL_FETCH_LIMIT_EXCEEDED,
                Severity.ERROR,
                SubjectType.PAGE,
                dict(page_key),
                "bounded canary unique-label fetch cap reached; page was not accepted",
                snapshot_sha=menu_ref.content_sha256,
            )
            self._persist_unaccepted_normalized_page(run, prepared)
            return PageOutcome.QUARANTINED_PAGE

        # Preserve M2's partial normalized cache for diagnostics and the
        # fixture-backed M6 demo, but never create affirmative page acceptance
        # when menu-item parsing or any nutrition-label validation failed.
        if parsed.value.item_failures or any(
            item.nutrition_source_state is None for item in prepared
        ):
            self._persist_unaccepted_normalized_page(run, prepared)
            return PageOutcome.OK

        outcome = self._d.pages.persist_validated_page(
            ValidatedMenuPage(
                page_version_id=self._d.ids.new_id(),
                service_date=service_date,
                meal_period=meal,
                campus_id=STACKS_CAMPUS_ID,
                snapshot_id=menu_ref.snapshot_id,
                ingestion_run_id=run.run_id,
                validation_state=MenuPageValidationState.VALIDATED_NONEMPTY,
                accepted_at=self._d.clock.now(),
                parser_version=self._d.parser_version,
                offerings=prepared,
            )
        )
        run.stats.inserted_offerings += outcome.inserted_offerings
        run.stats.updated_offerings += outcome.updated_offerings
        run.stats.profiles_persisted += outcome.profiles_persisted
        run.stats.profile_linked_offerings += sum(
            item.nutrition_source_state is NutritionSourceState.PROFILE_AVAILABLE
            for item in prepared
        )
        source_placeholder = sum(
            item.nutrition_source_state is NutritionSourceState.SOURCE_PLACEHOLDER
            for item in prepared
        )
        source_incomplete = sum(
            item.nutrition_source_state is NutritionSourceState.SOURCE_INCOMPLETE
            for item in prepared
        )
        source_unavailable = sum(
            item.nutrition_source_state is NutritionSourceState.SOURCE_UNAVAILABLE
            for item in prepared
        )
        run.stats.source_placeholder += source_placeholder
        run.stats.source_incomplete += source_incomplete
        run.stats.source_unavailable += source_unavailable
        run.stats.non_profile_offerings += (
            source_placeholder + source_incomplete + source_unavailable
        )

        return PageOutcome.OK

    # ----------------------------------------------------------------- labels

    def _prepare_profile(
        self,
        run: IngestionRun,
        food_id: UUID,
        offering_input: NormalizedOfferingInput,
        reusable_page_label: ReusablePageLabelAuthority | None,
        reusable: ReusableNutritionAuthority | None,
    ) -> tuple[
        NutritionProfile | None,
        SnapshotRef | None,
        NutritionSourceState | None,
        SnapshotRef | None,
    ]:
        item_key = {
            "name_normalized": offering_input.name_normalized,
            "mid": offering_input.source_mid,
        }
        resolved = self._resolved_label_cache.get(offering_input.source_mid)
        if resolved is not None and resolved.name_normalized != offering_input.name_normalized:
            run.stats.items_quarantined += 1
            self._quarantine(
                run.run_id,
                ErrorCode.NUTRITION_MALFORMED,
                Severity.ERROR,
                SubjectType.ITEM,
                item_key,
                "one source mid appeared under multiple normalized menu names",
            )
            return None, None, None, None
        if reusable_page_label is not None:
            resolved = _ResolvedLabel(
                name_normalized=offering_input.name_normalized,
                profile=reusable_page_label.profile,
                nutrition_source_state=reusable_page_label.nutrition_source_state,
                nutrition_snapshot=reusable_page_label.nutrition_snapshot,
            )
            self._resolved_label_cache[offering_input.source_mid] = resolved
            run.stats.labels_reused += 1
            return (
                resolved.profile,
                resolved.nutrition_snapshot if resolved.profile is not None else None,
                resolved.nutrition_source_state,
                resolved.nutrition_snapshot,
            )
        if resolved is not None:
            run.stats.labels_reused += 1
            return (
                resolved.profile,
                resolved.nutrition_snapshot if resolved.profile is not None else None,
                resolved.nutrition_source_state,
                resolved.nutrition_snapshot,
            )
        cached = self._label_fetch_cache.get(offering_input.source_mid)
        if cached is not None and cached.name_normalized != offering_input.name_normalized:
            run.stats.items_quarantined += 1
            self._quarantine(
                run.run_id,
                ErrorCode.NUTRITION_MALFORMED,
                Severity.ERROR,
                SubjectType.ITEM,
                item_key,
                "one source mid appeared under multiple normalized menu names",
            )
            return None, None, None, None

        if reusable is not None:
            resolved = _ResolvedLabel(
                name_normalized=offering_input.name_normalized,
                profile=reusable.profile,
                nutrition_source_state=NutritionSourceState.PROFILE_AVAILABLE,
                nutrition_snapshot=reusable.nutrition_snapshot,
            )
            self._resolved_label_cache[offering_input.source_mid] = resolved
            run.stats.labels_reused += 1
            return (
                reusable.profile,
                reusable.nutrition_snapshot,
                NutritionSourceState.PROFILE_AVAILABLE,
                reusable.nutrition_snapshot,
            )

        if cached is None:
            if (
                self._d.max_unique_label_fetches is not None
                and self._unique_label_requests >= self._d.max_unique_label_fetches
            ):
                raise _LabelFetchCapReached
            self._unique_label_requests += 1
            run.stats.unique_label_requests += 1
            try:
                raw_label = self._d.source.fetch_label(offering_input.source_mid)
            except TransportError as exc:
                cached = _CachedLabelFetch(
                    name_normalized=offering_input.name_normalized,
                    transport_error=str(exc),
                )
            else:
                cached = _CachedLabelFetch(
                    name_normalized=offering_input.name_normalized,
                    raw=raw_label,
                )
                run.stats.labels_fetched += 1
            self._label_fetch_cache[offering_input.source_mid] = cached
        else:
            run.stats.labels_reused += 1

        if cached.transport_error is not None:
            run.stats.items_quarantined += 1
            self._quarantine(
                run.run_id,
                ErrorCode.TRANSPORT_FAILURE,
                Severity.ERROR,
                SubjectType.ITEM,
                item_key,
                cached.transport_error,
            )
            return None, None, None, None

        cached_raw = cached.raw
        if cached_raw is None:
            run.stats.items_quarantined += 1
            self._quarantine(
                run.run_id,
                ErrorCode.DB_CONSTRAINT_FAILURE,
                Severity.ERROR,
                SubjectType.ITEM,
                item_key,
                "accepted nutrition authority cache was inconsistent",
            )
            return None, None, None, None
        raw_label = cached_raw

        label_ref = self._save_snapshot(run, raw_label)
        if raw_label.http_status != 200:
            run.stats.items_quarantined += 1
            self._quarantine(
                run.run_id,
                ErrorCode.TRANSPORT_FAILURE,
                Severity.ERROR,
                SubjectType.LABEL,
                item_key,
                f"nutrition label returned HTTP {raw_label.http_status}",
                snapshot_sha=label_ref.content_sha256,
            )
            return None, None, None, label_ref

        parsed = LabelPageParser().parse(raw_label.body.decode("utf-8"))
        if not parsed.ok or parsed.value is None:
            run.stats.items_quarantined += 1
            self._quarantine(
                run.run_id,
                _error_code_from_parser(parsed.failure.code)
                if parsed.failure
                else ErrorCode.PARSER_MARKUP_MISMATCH,
                Severity.ERROR,
                SubjectType.LABEL,
                item_key,
                parsed.failure.detail if parsed.failure else "unknown parse failure",
                snapshot_sha=label_ref.content_sha256,
            )
            return None, None, None, label_ref

        label = parsed.value
        if label.source_state is not NutritionSourceState.PROFILE_AVAILABLE:
            self._resolved_label_cache[offering_input.source_mid] = _ResolvedLabel(
                name_normalized=offering_input.name_normalized,
                profile=None,
                nutrition_source_state=label.source_state,
                nutrition_snapshot=label_ref,
            )
            return None, None, label.source_state, label_ref

        provenance = Provenance(
            snapshot_id=label_ref.snapshot_id,
            content_sha256=label_ref.content_sha256,
            source_url=label_ref.source_url,
            parser_version=self._d.parser_version,
            fetched_at=label_ref.fetched_at,
        )
        profile = profile_from_parsed(label, food_id, provenance)
        self._resolved_label_cache[offering_input.source_mid] = _ResolvedLabel(
            name_normalized=offering_input.name_normalized,
            profile=profile,
            nutrition_source_state=NutritionSourceState.PROFILE_AVAILABLE,
            nutrition_snapshot=label_ref,
        )
        return (
            profile,
            label_ref,
            NutritionSourceState.PROFILE_AVAILABLE,
            label_ref,
        )

    def _persist_unaccepted_normalized_page(
        self, run: IngestionRun, prepared: list[PreparedMenuPageOffering]
    ) -> None:
        """Preserve the M2 partial cache without creating acceptance evidence."""
        for item in prepared:
            candidate = item.offering
            food_id, _ = self._d.foods.upsert_by_name(
                candidate.campus_id,
                item.food_name_raw,
                item.food_name_normalized,
            )
            offering_id, inserted = self._d.offerings.upsert(replace(candidate, food_id=food_id))
            if inserted:
                run.stats.inserted_offerings += 1
            else:
                run.stats.updated_offerings += 1
            if item.profile is not None and item.profile_snapshot is not None:
                profile_id = self._d.profiles.insert_version(
                    item.profile,
                    item.profile_snapshot,
                )
                self._d.offerings.link_profile(offering_id, profile_id)
                run.stats.profiles_persisted += 1

    # -------------------------------------------------------------- plumbing

    def _save_snapshot(self, run: IngestionRun, raw: RawPage) -> SnapshotRef:
        ref = self._d.snapshots.save(raw, run.run_id)
        return self._d.snapshot_repo.record(ref, self._d.parser_version)

    def _quarantine(
        self,
        run_id: UUID,
        code: ErrorCode,
        severity: Severity,
        subject: SubjectType,
        natural_key: dict[str, str],
        detail: str,
        snapshot_sha: str | None = None,
    ) -> None:
        record = QuarantineRecord(
            record_id=self._d.ids.new_id(),
            run_id=run_id,
            code=code,
            severity=severity,
            subject_type=subject,
            natural_key=natural_key,
            detail=detail,
            parser_version=self._d.parser_version,
            snapshot_ref_sha256=snapshot_sha,
            created_at=self._d.clock.now(),
        )
        self._d.quarantine.add(record)

    def _advertised_window(self) -> list[date]:
        today = self._d.clock.now().astimezone(CAMPUS_TZ).date()
        return [today + timedelta(days=offset) for offset in range(WINDOW_DAYS)]

    @staticmethod
    def _window_bounds(allowed: list[date]) -> str:
        if not allowed:
            return "unavailable"
        return f"{allowed[0].isoformat()}..{allowed[-1].isoformat()}"

    def _start_run(self, command: IngestCommand) -> IngestionRun:
        run = IngestionRun(
            run_id=self._d.ids.new_id(),
            status=RunStatus.STARTED,
            mode=self._d.gate.mode.value,
            params={
                "service_date": command.service_date.isoformat(),
                "meals": ",".join(m.value for m in command.meal_periods),
            },
            config_fingerprint=f"parser={self._d.parser_version}",
            started_at=self._d.clock.now(),
        )
        self._d.runs.create(run)
        return run

    def _set_status(self, run: IngestionRun, status: RunStatus) -> None:
        run.status = status
        self._d.runs.update(run)

    def _finish_blocked(self, run: IngestionRun, detail: str) -> IngestionRunReport:
        self._quarantine(
            run.run_id,
            ErrorCode.TRANSPORT_BLOCKED_BY_POLICY,
            Severity.INFO,
            SubjectType.RUN,
            {"mode": run.mode},
            detail,
        )
        return self._finish(run, RunStatus.BLOCKED_BY_POLICY)

    def _finish(self, run: IngestionRun, status: RunStatus) -> IngestionRunReport:
        run.status = status
        run.finished_at = self._d.clock.now()
        self._d.runs.update(run)
        records = self._d.quarantine.list_by_run(run.run_id)
        by_code: dict[str, int] = {}
        for record in records:
            by_code[record.code.value] = by_code.get(record.code.value, 0) + 1
        return IngestionRunReport(
            run_id=run.run_id,
            status=status,
            stats=run.stats.to_dict(),
            quarantines_by_code=by_code,
            advertised_dates=self._observed_advertised_dates,
        )


def _mid_from_detail(detail: str) -> str:
    marker = "mid="
    index = detail.find(marker)
    if index == -1:
        return ""
    tail = detail[index + len(marker) :]
    return tail.split(":", 1)[0].split(" ", 1)[0].strip("':, ")


def _error_code_from_parser(code: str) -> ErrorCode:
    try:
        return ErrorCode(code.lower())
    except ValueError:
        return ErrorCode.PARSER_MARKUP_MISMATCH
