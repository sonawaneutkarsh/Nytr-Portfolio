import XCTest
@testable import NutritionHealthCompanion

@MainActor
final class AIReviewViewModelTests: XCTestCase {
    private final class ReviewBackend: BackendClient, @unchecked Sendable {
        private let lock = NSLock()
        var results: [Result<AIReviewResponse, Error>] = []
        var shouldBlock = false
        private var continuation: CheckedContinuation<Void, Never>?
        private var releaseRequested = false
        private(set) var calls: [(Date, String)] = []

        func submitBatch(
            added _: [BodyMassSampleDTO],
            deletions _: [DeletedSampleDTO]
        ) async throws -> SyncResponse {
            throw BackendError.retryable("unused")
        }

        func fetchSyncStatus() async throws -> StatusResponse {
            throw BackendError.retryable("unused")
        }

        func generateAIReview(asOfDate: Date, timezone: String) async throws
            -> AIReviewResponse
        {
            let (result, block) = lock.withLock {
                calls.append((asOfDate, timezone))
                let result = results.removeFirst()
                let block = shouldBlock
                shouldBlock = false
                return (result, block)
            }
            if block {
                await withCheckedContinuation { waiting in
                    let resumeNow = lock.withLock {
                        if releaseRequested {
                            releaseRequested = false
                            return true
                        }
                        continuation = waiting
                        return false
                    }
                    if resumeNow { waiting.resume() }
                }
            }
            return try result.get()
        }

        func release() {
            let waiting = lock.withLock {
                let waiting = continuation
                continuation = nil
                if waiting == nil { releaseRequested = true }
                return waiting
            }
            waiting?.resume()
        }

        var callCount: Int { lock.withLock { calls.count } }
    }

    private let fixedDate = Date(timeIntervalSince1970: 1_788_854_400)

    private func makeViewModel(
        backend: ReviewBackend,
        onUnauthorized: @escaping @MainActor () -> Void = {}
    ) -> AIReviewViewModel {
        AIReviewViewModel(
            backend: backend,
            requestDate: { self.fixedDate },
            timezoneIdentifier: { "America/New_York" },
            onUnauthorized: onUnauthorized
        )
    }

    func testActivationIsExplicitTriggerOnly() async {
        let backend = ReviewBackend()
        let viewModel = makeViewModel(backend: backend)

        viewModel.activate(subject: "owner-a")
        await Task.yield()

        XCTAssertEqual(viewModel.phase, .idle)
        XCTAssertEqual(backend.callCount, 0)
    }

    func testGenerateShowsLoadingThenSuccessAndUsesOneRequest() async {
        let backend = ReviewBackend()
        backend.results = [.success(Self.availableResponse)]
        backend.shouldBlock = true
        let viewModel = makeViewModel(backend: backend)
        viewModel.activate(subject: "owner-a")

        let task = Task { await viewModel.generate() }
        await waitUntil { backend.callCount == 1 }
        XCTAssertEqual(viewModel.phase, .loading)
        XCTAssertTrue(viewModel.isRequesting)
        backend.release()
        await task.value

        XCTAssertEqual(viewModel.phase, .result(Self.availableResponse))
        XCTAssertFalse(viewModel.isRequesting)
        XCTAssertEqual(backend.calls.first?.1, "America/New_York")
    }

    func testUnavailableRequiresExplicitRetryAndLeavesDeterministicFactsVisible() async {
        let backend = ReviewBackend()
        backend.results = [
            .success(Self.unavailableResponse),
            .success(Self.availableResponse),
        ]
        let viewModel = makeViewModel(backend: backend)
        viewModel.activate(subject: "owner-a")

        await viewModel.generate()
        XCTAssertEqual(viewModel.phase, .unavailable(Self.unavailableResponse))
        XCTAssertEqual(backend.callCount, 1)

        await Task.yield()
        XCTAssertEqual(backend.callCount, 1)
        await viewModel.generate()
        XCTAssertEqual(viewModel.phase, .result(Self.availableResponse))
        XCTAssertEqual(backend.callCount, 2)
    }

