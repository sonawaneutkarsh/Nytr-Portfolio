import XCTest
@testable import NutritionHealthCompanion

/// Per-page anchor progression + incremental/deletion behavior (plan §11).
final class CoordinatorIncrementalTests: XCTestCase {
    private func makeSample(_ n: Int) -> BodyMassSampleDTO {
        BodyMassSampleDTO(
            sampleUUID: UUID(uuidString: "00000000-0000-0000-0000-\(String(format: "%012x", n))")!,
            valueKgDecimalString: "70.5",
            sampleStart: Date(timeIntervalSince1970: 1_760_000_000),
            sampleEnd: Date(timeIntervalSince1970: 1_760_000_000)
        )
    }

    func test_historicalImport_persistsAnchorPerPage_noFinalAnchorShortcut() async {
        let page1 = AnchoredChanges(
            added: [makeSample(1)], deletions: [],
            transientAnchorData: Data("anchor-2".utf8))
        let page2 = AnchoredChanges(
            added: [makeSample(2)], deletions: [],
            transientAnchorData: Data("anchor-3".utf8))
        let page3 = AnchoredChanges(added: [], deletions: [], transientAnchorData: Data("anchor-4".utf8))

        let fakeHealth = FakeHealthKitPort(pages: [page1, page2, page3])
        let store = InMemoryStateStore()
        let backend = FakeBackend()
        let auth = FakeAccessTokenProvider()

        let coordinator = HealthSyncCoordinator(
            reading: fakeHealth, backend: backend, store: store, auth: auth
        )
        let outcome = await coordinator.runFullSync()

        XCTAssertEqual(outcome, .synced)
        // Three pages uploaded (page3 empty terminates the loop without upload).
        XCTAssertEqual(backend.batchCount, 2)
        // Per-page progression invariant: after each upload the store holds
        // exactly that page's anchor. After page1 commit the durable anchor
        // must be anchor-2 BEFORE page2 was fetched; after page2, anchor-3.
        XCTAssertEqual(store.state?.durableAnchorData, Data("anchor-3".utf8))
        // The fetch chain used transient anchors in order.
        XCTAssertEqual(fakeHealth.fetchCalls.count, 3)
        XCTAssertEqual(fakeHealth.fetchCalls[0], nil)
        XCTAssertEqual(fakeHealth.fetchCalls[1], Data("anchor-2".utf8))
        XCTAssertEqual(fakeHealth.fetchCalls[2], Data("anchor-3".utf8))
    }

    func test_incrementalSync_emptyPageTerminates_andDeletionPageIsUploaded() async {
        let deletionPage = AnchoredChanges(
            added: [],
            deletions: [DeletedSampleDTO(sampleUUID: UUID(uuidString: "00000000-0000-0000-0000-000000000009")!)],
            transientAnchorData: Data("inc-anchor".utf8))
        let emptyPage = AnchoredChanges(added: [], deletions: [], transientAnchorData: Data("inc-anchor".utf8))

        var state = DurableSyncState(
            durableAnchorData: Data("prior-anchor".utf8),
            lastSuccessfulSync: nil, lastAttempt: nil, lastErrorDescription: nil)
        let store = InMemoryStateStore()
        try? store.persist(state)

        let fakeHealth = FakeHealthKitPort(pages: [deletionPage, emptyPage])
        let backend = FakeBackend()
        let coordinator = HealthSyncCoordinator(
            reading: fakeHealth, backend: backend, store: store, auth: FakeAccessTokenProvider()
        )

        let outcome = await coordinator.runIncrementalSync()

        XCTAssertEqual(outcome, .synced)
        XCTAssertEqual(backend.batchCount, 1)
        XCTAssertEqual(backend.batches[0].deletedUUIDs.count, 1)
        XCTAssertEqual(store.state?.durableAnchorData, Data("inc-anchor".utf8))
    }

