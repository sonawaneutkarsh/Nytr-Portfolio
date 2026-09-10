import Foundation

#if canImport(HealthKit)
import HealthKit
#endif

enum WorkoutSampleMapping {
    private static let quantizationHandler = NSDecimalNumberHandler(
        roundingMode: .bankers,
        scale: 3,
        raiseOnExactness: false,
        raiseOnOverflow: true,
        raiseOnUnderflow: false,
        raiseOnDivideByZero: true
    )

    static func quantizedNonnegativeString(from value: Double) -> String? {
        guard value.isFinite, value >= 0 else { return nil }
        let rounded = NSDecimalNumber(decimal: Decimal(value))
            .rounding(accordingToBehavior: quantizationHandler)
        let formatter = NumberFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.numberStyle = .decimal
        formatter.usesGroupingSeparator = false
        formatter.minimumFractionDigits = 3
        formatter.maximumFractionDigits = 3
        formatter.decimalSeparator = "."
        let rendered = formatter.string(from: rounded) ?? rounded.stringValue
        return rendered == "-0.000" ? "0.000" : rendered
    }
}

#if canImport(HealthKit)
extension WorkoutSampleMapping {
    static func dto(from workout: HKWorkout) -> WorkoutSampleDTO {
        let energy = workout.totalEnergyBurned.map {
            $0.doubleValue(for: .kilocalorie())
        }
        let timezone = workout.metadata?[HKMetadataKeyTimeZone] as? String
        return WorkoutSampleDTO(
            sourceRecordID: workout.uuid,
            activityType: String(workout.workoutActivityType.rawValue),
            startedAt: workout.startDate,
            endedAt: workout.endDate,
            activeDurationSecondsDecimalString: quantizedNonnegativeString(
                from: workout.duration
            ),
            activeEnergyKcalDecimalString: energy.flatMap(quantizedNonnegativeString),
            timezoneIdentifier: timezone,
            sourceName: workout.sourceRevision.source.name,
            sourceBundleID: workout.sourceRevision.source.bundleIdentifier,
            sourceRevision: workout.sourceRevision.version
        )
    }
}
#endif
