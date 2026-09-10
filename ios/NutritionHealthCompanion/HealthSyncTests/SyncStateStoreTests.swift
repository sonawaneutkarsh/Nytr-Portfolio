import XCTest
@testable import NutritionHealthCompanion

final class SyncStateStoreTests: XCTestCase {
    func test_roundTrip() throws {
        let defaults = UserDefaults(suiteName: #function)!
        defer { defaults.removePersistentDomain(forName: #function) }
        let store = UserDefaultsDurableStore(defaults: defaults)

        let state = DurableSyncState(
            durableAnchorData: Data("anchor".utf8),
            lastSuccessfulSync: Date(timeIntervalSince1970: 1_760_000_000),
            lastAttempt: Date(timeIntervalSince1970: 1_760_000_100),
            lastErrorDescription: nil
        )
        try store.persist(state)
        let loaded = try store.load()
        XCTAssertEqual(loaded, state)
    }

    func test_corruptState_recoversAsAbsent() throws {
        let defaults = UserDefaults(suiteName: #function)!
        defer { defaults.removePersistentDomain(forName: #function) }
        let key = "health.sync.durableState.v\(DurableSyncState.schemaVersion)"
        defaults.set(Data("not-json".utf8), forKey: key)
        let store = UserDefaultsDurableStore(defaults: defaults)

        let loaded = try store.load()
        XCTAssertNil(loaded, "corrupt state must be treated as absent (bounded re-import)")
    }

    func test_emptyStore_loadsNil() throws {
        let defaults = UserDefaults(suiteName: #function)!
        defer { defaults.removePersistentDomain(forName: #function) }
        let store = UserDefaultsDurableStore(defaults: defaults)
        XCTAssertNil(try store.load())
    }

    func test_workoutAnchorHasDistinctNamespaceAndBodyMassKeyIsFrozen() throws {
        let suite = #function
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let body = UserDefaultsDurableStore(defaults: defaults)
        let workout = UserDefaultsDurableStore(defaults: defaults, namespace: .workout)
        let bodyState = DurableSyncState(
            durableAnchorData: Data("body".utf8),
            lastSuccessfulSync: nil, lastAttempt: nil, lastErrorDescription: nil
        )
        let workoutState = DurableSyncState(
            durableAnchorData: Data("workout".utf8),
            lastSuccessfulSync: nil, lastAttempt: nil, lastErrorDescription: nil
        )
        try body.persist(bodyState)
        try workout.persist(workoutState)

        XCTAssertEqual(try body.load(), bodyState)
        XCTAssertEqual(try workout.load(), workoutState)
        XCTAssertNotNil(
            defaults.data(
                forKey: "health.sync.durableState.v\(DurableSyncState.schemaVersion)"
            )
        )
        XCTAssertNotNil(
            defaults.data(
                forKey: "health.sync.workout.durableState.v\(DurableSyncState.schemaVersion)"
            )
        )
    }
}

final class PolicyFormulaTests: XCTestCase {
    /// The canonical formula — identical everywhere (plan §5):
    /// effectiveStart = max(now - 365 days, earliestAuthorizedSampleDate).
    func test_effectiveStart_isMaxOfLookbackAndEarliestAuthorized() {
        let now = Date(timeIntervalSince1970: 1_760_000_000)
        let lookbackStart = now.addingTimeInterval(-365 * 24 * 3600)

        // Full history grant: lookback wins (later date).
        XCTAssertEqual(
            HealthSyncPolicy.effectiveHistoricalStart(now: now, earliestAuthorized: .distantPast),
            lookbackStart
        )
        // Limited-window grant: Apple's floor clamps tighter.
        let limited = now.addingTimeInterval(-30 * 24 * 3600)
        XCTAssertEqual(
            HealthSyncPolicy.effectiveHistoricalStart(now: now, earliestAuthorized: limited),
            limited
        )
        // Earliest authorized BEFORE lookback window: lookback still governs.
        XCTAssertEqual(
            HealthSyncPolicy.effectiveHistoricalStart(now: now, earliestAuthorized: lookbackStart.addingTimeInterval(-86400)),
            lookbackStart
        )
    }

    /// M11B SDK-compatibility regression: the installed HealthKit SDK does not
    /// declare getEarliestAuthorizedSampleDate(for:), so the real adapter
    /// reports .distantPast ("boundary unknown"). The formula must treat that
    /// exactly like a full-history grant — full 365-day product lookback,
    /// never wider, never an error — and HealthKit itself remains the only
    /// authority over which samples are actually returned.
    func test_unknownAuthorizationBoundary_fallsBackToFullProductLookback() {
        let now = Date(timeIntervalSince1970: 1_760_000_000)
        let lookbackStart = now.addingTimeInterval(-365 * 24 * 3600)

        let effective = HealthSyncPolicy.effectiveHistoricalStart(
            now: now, earliestAuthorized: .distantPast
        )

        XCTAssertEqual(effective, lookbackStart)
        // The unknown boundary must never pull the start later than the
        // product lookback (no silent narrowing) nor earlier than it.
        XCTAssertTrue(effective <= now)
        XCTAssertTrue(effective >= lookbackStart)
    }
}
