import XCTest
@testable import NutritionHealthCompanion

/// ViewModel state mapping incl. permission-neutral noReadableSamples copy
/// and latest_sample semantics (most recent ACTIVE sample by sample_start).
@MainActor
final class ViewModelTests: XCTestCase {
    private func makeViewModel(
        health: FakeHealthKitPort,
        backend: FakeBackend,
        store: InMemoryStateStore,
        auth: FakeAccessTokenProvider = FakeAccessTokenProvider(),
        onSyncCompleted: @escaping @MainActor () -> Void = {}
    ) -> HealthSyncViewModel {
        // Same convergence as production: the VM routes through the shared
        // single-flight engine rather than owning a coordinator.
        let engine = HealthSyncEngine(
            reading: health, backend: backend, store: store, auth: auth
        )
        return HealthSyncViewModel(
            engine: engine,
            backend: backend,
            store: store,
            onSyncCompleted: onSyncCompleted
        )
    }

    func test_unavailableDevice_showsUnavailable_neverQueries() async {
        var health = FakeHealthKitPort(pages: [])
        health.available = false
        let vm = makeViewModel(health: health, backend: FakeBackend(), store: InMemoryStateStore())
        await vm.syncNow()
        XCTAssertEqual(vm.phase, .unavailable)
        XCTAssertEqual(health.fetchCalls.count, 0)
    }

    func test_noReadableSamples_isPermissionNeutralState() async {
        // Observed-empty history: completed query returning zero rows.
        let empty = AnchoredChanges(added: [], deletions: [], transientAnchorData: nil)
        let health = FakeHealthKitPort(pages: [empty])
        let vm = makeViewModel(health: health, backend: FakeBackend(), store: InMemoryStateStore())
        await vm.syncNow()
        XCTAssertEqual(vm.phase, .noReadableSamples)
    }

    func test_failedSync_mapsClassification() async {
        let page = AnchoredChanges(
            added: [BodyMassSampleDTO(
                sampleUUID: UUID(), valueKgDecimalString: "70.000",
                sampleStart: Date(), sampleEnd: Date())],
            deletions: [], transientAnchorData: Data("x".utf8))
        let health = FakeHealthKitPort(pages: [page])
        let backend = FakeBackend(scriptedFailures: [BackendError.retryable("down")])
        let vm = makeViewModel(health: health, backend: backend, store: InMemoryStateStore())
        await vm.syncNow()
        guard case .failed(let reason) = vm.phase else {
            return XCTFail("expected failed phase, got \(vm.phase)")
        }
        XCTAssertEqual(reason, SyncErrorClassification.networkUnreachable.rawValue)
    }

    func test_successfulSyncInvokesCompletionCallbackExactlyOnce() async {
        let page = AnchoredChanges(
            added: [BodyMassSampleDTO(
                sampleUUID: UUID(), valueKgDecimalString: "70.000",
                sampleStart: Date(), sampleEnd: Date())],
            deletions: [], transientAnchorData: Data("anchor".utf8))
        let health = FakeHealthKitPort(pages: [page])
        var completions = 0
        let vm = makeViewModel(
            health: health,
            backend: FakeBackend(),
            store: InMemoryStateStore(),
            onSyncCompleted: { completions += 1 }
        )

        await vm.syncNow()

        XCTAssertEqual(completions, 1)
    }

    func test_failedSyncDoesNotInvokeCompletionCallback() async {
        let page = AnchoredChanges(
            added: [BodyMassSampleDTO(
                sampleUUID: UUID(), valueKgDecimalString: "70.000",
                sampleStart: Date(), sampleEnd: Date())],
            deletions: [], transientAnchorData: Data("anchor".utf8))
        var completions = 0
        let vm = makeViewModel(
            health: FakeHealthKitPort(pages: [page]),
            backend: FakeBackend(scriptedFailures: [BackendError.retryable("down")]),
            store: InMemoryStateStore(),
            onSyncCompleted: { completions += 1 }
        )

        await vm.syncNow()

        XCTAssertEqual(completions, 0)
    }

    func test_successfulBodyMassAndWorkoutSyncCompleteAsOneForegroundOperation() async {
        let bodyPage = AnchoredChanges(
            added: [BodyMassSampleDTO(
                sampleUUID: UUID(), valueKgDecimalString: "70.000",
                sampleStart: Date(), sampleEnd: Date()
            )],
            deletions: [], transientAnchorData: Data("body-anchor".utf8)
        )
        let workoutPage = WorkoutAnchoredChanges(
            added: [WorkoutSampleDTO(
                sourceRecordID: UUID(), activityType: "37",
                startedAt: Date(), endedAt: Date(),
                activeDurationSecondsDecimalString: "0.000",
                activeEnergyKcalDecimalString: nil,
                timezoneIdentifier: nil, sourceName: nil,
                sourceBundleID: nil, sourceRevision: nil
            )],
            deletions: [], transientAnchorData: Data("workout-anchor".utf8)
        )
        let health = FakeHealthKitPort(
            pages: [bodyPage], workoutPages: [workoutPage]
        )
        let backend = FakeBackend()
        let bodyStore = InMemoryStateStore()
        let workoutStore = InMemoryStateStore()
        let bodyEngine = HealthSyncEngine(
            reading: health, backend: backend, store: bodyStore,
            auth: FakeAccessTokenProvider()
        )
        let workoutEngine = WorkoutSyncEngine(
            reading: health, backend: backend, store: workoutStore,
            auth: FakeAccessTokenProvider()
        )
        var completions = 0
        let vm = HealthSyncViewModel(
            engine: bodyEngine,
            workoutEngine: workoutEngine,
            backend: backend,
            store: bodyStore,
            onSyncCompleted: { completions += 1 }
        )

        await vm.syncNow()

        XCTAssertEqual(backend.batchCount, 1)
        XCTAssertEqual(backend.workoutBatchCount, 1)
        XCTAssertEqual(completions, 1)
        XCTAssertFalse(vm.isSyncingNow)
    }

    func test_combinedFreshnessUsesOlderSuccessfulStreamTimestamp() throws {
        let bodyStore = InMemoryStateStore()
        let workoutStore = InMemoryStateStore()
        let bodySuccess = Date(timeIntervalSince1970: 1_760_000_000)
        let workoutSuccess = Date(timeIntervalSince1970: 1_760_000_100)
        try bodyStore.persist(DurableSyncState(
            durableAnchorData: Data("body".utf8),
            lastSuccessfulSync: bodySuccess,
            lastAttempt: bodySuccess,
            lastErrorDescription: nil
        ))
        try workoutStore.persist(DurableSyncState(
            durableAnchorData: Data("workout".utf8),
            lastSuccessfulSync: workoutSuccess,
            lastAttempt: workoutSuccess,
            lastErrorDescription: nil
        ))
        let health = FakeHealthKitPort(pages: [])
        let vm = HealthSyncViewModel(
            engine: HealthSyncEngine(
                reading: health,
                backend: FakeBackend(),
                store: bodyStore,
                auth: FakeAccessTokenProvider()
            ),
            backend: FakeBackend(),
            store: bodyStore,
            workoutStore: workoutStore
        )

        vm.refreshFromStore()

        XCTAssertEqual(vm.lastSuccessfulSync, bodySuccess)
    }
}
