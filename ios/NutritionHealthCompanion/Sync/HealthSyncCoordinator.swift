import Foundation

/// The sync state machine (plan §4). Owns the A/B anchor discipline:
///
///   A. Transient paging anchor — in-memory only, used to fetch the next page
///      within one attempt; never persisted; never survives a crash.
///   B. Durable anchor — written to SyncStateStore strictly AFTER the backend
///      returns success for that page, and BEFORE querying the next page.
///
/// Historical-import invariant: page N is fetched with anchor N-1, uploaded,
/// and ONLY after a successful commit is anchor N persisted. The
/// "final-anchor shortcut" (persist only after all pages) is prohibited.
///
/// The reading port is held as a `Sendable`-constrained existential because
/// every trigger reaches this type through `HealthSyncEngine`, which stores
/// concrete adapters behind those same protocol existentials.
struct HealthSyncCoordinator {
    private let reading: any HealthKitAuthorizing & HealthKitBodyMassReading
    private let backend: BackendClient
    private let store: SyncStateStore
    private let auth: AccessTokenProvider
    private let now: @Sendable () -> Date

    init(
        reading: any HealthKitAuthorizing & HealthKitBodyMassReading,
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

    // MARK: - Public entry points (all reliable paths)

    /// Full catch-up: authorization check + historical import or incremental
    /// anchored sync. Returns the terminal UI-relevant state.
    func runFullSync() async -> SyncOutcome {
        guard reading.isAvailable() else { return .unavailable }
        do {
            _ = try await reading.requestAuthorization()
        } catch {
            return .failed(.needsAuthorization)
        }
        return await performAnchoredLoop()
    }

    /// Incremental-only catch-up (no authorization sheet). Used for observer
    /// wakes and foreground refresh when already authorized.
    func runIncrementalSync() async -> SyncOutcome {
        guard reading.isAvailable() else { return .unavailable }
        return await performAnchoredLoop()
    }

    // MARK: - The anchored loop

    private func performAnchoredLoop() async -> SyncOutcome {
        // THE authentication gate for every sync path: no HealthKit work
        // proceeds without a valid session. AccessTokenProvider refreshes
        // internally; BackendClient reuses the same token per request, so one
        // validity check here is sufficient and there must be no duplicate.
        do {
            _ = try await auth.validAccessToken()
        } catch {
            return .failed(.needsSignIn)
        }

        let durable = (try? store.load()) ?? nil

        if durable?.durableAnchorData == nil {
            return await historicalImport()
        } else {
            return await incrementalSync(from: durable)
        }
    }

    /// First sync: bounded by effectiveStart = max(now - 365d,
    /// earliestAuthorizedSampleDate) — the canonical policy formula.
    private func historicalImport() async -> SyncOutcome {
        var state = (try? store.load()) ?? DurableSyncState(
            durableAnchorData: nil, lastSuccessfulSync: nil,
            lastAttempt: nil, lastErrorDescription: nil
        )
        state.lastAttempt = now()

        let earliest = await reading.earliestAuthorizedStartDate()
        let start = HealthSyncPolicy.effectiveHistoricalStart(now: now(), earliestAuthorized: earliest)

        var sawAnySample = false
        var transientAnchorData: Data? = nil
        var firstPage = true
        var sawEmptyPage = false

        while !sawEmptyPage {
            let changes: AnchoredChanges
            do {
                changes = try await withCheckedThrowingContinuation { continuation in
                    reading.startAnchoredChangesFetch(
                        anchorData: transientAnchorData,
                        after: firstPage ? start : nil,
                        limit: HealthSyncPolicy.pageLimit
                    ) { result in
                        continuation.resume(with: result)
                    }
                }
            } catch {
                return finishFailure(state, classification: .serverUnavailable)
            }
            firstPage = false
            sawAnySample = sawAnySample || !changes.added.isEmpty

            if !changes.isEmptyPage {
                // A: upload page N.
                let result = await upload(changes)
                switch result {
                case .committed:
                    break
                case .rejectedPermanent:
                    // Fail-closed: drop batch, DO NOT advance anchor.
                    return finishFailure(state, classification: .rejectedPermanent)
                case .failedRetryable:
                    return finishFailure(state, classification: .networkUnreachable)
                case .needsSignIn:
                    return finishFailure(state, classification: .needsSignIn)
                }
                // B: persist anchor N durably — ONLY now, before page N+1.
                // Ownership rule: the working `state` mirrors what the store
                // has DURABLY accepted. Advancement goes into a candidate
                // first; on persist failure the storage-failure path receives
                // the pre-advancement state, so the error-path bookkeeping
                // retry can never write an anchor the store just rejected.
                var committed = state
                committed.durableAnchorData = changes.transientAnchorData
                committed.lastSuccessfulSync = now()
                do {
                    try store.persist(committed)
                } catch {
                    return finishFailure(state, classification: .storageFailure)
                }
                state = committed
            }
            // Next page uses the transient anchor from this page.
            transientAnchorData = changes.transientAnchorData
            sawEmptyPage = changes.isEmptyPage
        }

        // A successfully observed empty terminal page still proves that the
        // local HealthKit cursor is caught up. Persist freshness without
        // advancing the durable anchor past backend-accepted data.
        state.lastSuccessfulSync = now()
        state.lastErrorDescription = nil
        do {
            try store.persist(state)
        } catch {
            return finishFailure(state, classification: .storageFailure)
        }
        return !sawAnySample && state.durableAnchorData == nil ? .noReadableSamples : .synced
    }

    /// Incremental: resume from durable anchor; terminate on empty page.
    private func incrementalSync(from durable: DurableSyncState?) async -> SyncOutcome {
        var state = durable ?? DurableSyncState(
            durableAnchorData: nil, lastSuccessfulSync: nil,
            lastAttempt: nil, lastErrorDescription: nil
        )
        state.lastAttempt = now()

        var transientAnchorData = state.durableAnchorData

        while true {
            let changes: AnchoredChanges
            do {
                changes = try await withCheckedThrowingContinuation { continuation in
                    reading.startAnchoredChangesFetch(
                        anchorData: transientAnchorData, after: nil,
                        limit: HealthSyncPolicy.pageLimit
                    ) { result in
                        continuation.resume(with: result)
                    }
                }
            } catch {
                return finishFailure(state, classification: .networkUnreachable)
            }

            if changes.isEmptyPage { break }

            let result = await upload(changes)
            switch result {
            case .committed:
                break
            case .rejectedPermanent:
                return finishFailure(state, classification: .rejectedPermanent)
            case .failedRetryable:
                return finishFailure(state, classification: .networkUnreachable)
            case .needsSignIn:
                return finishFailure(state, classification: .needsSignIn)
            }
            // Same ownership rule as historicalImport: candidate first, adopt
            // only after the store accepts, so a persistence failure reports
            // failure against the last durably committed anchor and the next
            // sync safely replays this backend-accepted page.
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
        // No-change incremental queries are successful catch-ups too. Update
        // only freshness metadata; the last backend-accepted anchor remains
        // byte-for-byte unchanged.
        state.lastSuccessfulSync = now()
        state.lastErrorDescription = nil
        do {
            try store.persist(state)
        } catch {
            return finishFailure(state, classification: .storageFailure)
        }
        return .synced
    }

    // MARK: - Upload + failure bookkeeping

    private enum UploadResult {
        case committed
        case rejectedPermanent
        case failedRetryable
        case needsSignIn
    }

    private func upload(_ changes: AnchoredChanges) async -> UploadResult {
        do {
            _ = try await backend.submitBatch(added: changes.added, deletions: changes.deletions)
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
        _ state: DurableSyncState, classification: SyncErrorClassification
    ) -> SyncOutcome {
        var failed = state
        failed.lastErrorDescription = classification.rawValue
        try? store.persist(failed)
        return .failed(classification)
    }
}

// MARK: - Outcome types (UI-facing, redacted)

enum SyncErrorClassification: String, Sendable {
    case needsAuthorization
    case needsSignIn
    case networkUnreachable
    case serverUnavailable
    case rejectedPermanent
    case storageFailure
}

enum SyncOutcome: Equatable, Sendable {
    case synced
    case noReadableSamples
    case unavailable
    case failed(SyncErrorClassification)
}
