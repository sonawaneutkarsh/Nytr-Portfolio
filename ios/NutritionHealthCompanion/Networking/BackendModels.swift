import Foundation

// Codable models matching the FROZEN backend contract (commit 2137664,
// docs/APPLE_HEALTH.md §API). No backend changes are permitted in M5.

struct SyncRequestPayload: Encodable, Equatable {
    struct AddedSample: Encodable, Equatable {
        let sample_uuid: String
        let value: String
        let sample_start: String
        let sample_end: String
        let source_name: String?
        let source_bundle_id: String?
    }
    struct DeletedSample: Encodable, Equatable {
        let sample_uuid: String
    }

    let client_batch_id: String
    let added: [AddedSample]
    let deleted: [DeletedSample]
}

struct SyncResponse: Decodable, Equatable, Sendable {
    struct LatestSample: Decodable, Equatable, Sendable {
        let sample_uuid: String
        let sample_start: String
        let value_kg: String
    }
    let accepted_added: Int
    let duplicate_added: Int
    let applied_deletions: Int
    let duplicate_deletions: Int
    let ingested_at: String?
    let latest_sample: LatestSample?
}

struct StatusResponse: Decodable, Equatable, Sendable {
    struct LatestSample: Decodable, Equatable, Sendable {
        let sample_uuid: String
        let sample_start: String
        let value_kg: String
    }
    /// Rows carrying a real measurement (including later-tombstoned ones).
    let record_count: Int
    let tombstone_count: Int
    /// Most recent ACTIVE sample by sample_start DESC — tombstones excluded.
    let latest_sample: LatestSample?
    let last_ingested_at: String?
}

struct WorkoutSyncRequestPayload: Encodable, Equatable {
    struct AddedSession: Encodable, Equatable {
        let source_system: String
        let source_record_id: String
        let activity_type: String?
        let started_at: String
        let ended_at: String
        let active_duration_seconds: String?
        let active_energy_kcal: String?
        let timezone_identifier: String?
        let source_name: String?
        let source_bundle_id: String?
        let source_revision: String?
    }

    struct DeletedSession: Encodable, Equatable {
        let source_system: String
        let source_record_id: String
    }

    let client_batch_id: String
    let added: [AddedSession]
    let deleted: [DeletedSession]
}

struct WorkoutSyncResponse: Decodable, Equatable, Sendable {
    let accepted_added: Int
    let duplicate_added: Int
    let applied_deletions: Int
    let duplicate_deletions: Int
}

enum BackendError: Error, Equatable {
    /// 401 after a refresh+retry already happened at the provider level, or
    /// the provider threw needsSignIn.
    case unauthorized
    /// 400/413: permanent batch problem. The batch is dropped client-side;
    /// the anchor must NOT advance (fail-closed).
    case rejectedPermanent(String)
    /// 422/500/503/transport failures: retry with backoff.
    case retryable(String)
    /// A non-retryable M7 request failure with the backend's safe envelope.
    case rejected(statusCode: Int, code: String?, detail: String?)
    /// A retryable M7 HTTP failure while preserving its safe error code.
    case retryableHTTP(statusCode: Int, code: String?, detail: String?)
}

/// ISO-8601 UTC formatting identical to what the backend validates.
enum WireDate {
    static let iso8601UTC: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    private static let iso8601FractionalUTC: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    static func string(from date: Date) -> String {
        iso8601UTC.string(from: date)
    }

    static func date(fromISO8601 string: String) -> Date? {
        iso8601UTC.date(from: string) ?? iso8601FractionalUTC.date(from: string)
    }
}
