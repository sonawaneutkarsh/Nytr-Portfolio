"""FastAPI application factory (M5 health sync and M9 derived trend API).

Wires settings, JWT verification, and the sync use case onto app.state so
routes stay thin and tests can swap any dependency. The repository is chosen
by configuration: a DSN selects the SQL implementation; otherwise the API runs
in degraded mode where every request returns 503 rather than bypassing auth.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from nutrition_agent.api.auth import TokenVerifier
from nutrition_agent.api.routes_ai_review import router as ai_review_router
from nutrition_agent.api.routes_barcode_foods import router as barcode_foods_router
from nutrition_agent.api.routes_body_goals import router as body_goals_router
from nutrition_agent.api.routes_consumption import router as consumption_router
from nutrition_agent.api.routes_generation import router as generation_router
from nutrition_agent.api.routes_health import router as health_router
from nutrition_agent.api.routes_manual_foods import router as manual_foods_router
from nutrition_agent.api.routes_nutrition import router as nutrition_router
from nutrition_agent.api.routes_planning import router as planning_router
from nutrition_agent.api.routes_progress import router as progress_router
from nutrition_agent.api.routes_recommendations import router as recommendations_router
from nutrition_agent.api.routes_target_reviews import router as target_review_router
from nutrition_agent.api.routes_training_analytics import router as training_analytics_router
from nutrition_agent.api.routes_training_detail import router as training_detail_router
from nutrition_agent.api.settings import HealthApiSettings
from nutrition_agent.application.ai_review import GenerateAIReviewUseCase
from nutrition_agent.application.barcode_foods import (
    ImportBarcodeFoodUseCase,
    LookupBarcodeFoodUseCase,
)
from nutrition_agent.application.body_goals import (
    AddWaistMeasurementUseCase,
    CreateStartingCalorieProposalUseCase,
    DecideStartingCalorieProposalUseCase,
    GetBodyGoalsUseCase,
    SaveBodyGoalProfileUseCase,
)
from nutrition_agent.application.body_mass_trend import BodyMassTrendUseCase
from nutrition_agent.application.consumption import (
    ListConsumptionForRunUseCase,
    RecordConsumptionUseCase,
)
from nutrition_agent.application.daily_nutrition_ledger import GetDailyNutritionLedgerUseCase
from nutrition_agent.application.daily_plan import GenerateDailyPlanUseCase
from nutrition_agent.application.detailed_training import (
    GetDetailedTrainingSessionUseCase,
    ListDetailedTrainingSessionsUseCase,
)
from nutrition_agent.application.health_sync import (
    HealthBodyMassSyncUseCase,
    HealthSyncDeps,
)
from nutrition_agent.application.hevy_sync import SyncHevyDetailedTrainingUseCase
from nutrition_agent.application.manual_foods import (
    AdjustManualFoodUseCase,
    CreateCustomFoodUseCase,
    ListCustomFoodsUseCase,
    RecordManualFoodUseCase,
)
from nutrition_agent.application.next_meal import GenerateNextMealRecommendationUseCase
from nutrition_agent.application.next_meal_consumption import (
    GetNextMealConsumptionUseCase,
    RecordNextMealConsumptionUseCase,
)
from nutrition_agent.application.nutrition_history import GetNutritionHistory7DayUseCase
from nutrition_agent.application.ports import (
    BarcodeProductProvider,
    BodyGoalsRepository,
    BodyMassHistoryRepository,
    ConsumptionRepository,
    DailyNutritionLedgerRepository,
    GoalPolicyRepository,
    HealthBodyMassRepository,
    MenuDayReadRepository,
    NextMealConsumptionRepository,
    NextMealRecommendationRepository,
    PersistedPlanView,
    PlanRunRepository,
    ProteinTargetProposalRepository,
    ResolvedMenuDay,
    TargetPolicyRepository,
    TargetReviewDecisionRepository,
    TargetReviewRepository,
)
from nutrition_agent.application.progress import GetLongitudinalProgressUseCase
from nutrition_agent.application.protein_target import (
    CreateProteinTargetProposalUseCase,
    DecideProteinTargetProposalUseCase,
)
from nutrition_agent.application.server_inputs import (
    PRODUCTION_SERVER_CONFIGURATION,
    DefaultServerInputsProvider,
    required_menu_periods,
)
from nutrition_agent.application.target_policy import ApproveTargetPolicyUseCase
from nutrition_agent.application.target_review import (
    CreateGoalPolicyUseCase,
    CreateTargetReviewUseCase,
    DecideTargetReviewUseCase,
)
from nutrition_agent.application.training_analytics import (
    GetExerciseIndexUseCase,
    GetExerciseTrainingHistoryUseCase,
    GetRecentTrainingAnalyticsUseCase,
)
from nutrition_agent.application.training_context import TrainingDayContextUseCase
from nutrition_agent.application.training_meal_context import TrainingAwareServerInputsProvider
from nutrition_agent.application.training_sync import (
    TrainingSessionSyncUseCase,
    TrainingSyncDeps,
)
from nutrition_agent.db.in_memory_repos import InMemoryHealthBodyMassRepository
from nutrition_agent.domain.consumption import ConsumptionEntry, RecordConsumptionOutcome
from nutrition_agent.domain.health.trend import BodyMassObservation
from nutrition_agent.domain.next_meal import NextMealRecommendation, PersistNextMealOutcome
from nutrition_agent.domain.next_meal_consumption import (
    NextMealConsumptionEntry,
    RecordNextMealConsumptionOutcome,
)
from nutrition_agent.domain.nutrition.ledger import ConsumedNutritionEvidence
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
from nutrition_agent.domain.target_review import (
    M10A_TARGET_REVIEW_POLICY,
    DecideTargetReviewOutcome,
    PersistTargetReviewOutcome,
    TargetReview,
    TargetReviewDecision,
)


class _UtcClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class HealthApiDeps:
    """Everything the health router needs, swappable for tests."""

    def __init__(
        self,
        settings: HealthApiSettings,
        verifier: TokenVerifier,
        use_case: HealthBodyMassSyncUseCase,
        trend_use_case: BodyMassTrendUseCase | None = None,
        training_use_case: TrainingSessionSyncUseCase | None = None,
        training_context_use_case: TrainingDayContextUseCase | None = None,
        training_detail_list_use_case: ListDetailedTrainingSessionsUseCase | None = None,
        training_detail_get_use_case: GetDetailedTrainingSessionUseCase | None = None,
        training_detail_sync_use_case: SyncHevyDetailedTrainingUseCase | None = None,
        training_analytics_recent: GetRecentTrainingAnalyticsUseCase | None = None,
        training_analytics_index: GetExerciseIndexUseCase | None = None,
        training_analytics_history: GetExerciseTrainingHistoryUseCase | None = None,
        ai_review_use_case: GenerateAIReviewUseCase | None = None,
        barcode_product_provider: BarcodeProductProvider | None = None,
        body_mass_repository: HealthBodyMassRepository | None = None,
    ) -> None:
        self.settings = settings
        self.verifier = verifier
        self.use_case = use_case
        self.trend_use_case = trend_use_case
        self.training_use_case = training_use_case
        self.training_context_use_case = training_context_use_case
        self.training_detail_list_use_case = training_detail_list_use_case
        self.training_detail_get_use_case = training_detail_get_use_case
        self.training_detail_sync_use_case = training_detail_sync_use_case
        self.training_analytics_recent = training_analytics_recent
        self.training_analytics_index = training_analytics_index
        self.training_analytics_history = training_analytics_history
        self.ai_review_use_case = ai_review_use_case
        self.barcode_product_provider = barcode_product_provider
        self.body_mass_repository = body_mass_repository


class _PlanningStorageNotConfigured(RuntimeError):
    """Planning routes were built without a durable database DSN (fail closed)."""


class _FailClosedPlanRunRepository:
    """Structurally satisfies PlanRunRepository; every call fails closed.

    ADR-019: planning is durable-system-of-record only — without a DSN the
    routes must degrade to 503 storage_unavailable, never run against
    throwaway in-memory state. The routes' existing exception mapping turns
    this into the retryable 503.
    """

    def find_replay(
        self, user_id: UUID, requested_date: date, inputs_fingerprint: str
    ) -> PersistedPlanView | None:
        raise _PlanningStorageNotConfigured("planning storage not configured (no database DSN)")

    def latest_for_user_date(self, user_id: UUID, requested_date: date) -> PersistedPlanView | None:
        raise _PlanningStorageNotConfigured("planning storage not configured (no database DSN)")

    def save(self, run: PlanRun, version: PlanVersion, items: Sequence[PlanItem]) -> None:
        raise _PlanningStorageNotConfigured("planning storage not configured (no database DSN)")


class _FailClosedTargetPolicyRepository:
    """Structurally satisfies TargetPolicyRepository; every call fails closed."""

    def save_approved(
        self,
        policy: TargetPolicyVersion,
        rationale: str,
        decided_by_clock: datetime,
    ) -> None:
        raise _PlanningStorageNotConfigured("planning storage not configured (no database DSN)")

    def latest_approved(self, user_id: UUID) -> TargetPolicyVersion | None:
        raise _PlanningStorageNotConfigured("planning storage not configured (no database DSN)")

    def list_approved_for_window(
        self,
        user_id: UUID,
        start_inclusive: datetime,
        end_exclusive: datetime,
    ) -> tuple[TargetPolicyVersion, ...]:
        del user_id, start_inclusive, end_exclusive
        raise _PlanningStorageNotConfigured("planning storage not configured (no database DSN)")

    def find_by_version_id(self, user_id: UUID, version_id: UUID) -> TargetPolicyVersion | None:
        raise _PlanningStorageNotConfigured("planning storage not configured (no database DSN)")

    def find_by_version_label(self, user_id: UUID, policy_version: str) -> bool:
        raise _PlanningStorageNotConfigured("planning storage not configured (no database DSN)")


class _FailClosedGoalPolicyRepository:
    def save(self, policy: object) -> None:
        del policy
        raise _PlanningStorageNotConfigured(
            "target-review storage not configured (no database DSN)"
        )

    def latest(self, user_id: UUID) -> None:
        del user_id
        raise _PlanningStorageNotConfigured(
            "target-review storage not configured (no database DSN)"
        )

    def find_by_version_id(self, user_id: UUID, version_id: UUID) -> None:
        del user_id, version_id
        raise _PlanningStorageNotConfigured(
            "target-review storage not configured (no database DSN)"
        )


class _FailClosedTargetReviewRepository:
    def save(self, review: TargetReview) -> PersistTargetReviewOutcome:
        del review
        raise _PlanningStorageNotConfigured(
            "target-review storage not configured (no database DSN)"
        )

    def find_by_digest(self, user_id: UUID, recommendation_digest: str) -> None:
        del user_id, recommendation_digest
        raise _PlanningStorageNotConfigured(
            "target-review storage not configured (no database DSN)"
        )

    def find_by_id(self, user_id: UUID, review_id: UUID) -> None:
        del user_id, review_id
        raise _PlanningStorageNotConfigured(
            "target-review storage not configured (no database DSN)"
        )


class _FailClosedTargetReviewDecisionRepository:
    def find_for_review(self, user_id: UUID, review_id: UUID) -> None:
        del user_id, review_id
        raise _PlanningStorageNotConfigured(
            "target-review storage not configured (no database DSN)"
        )

    def decide(
        self,
        review: TargetReview,
        decision: TargetReviewDecision,
        resulting_policy: TargetPolicyVersion | None,
        target_decision_log: DecisionLogEntry | None,
    ) -> DecideTargetReviewOutcome:
        del review, decision, resulting_policy, target_decision_log
        raise _PlanningStorageNotConfigured(
            "target-review storage not configured (no database DSN)"
        )


class _FailClosedMenuDayReadRepository:
    """No durable menu store is available; provider builds must fail closed."""

    def get_for_date(self, service_date: date) -> ResolvedMenuDay | None:
        del service_date
        raise _PlanningStorageNotConfigured("planning storage not configured (no database DSN)")


class _FailClosedConsumptionRepository:
    """Consumption requires the same durable user-owned storage as plans."""

    def save(self, entry: ConsumptionEntry) -> RecordConsumptionOutcome:
        del entry
        raise _PlanningStorageNotConfigured("planning storage not configured (no database DSN)")

    def list_for_run(self, user_id: UUID, plan_run_id: UUID) -> tuple[ConsumptionEntry, ...]:
        del user_id, plan_run_id
        raise _PlanningStorageNotConfigured("planning storage not configured (no database DSN)")

    def list_eaten_evidence(
        self,
        user_id: UUID,
        start_inclusive: datetime,
        end_exclusive: datetime,
    ) -> tuple[ConsumedNutritionEvidence, ...]:
        del user_id, start_inclusive, end_exclusive
        raise _PlanningStorageNotConfigured("planning storage not configured (no database DSN)")


class _FailClosedNextMealRepository:
    def save(self, recommendation: NextMealRecommendation) -> PersistNextMealOutcome:
        del recommendation
        raise _PlanningStorageNotConfigured("next-meal storage not configured (no database DSN)")

    def latest(self, user_id: UUID) -> NextMealRecommendation | None:
        del user_id
        raise _PlanningStorageNotConfigured("next-meal storage not configured (no database DSN)")

    def find_by_client_request_id(
        self, user_id: UUID, client_request_id: UUID
    ) -> NextMealRecommendation | None:
        del user_id, client_request_id
        raise _PlanningStorageNotConfigured("next-meal storage not configured (no database DSN)")

    def find_by_id(self, user_id: UUID, recommendation_id: UUID) -> NextMealRecommendation | None:
        del user_id, recommendation_id
        raise _PlanningStorageNotConfigured("next-meal storage not configured (no database DSN)")


class _FailClosedNextMealConsumptionRepository:
    def save(self, entry: NextMealConsumptionEntry) -> RecordNextMealConsumptionOutcome:
        del entry
        raise _PlanningStorageNotConfigured("next-meal consumption storage not configured")

    def find_for_recommendation(
        self, user_id: UUID, recommendation_id: UUID
    ) -> NextMealConsumptionEntry | None:
        del user_id, recommendation_id
        raise _PlanningStorageNotConfigured("next-meal consumption storage not configured")


class _FailClosedProteinProposalRepository:
    def save(self, proposal: ProteinTargetProposal) -> tuple[ProteinTargetProposal, bool]:
        del proposal
        raise _PlanningStorageNotConfigured("protein proposal storage not configured")

    def latest(self, user_id: UUID) -> ProteinTargetProposal | None:
        del user_id
        raise _PlanningStorageNotConfigured("protein proposal storage not configured")

    def find_by_id(self, user_id: UUID, proposal_id: UUID) -> ProteinTargetProposal | None:
        del user_id, proposal_id
        raise _PlanningStorageNotConfigured("protein proposal storage not configured")

    def find_decision(
        self, user_id: UUID, proposal_id: UUID
    ) -> ProteinTargetProposalDecision | None:
        del user_id, proposal_id
        raise _PlanningStorageNotConfigured("protein proposal storage not configured")

    def decide(
        self,
        proposal: ProteinTargetProposal,
        decision: ProteinTargetProposalDecision,
        resulting_policy: TargetPolicyVersion | None,
        target_decision_log: DecisionLogEntry | None,
    ) -> DecideProteinProposalOutcome:
        del proposal, decision, resulting_policy, target_decision_log
        raise _PlanningStorageNotConfigured("protein proposal storage not configured")


class _HealthStorageNotConfigured(RuntimeError):
    """Derived health reads require the durable M5 system of record."""


class _FailClosedBodyMassHistoryRepository(BodyMassHistoryRepository):
    def list_active(
        self,
        user_id: UUID,
        start_inclusive: datetime,
        end_exclusive: datetime,
    ) -> tuple[BodyMassObservation, ...]:
        del user_id, start_inclusive, end_exclusive
        raise _HealthStorageNotConfigured("health storage not configured (no database DSN)")


def create_health_app(
    deps: HealthApiDeps | None = None,
    *,
    database_url: str | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    With no explicit deps: settings come from the environment; a configured
    DSN selects SqlHealthBodyMassRepository; without a DSN the sync use case
    retains its in-memory development behavior while the derived trend read
    fails closed because it requires the durable source of truth. Auth still
    gates everything (503 when unconfigured). Tests pass ``deps`` to inject
    fakes.
    """
    app = FastAPI(title="nutrition-agent API", version="0.7.0")
    clock = _UtcClock()
    ids = _Uuid4Generator()

    if deps is not None:
        settings, verifier, use_case = deps.settings, deps.verifier, deps.use_case
        repo: HealthBodyMassRepository = (
            deps.body_mass_repository or InMemoryHealthBodyMassRepository()
        )
        trend_use_case = deps.trend_use_case or BodyMassTrendUseCase(
            _FailClosedBodyMassHistoryRepository()
        )
        training_use_case = deps.training_use_case
        training_context_use_case = deps.training_context_use_case
        training_detail_list_use_case = deps.training_detail_list_use_case
        training_detail_get_use_case = deps.training_detail_get_use_case
        training_detail_sync_use_case = deps.training_detail_sync_use_case
        training_analytics_recent = deps.training_analytics_recent
        training_analytics_index = deps.training_analytics_index
        training_analytics_history = deps.training_analytics_history
        ai_review_use_case = deps.ai_review_use_case
        barcode_product_provider = deps.barcode_product_provider
        body_mass_history_repo: BodyMassHistoryRepository = _FailClosedBodyMassHistoryRepository()
    else:
        settings = HealthApiSettings.from_env()
        verifier = TokenVerifier(settings)
        if database_url is None:
            database_url = settings.database_url
        if database_url is not None:
            from nutrition_agent.db.sql_repos import (
                SqlDetailedTrainingRepository,
                SqlHealthBodyMassRepository,
                SqlTrainingSessionRepository,
            )

            repo = SqlHealthBodyMassRepository(database_url)
            body_mass_history_repo = repo
            trend_use_case = BodyMassTrendUseCase(repo)
            training_repository = SqlTrainingSessionRepository(database_url)
            training_use_case = TrainingSessionSyncUseCase(
                TrainingSyncDeps(training_repository, clock)
            )
            training_context_use_case = TrainingDayContextUseCase(training=training_repository)
            detail_repository = SqlDetailedTrainingRepository(database_url)
            training_detail_list_use_case = ListDetailedTrainingSessionsUseCase(detail_repository)
            training_detail_get_use_case = GetDetailedTrainingSessionUseCase(detail_repository)
            from nutrition_agent.infrastructure.hevy_api_provider import HevyApiProvider

            training_detail_sync_use_case = SyncHevyDetailedTrainingUseCase(
                source=HevyApiProvider(settings.hevy_api_key),
                repository=detail_repository,
                clock=clock,
                ids=ids,
            )
            training_analytics_recent = GetRecentTrainingAnalyticsUseCase(detail_repository)
            training_analytics_index = GetExerciseIndexUseCase(detail_repository)
            training_analytics_history = GetExerciseTrainingHistoryUseCase(detail_repository)
        else:
            repo = InMemoryHealthBodyMassRepository()
            body_mass_history_repo = _FailClosedBodyMassHistoryRepository()
            trend_use_case = BodyMassTrendUseCase(_FailClosedBodyMassHistoryRepository())
            training_use_case = None
            training_context_use_case = None
            training_detail_list_use_case = None
            training_detail_get_use_case = None
            training_detail_sync_use_case = None
            training_analytics_recent = None
            training_analytics_index = None
            training_analytics_history = None
        use_case = HealthBodyMassSyncUseCase(HealthSyncDeps(repository=repo, clock=clock))
        ai_review_use_case = None
        if settings.open_food_facts_user_agent is not None:
            from nutrition_agent.infrastructure.open_food_facts import OpenFoodFactsProvider

            barcode_product_provider = OpenFoodFactsProvider(settings.open_food_facts_user_agent)
        else:
            barcode_product_provider = None

    app.state.health_settings = settings
    app.state.health_token_verifier = verifier
    app.state.health_use_case = use_case
    app.state.health_trend_use_case = trend_use_case
    app.state.training_sync_use_case = training_use_case
    app.state.training_detail_list_use_case = training_detail_list_use_case
    app.state.training_detail_get_use_case = training_detail_get_use_case
    app.state.training_detail_sync_use_case = training_detail_sync_use_case
    app.state.training_analytics_recent = training_analytics_recent
    app.state.training_analytics_index = training_analytics_index
    app.state.training_analytics_history = training_analytics_history

    # M6 planning deps (ADR-019): SQL repositories when a database is
    # configured; otherwise FAIL CLOSED — durable storage is mandatory for
    # plan/policy writes, so the routes degrade to 503 storage_unavailable
    # instead of running against throwaway in-memory state. Explicit
    # dependency injection (tests) remains free to substitute in-memory
    # implementations.
    plan_runs: PlanRunRepository
    target_repo: TargetPolicyRepository
    menu_days: MenuDayReadRepository
    consumption_repo: ConsumptionRepository
    ledger_repo: DailyNutritionLedgerRepository
    goal_repo: GoalPolicyRepository
    review_repo: TargetReviewRepository
    review_decision_repo: TargetReviewDecisionRepository
    protein_proposal_repo: ProteinTargetProposalRepository
    next_meal_repo: NextMealRecommendationRepository
    next_meal_consumption_repo: NextMealConsumptionRepository
    body_goals_repo: BodyGoalsRepository | None
    if database_url is not None:
        from nutrition_agent.db.sql_repos import (
            SqlBodyGoalsRepository,
            SqlConsumptionRepository,
            SqlCustomFoodRepository,
            SqlGoalPolicyRepository,
            SqlMenuDayReadRepository,
            SqlNextMealConsumptionRepository,
            SqlNextMealRecommendationRepository,
            SqlPlanRunRepository,
            SqlProteinTargetProposalRepository,
            SqlTargetPolicyRepository,
            SqlTargetReviewDecisionRepository,
            SqlTargetReviewRepository,
        )

        plan_runs = SqlPlanRunRepository(database_url)
        target_repo = SqlTargetPolicyRepository(database_url)
        menu_days = SqlMenuDayReadRepository(
            database_url,
            required_periods=required_menu_periods(PRODUCTION_SERVER_CONFIGURATION),
        )
        consumption_repo = SqlConsumptionRepository(database_url)
        custom_food_repo = SqlCustomFoodRepository(database_url)
        ledger_repo = consumption_repo
        goal_repo = SqlGoalPolicyRepository(database_url)
        review_repo = SqlTargetReviewRepository(database_url)
        review_decision_repo = SqlTargetReviewDecisionRepository(database_url)
        protein_proposal_repo = SqlProteinTargetProposalRepository(database_url)
        next_meal_repo = SqlNextMealRecommendationRepository(database_url)
        next_meal_consumption_repo = SqlNextMealConsumptionRepository(database_url)
        body_goals_repo = SqlBodyGoalsRepository(database_url)
    else:
        plan_runs = _FailClosedPlanRunRepository()
        target_repo = _FailClosedTargetPolicyRepository()
        menu_days = _FailClosedMenuDayReadRepository()
        consumption_repo = _FailClosedConsumptionRepository()
        ledger_repo = consumption_repo
        goal_repo = _FailClosedGoalPolicyRepository()
        review_repo = _FailClosedTargetReviewRepository()
        review_decision_repo = _FailClosedTargetReviewDecisionRepository()
        custom_food_repo = None
        protein_proposal_repo = _FailClosedProteinProposalRepository()
        next_meal_repo = _FailClosedNextMealRepository()
        next_meal_consumption_repo = _FailClosedNextMealConsumptionRepository()
        body_goals_repo = None
    base_inputs_provider = DefaultServerInputsProvider(
        menu_days=menu_days,
        configuration=PRODUCTION_SERVER_CONFIGURATION,
    )
    inputs_provider = TrainingAwareServerInputsProvider(
        base=base_inputs_provider,
        training_contexts=training_context_use_case,
    )
    app.state.planning_run_repository = plan_runs
    app.state.planning_target_repository = target_repo
    app.state.planning_approval_use_case = ApproveTargetPolicyUseCase(
        repository=target_repo, clock=clock, ids=ids
    )
    app.state.planning_inputs_provider = inputs_provider
    app.state.planning_clock = clock
    app.state.planning_id_generator = ids
    app.state.planning_generation_use_case = GenerateDailyPlanUseCase(
        runs=plan_runs,
        clock=clock,
        ids=ids,
    )
    app.state.planning_consumption_repository = consumption_repo
    app.state.planning_record_consumption_use_case = RecordConsumptionUseCase(
        repository=consumption_repo,
        clock=clock,
        ids=ids,
    )
    app.state.planning_list_consumption_use_case = ListConsumptionForRunUseCase(
        repository=consumption_repo
    )
    app.state.daily_nutrition_ledger_use_case = GetDailyNutritionLedgerUseCase(
        consumption=ledger_repo,
        targets=target_repo,
    )
    app.state.nutrition_history_use_case = GetNutritionHistory7DayUseCase(
        consumption=ledger_repo,
        targets=target_repo,
    )
    app.state.longitudinal_progress_use_case = GetLongitudinalProgressUseCase(
        body_mass=body_mass_history_repo,
        consumption=ledger_repo,
        targets=target_repo,
        goals=goal_repo,
    )
    app.state.create_custom_food_use_case = (
        CreateCustomFoodUseCase(custom_food_repo, clock, ids)
        if custom_food_repo is not None
        else None
    )
    app.state.list_custom_foods_use_case = (
        ListCustomFoodsUseCase(custom_food_repo) if custom_food_repo is not None else None
    )
    app.state.record_manual_food_use_case = (
        RecordManualFoodUseCase(custom_food_repo, clock, ids)
        if custom_food_repo is not None
        else None
    )
    app.state.adjust_manual_food_use_case = (
        AdjustManualFoodUseCase(custom_food_repo, clock, ids)
        if custom_food_repo is not None
        else None
    )
    app.state.lookup_barcode_food_use_case = (
        LookupBarcodeFoodUseCase(barcode_product_provider)
        if barcode_product_provider is not None
        else None
    )
    app.state.import_barcode_food_use_case = (
        ImportBarcodeFoodUseCase(
            provider=barcode_product_provider,
            repository=custom_food_repo,
            create_food=app.state.create_custom_food_use_case,
        )
        if barcode_product_provider is not None and custom_food_repo is not None
        else None
    )
    app.state.target_review_goal_repository = goal_repo
    app.state.target_review_repository = review_repo
    app.state.target_review_decision_repository = review_decision_repo
    app.state.target_review_create_goal_use_case = CreateGoalPolicyUseCase(
        repository=goal_repo,
        clock=clock,
        ids=ids,
    )
    app.state.target_review_create_use_case = CreateTargetReviewUseCase(
        trends=trend_use_case,
        goals=goal_repo,
        targets=target_repo,
        reviews=review_repo,
        review_policy=M10A_TARGET_REVIEW_POLICY,
        clock=clock,
        ids=ids,
    )
    app.state.target_review_decide_use_case = DecideTargetReviewUseCase(
        reviews=review_repo,
        goals=goal_repo,
        targets=target_repo,
        decisions=review_decision_repo,
        clock=clock,
        ids=ids,
    )
    app.state.protein_proposal_repository = protein_proposal_repo
    app.state.protein_proposal_create_use_case = CreateProteinTargetProposalUseCase(
        body_mass=body_mass_history_repo,
        targets=target_repo,
        proposals=protein_proposal_repo,
        clock=clock,
        ids=ids,
    )
    app.state.protein_proposal_decide_use_case = DecideProteinTargetProposalUseCase(
        targets=target_repo,
        proposals=protein_proposal_repo,
        clock=clock,
        ids=ids,
    )
    app.state.next_meal_repository = next_meal_repo
    app.state.next_meal_generate_use_case = GenerateNextMealRecommendationUseCase(
        ledger=app.state.daily_nutrition_ledger_use_case,
        targets=target_repo,
        inputs=inputs_provider,
        recommendations=next_meal_repo,
        clock=clock,
        ids=ids,
    )
    app.state.next_meal_consumption_repository = next_meal_consumption_repo
    app.state.next_meal_consumption_record_use_case = RecordNextMealConsumptionUseCase(
        recommendations=next_meal_repo,
        consumptions=next_meal_consumption_repo,
        clock=clock,
        ids=ids,
    )
    app.state.next_meal_consumption_get_use_case = GetNextMealConsumptionUseCase(
        next_meal_consumption_repo
    )
    app.state.body_goals_repository = body_goals_repo
    app.state.body_goals_get_use_case = (
        GetBodyGoalsUseCase(
            repository=body_goals_repo,
            health=repo,
            trends=trend_use_case,
            goals=goal_repo,
            targets=target_repo,
        )
        if body_goals_repo is not None
        else None
    )
    app.state.body_goals_save_profile_use_case = (
        SaveBodyGoalProfileUseCase(body_goals_repo, clock, ids) if body_goals_repo else None
    )
    app.state.body_goals_add_waist_use_case = (
        AddWaistMeasurementUseCase(body_goals_repo, clock, ids) if body_goals_repo else None
    )
    app.state.body_goals_create_proposal_use_case = (
        CreateStartingCalorieProposalUseCase(
            repository=body_goals_repo,
            health=repo,
            goals=goal_repo,
            targets=target_repo,
            clock=clock,
            ids=ids,
        )
        if body_goals_repo
        else None
    )
    app.state.body_goals_decide_proposal_use_case = (
        DecideStartingCalorieProposalUseCase(body_goals_repo, target_repo, clock, ids)
        if body_goals_repo
        else None
    )
    if ai_review_use_case is None:
        from nutrition_agent.infrastructure.gemini_ai_review import GeminiAIReviewProvider

        ai_review_use_case = GenerateAIReviewUseCase(
            ledger=app.state.daily_nutrition_ledger_use_case,
            progress=app.state.longitudinal_progress_use_case,
            next_meals=next_meal_repo,
            provider=GeminiAIReviewProvider(
                settings.gemini_api_key,
                model=settings.gemini_model,
                enabled=settings.gemini_ai_review_enabled,
            ),
        )
    app.state.ai_review_use_case = ai_review_use_case
    app.include_router(health_router)
    app.include_router(planning_router)
    app.include_router(generation_router)
    app.include_router(consumption_router)
    app.include_router(nutrition_router)
    app.include_router(progress_router)
    app.include_router(manual_foods_router)
    app.include_router(barcode_foods_router)
    app.include_router(body_goals_router)
    app.include_router(target_review_router)
    app.include_router(recommendations_router)
    app.include_router(training_detail_router)
    app.include_router(training_analytics_router)
    app.include_router(ai_review_router)

    @app.get("/healthz", tags=["health"])
    def healthz() -> Response:
        """Unauthenticated process-liveness probe for deployment platforms.

        Deliberately returns no configuration, database, auth, or version
        detail: readiness of authenticated surfaces is expressed by the
        routes themselves (503 fail-closed when unconfigured).
        """

        return JSONResponse(status_code=200, content={"status": "ok"})

    return app


class _Uuid4Generator:
    def new_id(self) -> UUID:
        from uuid import uuid4

        return uuid4()
