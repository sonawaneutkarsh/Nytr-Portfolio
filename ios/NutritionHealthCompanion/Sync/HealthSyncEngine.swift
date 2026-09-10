import Foundation

/// The ONE shared sync execution path for the app (M5 fix pass).
///
/// Every trigger — manual `HealthSyncViewModel.syncNow()`, scenePhase
/// activation, HealthKit observer/background wakes, and the initial
/// app-triggered sync — routes through this actor-owned instance:
///
///   - The first trigger starts exactly one run on a freshly constructed
///     `HealthSyncCoordinator`.
///   - Concurrent triggers coalesce onto that in-flight run and receive its
///     terminal outcome; they never construct or own an independent
///     coordinator and can never interleave a second anchored loop.
///   - Only when no run is active does a new coordinator get created.
///
/// This is intentionally the smallest serialization mechanism for Swift
/// concurrency: one actor with one serialized run entrypoint. No state
/// machines, no queues, no frameworks.
///
/// The anchored-loop discipline itself is unchanged and remains owned by
/// `HealthSyncCoordinator`:
///   fetch page -> backend accepts page -> persist durable anchor -> fetch
///   next page.
actor HealthSyncEngine {
    enum RunKind {
        /// Authorization-capable entry (manual sync / foreground catch-up).
        case full
        /// Already-authorized incremental catch-up (observer/background wake).
        case incremental
    }

    private let reading: any HealthKitAuthorizing & HealthKitBodyMassReading
    private let backend: BackendClient
    private let store: SyncStateStore
    private let auth: AccessTokenProvider

    /// The in-flight run, if any. Late-finishing joiners may observe a
    /// completed task here until cleanup runs; joining it returns that run's
    /// cached terminal outcome, which only ever suppresses redundant loops.
    private var activeRun: Task<SyncOutcome, Never>?
    /// Monotonic token bumped ONLY when a new run starts, guarding conditional
    /// cleanup so a stale joiner can never clear a newer run's task.
    private var generation = 0

    init(
        reading: any HealthKitAuthorizing & HealthKitBodyMassReading,
        backend: BackendClient,
        store: SyncStateStore,
        auth: AccessTokenProvider
    ) {
        self.reading = reading
        self.backend = backend
        self.store = store
        self.auth = auth
    }

    func runFullSync() async -> SyncOutcome {
        await run(.full)
    }

    func runIncrementalSync() async -> SyncOutcome {
        await run(.incremental)
    }

    private func run(_ kind: RunKind) async -> SyncOutcome {
        if let active = activeRun {
            // Single-flight: coalesce onto the running loop; no second
            // coordinator is created while one is active.
            return await active.value
        }

        generation += 1
        let startedGeneration = generation

        // Unstructured Task on purpose: the shared run must survive joiner
        // cancellation (e.g., scene teardown), and cancellation of an
        // individual waiting trigger must not abort the loop for others.
        // Anchor safety is preserved by the coordinator discipline regardless:
        // a cancelled fetch surfaces as a retryable failure that leaves the
        // durable anchor at the last successfully committed page.
        let task = Task<SyncOutcome, Never> {
            let coordinator = HealthSyncCoordinator(
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
        // Actor-isolated, suspension-free after resume: safe conditional
        // cleanup. If a newer run started meanwhile, leave its task alone.
        if generation == startedGeneration {
            activeRun = nil
        }
        return outcome
    }
}
