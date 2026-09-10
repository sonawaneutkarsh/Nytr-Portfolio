import Foundation

enum ConsumptionState: String, Codable, Equatable, Hashable, Sendable, CaseIterable {
    case eaten
    case skipped
    case unavailable
    case alternative
}

struct RecordConsumptionRequest: Encodable, Equatable, Sendable {
    let planVersionId: UUID
    let itemId: UUID
    let state: ConsumptionState
    /// Omitted when nil so the backend may generate and return the event ID.
    let clientEventId: UUID?

    private enum CodingKeys: String, CodingKey {
        case planVersionId = "plan_version_id"
        case itemId = "item_id"
        case state
        case clientEventId = "client_event_id"
    }
}

struct ConsumptionEntryResponse: Codable, Equatable, Sendable {
    let entryId: UUID
    let planRunId: UUID
    let planVersionId: UUID
    let itemId: UUID
    let state: ConsumptionState
    let recordedAt: Date
    let clientEventId: UUID

    private enum CodingKeys: String, CodingKey {
        case entryId = "entry_id"
        case planRunId = "plan_run_id"
        case planVersionId = "plan_version_id"
        case itemId = "item_id"
        case state
        case recordedAt = "recorded_at"
        case clientEventId = "client_event_id"
    }
}

struct ConsumptionListResponse: Codable, Equatable, Sendable {
    let entries: [ConsumptionEntryResponse]
}
