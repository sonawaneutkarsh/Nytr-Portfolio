import Foundation
import Observation

/// Observable UI state machine mapping sync outcomes to the minimal Health
/// Sync screen (plan §12). All mutations on @MainActor.
///
/// Sync execution is delegated to the shared `HealthSyncEngine` — the single
/// flight path every trigger (manual, scenePhase, observer wake) converges on.
/// The view model never constructs a `HealthSyncCoordinator`.
@MainActor
@Observable
final class HealthSyncViewModel {
    enum Phase: Equatable {
        case idle
        case authorizing
        case syncing
        case unavailable
        case needsAuthorization
        case needsSignIn
        case noReadableSamples
        case synced(latestKg: String?, latestDate: Date?)
        case stale(latestKg: String?, latestDate: Date?)
        case failed(String)
    }

    private(set) var phase: Phase = .idle
    private(set) var lastSuccessfulSync: Date?
    private(set) var recordCount: Int?
    private(set) var isSyncingNow = false

    private let engine: HealthSyncEngine
    private let workoutEngine: WorkoutSyncEngine?
    private let backend: BackendClient
    private let store: SyncStateStore
    private let workoutStore: SyncStateStore?
    private let onSyncCompleted: @MainActor () -> Void

    init(
        engine: HealthSyncEngine,
        workoutEngine: WorkoutSyncEngine? = nil,
        backend: BackendClient,
        store: SyncStateStore,
        workoutStore: SyncStateStore? = nil,
        onSyncCompleted: @escaping @MainActor () -> Void = {}
    ) {
        self.engine = engine
        self.workoutEngine = workoutEngine
        self.backend = backend
        self.store = store
        self.workoutStore = workoutStore
        self.onSyncCompleted = onSyncCompleted
    }

    func refreshFromStore() {
        let bodyMassSuccess = (try? store.load())?.lastSuccessfulSync
        if let workoutStore {
            let workoutSuccess = (try? workoutStore.load())?.lastSuccessfulSync
            // The screen represents the combined foreground operation. Its
            // conservative freshness is the older success across both
            // configured streams, never a body-only or workout-only claim.
            if let bodyMassSuccess, let workoutSuccess {
                lastSuccessfulSync = min(bodyMassSuccess, workoutSuccess)
            } else {
                lastSuccessfulSync = nil
            }
        } else {
            lastSuccessfulSync = bodyMassSuccess
        }
        markStaleIfAppropriate()
    }

    func syncNow() async {
        guard !isSyncingNow else { return }
        isSyncingNow = true
        phase = .syncing
        // Availability, authorization, and auth gating all live inside the
        // shared engine path; the outcome maps straight onto UI phases.
        let bodyMassOutcome = await engine.runFullSync()
        let outcome: SyncOutcome
        switch bodyMassOutcome {
        case .synced, .noReadableSamples:
            if let workoutEngine {
                outcome = Self.combined(
                    bodyMass: bodyMassOutcome,
                    workout: await workoutEngine.runFullSync()
                )
            } else {
                outcome = bodyMassOutcome
            }
        default:
            outcome = bodyMassOutcome
        }
        isSyncingNow = false
        apply(outcome)
    }

    private static func combined(bodyMass: SyncOutcome, workout: SyncOutcome) -> SyncOutcome {
        switch workout {
        case .failed, .unavailable:
            workout
        case .synced:
            .synced
        case .noReadableSamples:
            bodyMass
        }
    }

    private func apply(_ outcome: SyncOutcome) {
        switch outcome {
        case .unavailable:
            phase = .unavailable
        case .failed(.needsAuthorization):
            phase = .needsAuthorization
        case .failed(.needsSignIn):
            phase = .needsSignIn
        case .failed(let classification):
            phase = .failed(classification.rawValue)
        case .noReadableSamples:
            // Permission-neutral by design (plan §4).
            phase = .noReadableSamples
        case .synced:
            loadStatus()
            onSyncCompleted()
        }
        refreshFromStore()
    }

    private func loadStatus() {
        Task {
            do {
                let status = try await backend.fetchSyncStatus()
                recordCount = status.record_count
                let latest = status.latest_sample
                let latestDate = latest.flatMap { WireDate.date(fromISO8601: $0.sample_start) }
                phase = .synced(latestKg: latest?.value_kg, latestDate: latestDate)
                markStaleIfAppropriate()
            } catch BackendError.unauthorized {
                phase = .needsSignIn
            } catch {
                // Status display failure does not invalidate a successful sync.
                phase = .synced(latestKg: nil, latestDate: nil)
            }
        }
    }

    private func markStaleIfAppropriate() {
        guard case .synced(let kg, let date) = phase else { return }
        if let last = lastSuccessfulSync,
           Date().timeIntervalSince(last) > HealthSyncPolicy.staleBadgeThresholdSeconds {
            phase = .stale(latestKg: kg, latestDate: date)
        }
    }
}
