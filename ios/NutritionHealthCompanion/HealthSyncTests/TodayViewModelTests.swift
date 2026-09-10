import XCTest
@testable import NutritionHealthCompanion

@MainActor
final class TodayViewModelTests: XCTestCase {
    private final class ScriptedBackend: BackendClient, @unchecked Sendable {
        private let lock = NSLock()
        var plans: [Result<DayPlanResponse, Error>] = []
        var generations: [Result<DayPlanResponse, Error>] = []
        var consumptionLists: [Result<ConsumptionListResponse, Error>] = []
        var consumptionWrites: [Result<ConsumptionEntryResponse, Error>] = []
        var trends: [Result<BodyMassTrendResponse, Error>] = []
        var ledgers: [Result<DailyNutritionLedgerResponse, Error>] = []
        var nextMeals: [Result<NextMealRecommendationResponse, Error>] = []
        var nextMealGenerations: [Result<NextMealRecommendationResponse, Error>] = []
        var nextMealConsumptionReads: [Result<NextMealConsumptionResponse, Error>] = []
        var nextMealConsumptionWrites: [Result<NextMealConsumptionResponse, Error>] = []
        var blockNextPlan = false
        var blockNextTrend = false
        var blockNextGeneration = false
        var blockNextMealGeneration = false
        private var blockedContinuation: CheckedContinuation<Void, Never>?
        private var blockedTrendContinuation: CheckedContinuation<Void, Never>?
        private var blockedGenerationContinuation: CheckedContinuation<Void, Never>?
        private var blockedNextMealContinuation: CheckedContinuation<Void, Never>?
        private var planReleaseRequested = false
        private var trendReleaseRequested = false
        private var generationReleaseRequested = false
        private var nextMealReleaseRequested = false

        private(set) var fetchDates: [Date] = []
        private(set) var generationCalls: [(Date, String)] = []
        private(set) var listRunIds: [UUID] = []
        private(set) var writes: [(UUID, UUID, UUID, ConsumptionState, UUID?)] = []
        private(set) var trendCalls: [(Date, String)] = []
        private(set) var ledgerCalls: [(Date, String)] = []
        private(set) var nextMealGenerationCalls: [(Date, String, UUID)] = []
        private(set) var nextMealConsumptionReadIds: [UUID] = []
        private(set) var nextMealConsumptionWriteCalls: [(UUID, UUID)] = []

        func submitBatch(
            added _: [BodyMassSampleDTO], deletions _: [DeletedSampleDTO]
        ) async throws -> SyncResponse {
            throw BackendError.retryable("unused")
        }

        func fetchSyncStatus() async throws -> StatusResponse {
            throw BackendError.retryable("unused")
        }

        func fetchBodyMassTrend(asOfDate: Date, timezone: String) async throws
            -> BodyMassTrendResponse
        {
            lock.lock()
            trendCalls.append((asOfDate, timezone))
            let result = trends.isEmpty ? .success(Self.noDataTrend) : trends.removeFirst()
            let shouldBlock = blockNextTrend
            blockNextTrend = false
            lock.unlock()
            if shouldBlock {
                await withCheckedContinuation { continuation in
                    lock.lock()
                    if trendReleaseRequested {
                        trendReleaseRequested = false
                        lock.unlock()
                        continuation.resume()
                        return
                    }
                    blockedTrendContinuation = continuation
                    lock.unlock()
                }
            }
            return try result.get()
        }

        func fetchDailyNutritionLedger(date: Date, timezone: String) async throws
            -> DailyNutritionLedgerResponse
        {
            let result = lock.withLock {
                ledgerCalls.append((date, timezone))
                return ledgers.isEmpty ? .success(Self.emptyLedger) : ledgers.removeFirst()
            }
            return try result.get()
        }

        func fetchLatestNextMeal() async throws -> NextMealRecommendationResponse {
            let result = lock.withLock {
                nextMeals.isEmpty
                    ? .failure(BackendError.rejected(statusCode: 404, code: "next_meal_not_found", detail: nil))
                    : nextMeals.removeFirst()
            }
            return try result.get()
        }

        func generateNextMeal(
            date: Date,
            timezone: String,
            clientRequestId: UUID
        ) async throws -> NextMealRecommendationResponse {
            let (result, shouldBlock) = lock.withLock {
                nextMealGenerationCalls.append((date, timezone, clientRequestId))
                let result = nextMealGenerations.removeFirst()
                let shouldBlock = blockNextMealGeneration
                blockNextMealGeneration = false
                return (result, shouldBlock)
            }
            if shouldBlock {
                await withCheckedContinuation { continuation in
                    let releaseImmediately = lock.withLock {
                        if nextMealReleaseRequested {
                            nextMealReleaseRequested = false
                            return true
                        }
                        blockedNextMealContinuation = continuation
                        return false
                    }
                    if releaseImmediately { continuation.resume() }
                }
            }
            return try result.get()
        }

        func fetchNextMealConsumption(recommendationId: UUID) async throws
            -> NextMealConsumptionResponse
        {
            let result = lock.withLock {
                nextMealConsumptionReadIds.append(recommendationId)
                return nextMealConsumptionReads.isEmpty
                    ? .failure(BackendError.rejected(
                        statusCode: 404,
                        code: "next_meal_consumption_not_found",
                        detail: nil
                    ))
                    : nextMealConsumptionReads.removeFirst()
            }
            return try result.get()
        }

        func recordNextMealConsumption(
            recommendationId: UUID,
            clientEventId: UUID
        ) async throws -> NextMealConsumptionResponse {
            let result = lock.withLock {
                nextMealConsumptionWriteCalls.append((recommendationId, clientEventId))
                return nextMealConsumptionWrites.removeFirst()
            }
            return try result.get()
        }

        func releaseNextMealGeneration() {
            let continuation = lock.withLock {
                let continuation = blockedNextMealContinuation
                blockedNextMealContinuation = nil
                if continuation == nil { nextMealReleaseRequested = true }
                return continuation
            }
            continuation?.resume()
        }

        func fetchDayPlan(date: Date) async throws -> DayPlanResponse {
            lock.lock()
            fetchDates.append(date)
            let result = plans.removeFirst()
            let shouldBlock = blockNextPlan
            blockNextPlan = false
            lock.unlock()
            if shouldBlock {
                await withCheckedContinuation { continuation in
                    lock.lock()
                    if planReleaseRequested {
                        planReleaseRequested = false
                        lock.unlock()
                        continuation.resume()
                        return
                    }
                    blockedContinuation = continuation
                    lock.unlock()
                }
            }
            return try result.get()
        }

        func releasePlanFetch() {
            lock.lock()
            let continuation = blockedContinuation
            blockedContinuation = nil
            if continuation == nil {
                planReleaseRequested = true
            }
            lock.unlock()
            continuation?.resume()
        }

        func releaseTrendFetch() {
            lock.lock()
            let continuation = blockedTrendContinuation
            blockedTrendContinuation = nil
            if continuation == nil {
                trendReleaseRequested = true
            }
            lock.unlock()
            continuation?.resume()
        }

        func generateDayPlan(date: Date, timezone: String) async throws -> DayPlanResponse {
            let (result, shouldBlock) = lock.withLock {
                generationCalls.append((date, timezone))
                let result = generations.removeFirst()
                let shouldBlock = blockNextGeneration
                blockNextGeneration = false
                return (result, shouldBlock)
            }
            if shouldBlock {
                await withCheckedContinuation { continuation in
                    let releaseImmediately = lock.withLock {
                        if generationReleaseRequested {
                            generationReleaseRequested = false
                            return true
                        }
                        blockedGenerationContinuation = continuation
                        return false
                    }
                    if releaseImmediately {
                        continuation.resume()
                    }
                }
            }
            return try result.get()
        }

        func releaseGeneration() {
            let continuation = lock.withLock {
                let continuation = blockedGenerationContinuation
                blockedGenerationContinuation = nil
                if continuation == nil {
                    generationReleaseRequested = true
                }
                return continuation
            }
            continuation?.resume()
        }

