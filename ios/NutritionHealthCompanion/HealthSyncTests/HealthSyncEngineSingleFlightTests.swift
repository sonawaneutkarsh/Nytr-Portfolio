import XCTest
@testable import NutritionHealthCompanion

/// Regression tests for the M5 fix pass single-flight requirement:
/// every sync trigger converges on ONE shared HealthSyncEngine run, so two
/// HealthKit anchored loops can never execute concurrently, and persistence /
/// cancellation failures can never advance the durable anchor past data the
/// backend has durably accepted.
@MainActor
final class HealthSyncEngineSingleFlightTests: XCTestCase {
    private struct CallerExecutorProbe: HealthKitAuthorizing, HealthKitBodyMassReading {
        func isAvailable() -> Bool { true }

        func requestAuthorization() async throws -> AuthorizationOutcome {
            .completed
        }

        func earliestAuthorizedStartDate() async -> Date {
            .distantPast
        }

        func startAnchoredChangesFetch(
            anchorData: Data?, after: Date?, limit: Int,
            completion: @escaping @Sendable (Result<AnchoredChanges, Error>) -> Void
        ) {
            MainActor.preconditionIsolated()
            completion(.success(
                AnchoredChanges(
                    added: [], deletions: [], transientAnchorData: anchorData
                )
            ))
        }

        var changesInBackground: (@Sendable () async -> Void)? {
            get { nil }
            set {}
        }
    }

    // MARK: - Scripted pages

    private func makeSample(_ n: Int) -> BodyMassSampleDTO {
        BodyMassSampleDTO(
            sampleUUID: UUID(uuidString: "00000000-0000-0000-0000-\(String(format: "%012x", n))")!,
            valueKgDecimalString: "70.500",
            sampleStart: Date(timeIntervalSince1970: 1_760_000_000),
            sampleEnd: Date(timeIntervalSince1970: 1_760_000_000)
        )
    }

    private func page(_ ids: [Int], anchor: String) -> AnchoredChanges {
        AnchoredChanges(
            added: ids.map(makeSample),
            deletions: [],
            transientAnchorData: Data(anchor.utf8)
        )
    }

