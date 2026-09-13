import XCTest

#if canImport(FoundationModels)
    import FoundationModels
#endif

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

        func fetchReviewSnapshot(asOfDate: Date, timezone: String) async throws
            -> OnDeviceReviewSnapshot
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
            return OnDeviceReviewSnapshot(
                snapshot: try result.get().snapshot,
                modelInput: ReviewModelInput(
                    goal: "gain", weightEvidence: "stale", nutritionEvidence: "recorded_partial", calorieTargetAvailable: true,
                    proteinTargetAvailable: true, nextMeal: "unavailable", includesEstimates: true,
                    limitation: "Only recorded evidence is known."))
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

        XCTAssertEqual(viewModel.phase, .idle)
        XCTAssertEqual(viewModel.evidence?.snapshot, Self.snapshot)
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
        XCTAssertEqual(viewModel.evidence?.snapshot, Self.snapshot)
        XCTAssertEqual(backend.callCount, 1)

        await Task.yield()
        XCTAssertEqual(backend.callCount, 1)
        await viewModel.generate()
        XCTAssertEqual(viewModel.phase, .idle)
        XCTAssertEqual(viewModel.evidence?.snapshot, Self.snapshot)
        XCTAssertEqual(backend.callCount, 2)
    }

    func testProviderErrorDoesNotInvalidateReviewSurface() async {
        let backend = ReviewBackend()
        backend.results = [.failure(BackendError.retryable("provider unavailable"))]
        let viewModel = makeViewModel(backend: backend)
        viewModel.activate(subject: "owner-a")

        await viewModel.generate()

        XCTAssertEqual(viewModel.phase, .error("Current evidence could not be refreshed. Try again."))
        XCTAssertFalse(viewModel.isRequesting)
    }

    func testSignOutClearsReviewAndUnauthorizedNotifiesSession() async {
        let backend = ReviewBackend()
        backend.results = [.success(Self.availableResponse), .failure(BackendError.unauthorized)]
        var unauthorizedCount = 0
        let viewModel = makeViewModel(backend: backend) { unauthorizedCount += 1 }
        viewModel.activate(subject: "owner-a")
        await viewModel.generate()
        XCTAssertEqual(viewModel.phase, .idle)
        XCTAssertEqual(viewModel.evidence?.snapshot, Self.snapshot)

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

    private final class LocalReviewer: OnDeviceReviewing {
        var isAvailable = true
        var failure: LocalReviewFailureCategory?
        var reportedUnavailableCategory: LocalReviewFailureCategory = .modelUnavailable
        var calls = 0
        var suspends = false
        var unavailableCategory: LocalReviewFailureCategory? {
            isAvailable ? nil : reportedUnavailableCategory
        }
        func explain(_ input: ReviewModelInput) async throws -> AIReviewContentDTO {
            calls += 1
            if suspends { try await Task.sleep(for: .seconds(60)) }
            if let failure { throw LocalReviewError.classified(failure) }
            return try AppleOnDeviceReview.validate(
                summary: "Evidence is partial.", keyFindings: "Weight evidence is stale.",
                considerations: "Consider only recorded evidence.",
                limitations: "Unlogged intake is unknown.", input: input)
        }
    }

    func testOnDeviceUnavailableAndFailurePreserveFactsWithoutAnotherBackendCall() async {
        let backend = ReviewBackend()
        backend.results = [.success(Self.availableResponse)]
        let local = LocalReviewer()
        local.isAvailable = false
        let vm = AIReviewViewModel(backend: backend, reviewer: local)
        vm.activate(subject: "owner")
        await vm.generate()
        await vm.explainOnDevice()
        XCTAssertEqual(local.calls, 0)
        XCTAssertEqual(vm.localMessage, "AI Review requires Apple Intelligence")
        XCTAssertEqual(vm.localFailureCategory, .modelUnavailable)
        local.reportedUnavailableCategory = .modelNotReady
        await vm.explainOnDevice()
        XCTAssertEqual(vm.localFailureCategory, .modelNotReady)
        XCTAssertTrue(vm.localMessage?.contains("still getting ready") == true)
        local.isAvailable = true
        local.failure = .outputValidationFailed
        await vm.explainOnDevice()
        XCTAssertEqual(vm.evidence?.snapshot, Self.snapshot)
        XCTAssertEqual(vm.localFailureCategory, .outputValidationFailed)
        XCTAssertEqual(backend.callCount, 1)
        local.failure = nil
        await vm.explainOnDevice()
        guard case .result = vm.phase else { return XCTFail("Expected local explanation") }
        XCTAssertNil(vm.localFailureCategory)
        XCTAssertEqual(backend.callCount, 1)
        vm.resetForSignOut()
        XCTAssertNil(vm.evidence)
    }

    func testStructuredReviewRejectsOversizeEmptyAndURLFields() throws {
        let input = ReviewModelInput(
            goal: "gain", weightEvidence: "ready", nutritionEvidence: "recorded_partial",
            calorieTargetAvailable: true, proteinTargetAvailable: true,
            nextMeal: "recommended", includesEstimates: false,
            limitation: "Recorded evidence only."
        )
        for summary in ["", String(repeating: "a", count: 521), "See https://example.com"] {
            do {
                _ = try AppleOnDeviceReview.validate(
                    summary: summary, keyFindings: "Evidence",
                    considerations: "Consider recorded evidence.", limitations: "Unknown",
                    input: input)
                XCTFail("Invalid generated content must be rejected")
            } catch LocalReviewError.invalidResponse {}
        }
    }

    func testRicherReviewAllowsOnlyExactSuppliedNumericFacts() throws {
        var input = ReviewModelInput(
            goal: "gain", weightEvidence: "ready",
            nutritionEvidence: "recorded_complete", calorieTargetAvailable: true,
            proteinTargetAvailable: true, nextMeal: "recommended",
            includesEstimates: false, limitation: "Recorded evidence only."
        )
        input.calorieTargetKcal = "2200"
        input.recordedCaloriesKcal = "450"
        input.proteinTargetG = "120"
        input.daysWithRecords7d = 3
        input.daysWithRecords28d = 6
        input.weightWeeklyRateKg = "-0.2"
        let accepted = try AppleOnDeviceReview.validate(
            summary: "You recorded 450 kcal against a 2200 kcal target.",
            keyFindings: "Your protein target is 120 g and the weekly rate is -0.2 kg.",
            considerations: "You have records on 3 of the 7-day window and 6 of the 28-day window.",
            limitations: "Unlogged intake remains unknown.", input: input
        )
        XCTAssertTrue(accepted.summary.contains("2200"))
        do {
            _ = try AppleOnDeviceReview.validate(
                summary: "You recorded 451 kcal.", keyFindings: "Facts",
                considerations: "Consider recorded evidence.",
                limitations: "Unlogged intake remains unknown.", input: input
            )
            XCTFail("An invented number must be rejected")
        } catch LocalReviewError.invalidResponse {}
    }

    func testRecordedConsumptionInvalidatesStaleEvidenceAndRequiresExplicitRefresh() async {
        let backend = ReviewBackend()
        backend.results = [.success(Self.availableResponse)]
        let viewModel = makeViewModel(backend: backend)
        viewModel.activate(subject: "owner-a")
        await viewModel.generate()
        XCTAssertNotNil(viewModel.evidence)

        viewModel.invalidateEvidenceAfterConsumption()

        XCTAssertNil(viewModel.evidence)
        XCTAssertEqual(backend.callCount, 1)
        XCTAssertTrue(viewModel.localMessage?.contains("Review current evidence again") == true)
    }

    func testLocalGenerationTimeoutAndCancellationLeaveDeterministicReviewUsable() async {
        let backend = ReviewBackend()
        backend.results = [.success(Self.availableResponse)]
        let local = LocalReviewer()
        local.suspends = true
        let vm = AIReviewViewModel(backend: backend, reviewer: local, generationTimeout: .milliseconds(20))
        vm.activate(subject: "owner")
        await vm.generate()
        await vm.explainOnDevice()
        XCTAssertFalse(vm.isRequesting)
        XCTAssertEqual(vm.evidence?.snapshot, Self.snapshot)
        XCTAssertTrue(vm.localMessage?.contains("too long") == true)
        XCTAssertEqual(vm.localFailureCategory, .generationTimeout)
        let pending = Task { await vm.explainOnDevice() }
        await waitUntil { vm.isGenerating }
        vm.cancelLocalReview()
        await pending.value
        XCTAssertFalse(vm.isGenerating)
        XCTAssertEqual(vm.localFailureCategory, .cancelled)
        XCTAssertEqual(vm.evidence?.snapshot, Self.snapshot)
        XCTAssertEqual(backend.callCount, 1)
    }

    func testEveryLocalFailureCategoryStaysLocalAndPreservesDeterministicEvidence() async {
        let categories: [LocalReviewFailureCategory] = [
            .modelNotReady, .unsupportedLocale, .generationRefused,
            .structuredGenerationFailed, .outputValidationFailed,
            .unknownLocalModelFailure,
        ]
        for category in categories {
            let backend = ReviewBackend()
            backend.results = [.success(Self.availableResponse)]
            let local = LocalReviewer()
            local.failure = category
            let vm = AIReviewViewModel(backend: backend, reviewer: local)
            vm.activate(subject: "owner")
            await vm.generate()
            await vm.explainOnDevice()

            XCTAssertEqual(vm.localFailureCategory, category)
            XCTAssertEqual(vm.evidence?.snapshot, Self.snapshot)
            XCTAssertEqual(backend.callCount, 1)
            XCTAssertFalse(vm.isRequesting)
        }
    }

    #if canImport(FoundationModels)
        @available(iOS 26.0, macOS 26.0, *)
        func testInstalledFoundationModelsErrorsMapWithoutReadingSensitiveContext() {
            let context = LanguageModelSession.GenerationError.Context(debugDescription: "ignored")
            XCTAssertEqual(
                AppleOnDeviceReview.failureCategory(for: .assetsUnavailable(context)),
                .modelNotReady
            )
            XCTAssertEqual(
                AppleOnDeviceReview.failureCategory(for: .unsupportedLanguageOrLocale(context)),
                .unsupportedLocale
            )
            XCTAssertEqual(
                AppleOnDeviceReview.failureCategory(for: .guardrailViolation(context)),
                .generationRefused
            )
            XCTAssertEqual(
                AppleOnDeviceReview.failureCategory(for: .decodingFailure(context)),
                .structuredGenerationFailed
            )
        }
    #endif

    private func waitUntil(_ predicate: @escaping () -> Bool) async {
        for _ in 0..<100 where !predicate() { await Task.yield() }
    }

    static let snapshot = AIReviewSnapshotDTO(
        snapshotVersion: "owner-ai-review-snapshot.v2",
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
        promptVersion: "owner-ai-review-prompt.v2",
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
        promptVersion: "owner-ai-review-prompt.v2",
        snapshot: snapshot,
        review: nil,
        failureCode: "ai_timeout",
        authorityNotice: "AI explanation; deterministic calculations remain authoritative."
    )

    func testEveryTypedFailureHasPrivacySafePresentation() {
        let cases: [(String?, String, Bool)] = [
            ("ai_disabled_for_privacy", "AI Review is disabled for privacy", false),
            ("ai_not_configured", "AI review not configured", false),
            ("ai_rate_limited", "AI review rate limited", true),
            ("ai_timeout", "AI provider temporarily unavailable", true),
            ("ai_provider_unavailable", "AI provider temporarily unavailable", true),
            ("ai_provider_invalid", "AI response could not be validated", true),
            ("ai_provider_refused", "AI review unavailable", true),
        ]
        for (code, title, allowsRetry) in cases {
            let presentation = AIReviewFailurePresentation.forCode(code)
            XCTAssertEqual(presentation.title, title)
            XCTAssertEqual(presentation.allowsRetry, allowsRetry)
            XCTAssertEqual(presentation.isPrivacyChoice, code == "ai_disabled_for_privacy")
            XCTAssertFalse(presentation.message.isEmpty)
            XCTAssertFalse(presentation.message.contains("GEMINI_API_KEY"))
            XCTAssertFalse(presentation.message.contains("http"))
        }
    }
}
