import Foundation

#if canImport(OSLog)
    import OSLog
#endif

/// DEBUG-only, privacy-safe trace of how a scanned product's serving basis was
/// classified.
///
/// It emits exactly three classifications: the selected nutrition basis, the
/// selected physical unit, and a reason code. Every value is matched against a
/// closed allow-list before it is formatted, so an unexpected or attacker-shaped
/// server string degrades to `unknown` instead of reaching a log.
///
/// It never records a barcode, product name, brand, provider payload, nutrition
/// value, serving amount, or owner identity.
enum NytrBarcodeDiagnostics {
    static let knownBases: Set<String> = ["per_serving", "per_100g", "per_100ml"]
    static let knownUnits: Set<String> = ["serving", "g", "ml"]
    static let knownReasons: Set<String> = [
        "structured_serving_volume",
        "structured_serving_mass",
        "source_serving_without_physical_quantity",
        "explicit_per_100ml",
        "no_trustworthy_volume_evidence",
        "owner_entered_serving",
    ]

    /// Builds the diagnostic line. Pure and always available so it can be tested
    /// without depending on build configuration or log capture.
    static func message(basis: String?, unit: String?, reason: String?) -> String {
        let basisToken = token(basis, allowed: knownBases)
        let unitToken = token(unit, allowed: knownUnits)
        let reasonToken = token(reason, allowed: knownReasons)
        return "[NytrBarcode] basis=\(basisToken) unit=\(unitToken) reason=\(reasonToken)"
    }

    /// Closed allow-list projection. Anything unrecognized becomes `unknown`,
    /// which is what keeps free-text out of the log by construction.
    static func token(_ value: String?, allowed: Set<String>) -> String {
        guard let value, allowed.contains(value) else { return "unknown" }
        return value
    }

    static func record(basis: String?, unit: String?, reason: String?) {
        #if DEBUG
            let line = message(basis: basis, unit: unit, reason: reason)
            #if canImport(OSLog)
                Logger(subsystem: "com.nytr.companion", category: "barcode").debug("\(line)")
            #else
                print(line)
            #endif
        #endif
    }
}