    func testProviderErrorDoesNotInvalidateReviewSurface() async {
        let backend = ReviewBackend()
        backend.results = [.failure(BackendError.retryable("provider unavailable"))]
        let viewModel = makeViewModel(backend: backend)
        viewModel.activate(subject: "owner-a")

        await viewModel.generate()

        XCTAssertEqual(viewModel.phase, .error("Nytr Review is temporarily unavailable."))
        XCTAssertFalse(viewModel.isRequesting)
    }

    func testSignOutClearsReviewAndUnauthorizedNotifiesSession() async {
        let backend = ReviewBackend()
        backend.results = [.success(Self.availableResponse), .failure(BackendError.unauthorized)]
        var unauthorizedCount = 0
        let viewModel = makeViewModel(backend: backend) { unauthorizedCount += 1 }
        viewModel.activate(subject: "owner-a")
        await viewModel.generate()
        XCTAssertEqual(viewModel.phase, .result(Self.availableResponse))

        viewModel.resetForSignOut()
        XCTAssertEqual(viewModel.phase, .signedOut)
        viewModel.activate(subject: "owner-a")
        await viewModel.generate()

        XCTAssertEqual(viewModel.phase, .signedOut)
        XCTAssertEqual(unauthorizedCount, 1)
    }

    func testStaleOwnerResponseIsIgnored() async {
        let backend = ReviewBackend()
        backend.results = [.success(Self.availableResponse)]
        backend.shouldBlock = true
        let viewModel = makeViewModel(backend: backend)
        viewModel.activate(subject: "owner-a")

        let task = Task { await viewModel.generate() }
        await waitUntil { backend.callCount == 1 }
        viewModel.activate(subject: "owner-b")
        backend.release()
        await task.value

        XCTAssertEqual(viewModel.phase, .idle)
        XCTAssertFalse(viewModel.isRequesting)
    }

    private func waitUntil(_ predicate: @escaping () -> Bool) async {
        for _ in 0..<100 where !predicate() { await Task.yield() }
    }

    private static let snapshot = AIReviewSnapshotDTO(
        snapshotVersion: "owner-ai-review-snapshot.v1",
        asOfDate: "2026-09-08",
        timezone: "America/New_York",
        goal: AIReviewGoalDTO(mode: "gain", bandStatus: "unavailable"),
        targets: AIReviewTargetsDTO(
            caloriesKcal: "2200",
            caloriesKind: "target",
            proteinG: "120",
            proteinKind: "floor"
        ),
        todayRecorded: AIReviewTodayRecordedDTO(
            itemCount: 1,
            completeness: "partial",
            caloriesKcal: "450",
            proteinG: nil,
            reasonCodes: ["protein_unknown"],
            authorities: ["partial"]
        ),
        bodyTrend: AIReviewBodyTrendDTO(
            status: "stale",
            latestMeasurementAgeDays: 15,
            representedDayCount: 6,
            coverageSpanDays: 18
        ),
        recordedNutritionProgress: AIReviewNutritionProgressDTO(
            daysWithRecords7d: 2,
            daysWithRecords28d: 4,
            calorieQuantifiedDays7d: 1,
            proteinQuantifiedDays7d: 0,
            includesEstimates7d: true
        ),
        nextMeal: AIReviewNextMealDTO(
            status: "unavailable",
            reasonCodes: ["nutrition_evidence_incomplete"]
        ),
        limitations: ["recorded_events_do_not_prove_complete_intake"]
    )

    private static let availableResponse = AIReviewResponse(
        status: "available",
        promptVersion: "owner-ai-review-prompt.v1",
        snapshot: snapshot,
        review: AIReviewContentDTO(
            summary: "Recorded evidence is incomplete.",
            attentionItems: ["Weight evidence is stale."],
            evidenceNotes: ["Only recorded evidence is included."],
            limitations: ["This explanation is non-authoritative."]
        ),
        failureCode: nil,
        authorityNotice: "AI explanation; deterministic calculations remain authoritative."
    )

    private static let unavailableResponse = AIReviewResponse(
        status: "unavailable",
        promptVersion: "owner-ai-review-prompt.v1",
        snapshot: snapshot,
        review: nil,
        failureCode: "ai_timeout",
        authorityNotice: "AI explanation; deterministic calculations remain authoritative."
    )
}
