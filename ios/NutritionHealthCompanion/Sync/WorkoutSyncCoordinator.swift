import Foundation

/// Separate HealthKit workout anchored loop. Its state store uses the workout
/// namespace; body-mass anchors never enter this coordinator.
struct WorkoutSyncCoordinator {
    private let reading: any HealthKitAuthorizing & HealthKitWorkoutReading
    private let backend: BackendClient
    private let store: SyncStateStore
    private let auth: AccessTokenProvider
    private let now: @Sendable () -> Date

    init(
        reading: any HealthKitAuthorizing & HealthKitWorkoutReading,
        backend: BackendClient,
        store: SyncStateStore,
        auth: AccessTokenProvider,
        now: @escaping @Sendable () -> Date = { Date() }
    ) {
        self.reading = reading
        self.backend = backend
        self.store = store
        self.auth = auth
        self.now = now
    }

    func runFullSync() async -> SyncOutcome {
        guard reading.isAvailable() else { return .unavailable }
        do {
            _ = try await reading.requestAuthorization()
        } catch {
            return .failed(.needsAuthorization)
        }
        return await performAnchoredLoop()
    }

    func runIncrementalSync() async -> SyncOutcome {
        guard reading.isAvailable() else { return .unavailable }
        return await performAnchoredLoop()
    }

    private func performAnchoredLoop() async -> SyncOutcome {
        do {
            _ = try await auth.validAccessToken()
        } catch {
            return .failed(.needsSignIn)
        }
        let durable = (try? store.load()) ?? nil
        return durable?.durableAnchorData == nil
            ? await historicalImport()
            : await incrementalSync(from: durable)
    }

    private func historicalImport() async -> SyncOutcome {
        var state = (try? store.load()) ?? DurableSyncState(
            durableAnchorData: nil,
            lastSuccessfulSync: nil,
            lastAttempt: nil,
            lastErrorDescription: nil
        )
        state.lastAttempt = now()
        let earliest = await reading.earliestAuthorizedWorkoutStartDate()
        let start = HealthSyncPolicy.effectiveHistoricalStart(
            now: now(), earliestAuthorized: earliest
        )
        var transientAnchorData: Data?
        var firstPage = true
        var sawAnySession = false

        while true {
            let changes: WorkoutAnchoredChanges
            do {
                changes = try await fetch(
                    anchorData: transientAnchorData,
                    after: firstPage ? start : nil
                )
            } catch {
                return finishFailure(state, classification: .serverUnavailable)
            }
            firstPage = false
            sawAnySession = sawAnySession || !changes.added.isEmpty
            if changes.isEmptyPage { break }
            let uploadResult = await upload(changes)
            switch uploadResult {
            case .committed:
                break
            case .rejectedPermanent:
                return finishFailure(state, classification: .rejectedPermanent)
            case .failedRetryable:
                return finishFailure(state, classification: .networkUnreachable)
            case .needsSignIn:
                return finishFailure(state, classification: .needsSignIn)
            }
            var committed = state
            committed.durableAnchorData = changes.transientAnchorData
            committed.lastSuccessfulSync = now()
            do {
                try store.persist(committed)
            } catch {
                return finishFailure(state, classification: .storageFailure)
            }
            state = committed
            transientAnchorData = changes.transientAnchorData
        }

        state.lastSuccessfulSync = now()
        state.lastErrorDescription = nil
        do {
            try store.persist(state)
        } catch {
            return finishFailure(state, classification: .storageFailure)
        }
        return !sawAnySession && state.durableAnchorData == nil ? .noReadableSamples : .synced
    }

    private func incrementalSync(from durable: DurableSyncState?) async -> SyncOutcome {
        var state = durable ?? DurableSyncState(
            durableAnchorData: nil,
            lastSuccessfulSync: nil,
            lastAttempt: nil,
            lastErrorDescription: nil
        )
        state.lastAttempt = now()
        var transientAnchorData = state.durableAnchorData
        while true {
            let changes: WorkoutAnchoredChanges
            do {
                changes = try await fetch(anchorData: transientAnchorData, after: nil)
            } catch {
                return finishFailure(state, classification: .networkUnreachable)
            }
            if changes.isEmptyPage { break }
            let uploadResult = await upload(changes)
            switch uploadResult {
            case .committed:
                break
            case .rejectedPermanent:
                return finishFailure(state, classification: .rejectedPermanent)
            case .failedRetryable:
                return finishFailure(state, classification: .networkUnreachable)
            case .needsSignIn:
                return finishFailure(state, classification: .needsSignIn)
            }
            var committed = state
            committed.durableAnchorData = changes.transientAnchorData
            committed.lastSuccessfulSync = now()
            do {
                try store.persist(committed)
            } catch {
                return finishFailure(state, classification: .storageFailure)
            }
            state = committed
            transientAnchorData = changes.transientAnchorData
        }
        state.lastSuccessfulSync = now()
        state.lastErrorDescription = nil
        do {
            try store.persist(state)
        } catch {
            return finishFailure(state, classification: .storageFailure)
        }
        return .synced
    }

    private func fetch(anchorData: Data?, after: Date?) async throws -> WorkoutAnchoredChanges {
        try await withCheckedThrowingContinuation { continuation in
            reading.startWorkoutAnchoredChangesFetch(
                anchorData: anchorData,
                after: after,
                limit: HealthSyncPolicy.pageLimit
            ) { result in
                continuation.resume(with: result)
            }
        }
    }

    private enum UploadResult {
        case committed
        case rejectedPermanent
        case failedRetryable
        case needsSignIn
    }

    private func upload(_ changes: WorkoutAnchoredChanges) async -> UploadResult {
        do {
            _ = try await backend.submitWorkoutBatch(
                added: changes.added, deletions: changes.deletions
            )
            return .committed
        } catch BackendError.rejectedPermanent {
            return .rejectedPermanent
        } catch BackendError.unauthorized {
            return .needsSignIn
        } catch {
            return .failedRetryable
        }
    }

    private func finishFailure(
        _ state: DurableSyncState,
        classification: SyncErrorClassification
    ) -> SyncOutcome {
        var failed = state
        failed.lastErrorDescription = classification.rawValue
        try? store.persist(failed)
        return .failed(classification)
    }
}
