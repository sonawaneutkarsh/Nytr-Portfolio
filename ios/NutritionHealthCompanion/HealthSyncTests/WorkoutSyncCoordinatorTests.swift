import XCTest
@testable import NutritionHealthCompanion

final class WorkoutSyncCoordinatorTests: XCTestCase {
    private func workout(_ n: Int) -> WorkoutSampleDTO {
        WorkoutSampleDTO(
            sourceRecordID: UUID(
                uuidString: "00000000-0000-0000-0000-\(String(format: "%012x", n))"
            )!,
            activityType: "37",
            startedAt: Date(timeIntervalSince1970: 1_760_000_000),
            endedAt: Date(timeIntervalSince1970: 1_760_003_600),
            activeDurationSecondsDecimalString: "3600.000",
            activeEnergyKcalDecimalString: "412.750",
            timezoneIdentifier: "America/New_York",
            sourceName: "Apple Watch",
            sourceBundleID: "com.apple.health",
            sourceRevision: "26.0"
        )
    }

    func test_historicalPagesUploadBeforeEachAnchorAndUseTransientPagingAnchor() async {
        let page1 = WorkoutAnchoredChanges(
            added: [workout(1)], deletions: [], transientAnchorData: Data("w2".utf8)
        )
        let page2 = WorkoutAnchoredChanges(
            added: [workout(2)], deletions: [], transientAnchorData: Data("w3".utf8)
        )
        let empty = WorkoutAnchoredChanges(
            added: [], deletions: [], transientAnchorData: Data("w4".utf8)
        )
        let health = FakeHealthKitPort(
            pages: [], workoutPages: [page1, page2, empty]
        )
        let backend = FakeBackend()
        let store = InMemoryStateStore()
        let outcome = await WorkoutSyncCoordinator(
            reading: health, backend: backend, store: store,
            auth: FakeAccessTokenProvider()
        ).runFullSync()

        XCTAssertEqual(outcome, .synced)
        XCTAssertEqual(backend.workoutBatchCount, 2)
        XCTAssertEqual(health.workoutFetchCalls, [nil, Data("w2".utf8), Data("w3".utf8)])
        XCTAssertEqual(store.state?.durableAnchorData, Data("w3".utf8))
    }

    func test_failedUploadDoesNotAdvanceWorkoutAnchor() async {
        let health = FakeHealthKitPort(
            pages: [],
            workoutPages: [WorkoutAnchoredChanges(
                added: [workout(1)], deletions: [], transientAnchorData: Data("never".utf8)
            )]
        )
        let store = InMemoryStateStore()
        try? store.persist(DurableSyncState(
            durableAnchorData: Data("previous".utf8),
            lastSuccessfulSync: nil,
            lastAttempt: nil,
            lastErrorDescription: nil
        ))
        let outcome = await WorkoutSyncCoordinator(
            reading: health,
            backend: FakeBackend(scriptedFailures: [BackendError.retryable("offline")]),
            store: store,
            auth: FakeAccessTokenProvider()
        ).runIncrementalSync()
        XCTAssertEqual(outcome, .failed(.networkUnreachable))
        XCTAssertEqual(store.state?.durableAnchorData, Data("previous".utf8))
    }

    func test_anchorPersistFailureReplaysWorkoutAndBackendDeduplicates() async {
        let page = WorkoutAnchoredChanges(
            added: [workout(1)], deletions: [], transientAnchorData: Data("accepted".utf8)
        )
        let health = FakeHealthKitPort(pages: [], workoutPages: [page])
        let backend = FakeBackend()
        let store = InMemoryStateStore()
        store.failPersistBeforeWrite = true
        let coordinator = WorkoutSyncCoordinator(
            reading: health, backend: backend, store: store,
            auth: FakeAccessTokenProvider()
        )
        let first = await coordinator.runFullSync()
        XCTAssertEqual(first, .failed(.storageFailure))
        XCTAssertNil(store.state?.durableAnchorData)

        store.failPersistBeforeWrite = false
        health.setWorkoutPages([
            page,
            WorkoutAnchoredChanges(added: [], deletions: [], transientAnchorData: nil),
        ])
        let second = await coordinator.runFullSync()
        XCTAssertEqual(second, .synced)
        XCTAssertEqual(backend.workoutBatchCount, 2)
        XCTAssertEqual(backend.logicalWorkoutCount, 1)
        XCTAssertEqual(store.state?.durableAnchorData, Data("accepted".utf8))
    }

