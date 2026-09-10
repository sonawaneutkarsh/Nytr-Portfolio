import XCTest
@testable import NutritionHealthCompanion

@MainActor
final class NutritionHistoryViewModelTests: XCTestCase {
    private final class Backend: BackendClient, @unchecked Sendable {
        private let lock = NSLock()
        var results: [Result<NutritionHistory7DayResponse, Error>] = []
        private(set) var calls: [(Date, String)] = []

        func submitBatch(
            added _: [BodyMassSampleDTO], deletions _: [DeletedSampleDTO]
        ) async throws -> SyncResponse {
            throw BackendError.retryable("unused")
        }

        func fetchSyncStatus() async throws -> StatusResponse {
            throw BackendError.retryable("unused")
        }

        func fetchNutritionHistory(endDate: Date, timezone: String) async throws
            -> NutritionHistory7DayResponse
        {
            try lock.withLock {
                calls.append((endDate, timezone))
                return try results.removeFirst().get()
            }
        }
    }

    private let endDate = Date(timeIntervalSince1970: 1_788_768_000)

    func test_activate_loads_history_with_explicit_day_and_timezone() async {
        let backend = Backend()
        backend.results = [.success(Self.history)]
        let viewModel = NutritionHistoryViewModel(
            backend: backend,
            requestDate: { self.endDate },
            timezoneIdentifier: { "America/New_York" }
        )

        await viewModel.activate(subject: "owner-a")

        XCTAssertEqual(viewModel.phase, .result(Self.history))
        XCTAssertEqual(backend.calls.count, 1)
        XCTAssertEqual(backend.calls[0].0, endDate)
        XCTAssertEqual(backend.calls[0].1, "America/New_York")
    }

    func test_same_subject_activation_doesNotRefetchButRefreshDoes() async {
        let backend = Backend()
        backend.results = [.success(Self.history), .success(Self.history)]
        let viewModel = NutritionHistoryViewModel(backend: backend)

        await viewModel.activate(subject: "owner-a")
        await viewModel.activate(subject: "owner-a")
        XCTAssertEqual(backend.calls.count, 1)

        await viewModel.refresh()
        XCTAssertEqual(backend.calls.count, 2)
    }

    func test_failureIsSafeAndUnauthorizedSignsOut() async {
        let failedBackend = Backend()
        failedBackend.results = [.failure(BackendError.retryable("offline"))]
        let failed = NutritionHistoryViewModel(backend: failedBackend)
        await failed.activate(subject: "owner-a")
        XCTAssertEqual(failed.phase, .error("Nutrition history is temporarily unavailable."))

        let unauthorizedBackend = Backend()
        unauthorizedBackend.results = [.failure(BackendError.unauthorized)]
        var signedOut = false
        let unauthorized = NutritionHistoryViewModel(
            backend: unauthorizedBackend,
            onUnauthorized: { signedOut = true }
        )
        await unauthorized.activate(subject: "owner-a")
        XCTAssertEqual(unauthorized.phase, .signedOut)
        XCTAssertTrue(signedOut)
    }

    private static let history = NutritionHistory7DayResponse(
        startDate: "2026-09-01",
        endDate: "2026-09-07",
        timezone: "America/New_York",
        days: [
            NutritionHistoryDayResponse(
                localDate: "2026-09-07",
                timezone: "America/New_York",
                target: nil,
                consumedEventCount: 0,
                knownCaloriesConsumed: nil,
                knownProteinGConsumed: nil,
                remainingKnownCalories: nil,
                remainingKnownProteinG: nil,
                nutritionCompleteness: .unavailable,
                nutritionAuthorities: [],
                unknownNutrients: [],
                consumedItems: [],
                reasonCodes: ["no_consumption", "target_unavailable"],
                targetStatus: .unavailable,
                calorieAdherence: .unavailable,
                proteinAdherence: .unavailable
            )
        ],
        summary: NutritionHistorySummaryResponse(
            daysWithConsumption: 0,
            daysComplete: 0,
            daysPartial: 0,
            daysUnavailable: 7,
            daysTargetAvailable: 0,
            daysTargetChanged: 0,
            knownCaloriesTotal: nil,
            knownProteinGTotal: nil
        )
    )
}
