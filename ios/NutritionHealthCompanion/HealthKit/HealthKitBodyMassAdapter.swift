import Foundation

// The sole importer of the HealthKit framework (acceptance criterion 2).
// Implements HealthKitAuthorizing + HealthKitBodyMassReading against
// HKHealthStore. All paging/looping decisions belong to the coordinator; this
// adapter returns exactly one anchored page per startAnchoredChangesFetch call.

#if canImport(HealthKit)
import HealthKit

/// The sole importer of the HealthKit framework (acceptance criterion 2).
/// Sendable-by-design: HKHealthStore is thread-safe per Apple, and the only
/// shared mutable state (`BackgroundHook`) is NSLock-guarded. It is shared
/// across the app dependency graph and the HealthSyncEngine actor.
final class HealthKitBodyMassAdapter: HealthKitAuthorizing, HealthKitBodyMassReading,
    HealthKitWorkoutReading,
    @unchecked Sendable {
    private let store = HKHealthStore()
    static let bodyMassType = HKQuantityType(.bodyMass)
    static let workoutType = HKObjectType.workoutType()
    static var requestedReadTypes: Set<HKObjectType> { [bodyMassType, workoutType] }
    static var requestedShareTypes: Set<HKSampleType> { [] }

    // MARK: HealthKitAuthorizing

    func isAvailable() -> Bool {
        HKHealthStore.isHealthDataAvailable()
    }

    func requestAuthorization() async throws -> AuthorizationOutcome {
        guard isAvailable() else { throw HealthPortError.healthDataUnavailable }
        do {
            // Read-only body mass + workouts. Workout-attached energy is read
            // from HKWorkout when present; no all-day energy type is requested.
            try await store.requestAuthorization(
                toShare: Self.requestedShareTypes,
                read: Self.requestedReadTypes
            )
            return .completed
        } catch {
            throw HealthPortError.authorizationFailed
        }
    }

    /// Apple documents getEarliestAuthorizedSampleDate(for:) as the
    /// limited-authorization detection API: full-history grants (and denied
    /// access) return no entry, and a limited grant returns the earliest date
    /// the person actually authorized for the requested types.
    ///
    /// SDK COMPATIBILITY (M11B): that API is NOT declared by the installed
    /// HealthKit SDK (verified absent from the iPhoneOS26.5.swiftinterface in
    /// Xcode 26.6), so the person's authorization boundary is UNKNOWN here.
    /// The honest behavior is to report .distantPast — the coordinator's
    /// canonical max(now - 365d, earliest) formula treats an unknown/full
    /// boundary identically and uses the full 365-day product-policy lookback.
    /// This broadens nothing: HealthKit only ever returns samples the person
    /// actually authorized, and inaccessible older samples are never
    /// interpreted as zero/missing evidence — they are simply not returned.
    /// earliestPermittedSampleDate() is NOT a substitute: it is the
    /// framework-wide minimum sample date, not a per-person authorization
    /// boundary, and adopting it would falsely claim knowledge of a boundary
    /// that this SDK cannot observe. If a future SDK declares the API,
    /// restore the call behind an explicit availability gate and keep this
    /// documented fallback for older SDKs.
    func earliestAuthorizedStartDate() async -> Date {
        .distantPast
    }

    // MARK: HealthKitBodyMassReading

    func startAnchoredChangesFetch(
        anchorData: Data?, after: Date?, limit: Int,
        completion: @escaping @Sendable (Result<AnchoredChanges, Error>) -> Void
    ) {
        guard isAvailable() else {
            completion(.failure(HealthPortError.healthDataUnavailable))
            return
        }
        let anchor = anchorData.flatMap { AnchorCoding.anchor(from: $0) as? HKQueryAnchor }

        var predicate: NSPredicate?
        if let after {
            predicate = HKQuery.predicateForSamples(
                withStart: after, end: nil, options: [.strictStartDate]
            )
        }

        let query = HKAnchoredObjectQuery(
            type: Self.bodyMassType,
            predicate: predicate,
            anchor: anchor,
            limit: limit
        ) { _, samples, deletedObjects, newAnchor, error in
            if error != nil {
                completion(.failure(HealthPortError.queryFailed))
                return
            }
            let added = (samples ?? []).compactMap { sample -> BodyMassSampleDTO? in
                guard let quantitySample = sample as? HKQuantitySample else {
                    return nil
                }
                return SampleMapping.dto(
                    from: quantitySample, kiloGramUnit: .gramUnit(with: .kilo)
                )
            }
            let deletions = (deletedObjects ?? []).map { deleted in
                DeletedSampleDTO(sampleUUID: deleted.uuid)
            }
            let transientData: Data? = newAnchor.flatMap { AnchorCoding.data(from: $0) }
            completion(.success(
                AnchoredChanges(
                    added: added,
                    deletions: deletions,
                    transientAnchorData: transientData
                )
            ))
        }
        store.execute(query)
    }

    // MARK: HealthKitWorkoutReading

    func earliestAuthorizedWorkoutStartDate() async -> Date {
        .distantPast
    }

    func startWorkoutAnchoredChangesFetch(
        anchorData: Data?, after: Date?, limit: Int,
        completion: @escaping @Sendable (Result<WorkoutAnchoredChanges, Error>) -> Void
    ) {
        guard isAvailable() else {
            completion(.failure(HealthPortError.healthDataUnavailable))
            return
        }
        let anchor = anchorData.flatMap { AnchorCoding.anchor(from: $0) as? HKQueryAnchor }
        let predicate = after.map {
            HKQuery.predicateForSamples(withStart: $0, end: nil, options: [.strictStartDate])
        }
        let query = HKAnchoredObjectQuery(
            type: Self.workoutType,
            predicate: predicate,
            anchor: anchor,
            limit: limit
        ) { _, samples, deletedObjects, newAnchor, error in
            if error != nil {
                completion(.failure(HealthPortError.queryFailed))
                return
            }
            let added = (samples ?? []).compactMap { sample in
                (sample as? HKWorkout).map(WorkoutSampleMapping.dto)
            }
            let deletions = (deletedObjects ?? []).map {
                DeletedWorkoutDTO(sourceRecordID: $0.uuid)
            }
            completion(.success(WorkoutAnchoredChanges(
                added: added,
                deletions: deletions,
                transientAnchorData: newAnchor.flatMap(AnchorCoding.data)
            )))
        }
        store.execute(query)
    }

    nonisolated var changesInBackground: (@Sendable () async -> Void)? {
        get { backgroundQueueHook.value }
        set { backgroundQueueHook.value = newValue }
    }

    private let backgroundQueueHook = BackgroundHook()

    /// Registers a foreground observer whose wake triggers `changesInBackground`.
    /// Best-effort delivery only (plan §9). Call once at app launch.
    /// The observer update handler carries (query, completionHandler, error).
    func registerObserver() {
        guard isAvailable() else { return }
        let observer = HKObserverQuery(
            sampleType: Self.bodyMassType, predicate: nil
        ) { [weak self] _, completionHandler, _ in
            guard let self else {
                completionHandler()
                return
            }
            Task { @Sendable [weak self] in
                await self?.backgroundQueueHook.value?()
                completionHandler()
            }
        }
        store.execute(observer)
    }

    /// Best-effort background delivery. Requires the
    /// com.apple.developer.healthkit.background-delivery entitlement AND a
    /// physical device (unsupported in Simulator). Failure is non-fatal:
    /// correctness rests on launch/foreground/manual sync paths.
    func enableBackgroundDeliveryIfPossible() async {
        guard isAvailable() else { return }
        #if !targetEnvironment(simulator)
        do {
            try await store.enableBackgroundDelivery(
                for: Self.bodyMassType, frequency: .immediate
            )
        } catch {
            // Non-fatal by design.
        }
        #endif
    }
}

