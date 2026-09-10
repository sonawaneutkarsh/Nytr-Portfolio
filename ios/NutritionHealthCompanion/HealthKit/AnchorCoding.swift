import Foundation

#if canImport(HealthKit)
import HealthKit
#endif

// HKQueryAnchor <-> Data archival (plan §6). HKQueryAnchor conforms to
// NSSecureCoding, so we archive with requiringSecureCoding: true and decode
// with a strict class allowlist. Corruption is recoverable: callers treat nil
// as "no anchor" and the next sync performs a bounded re-import; backend
// deduplication makes replay harmless.

enum AnchorCodingError: Error {
    case encodingFailed
    case decodingFailed
}

enum AnchorCoding {
    static func data(from anchor: AnyObject) -> Data? {
#if canImport(HealthKit)
        guard let hkAnchor = anchor as? NSObject else { return nil }
        return try? NSKeyedArchiver.archivedData(
            withRootObject: hkAnchor, requiringSecureCoding: true
        )
#else
        return nil
#endif
    }

    /// Decodes with a class allowlist. Any failure (corrupt bytes, wrong
    /// class) returns nil rather than crashing.
    static func anchor(from data: Data) -> AnyObject? {
#if canImport(HealthKit)
        let unarchiver = try? NSKeyedUnarchiver(forReadingFrom: data)
        guard let unarchiver else { return nil }
        let decoded = unarchiver.decodeObject(
            of: HKQueryAnchor.self, forKey: NSKeyedArchiveRootObjectKey
        )
        unarchiver.finishDecoding()
        return decoded
#else
        return nil
#endif
    }
}
