import Foundation
@testable import NutritionHealthCompanion

// Test fakes — no real health data, deterministic behavior.

/// Reference-semantics scripted HealthKit port. Pages are consumed in order;
/// `setPages` replaces the script between attempts (e.g., replaying the same
/// page after a simulated crash).
///
/// Deterministic instrumentation for the single-flight regression tests:
///   - `gateOnFetchNumber` parks the Nth fetch callback so tests can stage
///     overlapping triggers while a loop is provably active.
///   - `failNthFetchWith` returns a scripted error on the Nth fetch, e.g.
///     simulating a cancellation surfacing at a suspension boundary.
final class FakeHealthKitPort: HealthKitAuthorizing, HealthKitBodyMassReading,
    HealthKitWorkoutReading, @unchecked Sendable {
    private let lock = NSLock()
    private var pages: [AnchoredChanges]
    private var workoutPages: [WorkoutAnchoredChanges]
    private(set) var fetchCalls: [Data?] = []
    private(set) var workoutFetchCalls: [Data?] = []

    var available = true
    var authorizationError: Error?
    var earliestAuthorized: Date = .distantPast

    /// Park the Nth (1-based) startAnchoredChangesFetch call until
    /// `releaseParkedFetch()` is invoked; nil disables the gate.
    var gateOnFetchNumber: Int?
    /// Return this error on the Nth (1-based) startAnchoredChangesFetch call.
    var failNthFetchWith: (callNumber: Int, error: Error)?

    private var fetchCounter = 0
    private var parkedCompletion: (@Sendable (Result<AnchoredChanges, Error>) -> Void)?
    private var gateAlreadyUsed = false

    init(pages: [AnchoredChanges], workoutPages: [WorkoutAnchoredChanges] = []) {
        self.pages = pages
        self.workoutPages = workoutPages
    }

    /// Release control between scenarios without touching scripted pages.
    func resetFetchInstrumentation() {
        lock.withLock {
            fetchCounter = 0
            gateAlreadyUsed = false
        }
    }

    func releaseParkedFetch() {
        let completion: (@Sendable (Result<AnchoredChanges, Error>) -> Void)? = lock.withLock {
            gateReleaseRequested = true
            let c = parkedCompletion
            parkedCompletion = nil
            return c
        }
        if let completion {
            completeFetch(completion)
        }
    }

    private var gateReleaseRequested = false

    func setPages(_ pages: [AnchoredChanges]) {
        lock.withLock { self.pages = pages }
    }

    func setWorkoutPages(_ pages: [WorkoutAnchoredChanges]) {
        lock.withLock { workoutPages = pages }
    }

    func isAvailable() -> Bool { available }

    func requestAuthorization() async throws -> AuthorizationOutcome {
        if let authorizationError { throw authorizationError }
        return .completed
    }

    func earliestAuthorizedStartDate() async -> Date { earliestAuthorized }

    func startAnchoredChangesFetch(
        anchorData: Data?, after: Date?, limit: Int,
        completion: @escaping @Sendable (Result<AnchoredChanges, Error>) -> Void
    ) {
        lock.lock()
        fetchCalls.append(anchorData)
        fetchCounter += 1
        let callNumber = fetchCounter
        let scriptedFailure = failNthFetchWith.flatMap {
            $0.callNumber == callNumber ? $0.error : nil
        }
        let shouldPark =
            !gateAlreadyUsed &&
            gateOnFetchNumber == callNumber &&
            scriptedFailure == nil
        if shouldPark { gateAlreadyUsed = true }

        if let scriptedFailure {
            lock.unlock()
            completion(.failure(scriptedFailure))
            return
        }

        if shouldPark {
            if gateReleaseRequested {
                lock.unlock()
                completeFetch(completion)
            } else {
                parkedCompletion = completion
                lock.unlock()
            }
            return
        }
        lock.unlock()
        completeFetch(completion)
    }

    private func completeFetch(
        _ completion: @escaping @Sendable (Result<AnchoredChanges, Error>) -> Void
    ) {
        lock.lock()
        let page = pages.isEmpty
            ? AnchoredChanges(added: [], deletions: [], transientAnchorData: nil)
            : pages.removeFirst()
        lock.unlock()
        completion(.success(page))
    }

    var changesInBackground: (@Sendable () async -> Void)? {
        get { nil }
        set {}
    }

    func earliestAuthorizedWorkoutStartDate() async -> Date { earliestAuthorized }

    func startWorkoutAnchoredChangesFetch(
        anchorData: Data?, after: Date?, limit: Int,
        completion: @escaping @Sendable (Result<WorkoutAnchoredChanges, Error>) -> Void
    ) {
        lock.lock()
        workoutFetchCalls.append(anchorData)
        let page = workoutPages.isEmpty
            ? WorkoutAnchoredChanges(added: [], deletions: [], transientAnchorData: nil)
            : workoutPages.removeFirst()
        lock.unlock()
        completion(.success(page))
    }
}

final class InMemoryStateStore: SyncStateStore, @unchecked Sendable {
    private let lock = NSLock()
    private(set) var state: DurableSyncState?
    private(set) var persistCallTimestamps: [Int] = []
    private var callCounter = 0
    /// Injected failure point for crash simulation.
    var failPersistBeforeWrite = false
    /// Fails exactly the next N persist attempts, then succeeds — lets tests
    /// distinguish the page-commit persist from the error-path bookkeeping
    /// persist that follows it.
    var remainingPersistFailures = 0

