import XCTest
@testable import NutritionHealthCompanion

/// THE mandatory M5 correctness test (plan §11 / spec §24):
/// backend commit succeeds -> crash BEFORE durable anchor write -> next sync
/// re-uploads the same page -> backend deduplicates -> final state exact.
final class CoordinatorCrashReplayTests: XCTestCase {
    private func makeSample(_ n: Int) -> BodyMassSampleDTO {
        BodyMassSampleDTO(
            sampleUUID: UUID(uuidString: "00000000-0000-0000-0000-\(String(format: "%012x", n))")!,
            valueKgDecimalString: "72.000",
            sampleStart: Date(timeIntervalSince1970: 1_760_000_000),
            sampleEnd: Date(timeIntervalSince1970: 1_760_000_000)
        )
    }

    func test_crashBetweenCommitAndDurableAnchorWrite_replaysAndDedupes() async {
        // Page 1 = {A,B,C} + transientAnchor2 (anchor1 = nil, first sync).
        let page1 = AnchoredChanges(
            added: [makeSample(1), makeSample(2), makeSample(3)],
            deletions: [],
            transientAnchorData: Data("anchor-2".utf8)
        )
        let fakeHealth = FakeHealthKitPort(pages: [page1])
        let store = InMemoryStateStore()
        // Simulated crash: persist throws BEFORE any durable write lands.
        store.failPersistBeforeWrite = true
        let backend = FakeBackend()
        let auth = FakeAccessTokenProvider()

        let coordinator = HealthSyncCoordinator(
            reading: fakeHealth, backend: backend, store: store, auth: auth
        )

        // Attempt 1: upload commits on the backend, then the "crash" prevents
        // the durable anchor from being written.
        let outcome1 = await coordinator.runFullSync()
        guard case .failed(.storageFailure) = outcome1 else {
            return XCTFail("expected storageFailure, got \(outcome1)")
        }
        XCTAssertEqual(store.state?.durableAnchorData, nil,
                       "durable anchor must NOT have advanced after simulated crash")
        XCTAssertEqual(store.state?.lastSuccessfulSync, nil)
        XCTAssertEqual(backend.batchCount, 1, "backend committed page once")

        // Attempt 2: same page replays from HealthKit (same UUIDs), because
        // the durable anchor never advanced.
        store.failPersistBeforeWrite = false
        fakeHealth.setPages([page1])
        let outcome2 = await coordinator.runFullSync()

        XCTAssertEqual(outcome2, .synced)
        XCTAssertEqual(backend.batchCount, 2, "page was re-uploaded")
        XCTAssertEqual(backend.batches[0], backend.batches[1],
                       "identical batch uploaded twice")
        // Backend dedup: exactly one logical record per source sample.
        XCTAssertEqual(backend.logicalRecordCount, 3)
        // Durable anchor advances only after the second successful commit.
        XCTAssertEqual(store.state?.durableAnchorData, Data("anchor-2".utf8))
        XCTAssertNotNil(store.state?.lastSuccessfulSync)
    }

    /// Persistence-failure asymmetry regression (M5 micro-fix):
    ///
    ///   backend accepts page N -> durable persist of N FAILS
    ///   -> failure bookkeeping persist SUCCEEDS.
    ///
    /// The reported failure and the durable record must agree: the error-path
    /// persist carries only the last durably committed anchor, never the
    /// locally advanced candidate. The next sync then replays page N and
    /// backend deduplication collapses it back to one logical record.
    func test_persistFailureAfterBackendCommit_errorPathCannotAdvanceAnchor_replaysWithDedup() async {
        // Seed an incremental resync from a previously committed anchor.
        let seedSyncedAt = Date(timeIntervalSince1970: 1_000_000)
        let store = InMemoryStateStore()
        try? store.persist(DurableSyncState(
            durableAnchorData: Data("prev-commit".utf8),
            lastSuccessfulSync: seedSyncedAt,
            lastAttempt: nil, lastErrorDescription: nil))
        // Fail ONLY the page-commit persist; the subsequent error-path
        // bookkeeping persist succeeds, making the asymmetry observable.
        store.remainingPersistFailures = 1

        let pageA = AnchoredChanges(
            added: [makeSample(41)], deletions: [],
            transientAnchorData: Data("post-A".utf8))
        let fakeHealth = FakeHealthKitPort(pages: [pageA])
        let backend = FakeBackend()

        let coordinator = HealthSyncCoordinator(
            reading: fakeHealth, backend: backend, store: store,
            auth: FakeAccessTokenProvider()
        )

        // Attempt 1: upload commits, candidate-anchor persist fails.
        let outcome1 = await coordinator.runIncrementalSync()
        guard case .failed(.storageFailure) = outcome1 else {
            return XCTFail("expected storageFailure, got \(outcome1)")
        }
        XCTAssertEqual(backend.batchCount, 1, "backend durably accepted page A")
        XCTAssertEqual(backend.batches[0].addedUUIDs, [makeSample(41).sampleUUID.uuidString])

        // THE asymmetry proof: failure reported AND durable state stayed at
        // the previous committed anchor, even though the error-path persist
        // itself succeeded (lastErrorDescription below proves it ran).
        XCTAssertEqual(
            store.state?.durableAnchorData, Data("prev-commit".utf8),
            "error-path bookkeeping must not write the locally advanced candidate anchor")
        XCTAssertEqual(
            store.state?.lastSuccessfulSync, seedSyncedAt,
            "lastSuccessfulSync must not reflect the un-persisted page")
        XCTAssertEqual(
            store.state?.lastErrorDescription,
            SyncErrorClassification.storageFailure.rawValue,
            "failure bookkeeping was durably recorded without advancing the anchor")

        // Attempt 2 replays page A from the unadvanced anchor...
        fakeHealth.setPages([
            AnchoredChanges(
                added: [makeSample(41)], deletions: [],
                transientAnchorData: Data("post-A".utf8)),
            AnchoredChanges(added: [], deletions: [], transientAnchorData: nil),
        ])
        let outcome2 = await coordinator.runIncrementalSync()

        XCTAssertEqual(outcome2, .synced)
        XCTAssertEqual(backend.batchCount, 2, "accepted page was safely replayed")
        XCTAssertEqual(backend.batches[0], backend.batches[1], "identical page replayed")
        // ...and deduplication prevents duplicate logical records.
        XCTAssertEqual(backend.logicalRecordCount, 1)
        XCTAssertEqual(store.state?.durableAnchorData, Data("post-A".utf8))
    }
}
