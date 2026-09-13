import XCTest

@testable import NutritionHealthCompanion

final class NytrNumberFormatTests: XCTestCase {
    func testMilliliterUnitUsesStandardDisplayCapitalization() {
        XCTAssertEqual(NytrNumberFormat.unitLabel("ml"), "mL")
        XCTAssertEqual(NytrNumberFormat.unitLabel("mL"), "mL")
        XCTAssertEqual(NytrNumberFormat.unitLabel("g"), "g")
        XCTAssertEqual(NytrNumberFormat.unitLabel("servings"), "servings")
    }

    func testWholeDailyValuesUseDecimalHalfUpWithoutChangingInput() {
        XCTAssertEqual(NytrNumberFormat.whole("417.8571428571462"), "418")
        XCTAssertEqual(NytrNumberFormat.whole("657.8571428571462"), "658")
        XCTAssertEqual(NytrNumberFormat.whole("71.271428571428538"), "71")
        XCTAssertEqual(NytrNumberFormat.whole("1536.5"), "1,537")
        XCTAssertEqual(NytrNumberFormat.whole("-2.5"), "-3")
    }

    func testDetailedNutritionKeepsOnlySensiblePrecision() {
        XCTAssertEqual(NytrNumberFormat.detail("47.714285714"), "47.7")
        XCTAssertEqual(NytrNumberFormat.detail("8.0"), "8")
        XCTAssertEqual(NytrNumberFormat.detail("1567"), "1,567")
        XCTAssertNil(NytrNumberFormat.whole(nil))
    }

    func testOwnerLocalDayDoesNotAdvanceAtUtcMidnight() {
        guard
            let instant = ISO8601DateFormatter().date(from: "2026-09-13T00:30:00Z"),
            let eastern = TimeZone(identifier: "America/New_York")
        else { return XCTFail("Expected valid fixed date and timezone") }
        let requestDate = WireDay.localRequestDate(from: instant, timeZone: eastern)
        XCTAssertEqual(WireDay.string(from: requestDate), "2026-09-12")
    }
}
