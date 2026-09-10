import XCTest
@testable import NutritionHealthCompanion

@MainActor
final class ProgressViewModelTests: XCTestCase {
    private final class Backend: BackendClient, @unchecked Sendable {
        private let lock = NSLock()
        var results: [Result<OwnerProgressResponse, Error>] = []
        private(set) var calls: [(Date, String)] = []

        func submitBatch(
            added _: [BodyMassSampleDTO], deletions _: [DeletedSampleDTO]
        ) async throws -> SyncResponse {
            throw BackendError.retryable("unused")
        }

        func fetchSyncStatus() async throws -> StatusResponse {
            throw BackendError.retryable("unused")
        }

        func fetchProgress(asOfDate: Date, timezone: String) async throws
            -> OwnerProgressResponse
        {
            try lock.withLock {
                calls.append((asOfDate, timezone))
                return try results.removeFirst().get()
            }
        }
    }

    private final class DelayedBackend: BackendClient, @unchecked Sendable {
        private let lock = NSLock()
        private var continuation: CheckedContinuation<OwnerProgressResponse, any Error>?

        var hasPendingRequest: Bool {
            lock.withLock { continuation != nil }
        }

        func submitBatch(
            added _: [BodyMassSampleDTO], deletions _: [DeletedSampleDTO]
        ) async throws -> SyncResponse {
            throw BackendError.retryable("unused")
        }

        func fetchSyncStatus() async throws -> StatusResponse {
            throw BackendError.retryable("unused")
        }

        func fetchProgress(asOfDate _: Date, timezone _: String) async throws
            -> OwnerProgressResponse
        {
            try await withCheckedThrowingContinuation { next in
                lock.withLock { continuation = next }
            }
        }

        func complete(with response: OwnerProgressResponse) {
            let pending = lock.withLock { () -> CheckedContinuation<OwnerProgressResponse, any Error>? in
                defer { continuation = nil }
                return continuation
            }
            pending?.resume(returning: response)
        }
    }

    private let requestDate = WireDate.date(fromISO8601: "2026-09-08T00:00:00Z")!

    func test_activateLoadsExplicitDayTimezoneAndExposesSparseState() async throws {
        let response = try Self.response()
        let backend = Backend()
        backend.results = [.success(response)]
        let viewModel = ProgressViewModel(
            backend: backend,
            requestDate: { self.requestDate },
            timezoneIdentifier: { "America/New_York" }
        )

        await viewModel.activate(subject: "owner-a")

        XCTAssertEqual(viewModel.phase, .result(response))
        XCTAssertEqual(backend.calls.count, 1)
        XCTAssertEqual(backend.calls[0].0, requestDate)
        XCTAssertEqual(backend.calls[0].1, "America/New_York")
        XCTAssertEqual(viewModel.selectedSummary?.windowDays, 7)
        XCTAssertEqual(viewModel.selectedSummary?.recordedProtein.partialRecordedDays, 1)
        XCTAssertEqual(response.body.trend28d.status, .stale)
        XCTAssertEqual(response.nutrition.days[0].recordedProtein.state, .partial)
    }

    func test_bodyAndNutritionSelectorsPreserveOrderingAndVisibleGaps() async throws {
        let base = try Self.response()
        let points = [
            ProgressBodyPoint(localDate: "2026-09-04", medianKg: "70.4", observationCount: 1),
            ProgressBodyPoint(localDate: "2026-06-20", medianKg: "71", observationCount: 1),
            ProgressBodyPoint(localDate: "2026-09-01", medianKg: "70.1", observationCount: 2),
            ProgressBodyPoint(localDate: "2026-09-03", medianKg: "70.3", observationCount: 1),
            ProgressBodyPoint(localDate: "2026-08-11", medianKg: "70.8", observationCount: 1),
        ]
        let body = ProgressBodyResponse(
            windowDays: 90,
            startDate: base.body.startDate,
            endDate: base.body.endDate,
            dailyMedians: points,
            trend28d: base.body.trend28d
        )
        let response = OwnerProgressResponse(
            policyVersion: base.policyVersion,
            asOfDate: base.asOfDate,
            timezone: base.timezone,
            body: body,
            goal: base.goal,
            nutrition: base.nutrition,
            limitations: base.limitations
        )
        let backend = Backend()
        backend.results = [.success(response)]
        let viewModel = ProgressViewModel(backend: backend)
        await viewModel.activate(subject: "owner-a")

        XCTAssertEqual(
            viewModel.selectedBodyPoints.map(\.localDate),
            ["2026-09-01", "2026-09-03", "2026-09-04"]
        )
        XCTAssertEqual(viewModel.bodyChartSegments.map { $0.map(\.localDate) }, [
            ["2026-09-01"], ["2026-09-03", "2026-09-04"],
        ])

        viewModel.bodyWindow = .days90
        XCTAssertEqual(viewModel.selectedBodyPoints.first?.localDate, "2026-06-20")
        XCTAssertTrue(viewModel.selectedBodyPoints.contains { $0.localDate == "2026-08-11" })
        viewModel.nutritionWindow = .days28
        XCTAssertEqual(viewModel.selectedSummary?.windowDays, 28)
    }

    func test_signOutAndOwnerChangeCannotPublishStaleAsyncResult() async throws {
        let response = try Self.response()
        let backend = DelayedBackend()
        let viewModel = ProgressViewModel(backend: backend)

        let first = Task { await viewModel.activate(subject: "owner-a") }
        while !backend.hasPendingRequest { await Task.yield() }
        await viewModel.activate(subject: "owner-b")
        backend.complete(with: response)
        await first.value
        XCTAssertEqual(viewModel.phase, .idle)

        let second = Task { await viewModel.activate(subject: "owner-b") }
        while !backend.hasPendingRequest { await Task.yield() }
        viewModel.resetForSignOut()
        backend.complete(with: response)
        await second.value
        XCTAssertEqual(viewModel.phase, .signedOut)
    }

    func test_errorAndUnauthorizedFailSafely() async throws {
        let failedBackend = Backend()
        failedBackend.results = [.failure(BackendError.retryable("offline"))]
        let failed = ProgressViewModel(backend: failedBackend)
        await failed.activate(subject: "owner-a")
        XCTAssertEqual(failed.phase, .error("Progress is temporarily unavailable."))

        let unauthorizedBackend = Backend()
        unauthorizedBackend.results = [.failure(BackendError.unauthorized)]
        var signedOut = false
        let unauthorized = ProgressViewModel(
            backend: unauthorizedBackend,
            onUnauthorized: { signedOut = true }
        )
        await unauthorized.activate(subject: "owner-a")
        XCTAssertEqual(unauthorized.phase, .signedOut)
        XCTAssertTrue(signedOut)
    }

    private static func response() throws -> OwnerProgressResponse {
        try JSONDecoder().decode(OwnerProgressResponse.self, from: ProgressClientTests.payload)
    }
}
