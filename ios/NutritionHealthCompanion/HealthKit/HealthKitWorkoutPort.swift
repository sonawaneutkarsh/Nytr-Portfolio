import Foundation

/// HealthKit-free normalized boundary for immutable workout observations.
struct WorkoutSampleDTO: Sendable, Equatable {
    let sourceRecordID: UUID
    let activityType: String?
    let startedAt: Date
    let endedAt: Date
    let activeDurationSecondsDecimalString: String?
    let activeEnergyKcalDecimalString: String?
    let timezoneIdentifier: String?
    let sourceName: String?
    let sourceBundleID: String?
    let sourceRevision: String?
}

struct DeletedWorkoutDTO: Sendable, Equatable {
    let sourceRecordID: UUID
}

struct WorkoutAnchoredChanges: Sendable, Equatable {
    let added: [WorkoutSampleDTO]
    let deletions: [DeletedWorkoutDTO]
    let transientAnchorData: Data?

    var isEmptyPage: Bool { added.isEmpty && deletions.isEmpty }
}

protocol HealthKitWorkoutReading: Sendable {
    /// SDK-compatible authorization-boundary observation. `.distantPast`
    /// means unknown/full and is still bounded by the 365-day product policy.
    func earliestAuthorizedWorkoutStartDate() async -> Date

    /// Synchronous callback entry preserves the physical-device-safe query
    /// dispatch boundary already proven for body mass in M11B.
    func startWorkoutAnchoredChangesFetch(
        anchorData: Data?, after: Date?, limit: Int,
        completion: @escaping @Sendable (Result<WorkoutAnchoredChanges, Error>) -> Void
    )
}
