import Foundation

// Minimal XCTest compatibility shim — HARNESS-ONLY, typecheck purposes.
//
// The non-Xcode validation harness (see Harness/run_harness.sh) runs under
// Command Line Tools where the real XCTest module is unavailable. This shim
// exposes exactly the API surface used by HealthSyncTests so the ENTIRE test
// suite can be type-checked (not executed) against production sources.
//
// It is NOT a fake test runner: nothing here executes test logic. Real test
// execution happens in Xcode via the HealthSyncTests bundle target.
//
// Assertions mirror real XCTest by being MODULE-LEVEL functions (that is why
// they never require explicit `self.` from escaping closures in test bodies).

// Real XCTest publicly imports Foundation, so test sources may reference
// Foundation symbols (Data, URLSession, ...) relying on that transitive
// export. Mirror the surface exactly.
@_exported import Foundation

open class XCTestCase {
    public init() {}

    // Lifecycle hooks overridden by test suites (no-op in the harness).
    open func setUp() {}
    open func tearDown() {}
}

public func XCTAssertEqual<T: Equatable>(
    _ a: T, _ b: T,
    _ message: @autoclosure () -> String = "",
    file: StaticString = #filePath, line: UInt = #line
) {}

public func XCTAssertEqual<Element: Equatable>(
    _ a: [Element], _ b: [Element],
    _ message: @autoclosure () -> String = "",
    file: StaticString = #filePath, line: UInt = #line
) {}

public func XCTAssertNotEqual<T: Equatable>(
    _ a: T, _ b: T,
    _ message: @autoclosure () -> String = "",
    file: StaticString = #filePath, line: UInt = #line
) {}

public func XCTAssertTrue(
    _ expression: @autoclosure () throws -> Bool,
    _ message: @autoclosure () -> String = "",
    file: StaticString = #filePath, line: UInt = #line
) {}

public func XCTAssertFalse(
    _ expression: @autoclosure () throws -> Bool,
    _ message: @autoclosure () -> String = "",
    file: StaticString = #filePath, line: UInt = #line
) {}

public func XCTAssertNil<T>(
    _ expression: @autoclosure () throws -> T?,
    _ message: @autoclosure () -> String = "",
    file: StaticString = #filePath, line: UInt = #line
) {}

public func XCTAssertNotNil<T>(
    _ expression: @autoclosure () throws -> T?,
    _ message: @autoclosure () -> String = "",
    file: StaticString = #filePath, line: UInt = #line
) {}

public func XCTFail(
    _ message: String = "",
    file: StaticString = #filePath, line: UInt = #line
) {}
