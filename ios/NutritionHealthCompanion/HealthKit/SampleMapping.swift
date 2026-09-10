import Foundation

#if canImport(HealthKit)
import HealthKit
#endif

// Quantization policy (plan §3): kg extraction -> Decimal -> ONE half-even
// quantization to 3 dp -> locale-pinned decimal string. Never double-round;
// never bias downward (.down was rejected by review as systematically
// under-reporting weight).

enum SampleMapping {
    private static let quantizationHandler = NSDecimalNumberHandler(
        roundingMode: .bankers,
        scale: 3,
        raiseOnExactness: false,
        raiseOnOverflow: true,
        raiseOnUnderflow: false,
        raiseOnDivideByZero: true
    )

    /// Locale-pinned rendering: '.' separator, no scientific notation, no
    /// thousands grouping regardless of device locale.
    private static func render(_ value: NSDecimalNumber) -> String {
        let formatter = NumberFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.numberStyle = .decimal
        formatter.usesGroupingSeparator = false
        formatter.minimumFractionDigits = 3
        formatter.maximumFractionDigits = 3
        formatter.decimalSeparator = "."
        if let s = formatter.string(from: value) {
            return s
        }
        // Fallback should not trigger for scale-3 values; keep deterministic.
        return value.stringValue
    }

    /// Quantize a raw kg double to the canonical 3-dp decimal string.
    static func quantizedKgString(fromKgDouble kg: Double) -> String {
        let decimal = Decimal(kg)
        let rounded = NSDecimalNumber(decimal: decimal)
            .rounding(accordingToBehavior: quantizationHandler)
        let rendered = render(rounded)
        // Negative-zero guard: "-0.000" is invalid downstream; normalize.
        if rendered == "-0.000" { return "0.000" }
        return rendered
    }
}

#if canImport(HealthKit)
extension SampleMapping {
    /// Map one HealthKit quantity sample to its DTO using the canonical unit.
    static func dto(
        from sample: HKQuantitySample, kiloGramUnit: HKUnit
    ) -> BodyMassSampleDTO {
        let kg = sample.quantity.doubleValue(for: kiloGramUnit)
        return BodyMassSampleDTO(
            sampleUUID: sample.uuid,
            valueKgDecimalString: quantizedKgString(fromKgDouble: kg),
            sampleStart: sample.startDate,
            sampleEnd: sample.endDate,
            sourceName: sample.sourceRevision.source.name,
            sourceBundleID: sample.sourceRevision.source.bundleIdentifier
        )
    }
}
#endif
