import Foundation

/// Durable, non-sensitive sync metadata. The anchor is an opaque cursor (no
/// PHI). Tokens never live here — they belong in the Keychain via TokenStore.
struct DurableSyncState: Codable, Equatable {
    /// B-anchor: persisted ONLY after the backend durably accepts the page
    /// it corresponds to. Schema version 1; bump key when shape changes.
    var durableAnchorData: Data?
    var lastSuccessfulSync: Date?
    var lastAttempt: Date?
    /// Redacted classification only — never raw payloads or values.
    var lastErrorDescription: String?

    static let schemaVersion = 1
}

enum SyncStoreError: Error, Equatable {
    case corruptState
}

protocol SyncStateStore: Sendable {
    func load() throws -> DurableSyncState?
    /// Single atomic write; replaces the whole state.
    func persist(_ state: DurableSyncState) throws
}

enum DurableSyncNamespace: Sendable {
    case bodyMass
    case workout

    var key: String {
        switch self {
        case .bodyMass:
            // Frozen M5/M11B key: existing physical-device anchors must load.
            "health.sync.durableState.v\(DurableSyncState.schemaVersion)"
        case .workout:
            "health.sync.workout.durableState.v\(DurableSyncState.schemaVersion)"
        }
    }
}

/// UserDefaults-backed store. The anchor is non-sensitive, so UserDefaults is
/// sufficient per plan §6; tokens are stored separately in the Keychain.
/// Sendable: UserDefaults itself is documented thread-safe, so sharing one
/// instance (app graph + HealthSyncEngine actor) is safe.
struct UserDefaultsDurableStore: SyncStateStore {
    private let defaults: UserDefaults
    private let key: String

    init(
        defaults: UserDefaults = .standard,
        namespace: DurableSyncNamespace = .bodyMass
    ) {
        self.defaults = defaults
        key = namespace.key
    }

    func load() throws -> DurableSyncState? {
        guard let data = defaults.data(forKey: key) else { return nil }
        do {
            return try JSONDecoder().decode(DurableSyncState.self, from: data)
        } catch {
            // Corrupt state: treat as absent so the next sync performs a bounded
            // re-import under the policy window; backend dedup makes replay safe.
            return nil
        }
    }

    func persist(_ state: DurableSyncState) throws {
        let data = try JSONEncoder().encode(state)
        defaults.set(data, forKey: key)
    }
}

// UserDefaults is thread-safe but not annotated Sendable in the SDK; the
// protocol conformance above is nevertheless required to be Sendable. This
// unchecked conformance documents that reality explicitly rather than silently.
extension UserDefaultsDurableStore: @unchecked Sendable {}