    func test_noChangeIncrementalSyncRefreshesSuccessWithoutAdvancingAnchor() async throws {
        let priorAnchor = Data("prior-anchor".utf8)
        let priorSuccess = Date(timeIntervalSince1970: 1_760_000_000)
        let refreshedAt = Date(timeIntervalSince1970: 1_760_086_400)
        let store = InMemoryStateStore()
        try store.persist(DurableSyncState(
            durableAnchorData: priorAnchor,
            lastSuccessfulSync: priorSuccess,
            lastAttempt: priorSuccess,
            lastErrorDescription: SyncErrorClassification.networkUnreachable.rawValue
        ))
        let health = FakeHealthKitPort(pages: [
            AnchoredChanges(
                added: [], deletions: [], transientAnchorData: Data("empty-anchor".utf8)
            )
        ])
        let backend = FakeBackend()

        let outcome = await HealthSyncCoordinator(
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
        XCTAssertEqual(backend.batchCount, 0)
    }

    func test_backendUnauthorizedMapsToNeedsSignInWithoutAdvancingAnchor() async throws {
        let priorAnchor = Data("prior-anchor".utf8)
        let store = InMemoryStateStore()
        try store.persist(DurableSyncState(
            durableAnchorData: priorAnchor,
            lastSuccessfulSync: nil,
            lastAttempt: nil,
            lastErrorDescription: nil
        ))
        let health = FakeHealthKitPort(pages: [AnchoredChanges(
            added: [makeSample(8)],
            deletions: [],
            transientAnchorData: Data("must-not-advance".utf8)
        )])

        let outcome = await HealthSyncCoordinator(
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

    func test_networkFailure_leavesAnchorUnchanged() async {
        var state = DurableSyncState(
            durableAnchorData: Data("keep-me".utf8),
            lastSuccessfulSync: nil, lastAttempt: nil, lastErrorDescription: nil)
        let store = InMemoryStateStore()
        try? store.persist(state)

        let changePage = AnchoredChanges(
            added: [makeSample(7)], deletions: [], transientAnchorData: Data("never".utf8))
        let fakeHealth = FakeHealthKitPort(pages: [changePage])
        let failingBackend = FakeBackend(scriptedFailures: [BackendError.retryable("offline")])
        let coordinator = HealthSyncCoordinator(
            reading: fakeHealth, backend: failingBackend,
            store: store, auth: FakeAccessTokenProvider()
        )

        let outcome = await coordinator.runIncrementalSync()

        guard case .failed(.networkUnreachable) = outcome else {
            return XCTFail("expected networkUnreachable, got \(outcome)")
        }
        // Anchor unchanged; nothing lost; retry repairs later.
        XCTAssertEqual(store.state?.durableAnchorData, Data("keep-me".utf8))
    }

    func test_rejectedPermanent_dropsBatchWithoutAdvancingAnchor() async {
        let badPage = AnchoredChanges(
            added: [makeSample(9)], deletions: [], transientAnchorData: Data("bad".utf8))
        let fakeHealth = FakeHealthKitPort(pages: [badPage])
        let store = InMemoryStateStore()
        let backend = FakeBackend(scriptedFailures: [
            BackendError.rejectedPermanent("value outside bounds")
        ])
        let coordinator = HealthSyncCoordinator(
            reading: fakeHealth, backend: backend, store: store, auth: FakeAccessTokenProvider()
        )

        let outcome = await coordinator.runFullSync()

        guard case .failed(.rejectedPermanent) = outcome else {
            return XCTFail("expected rejectedPermanent, got \(outcome)")
        }
        XCTAssertEqual(store.state?.durableAnchorData, nil,
                       "fail-closed: anchor must not advance on permanent rejection")
    }

    func test_needsSignIn_blocksHealthWork() async {
        let fakeHealth = FakeHealthKitPort(pages: [])
        let store = InMemoryStateStore()
        let coordinator = HealthSyncCoordinator(
            reading: fakeHealth, backend: FakeBackend(), store: store,
            auth: FakeAccessTokenProvider(failWithNeedsSignIn: true)
        )
        let outcome = await coordinator.runFullSync()
        XCTAssertEqual(outcome, .failed(.needsSignIn))
        XCTAssertEqual(fakeHealth.fetchCalls.count, 0,
                       "no HealthKit queries may occur without a valid session")
    }
}
