import XCTest
@testable import NutritionHealthCompanion

final class TrainingSourceAuthorityCopyTests: XCTestCase {
    func testHealthSyncCopyDescribesOccurrenceLaneWithoutDetailedTraining() {
        XCTAssertTrue(TrainingSourceAuthorityCopy.healthSync.contains("Apple Health"))
        XCTAssertTrue(TrainingSourceAuthorityCopy.healthSync.contains("workout occurrences"))
        XCTAssertTrue(TrainingSourceAuthorityCopy.healthSync.contains("does not create"))
    }

    func testTrainingCopyNamesHevyAndRejectsHealthKitPopulation() {
        XCTAssertTrue(TrainingSourceAuthorityCopy.training.contains("Hevy"))
        XCTAssertTrue(TrainingSourceAuthorityCopy.training.contains("Apple Health"))
        XCTAssertTrue(TrainingSourceAuthorityCopy.training.contains("do not populate"))
    }
}
