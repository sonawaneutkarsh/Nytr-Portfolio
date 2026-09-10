import XCTest
@testable import NutritionHealthCompanion

@MainActor
final class TrainingViewModelTests: XCTestCase {
    private final class Backend: BackendClient, @unchecked Sendable {
        private let lock = NSLock()
        var recentResults: [Result<TrainingAnalyticsRecentResponse, Error>] = []
        var indexResults: [Result<ExerciseIndexResponse, Error>] = []
        var historyResults: [Result<ExerciseTrainingHistoryResponse, Error>] = []
        var detailResults: [Result<DetailedTrainingSessionResponse, Error>] = []
        var syncResults: [Result<HevySyncResponse, Error>] = []
        var suspendSync = false
        private(set) var recentLimits: [Int] = []
        private(set) var indexCalls: [(Date, String, Int)] = []
        private(set) var historyCalls: [(String, String, Date, String, Int)] = []
        private(set) var detailRevisionIds: [String] = []
        private(set) var syncCallCount = 0
        private var syncContinuation: CheckedContinuation<HevySyncResponse, Error>?

        var hasPendingSync: Bool {
            lock.withLock { syncContinuation != nil }
        }

        func submitBatch(
            added _: [BodyMassSampleDTO], deletions _: [DeletedSampleDTO]
        ) async throws -> SyncResponse {
            throw BackendError.retryable("unused")
        }

        func fetchSyncStatus() async throws -> StatusResponse {
            throw BackendError.retryable("unused")
        }

        func fetchRecentTrainingAnalytics(limit: Int) async throws
            -> TrainingAnalyticsRecentResponse
        {
            try lock.withLock {
                recentLimits.append(limit)
                return try recentResults.removeFirst().get()
            }
        }

        func syncHevyTraining() async throws -> HevySyncResponse {
            let shouldSuspend = lock.withLock {
                syncCallCount += 1
                return suspendSync
            }
            if shouldSuspend {
                return try await withCheckedThrowingContinuation { continuation in
                    lock.withLock { syncContinuation = continuation }
                }
            }
            return try lock.withLock { try syncResults.removeFirst().get() }
        }

        func resumeSync(with result: Result<HevySyncResponse, Error>) {
            let continuation = lock.withLock {
                let value = syncContinuation
                syncContinuation = nil
                return value
            }
            continuation?.resume(with: result)
        }

        func fetchExerciseIndex(asOfDate: Date, timezone: String, limit: Int) async throws
            -> ExerciseIndexResponse
        {
            try lock.withLock {
                indexCalls.append((asOfDate, timezone, limit))
                return try indexResults.removeFirst().get()
            }
        }

        func fetchExerciseHistory(
            sourceExerciseId: String,
            sourceSystem: String,
            asOfDate: Date,
            timezone: String,
            limit: Int
        ) async throws -> ExerciseTrainingHistoryResponse {
            try lock.withLock {
                historyCalls.append(
                    (sourceExerciseId, sourceSystem, asOfDate, timezone, limit)
                )
                return try historyResults.removeFirst().get()
            }
        }

        func fetchDetailedTrainingSession(revisionId: String) async throws
            -> DetailedTrainingSessionResponse
        {
            try lock.withLock {
                detailRevisionIds.append(revisionId)
                return try detailResults.removeFirst().get()
            }
        }
    }

    private let asOfDate = Date(timeIntervalSince1970: 1_788_547_200)

    func testActivationLoadsRecentOnceAndExplicitRefreshReloads() async {
        let backend = Backend()
        backend.recentResults = [.success(Self.recent), .success(Self.recent)]
        let viewModel = TrainingViewModel(backend: backend)

        await viewModel.activate(subject: "owner-a")
        await viewModel.activate(subject: "owner-a")

        XCTAssertEqual(viewModel.recentPhase, .result(Self.recent))
        XCTAssertEqual(backend.recentLimits, [20])
        XCTAssertEqual(backend.syncCallCount, 0)

        await viewModel.refreshRecent()
        XCTAssertEqual(backend.recentLimits, [20, 20])
    }

