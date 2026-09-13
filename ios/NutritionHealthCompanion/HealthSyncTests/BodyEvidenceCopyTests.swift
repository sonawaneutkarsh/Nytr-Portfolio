import XCTest

@testable import NutritionHealthCompanion

final class BodyEvidenceCopyTests: XCTestCase {
    func testAssessmentCopyKeepsTentativeEvidenceTentative() {
        for code in ["mixed_gain_evidence", "mixed_loss_evidence", "mixed_maintenance_evidence"] {
            XCTAssertTrue(BodyEvidenceCopy.phaseReason(code).contains("Keep observing"))
        }
        XCTAssertEqual(
            BodyEvidenceCopy.phaseReason("insufficient_weight_or_waist_evidence"),
            "More current weight and waist measurements are needed."
        )
    }

    func testUnknownAssessmentNeverDisplaysInternalCodeOrClaimsSuccess() {
        XCTAssertEqual(BodyEvidenceCopy.phaseReason("future_internal_code"), "This assessment needs further review.")
    }
}
