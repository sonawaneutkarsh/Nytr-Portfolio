import CryptoKit
import Foundation

struct CachedDayPlan: Codable, Equatable, Sendable {
    let schemaVersion: Int
    let planDate: String
    let cachedAt: Date
    let response: DayPlanResponse
}

protocol DayPlanCaching {
    func load(forSubject subject: String) -> CachedDayPlan?

    @discardableResult
    func save(_ response: DayPlanResponse, forSubject subject: String) -> Bool

    func clear(forSubject subject: String)
    func clearAll()
}

/// One last-known completed plan per authenticated subject. This is display
/// resilience only: it stores no credentials, consumption queue, or history.
final class DayPlanCache: DayPlanCaching {
    static let schemaVersion = 1
    private static let keyPrefix = "dayplan.cache.v1."
    private static let allVersionsPrefix = "dayplan.cache."

    private let defaults: UserDefaults
    private let now: () -> Date

    init(defaults: UserDefaults = .standard, now: @escaping () -> Date = Date.init) {
        self.defaults = defaults
        self.now = now
    }

    func load(forSubject subject: String) -> CachedDayPlan? {
        let key = Self.cacheKey(forSubject: subject)
        guard
            let data = defaults.data(forKey: key),
            let record = try? JSONDecoder().decode(CachedDayPlan.self, from: data),
            record.schemaVersion == Self.schemaVersion,
            case .completed(let completed) = record.response,
            record.planDate == completed.planDate
        else {
            if defaults.object(forKey: key) != nil {
                defaults.removeObject(forKey: key)
            }
            return nil
        }
        return record
    }

    @discardableResult
    func save(_ response: DayPlanResponse, forSubject subject: String) -> Bool {
        guard case .completed(let completed) = response else { return false }
        let record = CachedDayPlan(
            schemaVersion: Self.schemaVersion,
            planDate: completed.planDate,
            cachedAt: now(),
            response: response
        )
        guard let data = try? JSONEncoder().encode(record) else { return false }
        defaults.set(data, forKey: Self.cacheKey(forSubject: subject))
        return true
    }

    func clear(forSubject subject: String) {
        defaults.removeObject(forKey: Self.cacheKey(forSubject: subject))
    }

    func clearAll() {
        for key in defaults.dictionaryRepresentation().keys
            where key.hasPrefix(Self.allVersionsPrefix)
        {
            defaults.removeObject(forKey: key)
        }
    }

    /// Internal for deterministic key regression tests via @testable import.
    static func cacheKey(forSubject subject: String) -> String {
        let digest = SHA256.hash(data: Data(subject.utf8))
        let hex = digest.map { String(format: "%02x", $0) }.joined()
        return keyPrefix + hex
    }
}