    func testExplicitHevySyncShowsLoadingPreventsDuplicatesAndReloadsRecent() async {
        let backend = Backend()
        backend.recentResults = [.success(Self.emptyRecent), .success(Self.recent)]
        backend.suspendSync = true
        let viewModel = TrainingViewModel(backend: backend)
        await viewModel.activate(subject: "owner-a")
        XCTAssertEqual(viewModel.recentPhase, .empty)

        let syncTask = Task { await viewModel.syncHevy() }
        while !backend.hasPendingSync { await Task.yield() }

        XCTAssertTrue(viewModel.isSyncingHevy)
        XCTAssertEqual(viewModel.hevySyncPhase, .syncing)
        await viewModel.syncHevy()
        XCTAssertEqual(backend.syncCallCount, 1)

        backend.resumeSync(with: .success(Self.syncResponse))
        await syncTask.value

        XCTAssertFalse(viewModel.isSyncingHevy)
        XCTAssertEqual(viewModel.recentPhase, .result(Self.recent))
        XCTAssertEqual(
            viewModel.hevySyncPhase,
            .success("Synced 1 workout from Hevy.")
        )
        XCTAssertEqual(backend.recentLimits, [20, 20])
        XCTAssertEqual(backend.syncCallCount, 1)
    }

    func testHevySyncFailurePreservesTrainingDataAndDoesNotRetry() async {
        let backend = Backend()
        backend.recentResults = [.success(Self.recent)]
        backend.syncResults = [.failure(BackendError.retryable("offline"))]
        let viewModel = TrainingViewModel(backend: backend)
        await viewModel.activate(subject: "owner-a")

        await viewModel.syncHevy()

        XCTAssertEqual(viewModel.recentPhase, .result(Self.recent))
        XCTAssertEqual(
            viewModel.hevySyncPhase,
            .error("Hevy sync is temporarily unavailable. Existing Training data is unchanged.")
        )
        XCTAssertEqual(backend.recentLimits, [20])
        XCTAssertEqual(backend.syncCallCount, 1)
    }

    func testHevySyncUnauthorizedSignsOutAndNotifiesSessionOwner() async {
        let backend = Backend()
        backend.recentResults = [.success(Self.recent)]
        backend.syncResults = [.failure(BackendError.unauthorized)]
        var signedOut = false
        let viewModel = TrainingViewModel(
            backend: backend,
            onUnauthorized: { signedOut = true }
        )
        await viewModel.activate(subject: "owner-a")

        await viewModel.syncHevy()

        XCTAssertEqual(viewModel.recentPhase, .signedOut)
        XCTAssertEqual(viewModel.hevySyncPhase, .signedOut)
        XCTAssertTrue(signedOut)
        XCTAssertEqual(backend.syncCallCount, 1)
    }

    func testStaleHevySyncResponseCannotPopulateReplacementOwner() async {
        let backend = Backend()
        backend.recentResults = [.success(Self.recent), .success(Self.emptyRecent)]
        backend.suspendSync = true
        let viewModel = TrainingViewModel(backend: backend)
        await viewModel.activate(subject: "owner-a")

        let oldOwnerSync = Task { await viewModel.syncHevy() }
        while !backend.hasPendingSync { await Task.yield() }
        await viewModel.activate(subject: "owner-b")
        backend.resumeSync(with: .success(Self.syncResponse))
        await oldOwnerSync.value

        XCTAssertEqual(viewModel.recentPhase, .empty)
        XCTAssertEqual(viewModel.hevySyncPhase, .idle)
        XCTAssertEqual(backend.recentLimits, [20, 20])
        XCTAssertEqual(backend.syncCallCount, 1)
    }

