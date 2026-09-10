import Foundation

// The ONLY boundary between the app and HealthKit. No HealthKit type may
// appear in this file or any signature below (acceptance criterion 2).

struct BodyMassSampleDTO: Sendable, Equatable {
    let sampleUUID: UUID              // HKObject.uuid — stable HealthKit identity
    let valueKgDecimalString: String  // canonical kg, half-even quantized, <=3 dp, '.' separator
    let sampleStart: Date
    let sampleEnd: Date
    let sourceName: String?
    let sourceBundleID: String?

    init(
        sampleUUID: UUID,
        valueKgDecimalString: String,
        sampleStart: Date,
        sampleEnd: Date,
        sourceName: String? = nil,
        sourceBundleID: String? = nil
    ) {
        self.sampleUUID = sampleUUID
        self.valueKgDecimalString = valueKgDecimalString
        self.sampleStart = sampleStart
        self.sampleEnd = sampleEnd
        self.sourceName = sourceName
        self.sourceBundleID = sourceBundleID
    }
}

struct DeletedSampleDTO: Sendable, Equatable {
    let sampleUUID: UUID

    init(sampleUUID: UUID) {
        self.sampleUUID = sampleUUID
    }
}

/// One page of an anchored object query. `transientAnchorData` is the
/// serialized anchor for fetching the NEXT page of this attempt; it must never
/// be persisted (see HealthSyncCoordinator for the durable-anchor discipline).
struct AnchoredChanges: Sendable, Equatable {
    let added: [BodyMassSampleDTO]
    let deletions: [DeletedSampleDTO]
    let transientAnchorData: Data?

    init(added: [BodyMassSampleDTO], deletions: [DeletedSampleDTO], transientAnchorData: Data?) {
        self.added = added
        self.deletions = deletions
        self.transientAnchorData = transientAnchorData
    }

    var isEmptyPage: Bool { added.isEmpty && deletions.isEmpty }
}

enum AuthorizationOutcome: Sendable, Equatable {
    /// A permission sheet was presented and completed. Read outcome stays
    /// opaque by Apple design; only queries can reveal what is readable.
    case completed
    /// Authorization was already determined previously; no sheet needed.
    case previouslyDetermined
}

enum HealthPortError: Error, Equatable {
    case healthDataUnavailable
    case authorizationFailed
    case queryFailed
}

protocol HealthKitAuthorizing: Sendable {
    func isAvailable() -> Bool
    func requestAuthorization() async throws -> AuthorizationOutcome
    /// Earliest KNOWN authorized-sample start for the person, from Apple's
    /// getEarliestAuthorizedSampleDate(for:). Returns .distantPast when full
    /// history appears readable AND when the installed SDK cannot observe an
    /// authorization boundary at all (M11B SDK compatibility): the
    /// coordinator's max() formula then uses the full product-policy lookback,
    /// and HealthKit still returns only what the person actually authorized.
    /// earliestPermittedSampleDate() is explicitly NOT a substitute — it is a
    /// framework-wide floor, not a per-person authorization boundary.
    func earliestAuthorizedStartDate() async -> Date
}

protocol HealthKitBodyMassReading: Sendable {
    /// Fetch ONE anchored page. `anchorData == nil` means a snapshot query;
    /// pass the previous page's transientAnchorData to continue paging within
    /// one sync attempt. Paging/looping belongs to the coordinator.
    ///
    /// This entry point is deliberately synchronous and callback-based because
    /// HKAnchoredObjectQuery is callback-based. The coordinator owns the async
    /// continuation bridge. This ensures query submission happens before the
    /// caller suspends and avoids an unnecessary Swift async-entry executor
    /// hop at the HealthKit boundary. Implementations must invoke `completion`
    /// exactly once for the requested page.
    func startAnchoredChangesFetch(
        anchorData: Data?, after: Date?, limit: Int,
        completion: @escaping @Sendable (Result<AnchoredChanges, Error>) -> Void
    )

    /// Observer hook: called when HealthKit reports possible changes while the
    /// app runs. Best-effort delivery only; correctness never depends on it.
    var changesInBackground: (@Sendable () async -> Void)? { get set }
}