private final class BackgroundHook: @unchecked Sendable {
    private let lock = NSLock()
    private var hook: (@Sendable () async -> Void)?

    var value: (@Sendable () async -> Void)? {
        get { lock.withLock { hook } }
        set { lock.withLock { hook = newValue } }
    }
}
#endif

#if !canImport(HealthKit)
// macOS/pre-CLT fallback so the module compiles outside Xcode/iOS toolchains.
// Never used on iOS; exists purely to keep non-Xcode builds green.
struct HealthKitBodyMassAdapter: HealthKitAuthorizing, HealthKitBodyMassReading,
    HealthKitWorkoutReading {
    func isAvailable() -> Bool { false }
    func requestAuthorization() async throws -> AuthorizationOutcome {
        throw HealthPortError.healthDataUnavailable
    }
    func earliestAuthorizedStartDate() async -> Date { .distantPast }
    func startAnchoredChangesFetch(
        anchorData: Data?, after: Date?, limit: Int,
        completion: @escaping @Sendable (Result<AnchoredChanges, Error>) -> Void
    ) {
        completion(.failure(HealthPortError.healthDataUnavailable))
    }
    func earliestAuthorizedWorkoutStartDate() async -> Date { .distantPast }
    func startWorkoutAnchoredChangesFetch(
        anchorData: Data?, after: Date?, limit: Int,
        completion: @escaping @Sendable (Result<WorkoutAnchoredChanges, Error>) -> Void
    ) {
        completion(.failure(HealthPortError.healthDataUnavailable))
    }
    var changesInBackground: (@Sendable () async -> Void)? {
        get { nil }
        set {}
    }
}
#endif