    func testSignedOutHevySyncResponseIsIgnored() async {
        let backend = Backend()
        backend.recentResults = [.success(Self.recent)]
        backend.suspendSync = true
        let viewModel = TrainingViewModel(backend: backend)
        await viewModel.activate(subject: "owner-a")

        let syncTask = Task { await viewModel.syncHevy() }
        while !backend.hasPendingSync { await Task.yield() }
        viewModel.resetForSignOut()
        backend.resumeSync(with: .success(Self.syncResponse))
        await syncTask.value

        XCTAssertEqual(viewModel.recentPhase, .signedOut)
        XCTAssertEqual(viewModel.hevySyncPhase, .signedOut)
        XCTAssertEqual(backend.recentLimits, [20])
    }

    func testExerciseIndexAndHistoryUseExplicitIdentityDateTimezoneAndBounds() async {
        let backend = Backend()
        backend.recentResults = [.success(Self.recent)]
        backend.indexResults = [.success(Self.index)]
        backend.historyResults = [.success(Self.history)]
        let viewModel = TrainingViewModel(
            backend: backend,
            requestDate: { self.asOfDate },
            timezoneIdentifier: { "America/New_York" }
        )
        await viewModel.activate(subject: "owner-a")

        await viewModel.loadExerciseIndex()
        await viewModel.loadExerciseHistory(
            sourceExerciseId: "fixture-row", sourceSystem: "hevy"
        )

        XCTAssertEqual(viewModel.indexPhase, .result(Self.index))
        XCTAssertEqual(viewModel.historyPhase, .result(Self.history))
        XCTAssertEqual(backend.indexCalls.count, 1)
        XCTAssertEqual(backend.indexCalls[0].0, asOfDate)
        XCTAssertEqual(backend.indexCalls[0].1, "America/New_York")
        XCTAssertEqual(backend.indexCalls[0].2, 200)
        XCTAssertEqual(backend.historyCalls.count, 1)
        XCTAssertEqual(backend.historyCalls[0].0, "fixture-row")
        XCTAssertEqual(backend.historyCalls[0].1, "hevy")
        XCTAssertEqual(backend.historyCalls[0].2, asOfDate)
        XCTAssertEqual(backend.historyCalls[0].3, "America/New_York")
        XCTAssertEqual(backend.historyCalls[0].4, 50)
    }

    func testSessionDetailLoadsExactImmutableRevisionAndDoesNotRefetchIt() async {
        let backend = Backend()
        backend.recentResults = [.success(Self.recent)]
        backend.detailResults = [.success(Self.detail)]
        let viewModel = TrainingViewModel(backend: backend)
        await viewModel.activate(subject: "owner-a")

        await viewModel.loadSessionDetail(revisionId: "revision-id")
        await viewModel.loadSessionDetail(revisionId: "revision-id")

        XCTAssertEqual(viewModel.sessionDetailPhase, .result(Self.detail))
        XCTAssertEqual(backend.detailRevisionIds, ["revision-id"])
        XCTAssertEqual(Self.detail.exercises[0].sets[0].reps, 10)
        XCTAssertEqual(Self.detail.exercises[0].sets[0].load?.value, "36.28743275485118")
        XCTAssertEqual(Self.detail.exercises[0].sets[0].setType, "normal")
        XCTAssertEqual(Self.detail.exercises[0].sets[0].rpe, "8.5")
    }

    func testMismatchedSessionDetailFailsClosed() async {
        let backend = Backend()
        backend.recentResults = [.success(Self.recent)]
        backend.detailResults = [.success(Self.detail)]
        let viewModel = TrainingViewModel(backend: backend)
        await viewModel.activate(subject: "owner-a")

        await viewModel.loadSessionDetail(revisionId: "different-revision")

        XCTAssertEqual(
            viewModel.sessionDetailPhase,
            .error(
                "different-revision",
                "The detailed workout response did not match the selected revision."
            )
        )
    }

