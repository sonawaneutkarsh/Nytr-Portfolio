import Foundation

/// Product policy constants for health synchronization (plan §5).
///
/// `historicalImportLookbackDays` is a PRODUCT POLICY decision of this
/// application, NOT an Apple/HealthKit requirement. Apple imposes no such
/// window; we bound the first-sync payload size and duration (chosen to cover
/// the owner's Feb–Aug lean-bulk history plus a year of future data). The
/// Apple-side limited-authorization grant can only ever CLAMP this window
/// tighter, never widen it. Not user-configurable in M5; changing the constant
/// is a versioned product-policy change.
enum HealthSyncPolicy {
    static let historicalImportLookbackDays: Int = 365

    /// Canonical historical-import formula — identical in code, tests, docs:
    ///   effectiveStart = max(now - 365 days, earliestAuthorizedSampleDate)
    static func effectiveHistoricalStart(now: Date, earliestAuthorized: Date) -> Date {
        let lookbackStart = Calendar(identifier: .gregorian).date(
            byAdding: .day, value: -historicalImportLookbackDays, to: now
        ) ?? now
        return max(lookbackStart, earliestAuthorized)
    }

    /// Foreground staleness threshold that triggers a catch-up on activation.
    static let foregroundRefreshThresholdSeconds: TimeInterval = 15 * 60

    /// UI staleness badge threshold.
    static let staleBadgeThresholdSeconds: TimeInterval = 48 * 60 * 60

    /// Anchored-query page size (also the backend batch cap).
    static let pageLimit: Int = 500

    /// Retry backoff bounds.
    static let retryInitialDelaySeconds: TimeInterval = 30
    static let retryMaxDelaySeconds: TimeInterval = 15 * 60
}