    func test_workoutDeletionPageIsUploadedAndAdvancesOnlyAfterAcceptance() async {
        let deletedID = workout(9).sourceRecordID
        let health = FakeHealthKitPort(
            pages: [],
            workoutPages: [
                WorkoutAnchoredChanges(
                    added: [], deletions: [DeletedWorkoutDTO(sourceRecordID: deletedID)],
                    transientAnchorData: Data("deleted".utf8)
                ),
                WorkoutAnchoredChanges(added: [], deletions: [], transientAnchorData: nil),
            ]
        )
        let backend = FakeBackend()
        let store = InMemoryStateStore()
        let outcome = await WorkoutSyncCoordinator(
            reading: health, backend: backend, store: store,
            auth: FakeAccessTokenProvider()
        ).runFullSync()
        XCTAssertEqual(outcome, .synced)
        XCTAssertEqual(backend.workoutBatches[0].deletedUUIDs, [deletedID.uuidString])
        XCTAssertEqual(store.state?.durableAnchorData, Data("deleted".utf8))
    }

    func test_noChangeIncrementalWorkoutSyncRefreshesSuccessWithoutAdvancingAnchor() async throws {
        let priorAnchor = Data("workout-prior".utf8)
        let priorSuccess = Date(timeIntervalSince1970: 1_760_000_000)
        let refreshedAt = Date(timeIntervalSince1970: 1_760_086_400)
        let store = InMemoryStateStore()
        try store.persist(DurableSyncState(
            durableAnchorData: priorAnchor,
            lastSuccessfulSync: priorSuccess,
            lastAttempt: priorSuccess,
            lastErrorDescription: SyncErrorClassification.networkUnreachable.rawValue
        ))
        let health = FakeHealthKitPort(
            pages: [],
            workoutPages: [WorkoutAnchoredChanges(
                added: [], deletions: [], transientAnchorData: Data("empty-anchor".utf8)
            )]
        )
        let backend = FakeBackend()

        let outcome = await WorkoutSyncCoordinator(
            reading: health,
            backend: backend,
            store: store,
            auth: FakeAccessTokenProvider(),
            now: { refreshedAt }
        ).runIncrementalSync()

        XCTAssertEqual(outcome, .synced)
        XCTAssertEqual(store.state?.durableAnchorData, priorAnchor)
        XCTAssertEqual(store.state?.lastAttempt, refreshedAt)
        XCTAssertEqual(store.state?.lastSuccessfulSync, refreshedAt)
        XCTAssertNil(store.state?.lastErrorDescription)
        XCTAssertEqual(backend.workoutBatchCount, 0)
    }

    func test_workoutBackendUnauthorizedMapsToNeedsSignInWithoutAdvancingAnchor() async throws {
        let priorAnchor = Data("workout-prior".utf8)
        let store = InMemoryStateStore()
        try store.persist(DurableSyncState(
            durableAnchorData: priorAnchor,
            lastSuccessfulSync: nil,
            lastAttempt: nil,
            lastErrorDescription: nil
        ))
        let health = FakeHealthKitPort(
            pages: [],
            workoutPages: [WorkoutAnchoredChanges(
                added: [workout(5)],
                deletions: [],
                transientAnchorData: Data("must-not-advance".utf8)
            )]
        )

        let outcome = await WorkoutSyncCoordinator(
            reading: health,
            backend: FakeBackend(scriptedFailures: [BackendError.unauthorized]),
            store: store,
            auth: FakeAccessTokenProvider()
        ).runIncrementalSync()

        XCTAssertEqual(outcome, .failed(.needsSignIn))
        XCTAssertEqual(store.state?.durableAnchorData, priorAnchor)
        XCTAssertEqual(
            store.state?.lastErrorDescription,
            SyncErrorClassification.needsSignIn.rawValue
        )
    }
}
