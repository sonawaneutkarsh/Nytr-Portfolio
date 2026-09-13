import XCTest

@testable import NutritionHealthCompanion

final class TrainingWeightUnitTests: XCTestCase {
    func testDefaultToggleAndPersistence() {
        let name = "nytr.unit-test.\(UUID())"
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        XCTAssertEqual(TrainingWeightUnit.preferred(in: defaults), .lb)
        defaults.set("kg", forKey: TrainingWeightUnit.preferenceKey)
        XCTAssertEqual(TrainingWeightUnit.preferred(in: UserDefaults(suiteName: name)!), .kg)
    }
    func testHistoryAndCoachingUseCanonicalConversionWithoutDrift() {
        let canonical = "100"
        XCTAssertEqual(TrainingWeightUnit.lb.display(canonical), "220.5 lb")
        XCTAssertEqual(TrainingWeightUnit.kg.display(canonical), "100 kg")
        XCTAssertEqual(TrainingWeightUnit.lb.display("25", sourceUnit: "lb"), "25 lb")
        for _ in 0..<10 { XCTAssertEqual(TrainingWeightUnit.lb.display(canonical), "220.5 lb") }
        XCTAssertEqual(canonical, "100")
        XCTAssertEqual(TrainingWeightUnit.lb.display("20"), "44.1 lb")  // same assistance, not lifted resistance
        XCTAssertEqual(TrainingWeightUnit.kg.display("unknown", sourceUnit: "other"), "unknown other")
    }
}
