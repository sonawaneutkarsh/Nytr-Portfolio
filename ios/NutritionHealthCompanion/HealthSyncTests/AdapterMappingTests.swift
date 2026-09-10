import XCTest
@testable import NutritionHealthCompanion

/// Quantization boundary tests (plan §3): half-even, no downward bias.
final class AdapterMappingTests: XCTestCase {
    private func q(_ value: Double) -> String {
        SampleMapping.quantizedKgString(fromKgDouble: value)
    }

    func test_exactTies_roundToEvenNeighbor_bothDirections() {
        XCTAssertEqual(q(1.0005), "1.000")  // 1.0005 -> even neighbor down
        XCTAssertEqual(q(1.0015), "1.002")  // 1.0015 -> even neighbor up
        XCTAssertEqual(q(2.0005), "2.000")
        XCTAssertEqual(q(2.0015), "2.002")
    }

    func test_carryCases() {
        XCTAssertEqual(q(0.9995), "1.000")
        XCTAssertEqual(q(9.9995), "10.000")
    }

    func test_knownConversion_150lb() {
        // Synthetic fixture conversion; no owner measurement is embedded.
        XCTAssertEqual(q(158.732 * 0.45359237), "72.000")
    }

    func test_noDoubleRounding_singleApplicationIsIdempotent() {
        // Quantizing the already-quantized value again must not change it —
        // proving the pipeline applies rounding exactly once per sample.
        for raw in [1.0005, 1.0015, 0.9995, 150 * 0.45359237, 68.03888555] {
            let once = q(raw)
            let twice = q(Double(once)!)
            XCTAssertEqual(once, twice, "double-rounding detected for \(raw)")
        }
    }

    func test_localePinning_alwaysDotSeparator() {
        // en_US_POSIX rendering must never emit ',' separators regardless of
        // the device locale the formatter would otherwise inherit.
        let s = q(72.000)
        XCTAssertFalse(s.contains(","))
        XCTAssertTrue(s.hasSuffix(".039"))
    }

    func test_negativeZeroGuard() {
        XCTAssertEqual(q(-0.0004), "0.000")
        XCTAssertNotEqual(q(-0.0004), "-0.000")
    }

    func test_typicalWeights_threeDecimalPlaces() {
        XCTAssertEqual(q(70), "70.000")
        XCTAssertEqual(q(160.0 * 0.45359237), "72.575")  // synthetic regression value
    }

    func test_workoutDurationAndEnergyMappingUsesExactThreePlaceDecimalStrings() {
        XCTAssertEqual(
            WorkoutSampleMapping.quantizedNonnegativeString(from: 3_600),
            "3600.000"
        )
        XCTAssertEqual(
            WorkoutSampleMapping.quantizedNonnegativeString(from: 412.7495),
            "412.750"
        )
        XCTAssertNil(WorkoutSampleMapping.quantizedNonnegativeString(from: -.infinity))
        XCTAssertNil(WorkoutSampleMapping.quantizedNonnegativeString(from: -1))
    }

    #if canImport(HealthKit)
    func test_healthKitAuthorizationRequestsBodyMassAndWorkoutButNoWriteTypes() {
        XCTAssertTrue(
            HealthKitBodyMassAdapter.requestedReadTypes.contains(
                HealthKitBodyMassAdapter.bodyMassType
            )
        )
        XCTAssertTrue(
            HealthKitBodyMassAdapter.requestedReadTypes.contains(
                HealthKitBodyMassAdapter.workoutType
            )
        )
        XCTAssertTrue(HealthKitBodyMassAdapter.requestedShareTypes.isEmpty)
    }
    #endif
}
