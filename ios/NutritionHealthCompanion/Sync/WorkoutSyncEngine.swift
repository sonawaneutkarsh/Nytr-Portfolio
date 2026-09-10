import Foundation

/// Single-flight execution for the workout anchor namespace.
actor WorkoutSyncEngine {
    enum RunKind {
        case full
        case incremental
    }

    private let reading: any HealthKitAuthorizing & HealthKitWorkoutReading
    private let backend: BackendClient
    private let store: SyncStateStore
    private let auth: AccessTokenProvider
    private var activeRun: Task<SyncOutcome, Never>?
    private var generation = 0

    init(
        reading: any HealthKitAuthorizing & HealthKitWorkoutReading,
        backend: BackendClient,
        store: SyncStateStore,
        auth: AccessTokenProvider
    ) {
        self.reading = reading
        self.backend = backend
        self.store = store
        self.auth = auth
    }

    func runFullSync() async -> SyncOutcome { await run(.full) }
    func runIncrementalSync() async -> SyncOutcome { await run(.incremental) }

    private func run(_ kind: RunKind) async -> SyncOutcome {
        if let activeRun { return await activeRun.value }
        generation += 1
        let startedGeneration = generation
        let task = Task<SyncOutcome, Never> {
            let coordinator = WorkoutSyncCoordinator(
                reading: reading, backend: backend, store: store, auth: auth
            )
            switch kind {
            case .full:
                return await coordinator.runFullSync()
            case .incremental:
                return await coordinator.runIncrementalSync()
            }
        }
        activeRun = task
        let outcome = await task.value
        if generation == startedGeneration { activeRun = nil }
        return outcome
    }
}