        func listConsumption(runId: UUID) async throws -> ConsumptionListResponse {
            lock.lock(); defer { lock.unlock() }
            listRunIds.append(runId)
            if consumptionLists.isEmpty { return ConsumptionListResponse(entries: []) }
            return try consumptionLists.removeFirst().get()
        }

        func recordConsumption(
            runId: UUID,
            planVersionId: UUID,
            itemId: UUID,
            state: ConsumptionState,
            clientEventId: UUID?
        ) async throws -> ConsumptionEntryResponse {
            lock.lock(); defer { lock.unlock() }
            writes.append((runId, planVersionId, itemId, state, clientEventId))
            return try consumptionWrites.removeFirst().get()
        }


        private static let noDataTrend = try! JSONDecoder().decode(
            BodyMassTrendResponse.self,
            from: Data("""
            {"status":"no_data","as_of_date":"2026-08-21","timezone":"America/New_York",
             "algorithm_version":"body-mass-trend-v1",
             "input_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
             "represented_day_count":0,"coverage_span_days":0,
             "first_measurement_date":null,"last_measurement_date":null,
             "latest_measurement_date":null,"latest_measurement_age_days":null,
             "trailing_7d_average_kg":null,"weekly_rate_kg":null}
            """.utf8)
        )

        private static let emptyLedger = DailyNutritionLedgerResponse(
            localDate: "2026-08-21",
            timezone: "America/New_York",
            target: nil,
            consumedItemCount: 0,
            knownCaloriesConsumed: "0",
            knownProteinGConsumed: "0",
            remainingKnownCalories: nil,
            remainingKnownProteinG: nil,
            nutritionCompleteness: .complete,
            nutritionAuthorities: [],
            unknownNutrients: [],
            consumedItems: [],
            reasonCodes: ["no_consumption", "target_unavailable"]
        )
    }

    private final class MemoryCache: DayPlanCaching {
        var records: [String: CachedDayPlan] = [:]
        var saved: [DayPlanResponse] = []

        func load(forSubject subject: String) -> CachedDayPlan? { records[subject] }
        func save(_ response: DayPlanResponse, forSubject subject: String) -> Bool {
            guard case .completed(let plan) = response else { return false }
            saved.append(response)
            records[subject] = CachedDayPlan(
                schemaVersion: DayPlanCache.schemaVersion,
                planDate: plan.planDate,
                cachedAt: Self.cachedAt,
                response: response
            )
            return true
        }
        func clear(forSubject subject: String) { records[subject] = nil }
        func clearAll() { records = [:] }

        static let cachedAt = Date(timeIntervalSince1970: 1_700_000_100)
    }

    private let subject = "owner-subject"
    private let today = WireDate.date(fromISO8601: "2026-08-21T00:00:00Z")!
    private let runId = UUID(uuidString: "10000000-0000-0000-0000-000000000001")!
    private let versionId = UUID(uuidString: "20000000-0000-0000-0000-000000000001")!
    private let itemId = UUID(uuidString: "50000000-0000-0000-0000-000000000001")!
    private let eventId = UUID(uuidString: "60000000-0000-0000-0000-000000000001")!

    private func makeViewModel(
        backend: ScriptedBackend,
        cache: MemoryCache = MemoryCache(),
        eventIds: [UUID]? = nil,
        unauthorized: @escaping @MainActor () -> Void = {},
        consumptionRecorded: @escaping @MainActor () async -> Void = {}
    ) -> TodayViewModel {
        var ids = eventIds ?? [eventId]
        return TodayViewModel(
            backend: backend,
            cache: cache,
            requestDate: { self.today },
            timezoneIdentifier: { "America/New_York" },
            eventId: { ids.removeFirst() },
            onUnauthorized: unauthorized,
            onNextMealConsumptionRecorded: consumptionRecorded
        )
    }

    func test_completedLoadCachesAndFetchesConsumptionUsingRun() async {
        let backend = ScriptedBackend()
        let cache = MemoryCache()
        let plan = completed(policy: "historical-p1")
        backend.plans = [.success(.completed(plan))]
        backend.consumptionLists = [.success(ConsumptionListResponse(entries: [entry()]))]
        let viewModel = makeViewModel(backend: backend, cache: cache)

        await viewModel.activate(subject: subject)

        XCTAssertEqual(viewModel.phase, .completed(plan))
        XCTAssertEqual(cache.saved, [.completed(plan)])
        XCTAssertEqual(backend.listRunIds, [runId])
        XCTAssertEqual(viewModel.latestConsumption(for: itemId)?.state, .eaten)
        XCTAssertEqual(plan.targetPolicy?.policyVersion, "historical-p1")
    }