    func testFailureIsSafeAndUnauthorizedClearsEveryTrainingState() async {
        let failedBackend = Backend()
        failedBackend.recentResults = [.failure(BackendError.retryable("offline"))]
        let failed = TrainingViewModel(backend: failedBackend)
        await failed.activate(subject: "owner-a")
        XCTAssertEqual(failed.recentPhase, .error("Training data is temporarily unavailable."))

        let unauthorizedBackend = Backend()
        unauthorizedBackend.recentResults = [.failure(BackendError.unauthorized)]
        var signedOut = false
        let unauthorized = TrainingViewModel(
            backend: unauthorizedBackend,
            onUnauthorized: { signedOut = true }
        )
        await unauthorized.activate(subject: "owner-a")

        XCTAssertEqual(unauthorized.recentPhase, .signedOut)
        XCTAssertEqual(unauthorized.indexPhase, .signedOut)
        XCTAssertEqual(unauthorized.historyPhase, .signedOut)
        XCTAssertEqual(unauthorized.sessionDetailPhase, .signedOut)
        XCTAssertTrue(signedOut)
    }

    private static let occurredAt = Date(timeIntervalSince1970: 1_788_547_200)
    private static let topSet = TopLoadSetEvidenceDTO(
        setIdentity: "0:0",
        setIndex: 0,
        setType: "normal",
        reps: 10,
        loadKg: "36.28743275485118",
        rpe: "8.5"
    )
    private static let occurrence = ExerciseOccurrenceAnalyticsDTO(
        occurrenceIdentity: "fixture-row:0",
        sourceExerciseId: "fixture-row",
        displayName: "Cable Row",
        exerciseOrder: 0,
        metricFamily: "rep_load",
        recordedSetCount: 1,
        workingSetCount: 1,
        warmupSetCount: 0,
        unsupportedSetCount: 0,
        repTotal: 10,
        maxLoadKg: "36.28743275485118",
        topLoadSet: topSet,
        volumeKgReps: "362.87432754851180",
        durationSeconds: nil,
        distanceMeters: nil,
        maxRpe: "8.5",
        metricCompleteness: "complete",
        reasonCodes: []
    )
    private static let session = TrainingSessionAnalyticsDTO(
        revisionId: "revision-id",
        sourceSystem: "hevy",
        sourceSessionId: "session-id",
        sourceRevision: "source-revision",
        title: "Pull 1",
        startedAt: occurredAt,
        endedAt: occurredAt.addingTimeInterval(3600),
        sessionDurationSeconds: "3600",
        exerciseCount: 1,
        recordedSetCount: 1,
        workingSetCount: 1,
        warmupSetCount: 0,
        unsupportedSetCount: 0,
        repTotal: 10,
        volumeKgReps: "362.87432754851180",
        volumeExerciseCount: 1,
        exercises: [occurrence]
    )
    private static let recent = TrainingAnalyticsRecentResponse(
        policyVersion: "owner-training-analytics.v1", sessions: [session]
    )
    private static let emptyRecent = TrainingAnalyticsRecentResponse(
        policyVersion: "owner-training-analytics.v1", sessions: []
    )
    private static let syncResponse = HevySyncResponse(
        status: "synced",
        mode: "incremental",
        sessionsCreated: 1,
        revisionsAppended: 0,
        deletionsRecorded: 0,
        hasMore: false,
        checkpointAdvanced: true
    )
    private static let completeness = TrainingHistoryCompletenessDTO(
        sourceBootstrapComplete: true,
        queryComplete: true,
        lifetimeGuaranteed: false,
        wording: "PR within synced Hevy history; Hevy lifetime completeness is not guaranteed."
    )
    private static let frequency = ExerciseFrequencyDTO(
        timezone: "America/New_York",
        asOfDate: "2026-09-04",
        sessionsLast7Days: 1,
        sessionsLast28Days: 1,
        daysSinceLastPerformance: 0
    )
    private static let point = ExerciseHistoryPointDTO(
        revisionId: "revision-id",
        sourceSessionId: "session-id",
        sourceRevision: "source-revision",
        sessionTitle: "Pull 1",
        startedAt: occurredAt,
        displayName: "Cable Row",
        occurrenceCount: 1,
        metricFamily: "rep_load",
        recordedSetCount: 1,
        workingSetCount: 1,
        warmupSetCount: 0,
        unsupportedSetCount: 0,
        repTotal: 10,
        maxLoadKg: "36.28743275485118",
        topLoadSet: topSet,
        volumeKgReps: "362.87432754851180",
        maxRpe: "8.5",
        metricCompleteness: "complete"
    )
    private static let coaching = ExerciseCoachingGuidanceDTO(
        policyVersion: "owner-training-coaching.v1",
        analyticsPolicyVersion: "owner-training-analytics.v1",
        timezone: "America/New_York",
        asOfDate: "2026-09-04",
        status: "unavailable",
        action: nil,
        metricFamily: "rep_load",
        latestRevisionId: "revision-id",
        previousRevisionId: nil,
        latestStartedAt: occurredAt,
        previousStartedAt: nil,
        target: nil,
        reasonCodes: ["insufficient_history"],
        limitations: ["advisory_only"]
    )
    private static let history = ExerciseTrainingHistoryResponse(
        policyVersion: "owner-training-analytics.v1",
        sourceSystem: "hevy",
        sourceExerciseId: "fixture-row",
        latestDisplayName: "Cable Row",
        history: [point],
        latest: point,
        previous: nil,
        comparison: ExerciseSessionComparisonDTO(
            latestRevisionId: "revision-id",
            previousRevisionId: nil,
            workingSetCountDelta: nil,
            topLoadDeltaKg: nil,
            repsAtSameTopLoadDelta: nil,
            volumeDeltaKgReps: nil,
            reasonCodes: ["no_previous_session"]
        ),
        frequency: frequency,
        prEvidence: [],
        completeness: completeness,
        coaching: coaching
    )
    private static let index = ExerciseIndexResponse(
        policyVersion: "owner-training-analytics.v1",
        completeness: completeness,
        exercises: [
            ExerciseIndexEntryDTO(
                sourceSystem: "hevy",
                sourceExerciseId: "fixture-row",
                latestDisplayName: "Cable Row",
                lastPerformedAt: occurredAt,
                sessionCount: 1,
                latestMetricFamily: "rep_load",
                latestTopLoadSet: topSet,
                frequency: frequency
            )
        ]
    )
    private static let detail = DetailedTrainingSessionResponse(
        revisionId: "revision-id",
        sourceSystem: "hevy",
        sourceSessionId: "session-id",
        sourceRevision: "source-revision",
        title: "Pull 1",
        description: nil,
        routineId: nil,
        startedAt: occurredAt,
        endedAt: occurredAt.addingTimeInterval(3600),
        sourceCreatedAt: occurredAt,
        sourceUpdatedAt: occurredAt,
        parserVersion: "hevy-public-api.v1",
        sourcePayloadSha256: String(repeating: "a", count: 64),
        ingestedAt: occurredAt,
        exercises: [
            DetailedTrainingExerciseDTO(
                occurrenceIdentity: "fixture-row:0",
                sourceExerciseId: "fixture-row",
                displayName: "Cable Row",
                exerciseOrder: 0,
                notes: nil,
                supersetId: nil,
                sets: [
                    DetailedTrainingSetDTO(
                        setIdentity: "0:0",
                        sourceSetId: "source-set",
                        setIndex: 0,
                        setType: "normal",
                        reps: 10,
                        load: DetailedTrainingMeasurementDTO(
                            value: "36.28743275485118", unit: "kg"
                        ),
                        distance: nil,
                        durationSeconds: nil,
                        rpe: "8.5",
                        customMetric: nil
                    )
                ]
            )
        ]
    )
}
