import Foundation

/// Presentation-only formatting for exact decimal strings supplied by the backend.
/// Whole daily values use decimal half-up rounding; canonical values are never changed.
enum NytrNumberFormat {
    static func unitLabel(_ raw: String) -> String {
        raw.caseInsensitiveCompare("ml") == .orderedSame ? "mL" : raw
    }

    static func whole(_ raw: String?) -> String? {
        guard let decimal = decimal(raw) else { return raw }
        var source = decimal
        var rounded = Decimal()
        NSDecimalRound(&rounded, &source, 0, .plain)
        return grouped(rounded, minimumFractionDigits: 0, maximumFractionDigits: 0)
    }

    static func detail(_ raw: String?, maximumFractionDigits: Int = 1) -> String? {
        guard let decimal = decimal(raw) else { return raw }
        return grouped(
            decimal,
            minimumFractionDigits: 0,
            maximumFractionDigits: maximumFractionDigits
        )
    }

    static func fraction(_ raw: String?, of target: String?) -> Double? {
        guard let value = decimal(raw), let targetValue = decimal(target), targetValue > 0 else {
            return nil
        }
        return min(max(NSDecimalNumber(decimal: value / targetValue).doubleValue, 0), 1)
    }

    private static func decimal(_ raw: String?) -> Decimal? {
        guard let raw else { return nil }
        return Decimal(string: raw, locale: Locale(identifier: "en_US_POSIX"))
    }

    private static func grouped(
        _ value: Decimal,
        minimumFractionDigits: Int,
        maximumFractionDigits: Int
    ) -> String {
        let formatter = NumberFormatter()
        formatter.locale = Locale.current
        formatter.numberStyle = .decimal
        formatter.roundingMode = .halfUp
        formatter.minimumFractionDigits = minimumFractionDigits
        formatter.maximumFractionDigits = maximumFractionDigits
        formatter.usesGroupingSeparator = true
        return formatter.string(from: NSDecimalNumber(decimal: value))
            ?? NSDecimalNumber(decimal: value).stringValue
    }
}