    func testNextMealIsExplicitAndReusesLogicalRequestIdAfterAmbiguousFailure() async {
        let backend = ScriptedBackend()
        backend.plans = [.success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21")))]
        backend.nextMealGenerations = [
            .failure(BackendError.retryable("offline")),
            .success(Self.nextMealFailure),
        ]
        let viewModel = makeViewModel(backend: backend, eventIds: [eventId])
        await viewModel.activate(subject: subject)
        XCTAssertEqual(viewModel.nextMealPhase, .notGenerated)
        XCTAssertTrue(backend.nextMealGenerationCalls.isEmpty)

        await viewModel.generateNextMeal()
        guard case .error = viewModel.nextMealPhase else {
            return XCTFail("expected explicit generation failure")
        }
        await viewModel.generateNextMeal()
        XCTAssertEqual(viewModel.nextMealPhase, .result(Self.nextMealFailure))
        XCTAssertEqual(backend.nextMealGenerationCalls.map { $0.2 }, [eventId, eventId])
        XCTAssertEqual(backend.ledgerCalls.count, 1)
    }

    func testNextMealRetrievalRejectsArtifactFromAnotherLocalDay() async {
        let backend = ScriptedBackend()
        backend.plans = [.success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21")))]
        backend.nextMeals = [.success(Self.nextMeal(on: "2026-08-20"))]
        let viewModel = makeViewModel(backend: backend)

        await viewModel.activate(subject: subject)

        XCTAssertEqual(viewModel.nextMealPhase, .notGenerated)
        XCTAssertTrue(backend.nextMealGenerationCalls.isEmpty)
    }

    func testDefinitiveNextMealFailureUsesFreshIdentityOnExplicitRetry() async {
        let backend = ScriptedBackend()
        let nextId = UUID(uuidString: "60000000-0000-0000-0000-000000000002")!
        backend.plans = [.success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21")))]
        backend.nextMealGenerations = [
            .failure(BackendError.rejected(
                statusCode: 409, code: "next_meal_request_conflict", detail: nil)),
            .success(Self.nextMealFailure),
        ]
        let viewModel = makeViewModel(backend: backend, eventIds: [eventId, nextId])
        await viewModel.activate(subject: subject)

        await viewModel.generateNextMeal()
        await viewModel.generateNextMeal()

        XCTAssertEqual(backend.nextMealGenerationCalls.map { $0.2 }, [eventId, nextId])
        XCTAssertEqual(viewModel.nextMealPhase, .result(Self.nextMealFailure))
    }

    func testNextMealStateIsClearedForSignOut() async {
        let backend = ScriptedBackend()
        backend.plans = [.success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21")))]
        backend.nextMeals = [.success(Self.nextMealFailure)]
        let viewModel = makeViewModel(backend: backend)
        await viewModel.activate(subject: subject)
        XCTAssertEqual(viewModel.nextMealPhase, .result(Self.nextMealFailure))

        viewModel.clearForSignOut()

        XCTAssertEqual(viewModel.nextMealPhase, .signedOut)
        XCTAssertNil(viewModel.nextMealConsumption)
        XCTAssertFalse(viewModel.nextMealConsumptionConfirmed)
    }

    func testNextMealConsumptionRequiresConfirmationAndRefreshesAfterDurableSuccess() async {
        let backend = ScriptedBackend()
        backend.plans = [
            .success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21")))
        ]
        let recommendation = recommendedNextMeal()
        backend.nextMeals = [.success(recommendation)]
        backend.nextMealConsumptionWrites = [.success(Self.nextMealConsumption)]
        var historyRefreshes = 0
        let viewModel = makeViewModel(
            backend: backend,
            eventIds: [eventId],
            consumptionRecorded: { historyRefreshes += 1 }
        )

        await viewModel.activate(subject: subject)

        XCTAssertFalse(viewModel.nextMealConsumptionConfirmed)
        XCTAssertFalse(viewModel.canRecordNextMealConsumption)
        XCTAssertTrue(backend.nextMealConsumptionWriteCalls.isEmpty)
        viewModel.setNextMealConsumptionConfirmed(true)
        XCTAssertTrue(viewModel.canRecordNextMealConsumption)

        let recorded = await viewModel.recordNextMealConsumption()
        XCTAssertTrue(recorded)
        XCTAssertEqual(viewModel.nextMealConsumption, Self.nextMealConsumption)
        XCTAssertFalse(viewModel.nextMealConsumptionConfirmed)
        XCTAssertEqual(backend.nextMealConsumptionWriteCalls.count, 1)
        XCTAssertEqual(backend.nextMealConsumptionWriteCalls[0].0, recommendation.recommendationId)
        XCTAssertEqual(backend.nextMealConsumptionWriteCalls[0].1, eventId)
        XCTAssertEqual(backend.ledgerCalls.count, 2)
        XCTAssertEqual(historyRefreshes, 1)
        XCTAssertTrue(backend.nextMealGenerationCalls.isEmpty)
    }

    func testAmbiguousNextMealConsumptionRetryReusesIdentityWithoutOptimisticLedger() async {
        let backend = ScriptedBackend()
        backend.plans = [
            .success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21")))
        ]
        backend.nextMeals = [.success(recommendedNextMeal())]
        backend.nextMealConsumptionWrites = [
            .failure(BackendError.retryable("offline")),
            .success(Self.nextMealConsumption),
        ]
        let viewModel = makeViewModel(backend: backend, eventIds: [eventId])
        await viewModel.activate(subject: subject)
        viewModel.setNextMealConsumptionConfirmed(true)

        let failed = await viewModel.recordNextMealConsumption()
        XCTAssertFalse(failed)
        XCTAssertNil(viewModel.nextMealConsumption)
        XCTAssertTrue(viewModel.nextMealConsumptionConfirmed)
        XCTAssertEqual(backend.ledgerCalls.count, 1)

        let retried = await viewModel.recordNextMealConsumption()
        XCTAssertTrue(retried)
        XCTAssertEqual(backend.nextMealConsumptionWriteCalls.map { $0.1 }, [eventId, eventId])
        XCTAssertEqual(backend.ledgerCalls.count, 2)
    }

    func testPersistedNextMealConsumptionLoadsReadOnlyAndDisablesRecording() async {
        let backend = ScriptedBackend()
        backend.plans = [
            .success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21")))
        ]
        let recommendation = recommendedNextMeal()
        backend.nextMeals = [.success(recommendation)]
        backend.nextMealConsumptionReads = [.success(Self.nextMealConsumption)]
        let viewModel = makeViewModel(backend: backend)

        await viewModel.activate(subject: subject)

        XCTAssertEqual(viewModel.nextMealConsumption, Self.nextMealConsumption)
        XCTAssertFalse(viewModel.canRecordNextMealConsumption)
        XCTAssertEqual(
            backend.nextMealConsumptionReadIds,
            [recommendation.recommendationId]
        )
        XCTAssertTrue(backend.nextMealConsumptionWriteCalls.isEmpty)
    }

    func testOldOwnerNextMealResponseCannotPopulateNewOwnerState() async {
        let backend = ScriptedBackend()
        backend.plans = [
            .success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21"))),
            .success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21"))),
        ]
        backend.nextMealGenerations = [.success(Self.nextMealFailure)]
        backend.blockNextMealGeneration = true
        let viewModel = makeViewModel(backend: backend)
        await viewModel.activate(subject: subject)

        let oldOwnerRequest = Task { await viewModel.generateNextMeal() }
        await Task.yield()
        await viewModel.activate(subject: "different-owner")
        backend.releaseNextMealGeneration()
        await oldOwnerRequest.value

        XCTAssertEqual(viewModel.nextMealPhase, .notGenerated)
    }

    private static func nextMeal(on localDate: String) -> NextMealRecommendationResponse {
        NextMealRecommendationResponse(
            recommendationId: nextMealFailure.recommendationId,
            clientRequestId: nextMealFailure.clientRequestId,
            localDate: localDate,
            timezone: nextMealFailure.timezone,
            decisionAt: nextMealFailure.decisionAt,
            status: nextMealFailure.status,
            reasonCodes: nextMealFailure.reasonCodes,
            inputsDigest: nextMealFailure.inputsDigest,
            artifactSha256: nextMealFailure.artifactSha256,
            artifact: nextMealFailure.artifact,
            created: nextMealFailure.created
        )
    }

    private static let nextMealFailure = NextMealRecommendationResponse(
        recommendationId: UUID(
            uuidString: "10000000-0000-0000-0000-000000000001")!,
        clientRequestId: UUID(
            uuidString: "60000000-0000-0000-0000-000000000001")!,
        localDate: "2026-08-21",
        timezone: "America/New_York",
        decisionAt: Date(timeIntervalSince1970: 1_700_000_300),
        status: .noRemainingMealOpportunity,
        reasonCodes: ["all_stacks_windows_elapsed"],
        inputsDigest: String(repeating: "a", count: 64),
        artifactSha256: String(repeating: "b", count: 64),
        artifact: NextMealArtifactDTO(
            status: .noRemainingMealOpportunity,
            reasonCodes: ["all_stacks_windows_elapsed"],
            decisionAt: Date(timeIntervalSince1970: 1_700_000_300),
            nextMealPolicyVersion: "next-meal.remaining-opportunities.v1",
            scheduleVersion: nil,
            targetPolicyVersion: nil,
            menuSnapshotSha256: nil,
            nutritionAuthorities: nil,
            ledger: nil,
            remainingOpportunities: nil,
            selectedOpportunity: nil,
            allocatedTargets: nil,
            selected: nil,
            alternatives: nil
        ),
        created: true
    )

    private func recommendedNextMeal() -> NextMealRecommendationResponse {
        NextMealRecommendationResponse(
            recommendationId: Self.nextMealConsumption.recommendationId,
            clientRequestId: eventId,
            localDate: "2026-08-21",
            timezone: "America/New_York",
            decisionAt: Self.nextMealConsumption.recordedAt,
            status: .recommended,
            reasonCodes: [],
            inputsDigest: String(repeating: "a", count: 64),
            artifactSha256: Self.nextMealConsumption.recommendationArtifactSha256,
            artifact: NextMealArtifactDTO(
                status: .recommended,
                reasonCodes: [],
                decisionAt: Self.nextMealConsumption.recordedAt,
                nextMealPolicyVersion: "next-meal.remaining-opportunities.v1",
                scheduleVersion: "schedule.v1",
                targetPolicyVersion: "target.v1",
                menuSnapshotSha256: String(repeating: "c", count: 64),
                nutritionAuthorities: ["official"],
                ledger: NextMealLedgerDTO(
                    consumedEntryIds: [],
                    knownCaloriesConsumed: "100",
                    knownProteinGConsumed: "10",
                    remainingCalories: "2100",
                    remainingProteinG: "110"
                ),
                remainingOpportunities: [],
                selectedOpportunity: NextMealOpportunityDTO(
                    context: "lunch",
                    menuPeriod: "Lunch",
                    window: ["12:00:00", "13:00:00"]
                ),
                allocatedTargets: NextMealAllocatedTargetsDTO(
                    caloriesKcal: "1050",
                    caloriesGoalKind: "target",
                    proteinG: "55",
                    proteinGoalKind: "floor",
                    proteinScoringActive: true,
                    opportunityCount: 2,
                    rule: "equal_share"
                ),
                selected: candidate(rank: 1, names: ["Chicken bowl"]),
                alternatives: []
            ),
            created: true
        )
    }

    private static let nextMealConsumption = NextMealConsumptionResponse(
        entryId: UUID(uuidString: "70000000-0000-0000-0000-000000000001")!,
        recommendationId: UUID(uuidString: "71000000-0000-0000-0000-000000000001")!,
        clientEventId: UUID(uuidString: "72000000-0000-0000-0000-000000000001")!,
        state: "eaten",
        localDate: "2026-08-21",
        timezone: "America/New_York",
        recordedAt: Date(timeIntervalSince1970: 1_700_000_400),
        recommendationArtifactSha256: String(repeating: "b", count: 64),
        nextMealPolicyVersion: "next-meal.remaining-opportunities.v1",
        mealContext: "lunch",
        menuPeriod: "Lunch",
        candidateId: "candidate-stable-1",
        itemName: "Chicken bowl",
        servingDescription: "1 × Chicken bowl",
        configurationSummary: nil,
        nutritionAuthority: "official",
        nutritionConfidence: "official_published",
        caloriesKcal: "900",
        proteinG: "50.5",
        unknownNutrients: [],
        selectedCandidateSha256: String(repeating: "d", count: 64),
        created: true
    )

    func test_noPlanAndNotGeneratedAreHonestAndNeverAutoGenerate() async {
        let backend = ScriptedBackend()
        let noPlan = NoPlanDay(
            requestedDate: "2026-08-21", planDate: nil,
            reasonCodes: ["empty_menu_period"], inputsFingerprint: "fp", runId: nil)
        backend.plans = [
            .success(.noPlan(noPlan)),
            .success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21"))),
        ]
        let viewModel = makeViewModel(backend: backend)
        await viewModel.activate(subject: subject)
        XCTAssertEqual(viewModel.phase, .noPlan(noPlan))
        XCTAssertNil(viewModel.displayedPlan)
        await viewModel.refresh()
        XCTAssertEqual(viewModel.phase, .notGenerated)
        XCTAssertEqual(backend.generationCalls.count, 0)
    }

    func test_explicitGeneratePostsThenFetchesCanonicalGet() async {
        let backend = ScriptedBackend()
        let plan = completed()
        backend.plans = [
            .success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21"))),
            .success(.completed(plan)),
        ]
        backend.generations = [.success(.completed(planWithoutGetMetadata()))]
        let viewModel = makeViewModel(backend: backend)
        await viewModel.activate(subject: subject)
        await viewModel.generate()

        XCTAssertEqual(backend.generationCalls.count, 1)
        XCTAssertEqual(backend.generationCalls[0].1, "America/New_York")
        XCTAssertEqual(backend.fetchDates.count, 2)
        XCTAssertEqual(viewModel.phase, .completed(plan))
    }

    func test_concurrentGenerationPerformsOneGetAndNoPolling() async {
        let backend = ScriptedBackend()
        let plan = completed()
        backend.plans = [
            .success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21"))),
            .success(.completed(plan)),
        ]
        backend.generations = [.failure(BackendError.rejected(
            statusCode: 409, code: "concurrent_generation", detail: nil))]
        let viewModel = makeViewModel(backend: backend)
        await viewModel.activate(subject: subject)
        await viewModel.generate()
        XCTAssertEqual(backend.fetchDates.count, 2)
        XCTAssertEqual(backend.generationCalls.count, 1)
        XCTAssertEqual(viewModel.phase, .completed(plan))
    }

    func test_existingPlanExposesRegenerationWhileNotGeneratedKeepsGenerateFlow() async {
        let completedBackend = ScriptedBackend()
        completedBackend.plans = [.success(.completed(completed()))]
        let completedViewModel = makeViewModel(backend: completedBackend)
        await completedViewModel.activate(subject: subject)

        XCTAssertTrue(completedViewModel.showsRegenerateAction)
        XCTAssertTrue(completedViewModel.canRegenerate)

        let newBackend = ScriptedBackend()
        newBackend.plans = [
            .success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21"))),
            .success(.completed(completed())),
        ]
        newBackend.generations = [.success(.completed(planWithoutGetMetadata()))]
        let newViewModel = makeViewModel(backend: newBackend)
        await newViewModel.activate(subject: subject)

        XCTAssertFalse(newViewModel.showsRegenerateAction)
        XCTAssertFalse(newViewModel.canRegenerate)
        await newViewModel.generate()
        XCTAssertEqual(newBackend.generationCalls.count, 1)
        XCTAssertEqual(newViewModel.phase, .completed(completed()))
    }

    func test_regenerationPostsOnceKeepsOldPlanUntilCanonicalReplacement() async {
        let backend = ScriptedBackend()
        let cache = MemoryCache()
        let oldPlan = completed(sha: "old-plan")
        let newPlan = completed(
            sha: "new-plan",
            policy: "planner.v3",
            runIdentifier: UUID(uuidString: "10000000-0000-0000-0000-000000000002")!,
            versionIdentifier: UUID(uuidString: "20000000-0000-0000-0000-000000000002")!,
            itemIdentifier: UUID(uuidString: "50000000-0000-0000-0000-000000000002")!
        )
        backend.plans = [.success(.completed(oldPlan)), .success(.completed(newPlan))]
        backend.generations = [.success(.completed(newPlan))]
        backend.blockNextGeneration = true
        let viewModel = makeViewModel(backend: backend, cache: cache)
        await viewModel.activate(subject: subject)

        let first = Task { await viewModel.regenerate() }
        while backend.generationCalls.isEmpty { await Task.yield() }

        XCTAssertTrue(viewModel.isRegenerating)
        XCTAssertFalse(viewModel.canRegenerate)
        XCTAssertFalse(viewModel.canMutate)
        XCTAssertEqual(viewModel.phase, .completed(oldPlan))
        XCTAssertEqual(cache.records[subject]?.response, .completed(oldPlan))

        let duplicate = Task { await viewModel.regenerate() }
        await duplicate.value
        XCTAssertEqual(backend.generationCalls.count, 1)

        backend.releaseGeneration()
        await first.value

        XCTAssertEqual(backend.generationCalls.count, 1)
        XCTAssertEqual(backend.fetchDates.count, 2)
        XCTAssertEqual(viewModel.phase, .completed(newPlan))
        XCTAssertEqual(cache.records[subject]?.response, .completed(newPlan))
        XCTAssertFalse(viewModel.isRegenerating)
        XCTAssertNil(viewModel.regenerationMessage)
    }

    func test_failedOfflineRegenerationPreservesDisplayedAndCachedPlan() async {
        let backend = ScriptedBackend()
        let cache = MemoryCache()
        let oldPlan = completed(sha: "old-plan")
        backend.plans = [.success(.completed(oldPlan))]
        backend.generations = [.failure(BackendError.retryable("offline"))]
        let viewModel = makeViewModel(backend: backend, cache: cache)
        await viewModel.activate(subject: subject)

        await viewModel.regenerate()

        XCTAssertEqual(backend.generationCalls.count, 1)
        XCTAssertEqual(backend.fetchDates.count, 1)
        XCTAssertEqual(viewModel.phase, .completed(oldPlan))
        XCTAssertEqual(viewModel.displayedPlan, oldPlan)
        XCTAssertEqual(cache.records[subject]?.response, .completed(oldPlan))
        XCTAssertTrue(viewModel.regenerationMessage?.contains("existing plan was kept") == true)
    }

    func test_successfulPostWithFailedCanonicalGetKeepsExistingPlanAndCache() async {
        let backend = ScriptedBackend()
        let cache = MemoryCache()
        let oldPlan = completed(sha: "old-plan")
        backend.plans = [
            .success(.completed(oldPlan)),
            .failure(BackendError.retryable("offline")),
        ]
        backend.generations = [.success(.completed(planWithoutGetMetadata()))]
        let viewModel = makeViewModel(backend: backend, cache: cache)
        await viewModel.activate(subject: subject)

        await viewModel.regenerate()

        XCTAssertEqual(backend.generationCalls.count, 1)
        XCTAssertEqual(backend.fetchDates.count, 2)
        XCTAssertEqual(viewModel.phase, .completed(oldPlan))
        XCTAssertEqual(cache.records[subject]?.response, .completed(oldPlan))
        XCTAssertTrue(viewModel.regenerationMessage?.contains("existing plan was kept") == true)
    }

    func test_regenerationMovesConsumptionActionsToNewImmutablePlanItems() async {
        let backend = ScriptedBackend()
        let oldPlan = completed(sha: "old-plan")
        let newRunId = UUID(uuidString: "10000000-0000-0000-0000-000000000002")!
        let newVersionId = UUID(uuidString: "20000000-0000-0000-0000-000000000002")!
        let newItemId = UUID(uuidString: "50000000-0000-0000-0000-000000000002")!
        let newEventId = UUID(uuidString: "60000000-0000-0000-0000-000000000002")!
        let newPlan = completed(
            sha: "new-plan",
            policy: "planner.v3",
            runIdentifier: newRunId,
            versionIdentifier: newVersionId,
            itemIdentifier: newItemId
        )
        backend.plans = [.success(.completed(oldPlan)), .success(.completed(newPlan))]
        backend.generations = [.success(.completed(newPlan))]
        backend.consumptionWrites = [
            .success(entry()),
            .success(entry(
                entryIdentifier: UUID(
                    uuidString: "70000000-0000-0000-0000-000000000002")!,
                runIdentifier: newRunId,
                versionIdentifier: newVersionId,
                itemIdentifier: newItemId,
                clientEventIdentifier: newEventId
            )),
        ]
        let viewModel = makeViewModel(
            backend: backend,
            eventIds: [eventId, newEventId]
        )
        await viewModel.activate(subject: subject)
        await viewModel.recordConsumption(item: oldPlan.planItems![0], state: .eaten)

        await viewModel.regenerate()
        await viewModel.recordConsumption(item: newPlan.planItems![0], state: .eaten)

        XCTAssertEqual(backend.writes.count, 2)
        XCTAssertEqual(backend.writes[0].0, runId)
        XCTAssertEqual(backend.writes[0].1, versionId)
        XCTAssertEqual(backend.writes[0].2, itemId)
        XCTAssertEqual(backend.writes[1].0, newRunId)
        XCTAssertEqual(backend.writes[1].1, newVersionId)
        XCTAssertEqual(backend.writes[1].2, newItemId)
        XCTAssertEqual(backend.writes[0].4, eventId)
        XCTAssertEqual(backend.writes[1].4, newEventId)
    }

    func test_noApprovedPolicyHasSpecificState() async {
        let backend = ScriptedBackend()
        backend.plans = [.success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21")))]
        backend.generations = [.failure(BackendError.rejected(
            statusCode: 409, code: "no_approved_target_policy", detail: nil))]
        let viewModel = makeViewModel(backend: backend)
        await viewModel.activate(subject: subject)
        await viewModel.generate()
        XCTAssertEqual(viewModel.phase, .noApprovedPolicy)
    }

    func test_menuDataUnavailableHasSpecificStateWithoutCanonicalFetch() async {
        let backend = ScriptedBackend()
        backend.plans = [.success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21")))]
        backend.generations = [.failure(BackendError.retryableHTTP(
            statusCode: 503,
            code: "menu_data_unavailable",
            detail: "retry later"
        ))]
        let viewModel = makeViewModel(backend: backend)
        await viewModel.activate(subject: subject)

        await viewModel.generate()

        XCTAssertEqual(backend.generationCalls.count, 1)
        XCTAssertEqual(backend.fetchDates.count, 1)
        XCTAssertEqual(viewModel.phase, .menuDataUnavailable)
    }

    func test_otherGenerationFailuresRemainGenericAndAreNotMisreportedAsMenuFailures() async {
        let backend = ScriptedBackend()
        backend.plans = [.success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21")))]
        backend.generations = [.failure(BackendError.retryableHTTP(
            statusCode: 503,
            code: "storage_unavailable",
            detail: "retry later"
        ))]
        let viewModel = makeViewModel(backend: backend)
        await viewModel.activate(subject: subject)

        await viewModel.generate()

        XCTAssertEqual(
            viewModel.phase,
            .error("The plan could not be generated. Please try again.")
        )
    }

    func test_cacheDisplaysDuringRefreshThenFreshResponseReplacesIt() async {
        let backend = ScriptedBackend()
        let cache = MemoryCache()
        let cached = completed(sha: "cached")
        let fresh = completed(sha: "fresh")
        cache.records[subject] = CachedDayPlan(
            schemaVersion: DayPlanCache.schemaVersion,
            planDate: cached.planDate,
            cachedAt: MemoryCache.cachedAt,
            response: .completed(cached)
        )
        backend.plans = [.success(.completed(fresh))]
        backend.blockNextPlan = true
        let viewModel = makeViewModel(backend: backend, cache: cache)

        let load = Task { await viewModel.activate(subject: subject) }
        await Task.yield()
        XCTAssertEqual(viewModel.phase, .cached(plan: cached, cachedAt: MemoryCache.cachedAt))
        backend.releasePlanFetch()
        await load.value
        XCTAssertEqual(viewModel.phase, .completed(fresh))
    }

    func test_networkFailureUsesMatchingCacheOrOfflineEmpty() async {
        let cachedBackend = ScriptedBackend()
        let cache = MemoryCache()
        let plan = completed()
        cache.records[subject] = CachedDayPlan(
            schemaVersion: DayPlanCache.schemaVersion,
            planDate: plan.planDate,
            cachedAt: MemoryCache.cachedAt,
            response: .completed(plan)
        )
        cachedBackend.plans = [.failure(BackendError.retryable("offline"))]
        let cachedVM = makeViewModel(backend: cachedBackend, cache: cache)
        await cachedVM.activate(subject: subject)
        XCTAssertEqual(cachedVM.phase, .offline(plan: plan, cachedAt: MemoryCache.cachedAt))

        let emptyBackend = ScriptedBackend()
        emptyBackend.plans = [.failure(BackendError.retryable("offline"))]
        let emptyVM = makeViewModel(backend: emptyBackend)
        await emptyVM.activate(subject: subject)
        XCTAssertEqual(emptyVM.phase, .offline(plan: nil, cachedAt: nil))
    }

    func test_unauthorizedTransitionsSignedOutAndNotifiesRoot() async {
        let backend = ScriptedBackend()
        backend.plans = [.failure(BackendError.unauthorized)]
        let cache = MemoryCache()
        let plan = completed()
        cache.records[subject] = CachedDayPlan(
            schemaVersion: DayPlanCache.schemaVersion,
            planDate: plan.planDate,
            cachedAt: MemoryCache.cachedAt,
            response: .completed(plan)
        )
        var notified = false
        let viewModel = makeViewModel(backend: backend, cache: cache) { notified = true }
        await viewModel.activate(subject: subject)
        XCTAssertEqual(viewModel.phase, .signedOut)
        XCTAssertTrue(notified)
        XCTAssertTrue(cache.records.isEmpty)
    }

    func test_consumptionUsesExactPlanItemUUIDAndStableEventIdAcrossRetry() async {
        let backend = ScriptedBackend()
        let plan = completed()
        backend.plans = [.success(.completed(plan))]
        backend.consumptionWrites = [
            .failure(BackendError.retryable("offline")),
            .success(entry()),
        ]
        let alternateId = UUID(uuidString: "60000000-0000-0000-0000-000000000002")!
        let viewModel = makeViewModel(
            backend: backend, eventIds: [eventId, alternateId])
        await viewModel.activate(subject: subject)
        let reference = plan.planItems![0]

        await viewModel.recordConsumption(item: reference, state: .eaten)
        XCTAssertNil(viewModel.latestConsumption(for: itemId))
        await viewModel.recordConsumption(item: reference, state: .eaten)

        XCTAssertEqual(backend.writes.count, 2)
        XCTAssertEqual(backend.writes[0].2, itemId)
        XCTAssertEqual(backend.writes[1].2, itemId)
        XCTAssertEqual(backend.writes[0].4, eventId)
        XCTAssertEqual(backend.writes[1].4, eventId)
        XCTAssertNotEqual(reference.itemId.uuidString, reference.candidateId)
        XCTAssertEqual(viewModel.latestConsumption(for: itemId)?.entryId, entry().entryId)
    }

    func test_conflictAndMissingTargetNeverFabricateSuccess() async {
        for error in [
            BackendError.rejected(statusCode: 409, code: "consumption_conflict", detail: nil),
            BackendError.rejected(
                statusCode: 404, code: "consumption_target_not_found", detail: nil),
        ] {
            let backend = ScriptedBackend()
            let plan = completed()
            backend.plans = [.success(.completed(plan))]
            backend.consumptionWrites = [.failure(error)]
            let viewModel = makeViewModel(backend: backend)
            await viewModel.activate(subject: subject)
            await viewModel.recordConsumption(item: plan.planItems![0], state: .skipped)
            XCTAssertNil(viewModel.latestConsumption(for: itemId))
            XCTAssertNotNil(viewModel.consumptionMessage)
        }
    }

    func test_offlineStateBlocksConsumptionMutation() async {
        let backend = ScriptedBackend()
        let cache = MemoryCache()
        let plan = completed()
        cache.records[subject] = CachedDayPlan(
            schemaVersion: DayPlanCache.schemaVersion,
            planDate: plan.planDate,
            cachedAt: MemoryCache.cachedAt,
            response: .completed(plan)
        )
        backend.plans = [.failure(BackendError.retryable("offline"))]
        let viewModel = makeViewModel(backend: backend, cache: cache)
        await viewModel.activate(subject: subject)
        await viewModel.recordConsumption(item: plan.planItems![0], state: .eaten)
        XCTAssertEqual(backend.writes.count, 0)
        XCTAssertFalse(viewModel.canMutate)
    }

    func test_everyRequestUsesInjectedTodayOnlyDate() async {
        let backend = ScriptedBackend()
        backend.plans = [
            .success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21"))),
            .success(.completed(completed())),
        ]
        backend.generations = [.success(.completed(planWithoutGetMetadata()))]
        let viewModel = makeViewModel(backend: backend)
        await viewModel.activate(subject: subject)
        await viewModel.generate()
        XCTAssertEqual(backend.fetchDates, [today, today])
        XCTAssertEqual(backend.generationCalls[0].0, today)
    }

    func test_trendUsesInjectedTodayIanaTimezoneAndMapsReadyResult() async {
        let backend = ScriptedBackend()
        backend.plans = [
            .success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21")))
        ]
        let ready = trend(status: "ready", average: nil, rate: "0.18")
        backend.trends = [.success(ready)]
        let viewModel = makeViewModel(backend: backend)

        await viewModel.activate(subject: subject)

        XCTAssertEqual(viewModel.trendPhase, .result(ready))
        XCTAssertEqual(backend.trendCalls.count, 1)
        XCTAssertEqual(backend.trendCalls[0].0, today)
        XCTAssertEqual(backend.trendCalls[0].1, "America/New_York")
        XCTAssertNil(ready.formattedTrailingAverageKg)
    }

    func test_ledgerUsesInjectedTodayAndRefreshesOnlyAfterDurableEatenSuccess() async {
        let backend = ScriptedBackend()
        let plan = completed()
        let before = ledger(consumed: "0", count: 0)
        let after = ledger(consumed: "901.2300", count: 1)
        backend.plans = [.success(.completed(plan))]
        backend.ledgers = [.success(before), .success(after)]
        backend.consumptionWrites = [.success(entry())]
        let viewModel = makeViewModel(backend: backend)

        await viewModel.activate(subject: subject)
        XCTAssertEqual(viewModel.ledgerPhase, .result(before))
        XCTAssertEqual(backend.ledgerCalls.count, 1)
        XCTAssertEqual(backend.ledgerCalls[0].0, today)
        XCTAssertEqual(backend.ledgerCalls[0].1, "America/New_York")

        await viewModel.recordConsumption(item: plan.planItems![0], state: .eaten)

        XCTAssertEqual(viewModel.ledgerPhase, .result(after))
        XCTAssertEqual(backend.ledgerCalls.count, 2)
        XCTAssertEqual(backend.writes.count, 1)
    }

    func test_failedEatenDoesNotOptimisticallyChangeLedger() async {
        let backend = ScriptedBackend()
        let plan = completed()
        let before = ledger(consumed: "0", count: 0)
        backend.plans = [.success(.completed(plan))]
        backend.ledgers = [.success(before)]
        backend.consumptionWrites = [.failure(BackendError.retryable("offline"))]
        let viewModel = makeViewModel(backend: backend)

        await viewModel.activate(subject: subject)
        await viewModel.recordConsumption(item: plan.planItems![0], state: .eaten)

        XCTAssertEqual(viewModel.ledgerPhase, .result(before))
        XCTAssertEqual(backend.ledgerCalls.count, 1)
        XCTAssertNil(viewModel.latestConsumption(for: itemId))
    }

    func test_trendLoadingAndNetworkFailureStayDistinctFromNoData() async {
        let loadingBackend = ScriptedBackend()
        loadingBackend.plans = [
            .success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21")))
        ]
        loadingBackend.blockNextTrend = true
        let loadingViewModel = makeViewModel(backend: loadingBackend)
        let load = Task { await loadingViewModel.activate(subject: subject) }
        await Task.yield()
        XCTAssertEqual(loadingViewModel.trendPhase, .loading)
        loadingBackend.releaseTrendFetch()
        await load.value
        guard case .result(let noData) = loadingViewModel.trendPhase else {
            return XCTFail("expected no-data result")
        }
        XCTAssertEqual(noData.status, .noData)

        let failureBackend = ScriptedBackend()
        failureBackend.plans = [
            .success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21")))
        ]
        failureBackend.trends = [.failure(BackendError.retryable("offline"))]
        let failureViewModel = makeViewModel(backend: failureBackend)
        await failureViewModel.activate(subject: subject)
        guard case .error = failureViewModel.trendPhase else {
            return XCTFail("network failure must not become no_data")
        }
    }

    func test_trendRefreshDoesNotRetainStaleSuccessAsOfflineAuthority() async {
        let backend = ScriptedBackend()
        backend.plans = [
            .success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21"))),
            .success(.notGenerated(NotGeneratedDay(requestedDate: "2026-08-21"))),
        ]
        backend.trends = [
            .success(trend(status: "ready", average: "68.4", rate: "0.18")),
            .failure(BackendError.retryable("offline")),
        ]
        let viewModel = makeViewModel(backend: backend)
        await viewModel.activate(subject: subject)
        guard case .result = viewModel.trendPhase else { return XCTFail("expected result") }

        await viewModel.refresh()

        guard case .error = viewModel.trendPhase else {
            return XCTFail("failed refresh must not display a cached trend")
        }
        XCTAssertEqual(backend.trendCalls.count, 2)
    }

    func test_planPresentationUsesRankOneAndBoundsAlternativesWithOriginalItems() {
        let candidates = (1...6).map { candidate(rank: $0, names: ["Meal \($0)"]) }
        let plan = completed(candidates: candidates)

        guard let presentation = plan.presentation(forSlotAt: 0) else {
            return XCTFail("expected slot presentation")
        }

        XCTAssertEqual(presentation.primary?.rank, 1)
        XCTAssertEqual(presentation.primary?.candidate.candidateId, "candidate-stable-1")
        XCTAssertEqual(presentation.primary?.itemReference?.itemId, rankedItemId(1))
        XCTAssertEqual(presentation.alternatives.map(\.rank), [2, 3, 4])
        XCTAssertEqual(
            presentation.alternatives.map(\.candidate.candidateId),
            ["candidate-stable-2", "candidate-stable-3", "candidate-stable-4"]
        )
        XCTAssertEqual(
            presentation.alternatives.compactMap(\.itemReference?.itemId),
            [rankedItemId(2), rankedItemId(3), rankedItemId(4)]
        )
    }

    func test_pairIsOneRecommendationAndSingleCandidateHasNoAlternatives() {
        let pair = candidate(
            rank: 1,
            names: ["Beyond Burger", "Chicken Tender Club"]
        )
        let plan = completed(candidates: [pair])

        guard let presentation = plan.presentation(forSlotAt: 0),
            let primary = presentation.primary
        else {
            return XCTFail("expected primary recommendation")
        }

        XCTAssertEqual(primary.mealName, "Beyond Burger + Chicken Tender Club")
        XCTAssertEqual(primary.candidate.lines.count, 2)
        XCTAssertTrue(presentation.alternatives.isEmpty)
    }

    func test_emptySlotPresentationRemainsEmpty() {
        let presentation = completed(candidates: []).presentation(forSlotAt: 0)

        XCTAssertNotNil(presentation)
        XCTAssertNil(presentation?.primary)
        XCTAssertEqual(presentation?.alternatives, [])
    }

    func test_cachedAndOfflinePlansUseTheSameBoundedPresentation() async {
        let backend = ScriptedBackend()
        let cache = MemoryCache()
        let plan = completed(
            candidates: (1...6).map { candidate(rank: $0, names: ["Meal \($0)"]) }
        )
        let expected = plan.presentation(forSlotAt: 0)
        cache.records[subject] = CachedDayPlan(
            schemaVersion: DayPlanCache.schemaVersion,
            planDate: plan.planDate,
            cachedAt: MemoryCache.cachedAt,
            response: .completed(plan)
        )
        backend.plans = [.failure(BackendError.retryable("offline"))]
        backend.blockNextPlan = true
        let viewModel = makeViewModel(backend: backend, cache: cache)

        let load = Task { await viewModel.activate(subject: subject) }
        await Task.yield()
        XCTAssertEqual(viewModel.displayedPlan?.presentation(forSlotAt: 0), expected)
        backend.releasePlanFetch()
        await load.value

        XCTAssertEqual(viewModel.phase, .offline(plan: plan, cachedAt: MemoryCache.cachedAt))
        XCTAssertEqual(viewModel.displayedPlan?.presentation(forSlotAt: 0), expected)
    }

    func test_estimatedCandidateDecodesDisclosureAndPreservesConsumptionMapping() throws {
        let candidate = try estimatedCandidate()
        let plan = completed(candidates: [candidate])
        guard let presentation = plan.presentation(forSlotAt: 0),
            let primary = presentation.primary
        else {
            return XCTFail("expected estimated recommendation")
        }

        XCTAssertEqual(candidate.candidateKind, "configurable_estimate")
        XCTAssertEqual(primary.mealName, "CYO Halal Bowl")
        XCTAssertEqual(
            candidate.configurableEstimate?.definition.configurationSummary,
            "4 oz rice, 4 oz chicken, 2 eggs, 4 x 1 oz quinoa, no sauces"
        )
        XCTAssertEqual(
            candidate.configurableEstimate?.definition.estimate.unknownNutrients,
            ["added_sugars_g", "trans_fat_g"]
        )
        XCTAssertEqual(
            candidate.configurableEstimate?.definition.estimate.caveats,
            ["Preparation differs from the cited generic references."]
        )
        XCTAssertEqual(primary.itemReference?.itemId, rankedItemId(1))
        XCTAssertTrue(candidate.lines.isEmpty)
        XCTAssertTrue(candidate.provenance.profileContentSha256s.isEmpty)
    }

    func test_estimatedCandidateCacheRoundTripPreservesExactMetadata() throws {
        let original = DayPlanResponse.completed(completed(candidates: [try estimatedCandidate()]))
        let encoded = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(DayPlanResponse.self, from: encoded)

        XCTAssertEqual(decoded, original)
        guard case .completed(let plan) = decoded else {
            return XCTFail("expected completed plan")
        }
        XCTAssertEqual(
            plan.presentation(forSlotAt: 0)?.primary?.candidate.configurableEstimate?
                .evidenceDigest,
            "definition-evidence-sha"
        )
    }

    private func trend(status: String, average: String?, rate: String?) -> BodyMassTrendResponse {
        let averageJSON = average.map { "\"\($0)\"" } ?? "null"
        let rateJSON = rate.map { "\"\($0)\"" } ?? "null"
        return try! JSONDecoder().decode(
            BodyMassTrendResponse.self,
            from: Data("""
            {"status":"\(status)","as_of_date":"2026-08-21",
             "timezone":"America/New_York","algorithm_version":"body-mass-trend-v1",
             "input_digest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
             "represented_day_count":7,"coverage_span_days":20,
             "first_measurement_date":"2026-08-01","last_measurement_date":"2026-08-21",
             "latest_measurement_date":"2026-08-21","latest_measurement_age_days":0,
             "trailing_7d_average_kg":\(averageJSON),"weekly_rate_kg":\(rateJSON)}
            """.utf8)
        )
    }

    private func completed(
        sha: String = "plan-sha",
        policy: String = "policy-p1",
        runIdentifier: UUID? = nil,
        versionIdentifier: UUID? = nil,
        itemIdentifier: UUID? = nil
    ) -> CompletedDayPlan {
        let candidate = PlanCandidate(
            candidateId: "candidate-stable-1",
            candidateKind: nil,
            caloriesKcal: "901.2300",
            categoryNames: ["Entree"],
            configurableEstimate: nil,
            dietaryTags: [],
            lines: [PlanLine(
                categoryName: "Entree",
                foodId: UUID(uuidString: "30000000-0000-0000-0000-000000000001")!,
                nameNormalized: "Example meal",
                occurrenceOrdinal: 0,
                offeringId: UUID(uuidString: "40000000-0000-0000-0000-000000000001")!,
                parserVersion: "parser.v1",
                profileContentSha256: "profile-sha",
                servings: "1.250",
                sourceMid: "mid-1"
            )],
            provenance: PlanProvenance(
                offeringIds: [], foodIds: [], profileContentSha256s: []),
            score: PlanScore(breakdown: [:], total: "-0.125"),
            totals: PlanNutritionFacts(
                confidence: "official_published",
                declaredUnavailable: [],
                presences: ["protein_g": "known_value"],
                publishedZero: [],
                quantities: ["protein_g": "50.500", "calories_kcal": "901.2300"])
        )
        return CompletedDayPlan(
            requestedDate: "2026-08-21",
            planDate: "2026-08-21",
            planSha256: sha,
            inputsFingerprint: "fingerprint",
            runId: runIdentifier ?? runId,
            versionId: versionIdentifier ?? versionId,
            plan: DailyPlanArtifact(
                artifactKind: "daily_plan",
                planDate: "2026-08-21",
                policyVersions: PlanPolicyVersions(
                    engine: "engine.v1", planner: "planner.v1",
                    schedule: "schedule.v1", target: "target.v1"),
                menuSnapshotSha256: "menu-sha",
                slots: [PlanSlot(
                    context: "lunch", menuPeriod: "Lunch", status: "ok",
                    window: ["12:00:00", "13:00:00"], failureReasons: [],
                    rejectionCounts: [:], rejectionDetails: [], candidates: [candidate])],
                status: "ok"
            ),
            planItems: [PlanItemReference(
                itemId: itemIdentifier ?? itemId, slotIndex: 0, rank: 1,
                candidateId: candidate.candidateId)],
            targetPolicy: TargetPolicyUsed(
                versionId: UUID(uuidString: "90000000-0000-0000-0000-000000000001")!,
                policyVersion: policy,
                payloadSha256: "policy-sha",
                approvedAt: nil),
            generatedAt: Date(timeIntervalSince1970: 1_700_000_000)
        )
    }

    private func completed(candidates: [PlanCandidate]) -> CompletedDayPlan {
        let base = completed()
        return CompletedDayPlan(
            requestedDate: base.requestedDate,
            planDate: base.planDate,
            planSha256: base.planSha256,
            inputsFingerprint: base.inputsFingerprint,
            runId: base.runId,
            versionId: base.versionId,
            plan: DailyPlanArtifact(
                artifactKind: base.plan.artifactKind,
                planDate: base.plan.planDate,
                policyVersions: base.plan.policyVersions,
                menuSnapshotSha256: base.plan.menuSnapshotSha256,
                slots: [PlanSlot(
                    context: "lunch", menuPeriod: "Lunch", status: "ok",
                    window: ["12:00:00", "13:00:00"], failureReasons: [],
                    rejectionCounts: [:], rejectionDetails: [], candidates: candidates)],
                status: base.plan.status
            ),
            planItems: candidates.enumerated().map { index, candidate in
                let rank = index + 1
                return PlanItemReference(
                    itemId: rankedItemId(rank),
                    slotIndex: 0,
                    rank: rank,
                    candidateId: candidate.candidateId
                )
            },
            targetPolicy: base.targetPolicy,
            generatedAt: base.generatedAt
        )
    }

    private func candidate(rank: Int, names: [String]) -> PlanCandidate {
        let lines = names.enumerated().map { index, name in
            PlanLine(
                categoryName: "Entree",
                foodId: UUID(uuidString: String(
                    format: "30000000-0000-0000-0000-%012d", rank * 10 + index + 1))!,
                nameNormalized: name,
                occurrenceOrdinal: index,
                offeringId: UUID(uuidString: String(
                    format: "40000000-0000-0000-0000-%012d", rank * 10 + index + 1))!,
                parserVersion: "parser.v1",
                profileContentSha256: "profile-sha-\(rank)-\(index)",
                servings: "1",
                sourceMid: "mid-\(rank)-\(index)"
            )
        }
        return PlanCandidate(
            candidateId: "candidate-stable-\(rank)",
            candidateKind: nil,
            caloriesKcal: "900",
            categoryNames: ["Entree"],
            configurableEstimate: nil,
            dietaryTags: [],
            lines: lines,
            provenance: PlanProvenance(
                offeringIds: lines.map(\.offeringId),
                foodIds: lines.map(\.foodId),
                profileContentSha256s: lines.map(\.profileContentSha256)
            ),
            score: PlanScore(breakdown: [:], total: "-0.1"),
            totals: PlanNutritionFacts(
                confidence: "official_published",
                declaredUnavailable: [],
                presences: ["calories_kcal": "known_value"],
                publishedZero: [],
                quantities: ["calories_kcal": "900"]
            )
        )
    }

    private func estimatedCandidate() throws -> PlanCandidate {
        try JSONDecoder().decode(
            PlanCandidate.self,
            from: Data(
                """
                {
                  "candidate_id":"post_workout_lunch-001",
                  "candidate_kind":"configurable_estimate",
                  "calories_kcal":"604.27781682500",
                  "category_names":["CREATE YOUR OWN"],
                  "configurable_estimate":{
                    "availability":{
                      "campus_id":50,
                      "category_name":"CREATE YOUR OWN",
                      "food_id":"30000000-0000-0000-0000-000000000099",
                      "menu_period":"Lunch",
                      "menu_snapshot_sha256":"menu-sha",
                      "nutrition_snapshot_sha256":"placeholder-sha",
                      "nutrition_source_state":"source_placeholder",
                      "occurrence_ordinal":0,
                      "offering_id":"40000000-0000-0000-0000-000000000099",
                      "service_date":"2026-08-31",
                      "source_page_snapshot_sha256":"source-page-sha",
                      "source_mid":"source-mid"
                    },
                    "definition":{
                      "campus_id":50,
                      "configuration_summary":"4 oz rice, 4 oz chicken, 2 eggs, 4 x 1 oz quinoa, no sauces",
                      "configuration_version":"owner-observed.v1",
                      "definition_id":"owner.cyo_halal_bowl",
                      "definition_version":"owner-cyo-halal-bowl.v1",
                      "display_name":"CYO Halal Bowl",
                      "estimate":{
                        "caveats":["Preparation differs from the cited generic references."],
                        "confidence":"estimated",
                        "resolved_components":[],
                        "state":"partial_estimate",
                        "totals":{
                          "confidence":"estimated",
                          "declared_unavailable":[],
                          "presences":{"calories_kcal":"known_value","protein_g":"known_value"},
                          "published_zero":[],
                          "quantities":{"calories_kcal":"604.27781682500","protein_g":"45.7386516982500"}
                        },
                        "unknown_nutrients":["added_sugars_g","trans_fat_g"],
                        "unresolved_components":[]
                      },
                      "selected_components":[{
                        "component_id":"turmeric_basmati_rice",
                        "portion":{
                          "amount":"4",
                          "evidence":{
                            "citation_urls":[],
                            "description":"Owner-observed portion.",
                            "reference_id":"owner-observation",
                            "source_class":"owner_observed_configuration",
                            "version":"v1"
                          },
                          "unit":"oz"
                        }
                      }],
                      "source_name_normalized":"CYO Halal Bowl",
                      "template_version":"halal-template.v1"
                    },
                    "evidence_digest":"definition-evidence-sha",
                    "nutritional_score":{"breakdown":{"calories_kcal:target":"-0.01"},"total":"-0.01"},
                    "score_adjustments":{"uncertainty:estimated_nutrition":"-0.10","preference:owner.cyo_halal_bowl":"0.05"}
                  },
                  "dietary_tags":[],
                  "lines":[],
                  "provenance":{
                    "offering_ids":["40000000-0000-0000-0000-000000000099"],
                    "food_ids":["30000000-0000-0000-0000-000000000099"],
                    "profile_content_sha256s":[]
                  },
                  "score":{
                    "breakdown":{"calories_kcal:target":"-0.01","uncertainty:estimated_nutrition":"-0.10","preference:owner.cyo_halal_bowl":"0.05"},
                    "total":"-0.06"
                  },
                  "totals":{
                    "confidence":"estimated",
                    "declared_unavailable":[],
                    "presences":{"calories_kcal":"known_value","protein_g":"known_value"},
                    "published_zero":[],
                    "quantities":{"calories_kcal":"604.27781682500","protein_g":"45.7386516982500"}
                  }
                }
                """.utf8
            )
        )
    }

    private func ledger(consumed: String, count: Int) -> DailyNutritionLedgerResponse {
        DailyNutritionLedgerResponse(
            localDate: "2026-08-21",
            timezone: "America/New_York",
            target: DailyNutritionTarget(
                policyVersionId: UUID(
                    uuidString: "90000000-0000-0000-0000-000000000001")!,
                policyVersion: "target.v1",
                caloriesKcal: "2500",
                caloriesGoalKind: "target",
                proteinG: "150",
                proteinGoalKind: "floor"
            ),
            consumedItemCount: count,
            knownCaloriesConsumed: consumed,
            knownProteinGConsumed: count == 0 ? "0" : "50.500",
            remainingKnownCalories: count == 0 ? "2500" : "1598.7700",
            remainingKnownProteinG: count == 0 ? "150" : "99.500",
            nutritionCompleteness: .complete,
            nutritionAuthorities: count == 0 ? [] : [.official],
            unknownNutrients: [],
            consumedItems: [],
            reasonCodes: count == 0 ? ["no_consumption"] : []
        )
    }

    private func rankedItemId(_ rank: Int) -> UUID {
        UUID(uuidString: String(format: "50000000-0000-0000-0000-%012d", rank))!
    }

    private func planWithoutGetMetadata() -> CompletedDayPlan {
        let plan = completed()
        return CompletedDayPlan(
            requestedDate: plan.requestedDate,
            planDate: plan.planDate,
            planSha256: plan.planSha256,
            inputsFingerprint: plan.inputsFingerprint,
            runId: plan.runId,
            versionId: plan.versionId,
            plan: plan.plan,
            planItems: nil,
            targetPolicy: nil,
            generatedAt: nil
        )
    }

    private func entry(
        entryIdentifier: UUID? = nil,
        runIdentifier: UUID? = nil,
        versionIdentifier: UUID? = nil,
        itemIdentifier: UUID? = nil,
        clientEventIdentifier: UUID? = nil
    ) -> ConsumptionEntryResponse {
        ConsumptionEntryResponse(
            entryId: entryIdentifier
                ?? UUID(uuidString: "70000000-0000-0000-0000-000000000001")!,
            planRunId: runIdentifier ?? runId,
            planVersionId: versionIdentifier ?? versionId,
            itemId: itemIdentifier ?? itemId,
            state: .eaten,
            recordedAt: Date(timeIntervalSince1970: 1_700_000_200),
            clientEventId: clientEventIdentifier ?? eventId
        )
    }
}