    private func waitForParkedFetch(
        _ health: FakeHealthKitPort, timeout: TimeInterval = 5
    ) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if health.fetchCalls.count >= 1 { return }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTFail("sync loop never reached its first parked fetch")
    }

    // MARK: Synchronous HealthKit entry

    func test_fetchRequirement_entersSynchronouslyThroughComposedExistential() async throws {
        let reading: any HealthKitAuthorizing & HealthKitBodyMassReading = CallerExecutorProbe()

        let result = try await withCheckedThrowingContinuation { continuation in
            reading.startAnchoredChangesFetch(
                anchorData: Data("probe-anchor".utf8), after: nil, limit: 1
            ) { result in
                continuation.resume(with: result)
            }
        }

        XCTAssertEqual(result.transientAnchorData, Data("probe-anchor".utf8))
        XCTAssertTrue(result.isEmptyPage)
    }

    // MARK: 1. Concurrent triggers are single-flight

    func test_concurrentTriggers_coalesceIntoOneAnchoredLoop_andUploadEachPageOnce() async throws {
        let health = FakeHealthKitPort(pages: [
            page([1], anchor: "anchor-2"),
            page([2], anchor: "anchor-3"),
            AnchoredChanges(added: [], deletions: [], transientAnchorData: Data("anchor-4".utf8)),
        ])
        health.gateOnFetchNumber = 1
        let store = InMemoryStateStore()
        let backend = FakeBackend()
        let engine = HealthSyncEngine(
            reading: health, backend: backend, store: store,
            auth: FakeAccessTokenProvider()
        )

        async let r1 = engine.runFullSync()
        async let r2 = engine.runIncrementalSync()
        async let r3 = engine.runFullSync()

        // Park mid-loop: trigger #1 is provably active inside fetch #1 while
        // triggers #2/#3 arrive, so they can only ever coalesce.
        try await waitForParkedFetch(health)
        // Small settle window so coalescing callers reach `activeRun.value`.
        try await Task.sleep(nanoseconds: 100_000_000)
        XCTAssertEqual(health.fetchCalls.count, 1, "loop stays parked until released")
        health.releaseParkedFetch()

        let outcomes = await [r1, r2, r3]
        XCTAssertTrue(outcomes.allSatisfy { $0 == .synced },
                      "all joiners receive the same terminal outcome: \(outcomes)")

        // Exactly ONE anchored loop ran: nil-start chain consumed each page
        // exactly once with no duplicated loop restarts.
        XCTAssertEqual(health.fetchCalls.count, 3)
        XCTAssertEqual(health.fetchCalls[0], nil)
        XCTAssertEqual(health.fetchCalls[1], Data("anchor-2".utf8))
        XCTAssertEqual(health.fetchCalls[2], Data("anchor-3".utf8))
        // Each non-empty page uploaded exactly once; empty terminator uploaded never.
        XCTAssertEqual(backend.batchCount, 2)
        XCTAssertEqual(backend.batches[0].addedUUIDs.count, 1)
        XCTAssertEqual(backend.batches[1].addedUUIDs.count, 1)
        XCTAssertNotEqual(backend.batches[0], backend.batches[1])
        // Durable anchor advanced per-page, ending at the last committed page.
        XCTAssertEqual(store.state?.durableAnchorData, Data("anchor-3".utf8))
        XCTAssertNotNil(store.state?.lastSuccessfulSync)
    }

    // MARK: 2. Observer wake + foreground activation overlap

    func test_observerWake_overlappingForegroundActivation_runsSingleLoop() async throws {
        let health = FakeHealthKitPort(pages: [
            page([11], anchor: "obs-2"),
            page([12], anchor: "obs-3"),
            AnchoredChanges(added: [], deletions: [], transientAnchorData: Data("obs-4".utf8)),
        ])
        health.gateOnFetchNumber = 1
        let store = InMemoryStateStore()
        let backend = FakeBackend()
        let engine = HealthSyncEngine(
            reading: health, backend: backend, store: store,
            auth: FakeAccessTokenProvider()
        )
        let viewModel = HealthSyncViewModel(engine: engine, backend: backend, store: store)

        // Observer/background surrogate + foreground scenePhase surrogate
        // firing concurrently at the same shared engine.
        async let observerOutcome = engine.runIncrementalSync()
        async let foregroundOutcome = viewModel.syncNow()

        try await waitForParkedFetch(health)
        try await Task.sleep(nanoseconds: 100_000_000)
        health.releaseParkedFetch()

        _ = await observerOutcome
        await foregroundOutcome

        // No second loop was spawned by the overlapping pair.
        XCTAssertEqual(health.fetchCalls.count, 3)
        XCTAssertEqual(health.fetchCalls[0], nil)
        XCTAssertEqual(backend.batchCount, 2)
        XCTAssertEqual(store.state?.durableAnchorData, Data("obs-3".utf8))
        XCTAssertFalse(viewModel.isSyncingNow)
    }

    // MARK: 4. Cancellation between commit and durable-anchor progress

    func test_cancellationAfterBackendCommit_leavesDurableAnchorAtLastCommittedPage_andReplaysSafely() async throws {
        let health = FakeHealthKitPort(pages: [
            page([21], anchor: "cancel-A"),
            page([22], anchor: "cancel-B"),
            AnchoredChanges(added: [], deletions: [], transientAnchorData: nil),
        ])
        // Fetch #1 returns page A normally (commit + durable persist of A).
        // Fetch #2 surfaces a cancellation at the suspension boundary — the
        // attempt must fail WITHOUT advancing past the last committed page.
        health.failNthFetchWith = (callNumber: 2, error: CancellationError())

        let store = InMemoryStateStore()
        try store.persist(DurableSyncState(
            durableAnchorData: Data("cancel-0".utf8),
            lastSuccessfulSync: nil,
            lastAttempt: nil,
            lastErrorDescription: nil
        ))
        let backend = FakeBackend()
        let engine = HealthSyncEngine(
            reading: health, backend: backend, store: store,
            auth: FakeAccessTokenProvider()
        )

        let cancelledOutcome = await engine.runIncrementalSync()
        guard case .failed(.networkUnreachable) = cancelledOutcome else {
            return XCTFail("expected networkUnreachable after cancellation, got \(cancelledOutcome)")
        }
        // Backend durably holds ONLY page A; the anchor matches exactly that.
        XCTAssertEqual(backend.batchCount, 1)
        XCTAssertEqual(backend.batches[0].addedUUIDs, [makeSample(21).sampleUUID.uuidString])
        XCTAssertEqual(store.state?.durableAnchorData, Data("cancel-A".utf8))

        // Next attempt resumes FROM cancel-A: page B uploads, nothing already
        // committed is re-uploaded or lost, and the anchor advances cleanly.
        health.resetFetchInstrumentation()
        health.failNthFetchWith = nil
        health.setPages([
            page([22], anchor: "cancel-B"),
            AnchoredChanges(added: [], deletions: [], transientAnchorData: nil),
        ])
        let replayOutcome = await engine.runIncrementalSync()
        XCTAssertEqual(replayOutcome, .synced)
        XCTAssertEqual(backend.batchCount, 2)
        XCTAssertEqual(backend.batches[1].addedUUIDs, [makeSample(22).sampleUUID.uuidString])
        XCTAssertEqual(backend.logicalRecordCount, 2)
        XCTAssertEqual(store.state?.durableAnchorData, Data("cancel-B".utf8))
    }

    // MARK: Completed runs do not suppress later triggers

    func test_completedRun_doesNotSuppressSubsequentTrigger() async throws {
        let health = FakeHealthKitPort(pages: [
            page([31], anchor: "done-2"),
            AnchoredChanges(added: [], deletions: [], transientAnchorData: nil),
        ])
        let store = InMemoryStateStore()
        let backend = FakeBackend()
        let engine = HealthSyncEngine(
            reading: health, backend: backend, store: store,
            auth: FakeAccessTokenProvider()
        )

        let first = await engine.runFullSync()
        XCTAssertEqual(first, .synced)

        health.resetFetchInstrumentation()
        health.setPages([page([32], anchor: "done-3")])
        let second = await engine.runFullSync()
        XCTAssertEqual(second, .synced)
        XCTAssertEqual(backend.batchCount, 2)
        XCTAssertEqual(store.state?.durableAnchorData, Data("done-3".utf8))
    }
}