    func load() throws -> DurableSyncState? {
        lock.lock()
        defer { lock.unlock() }
        return state
    }

    func persist(_ newState: DurableSyncState) throws {
        lock.lock()
        callCounter += 1
        if failPersistBeforeWrite || remainingPersistFailures > 0 {
            if !failPersistBeforeWrite { remainingPersistFailures -= 1 }
            // Simulate failure BEFORE durable write: state unchanged on disk.
            lock.unlock()
            throw SyncStoreError.corruptState
        }
        state = newState
        persistCallTimestamps.append(callCounter)
        lock.unlock()
    }
}

/// Records every batch and simulates backend dedup semantics on
/// (user, sample_uuid) so tests can assert duplicate counts.
final class FakeBackend: BackendClient, @unchecked Sendable {
    struct RecordedBatch: Equatable {
        let addedUUIDs: [String]
        let deletedUUIDs: [String]
    }

    private let lock = NSLock()
    private(set) var batches: [RecordedBatch] = []
    private var knownSampleIDs = Set<String>()
    private var tombstonedIDs = Set<String>()
    private(set) var workoutBatches: [RecordedBatch] = []
    private var knownWorkoutIDs = Set<String>()
    private var tombstonedWorkoutIDs = Set<String>()
    /// Scripted failures consumed in order before success.
    private var scriptedFailures: [Error]

    init(scriptedFailures: [Error] = []) {
        self.scriptedFailures = scriptedFailures
    }

    func submitBatch(
        added: [BodyMassSampleDTO], deletions: [DeletedSampleDTO]
    ) async throws -> SyncResponse {
        lock.lock()
        defer { lock.unlock() }
        if !scriptedFailures.isEmpty {
            throw scriptedFailures.removeFirst()
        }
        batches.append(RecordedBatch(
            addedUUIDs: added.map(\.sampleUUID.uuidString),
            deletedUUIDs: deletions.map(\.sampleUUID.uuidString)
        ))
        var accepted = 0
        var duplicates = 0
        for sample in added {
            let id = sample.sampleUUID.uuidString
            if tombstonedIDs.contains(id) || knownSampleIDs.contains(id) {
                duplicates += 1
            } else {
                knownSampleIDs.insert(id)
                accepted += 1
            }
        }
        var applied = 0
        var dupDeletions = 0
        for deletion in deletions {
            let id = deletion.sampleUUID.uuidString
            if tombstonedIDs.contains(id) {
                dupDeletions += 1
            } else {
                tombstonedIDs.insert(id)
                applied += 1
            }
        }
        return SyncResponse(
            accepted_added: accepted, duplicate_added: duplicates,
            applied_deletions: applied, duplicate_deletions: dupDeletions,
            ingested_at: WireDate.string(from: Date()), latest_sample: nil
        )
    }

    func fetchSyncStatus() async throws -> StatusResponse {
        StatusResponse(
            record_count: knownSampleIDs.count,
            tombstone_count: tombstonedIDs.count,
            latest_sample: nil, last_ingested_at: nil
        )
    }

    func submitWorkoutBatch(
        added: [WorkoutSampleDTO], deletions: [DeletedWorkoutDTO]
    ) async throws -> WorkoutSyncResponse {
        lock.lock()
        defer { lock.unlock() }
        if !scriptedFailures.isEmpty {
            throw scriptedFailures.removeFirst()
        }
        workoutBatches.append(RecordedBatch(
            addedUUIDs: added.map(\.sourceRecordID.uuidString),
            deletedUUIDs: deletions.map(\.sourceRecordID.uuidString)
        ))
        var accepted = 0
        var duplicates = 0
        for sample in added {
            let id = sample.sourceRecordID.uuidString
            if knownWorkoutIDs.contains(id) || tombstonedWorkoutIDs.contains(id) {
                duplicates += 1
            } else {
                knownWorkoutIDs.insert(id)
                accepted += 1
            }
        }
        var applied = 0
        var duplicateDeletions = 0
        for deletion in deletions {
            let id = deletion.sourceRecordID.uuidString
            if tombstonedWorkoutIDs.contains(id) {
                duplicateDeletions += 1
            } else {
                tombstonedWorkoutIDs.insert(id)
                applied += 1
            }
        }
        return WorkoutSyncResponse(
            accepted_added: accepted,
            duplicate_added: duplicates,
            applied_deletions: applied,
            duplicate_deletions: duplicateDeletions
        )
    }

    var logicalRecordCount: Int { lock.withLock { knownSampleIDs.count } }
    var batchCount: Int { lock.withLock { batches.count } }
    var workoutBatchCount: Int { lock.withLock { workoutBatches.count } }
    var logicalWorkoutCount: Int { lock.withLock { knownWorkoutIDs.count } }
}

struct FakeAccessTokenProvider: AccessTokenProvider {
    var token = "fake-access-token"
    var failWithNeedsSignIn = false

    func validAccessToken() async throws -> String {
        if failWithNeedsSignIn { throw AuthProviderError.needsSignIn }
        return token
    }
}
