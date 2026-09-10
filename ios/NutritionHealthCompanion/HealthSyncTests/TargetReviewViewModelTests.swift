import XCTest
@testable import NutritionHealthCompanion

@MainActor
final class TargetReviewViewModelTests: XCTestCase {
    private final class ScriptedBackend: BackendClient, @unchecked Sendable {
        var latestGoals: [Result<GoalPolicyResponse?, Error>] = []
        var createdGoals: [Result<GoalPolicyResponse, Error>] = []
        var reviews: [Result<TargetReviewResponse, Error>] = []
        var decisions: [Result<TargetReviewDecisionResponse, Error>] = []
        var latestTargets: [Result<TargetPolicyLatest?, Error>] = []
        var targetApprovals: [Result<TargetPolicyApprovalResponse, Error>] = []
        var latestProteinProposals: [Result<ProteinTargetProposalResponse?, Error>] = []
        var generatedProteinProposals: [Result<ProteinTargetProposalResponse, Error>] = []
        var proteinDecisions: [Result<ProteinProposalDecisionResponse, Error>] = []
        var proteinGenerationDelayNanoseconds: UInt64 = 0

        private(set) var goalCreates: [(String, GoalDirectionDTO, String)] = []
        private(set) var reviewCalls: [(Date, String)] = []
        private(set) var decisionCalls: [(UUID, TargetReviewDecisionDTO, UUID)] = []
        private(set) var targetRefreshCount = 0
        private(set) var targetApprovalCalls: [(String, String, String, String)] = []
        private(set) var proteinLatestCount = 0
        private(set) var proteinGenerationCount = 0
        private(set) var proteinDecisionCalls: [
            (UUID, ProteinProposalDecisionDTO, UUID, String)
        ] = []
        private(set) var nextMealGenerationCount = 0

        func submitBatch(
            added _: [BodyMassSampleDTO], deletions _: [DeletedSampleDTO]
        ) async throws -> SyncResponse {
            throw BackendError.retryable("unused")
        }

        func fetchSyncStatus() async throws -> StatusResponse {
            throw BackendError.retryable("unused")
        }

        func fetchLatestGoalPolicy() async throws -> GoalPolicyResponse? {
            try latestGoals.removeFirst().get()
        }

        func createGoalPolicy(
            policyVersion: String,
            direction: GoalDirectionDTO,
            desiredRateKgPerWeek: String
        ) async throws -> GoalPolicyResponse {
            goalCreates.append((policyVersion, direction, desiredRateKgPerWeek))
            return try createdGoals.removeFirst().get()
        }

        func createTargetReview(asOfDate: Date, timezone: String) async throws
            -> TargetReviewResponse
        {
            reviewCalls.append((asOfDate, timezone))
            return try reviews.removeFirst().get()
        }

        func decideTargetReview(
            reviewId: UUID,
            decision: TargetReviewDecisionDTO,
            idempotencyKey: UUID
        ) async throws -> TargetReviewDecisionResponse {
            decisionCalls.append((reviewId, decision, idempotencyKey))
            return try decisions.removeFirst().get()
        }

        func fetchLatestTargetPolicy() async throws -> TargetPolicyLatest? {
            targetRefreshCount += 1
            if latestTargets.isEmpty { return nil }
            return try latestTargets.removeFirst().get()
        }

        func approveInitialCalorieTarget(
            policyVersion: String,
            caloriesKcal: String,
            calorieWeight: String,
            rationale: String
        ) async throws -> TargetPolicyApprovalResponse {
            targetApprovalCalls.append(
                (policyVersion, caloriesKcal, calorieWeight, rationale))
            return try targetApprovals.removeFirst().get()
        }

        func fetchLatestProteinProposal() async throws -> ProteinTargetProposalResponse? {
            proteinLatestCount += 1
            if latestProteinProposals.isEmpty { return nil }
            return try latestProteinProposals.removeFirst().get()
        }

        func generateProteinProposal() async throws -> ProteinTargetProposalResponse {
            proteinGenerationCount += 1
            if proteinGenerationDelayNanoseconds > 0 {
                try await Task.sleep(nanoseconds: proteinGenerationDelayNanoseconds)
            }
            return try generatedProteinProposals.removeFirst().get()
        }

        func decideProteinProposal(
            proposalId: UUID,
            decision: ProteinProposalDecisionDTO,
            clientEventId: UUID,
            rationale: String
        ) async throws -> ProteinProposalDecisionResponse {
            proteinDecisionCalls.append((proposalId, decision, clientEventId, rationale))
            return try proteinDecisions.removeFirst().get()
        }

        func generateNextMeal(
            date _: Date,
            timezone _: String,
            clientRequestId _: UUID
        ) async throws -> NextMealRecommendationResponse {
            nextMealGenerationCount += 1
            throw BackendError.retryable("not expected")
        }
    }

    private let day = WireDate.date(fromISO8601: "2026-08-28T00:00:00Z")!
    private let eventId = UUID(uuidString: "20000000-0000-0000-0000-000000000001")!

    func test_activationDoesNotCreateReviewAndGoalValidationIsUsabilityOnly() async throws {
        let backend = ScriptedBackend()
        backend.latestGoals = [.success(nil)]
        backend.createdGoals = [.success(try Self.goal())]
        let viewModel = makeViewModel(backend: backend)

        await viewModel.activate(subject: "owner")
        XCTAssertEqual(viewModel.phase, .noGoalConfigured)
        XCTAssertEqual(backend.reviewCalls.count, 0)

        viewModel.selectGoalDirection(.gain)
        XCTAssertEqual(viewModel.selectedGoalDirection, .gain)
        XCTAssertEqual(backend.goalCreates.count, 0)

        await viewModel.createGoal(desiredRateKgPerWeek: "-0.2")
        guard case .goalInputError = viewModel.phase else {
            XCTFail("direction/sign mismatch should be visible")
            return
        }
        XCTAssertEqual(backend.goalCreates.count, 0)

        await viewModel.createGoal(desiredRateKgPerWeek: "0.200000")
        XCTAssertEqual(backend.goalCreates.count, 1)
        XCTAssertEqual(backend.goalCreates[0].1, .gain)
        XCTAssertEqual(backend.goalCreates[0].2, "0.200000")
        XCTAssertEqual(viewModel.phase, .goalConfigured(try Self.goal()))
    }

    func test_directionSelectionIsDraftOnlyUntilExplicitSave() async throws {
        let backend = ScriptedBackend()
        backend.latestGoals = [.success(nil)]
        backend.createdGoals = [.success(try Self.goal())]
        let viewModel = makeViewModel(backend: backend)

        await viewModel.activate(subject: "owner")
        XCTAssertEqual(viewModel.selectedGoalDirection, .maintain)

        viewModel.selectGoalDirection(.lose)

        XCTAssertEqual(viewModel.selectedGoalDirection, .lose)
        XCTAssertEqual(viewModel.phase, .noGoalConfigured)
        XCTAssertEqual(backend.goalCreates.count, 0)
        XCTAssertEqual(backend.reviewCalls.count, 0)

        viewModel.selectGoalDirection(.gain)
        await viewModel.createGoal(desiredRateKgPerWeek: "0.200000")

        XCTAssertEqual(backend.goalCreates.count, 1)
        XCTAssertEqual(backend.goalCreates[0].1, .gain)
        XCTAssertEqual(backend.goalCreates[0].2, "0.200000")
    }

    func test_existingGoalCanCreateNewImmutableVersionOnlyAfterExplicitSave() async throws {
        let badGoal = try Self.goal(
            policyVersion: "mistaken-goal-v1",
            desiredRate: "67"
        )
        let correctedGoal = try Self.goal(
            versionId: "30000000-0000-0000-0000-000000000002",
            policyVersion: "corrected-goal-v2",
            direction: "maintain",
            desiredRate: "0"
        )
        let backend = ScriptedBackend()
        backend.latestGoals = [.success(badGoal)]
        backend.createdGoals = [.success(correctedGoal)]
        let viewModel = makeViewModel(backend: backend, eventIds: [eventId])

        await viewModel.activate(subject: "owner")
        XCTAssertEqual(viewModel.phase, .goalConfigured(badGoal))
        XCTAssertFalse(viewModel.isCreatingNewGoalVersion)

        viewModel.beginNewGoalVersion()

        XCTAssertTrue(viewModel.isCreatingNewGoalVersion)
        XCTAssertEqual(viewModel.selectedGoalDirection, .maintain)
        XCTAssertEqual(viewModel.phase, .goalConfigured(badGoal))
        XCTAssertEqual(backend.goalCreates.count, 0)

        await viewModel.createGoal(desiredRateKgPerWeek: "0")

        XCTAssertEqual(backend.goalCreates.count, 1)
        XCTAssertEqual(
            backend.goalCreates[0].0,
            "ios-goal-\(eventId.uuidString.lowercased())"
        )
        XCTAssertEqual(backend.goalCreates[0].1, .maintain)
        XCTAssertEqual(backend.goalCreates[0].2, "0")
        XCTAssertEqual(viewModel.phase, .goalConfigured(correctedGoal))
        XCTAssertFalse(viewModel.isCreatingNewGoalVersion)
        XCTAssertEqual(badGoal.desiredRateKgPerWeek, "67")
    }

    func test_allReviewStatusesRemainDistinctAndNoTargetIsTyped() async throws {
        let backend = ScriptedBackend()
        backend.latestGoals = [.success(try Self.goal())]
        let viewModel = makeViewModel(backend: backend)
        await viewModel.activate(subject: "owner")

        backend.reviews = [
            .success(.noTargetPolicy),
            .success(try Self.review(status: "evidence_unavailable")),
            .success(try Self.review(status: "cooldown_hold")),
            .success(try Self.review(status: "within_band")),
            .success(try Self.review(status: "bound_hold")),
            .success(try Self.review(status: "recommendation_ready")),
        ]

        await viewModel.requestReview()
        XCTAssertEqual(viewModel.phase, .noTargetPolicy)
        await viewModel.requestReview()
        guard case .evidenceUnavailable = viewModel.phase else { return XCTFail() }
        await viewModel.requestReview()
        guard case .cooldownHold = viewModel.phase else { return XCTFail() }
        await viewModel.requestReview()
        guard case .withinBand = viewModel.phase else { return XCTFail() }
        await viewModel.requestReview()
        guard case .boundHold = viewModel.phase else { return XCTFail() }
        await viewModel.requestReview()
        guard case .recommendationReady = viewModel.phase else { return XCTFail() }
        XCTAssertEqual(backend.reviewCalls.count, 6)
    }

    func test_initialTargetRequiresExplicitValidOwnerInputsAndDoesNotInferFromGoal() async throws {
        let goal = try Self.goal(desiredRate: "0.250000")
        let backend = ScriptedBackend()
        backend.latestGoals = [.success(goal)]
        backend.reviews = [.success(.noTargetPolicy)]
        let viewModel = makeViewModel(backend: backend)
        await viewModel.activate(subject: "owner")
        await viewModel.requestReview()

        await viewModel.approveInitialCalorieTarget(
            caloriesKcal: "", calorieWeight: "1", rationale: "owner baseline")
        XCTAssertEqual(viewModel.initialTargetMessage, "Enter a positive daily calorie target.")
        await viewModel.approveInitialCalorieTarget(
            caloriesKcal: "2450", calorieWeight: "0", rationale: "owner baseline")
        XCTAssertEqual(
            viewModel.initialTargetMessage,
            "Enter a positive calorie scoring weight."
        )
        await viewModel.approveInitialCalorieTarget(
            caloriesKcal: "2450", calorieWeight: "1", rationale: " ")
        XCTAssertEqual(
            viewModel.initialTargetMessage,
            "Explain why you are approving this initial target."
        )

        XCTAssertEqual(backend.targetApprovalCalls.count, 0)
        XCTAssertEqual(backend.reviewCalls.count, 1)
        XCTAssertEqual(viewModel.phase, .noTargetPolicy)
    }

    func test_initialTargetApprovalIsExplicitThenLatestBecomesCurrentWithoutAutomaticReview()
        async throws
    {
        let goal = try Self.goal(desiredRate: "0.250000")
        let latestTarget = Self.targetPolicy(policyVersion: "owner-target-v1")
        let backend = ScriptedBackend()
        backend.latestGoals = [.success(goal)]
        backend.reviews = [.success(.noTargetPolicy)]
        backend.targetApprovals = [.success(try Self.targetApproval())]
        backend.latestTargets = [.success(nil), .success(latestTarget)]
        let viewModel = makeViewModel(backend: backend, eventIds: [eventId])
        await viewModel.activate(subject: "owner")
        await viewModel.requestReview()

        await viewModel.approveInitialCalorieTarget(
            caloriesKcal: "2450.000000",
            calorieWeight: "1.250000",
            rationale: " owner selected baseline "
        )

        XCTAssertEqual(backend.targetApprovalCalls.count, 1)
        XCTAssertEqual(
            backend.targetApprovalCalls[0].0,
            "ios-target-\(eventId.uuidString.lowercased())"
        )
        XCTAssertEqual(backend.targetApprovalCalls[0].1, "2450.000000")
        XCTAssertEqual(backend.targetApprovalCalls[0].2, "1.250000")
        XCTAssertEqual(backend.targetApprovalCalls[0].3, "owner selected baseline")
        XCTAssertEqual(backend.targetRefreshCount, 2)
        XCTAssertEqual(viewModel.latestTargetPolicy, latestTarget)
        XCTAssertEqual(viewModel.phase, .goalConfigured(goal))
        XCTAssertEqual(backend.reviewCalls.count, 1)
    }

    func test_ambiguousInitialApprovalRetryResolvesOnlyItsImmutableVersion() async throws {
        let goal = try Self.goal()
        let backend = ScriptedBackend()
        backend.latestGoals = [.success(goal)]
        backend.reviews = [.success(.noTargetPolicy)]
        backend.targetApprovals = [
            .failure(BackendError.retryable("response lost")),
            .failure(BackendError.rejected(
                statusCode: 409,
                code: "duplicate_target_policy_version",
                detail: "version already approved"
            )),
        ]
        backend.latestTargets = [
            .success(nil),
            .success(Self.targetPolicy(
                policyVersion: "ios-target-\(eventId.uuidString.lowercased())"))
        ]
        let viewModel = makeViewModel(backend: backend, eventIds: [eventId])
        await viewModel.activate(subject: "owner")
        await viewModel.requestReview()

        await viewModel.approveInitialCalorieTarget(
            caloriesKcal: "2450", calorieWeight: "1", rationale: "owner baseline")
        XCTAssertEqual(
            viewModel.initialTargetMessage,
            "The initial target could not be approved. Try again."
        )
        await viewModel.approveInitialCalorieTarget(
            caloriesKcal: "2450", calorieWeight: "1", rationale: "owner baseline")

        XCTAssertEqual(backend.targetApprovalCalls.count, 2)
        XCTAssertEqual(backend.targetApprovalCalls[0].0, backend.targetApprovalCalls[1].0)
        XCTAssertEqual(viewModel.phase, .goalConfigured(goal))
    }

    func test_approvalRetryReusesEventAndReturnsTerminalStateThenRefreshesTarget() async throws {
        let backend = ScriptedBackend()
        backend.latestGoals = [.success(try Self.goal())]
        backend.reviews = [.success(try Self.review(status: "recommendation_ready"))]
        backend.decisions = [
            .failure(BackendError.retryable("offline")),
            .success(try Self.decision("approved")),
        ]
        let viewModel = makeViewModel(backend: backend, eventIds: [eventId])
        await viewModel.activate(subject: "owner")
        await viewModel.requestReview()

        await viewModel.decide(.approved)
        guard case .networkFailure = viewModel.phase else { return XCTFail() }
        viewModel.restoreReviewAfterNetworkFailure()
        await viewModel.decide(.approved)

        XCTAssertEqual(backend.decisionCalls.count, 2)
        XCTAssertEqual(backend.decisionCalls[0].2, eventId)
        XCTAssertEqual(backend.decisionCalls[1].2, eventId)
        XCTAssertEqual(backend.targetRefreshCount, 2)
        XCTAssertEqual(viewModel.phase, .approved(try Self.decision("approved")))
    }

    func test_rejectionNeverRefreshesTargetAndConflictIsNotSuccess() async throws {
        let rejectedBackend = ScriptedBackend()
        rejectedBackend.latestGoals = [.success(try Self.goal())]
        rejectedBackend.reviews = [.success(try Self.review(status: "recommendation_ready"))]
        rejectedBackend.decisions = [.success(try Self.decision("rejected"))]
        let rejected = makeViewModel(backend: rejectedBackend, eventIds: [eventId])
        await rejected.activate(subject: "owner")
        await rejected.requestReview()
        await rejected.decide(.rejected)
        XCTAssertEqual(rejected.phase, .rejected(try Self.decision("rejected")))
        XCTAssertEqual(rejectedBackend.targetRefreshCount, 1)

        let conflictBackend = ScriptedBackend()
        conflictBackend.latestGoals = [.success(try Self.goal())]
        conflictBackend.reviews = [.success(try Self.review(status: "recommendation_ready"))]
        conflictBackend.decisions = [.failure(BackendError.rejected(
            statusCode: 409, code: "target_review_stale", detail: "refresh"))]
        let conflict = makeViewModel(backend: conflictBackend, eventIds: [eventId])
        await conflict.activate(subject: "owner")
        await conflict.requestReview()
        await conflict.decide(.approved)
        guard case .conflict = conflict.phase else { return XCTFail() }
        XCTAssertEqual(conflictBackend.targetRefreshCount, 1)
    }

    func test_proteinLoadShowsNoProposalAndExistingApprovedFloor() async throws {
        let backend = ScriptedBackend()
        backend.latestGoals = [.success(try Self.goal())]
        backend.latestTargets = [.success(Self.targetPolicy(proteinG: "116"))]
        backend.latestProteinProposals = [.success(nil)]
        let viewModel = makeViewModel(backend: backend)

        await viewModel.activate(subject: "owner")

        XCTAssertEqual(viewModel.proteinPhase, .noProposal)
        XCTAssertEqual(viewModel.approvedProteinFloor?.value, "116")
        XCTAssertEqual(viewModel.approvedProteinFloor?.kind, "floor")
        XCTAssertEqual(backend.proteinGenerationCount, 0)
    }

    func test_proteinProposalGenerationIsExplicitAndEvidenceFailureIsUnderstandable()
        async throws
    {
        let pending = try Self.proteinProposal()
        let backend = ScriptedBackend()
        backend.latestGoals = [.success(try Self.goal())]
        backend.latestTargets = [.success(Self.targetPolicy())]
        backend.latestProteinProposals = [.success(nil)]
        backend.generatedProteinProposals = [
            .failure(BackendError.rejected(
                statusCode: 409,
                code: "protein_proposal_evidence_unavailable",
                detail: "A recent persisted HealthKit body-mass sample is required."
            )),
            .success(pending),
        ]
        let viewModel = makeViewModel(backend: backend)
        await viewModel.activate(subject: "owner")

        XCTAssertEqual(backend.proteinGenerationCount, 0)
        await viewModel.generateProteinProposal()
        guard case .evidenceUnavailable(let message) = viewModel.proteinPhase else {
            return XCTFail("missing evidence must remain a distinct fail-closed state")
        }
        XCTAssertTrue(message.contains("HealthKit body-mass"))
        await viewModel.generateProteinProposal()

        XCTAssertEqual(viewModel.proteinPhase, .proposal(pending))
        XCTAssertEqual(backend.proteinGenerationCount, 2)
        XCTAssertEqual(pending.proposedProteinG, "116")
        XCTAssertEqual(pending.targetKind, .floor)
        XCTAssertEqual(pending.bodyMassKg, "65.8")
        XCTAssertEqual(pending.policyVersion, "owner-protein-target.v1")
        XCTAssertTrue(pending.provenance.contains("HealthKit"))
    }

    func test_proteinApprovalRequiresConfirmationRefreshesTargetAndNeverGeneratesNextMeal()
        async throws
    {
        let pending = try Self.proteinProposal()
        let approved = try Self.proteinProposal(status: "approved")
        let result = try Self.proteinDecision("approved")
        let approvedTarget = Self.targetPolicy(
            versionId: "80000000-0000-0000-0000-000000000001",
            policyVersion: "protein-approved-v1",
            proteinG: "116"
        )
        let backend = ScriptedBackend()
        backend.latestGoals = [.success(try Self.goal())]
        backend.latestTargets = [.success(Self.targetPolicy()), .success(approvedTarget)]
        backend.latestProteinProposals = [.success(pending), .success(approved)]
        backend.proteinDecisions = [.success(result)]
        var approvedRefreshes = 0
        let viewModel = makeViewModel(
            backend: backend,
            eventIds: [eventId],
            proteinApproved: { approvedRefreshes += 1 }
        )
        await viewModel.activate(subject: "owner")

        XCTAssertFalse(viewModel.canApproveProteinProposal)
        await viewModel.decideProteinProposal(.approved)
        XCTAssertEqual(backend.proteinDecisionCalls.count, 0)
        XCTAssertNil(viewModel.approvedProteinFloor)

        viewModel.setProteinApprovalConfirmed(true)
        XCTAssertTrue(viewModel.canApproveProteinProposal)
        await viewModel.decideProteinProposal(.approved)

        XCTAssertEqual(backend.proteinDecisionCalls.count, 1)
        XCTAssertEqual(backend.proteinDecisionCalls[0].0, pending.proposalId)
        XCTAssertEqual(backend.proteinDecisionCalls[0].1, .approved)
        XCTAssertEqual(backend.proteinDecisionCalls[0].2, eventId)
        XCTAssertTrue(backend.proteinDecisionCalls[0].3.contains("after reviewing"))
        XCTAssertEqual(viewModel.proteinPhase, .proposal(approved))
        XCTAssertEqual(viewModel.approvedProteinFloor?.value, "116")
        XCTAssertFalse(viewModel.proteinApprovalConfirmed)
        XCTAssertEqual(approvedRefreshes, 1)
        XCTAssertEqual(backend.nextMealGenerationCount, 0)
    }

    func test_proteinRejectionIsExplicitAndDoesNotRefreshTarget() async throws {
        let pending = try Self.proteinProposal()
        let rejected = try Self.proteinProposal(status: "rejected")
        let backend = ScriptedBackend()
        backend.latestGoals = [.success(try Self.goal())]
        backend.latestTargets = [.success(Self.targetPolicy())]
        backend.latestProteinProposals = [.success(pending), .success(rejected)]
        backend.proteinDecisions = [.success(try Self.proteinDecision("rejected"))]
        var approvedRefreshes = 0
        let viewModel = makeViewModel(
            backend: backend,
            eventIds: [eventId],
            proteinApproved: { approvedRefreshes += 1 }
        )
        await viewModel.activate(subject: "owner")

        await viewModel.decideProteinProposal(.rejected)

        XCTAssertEqual(backend.proteinDecisionCalls.count, 1)
        XCTAssertEqual(backend.proteinDecisionCalls[0].1, .rejected)
        XCTAssertEqual(viewModel.proteinPhase, .proposal(rejected))
        XCTAssertNil(viewModel.approvedProteinFloor)
        XCTAssertEqual(backend.targetRefreshCount, 1)
        XCTAssertEqual(approvedRefreshes, 0)
        XCTAssertEqual(backend.nextMealGenerationCount, 0)
    }

    func test_ambiguousProteinApprovalRetryReusesIdentityWithoutOptimisticTarget()
        async throws
    {
        let pending = try Self.proteinProposal()
        let approved = try Self.proteinProposal(status: "approved")
        let approvedTarget = Self.targetPolicy(
            versionId: "80000000-0000-0000-0000-000000000001",
            proteinG: "116"
        )
        let backend = ScriptedBackend()
        backend.latestGoals = [.success(try Self.goal())]
        backend.latestTargets = [.success(Self.targetPolicy()), .success(approvedTarget)]
        backend.latestProteinProposals = [.success(pending), .success(approved)]
        backend.proteinDecisions = [
            .failure(BackendError.retryable("response lost")),
            .success(try Self.proteinDecision("approved")),
        ]
        let viewModel = makeViewModel(backend: backend, eventIds: [eventId])
        await viewModel.activate(subject: "owner")
        viewModel.setProteinApprovalConfirmed(true)

        await viewModel.decideProteinProposal(.approved)
        XCTAssertNil(viewModel.approvedProteinFloor)
        XCTAssertTrue(viewModel.proteinApprovalConfirmed)
        XCTAssertEqual(backend.targetRefreshCount, 1)
        await viewModel.decideProteinProposal(.approved)

        XCTAssertEqual(backend.proteinDecisionCalls.count, 2)
        XCTAssertEqual(backend.proteinDecisionCalls[0].2, eventId)
        XCTAssertEqual(backend.proteinDecisionCalls[1].2, eventId)
        XCTAssertEqual(viewModel.approvedProteinFloor?.value, "116")
    }

    func test_proteinStateClearsOnSignOutAndLateResponseCannotRepopulateIt() async throws {
        let backend = ScriptedBackend()
        backend.latestGoals = [.success(try Self.goal())]
        backend.latestTargets = [.success(Self.targetPolicy())]
        backend.latestProteinProposals = [.success(nil)]
        backend.generatedProteinProposals = [.success(try Self.proteinProposal())]
        backend.proteinGenerationDelayNanoseconds = 30_000_000
        let viewModel = makeViewModel(backend: backend)
        await viewModel.activate(subject: "owner-a")

        let lateGeneration = Task { await viewModel.generateProteinProposal() }
        await Task.yield()
        viewModel.resetForSignOut()
        await lateGeneration.value

        XCTAssertEqual(viewModel.proteinPhase, .signedOut)
        XCTAssertNil(viewModel.approvedProteinFloor)
        XCTAssertNil(viewModel.proteinDecisionResult)
        XCTAssertFalse(viewModel.proteinApprovalConfirmed)
    }

    func test_unauthorizedClearsStateAndNotifiesRoot() async {
        let backend = ScriptedBackend()
        backend.latestGoals = [.failure(BackendError.unauthorized)]
        var signedOut = false
        let viewModel = makeViewModel(
            backend: backend,
            unauthorized: { signedOut = true }
        )
        await viewModel.activate(subject: "owner")
        XCTAssertEqual(viewModel.phase, .signedOut)
        XCTAssertTrue(signedOut)
    }

    private func makeViewModel(
        backend: ScriptedBackend,
        eventIds: [UUID] = [],
        unauthorized: @escaping @MainActor () -> Void = {},
        proteinApproved: @escaping @MainActor () async -> Void = {}
    ) -> TargetReviewViewModel {
        var ids = eventIds
        return TargetReviewViewModel(
            backend: backend,
            requestDate: { self.day },
            timezoneIdentifier: { "America/New_York" },
            eventId: { ids.isEmpty ? UUID() : ids.removeFirst() },
            onUnauthorized: unauthorized,
            onProteinTargetApproved: proteinApproved
        )
    }

    private static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let raw = try container.decode(String.self)
            guard let date = WireDate.date(fromISO8601: raw) else {
                throw DecodingError.dataCorruptedError(
                    in: container, debugDescription: "invalid timestamp")
            }
            return date
        }
        return decoder
    }

    private static func goal(
        versionId: String = "30000000-0000-0000-0000-000000000001",
        policyVersion: String = "owner-goal-v1",
        direction: String = "gain",
        desiredRate: String = "0.200000"
    ) throws -> GoalPolicyResponse {
        try decoder().decode(GoalPolicyResponse.self, from: Data("""
        {"version_id":"\(versionId)",
         "policy_version":"\(policyVersion)","direction":"\(direction)",
         "desired_rate_kg_per_week":"\(desiredRate)",
         "payload_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
         "created_at":"2026-08-01T12:00:00Z"}
        """.utf8))
    }

    private static func review(status: String) throws -> TargetReviewResponse {
        let hasProposal = status == "recommendation_ready"
        return try decoder().decode(
            TargetReviewResponse.self,
            from: TargetReviewClientTests.reviewPayload(
                status: status, includeProposal: hasProposal)
        )
    }

    private static func decision(_ value: String) throws -> TargetReviewDecisionResponse {
        try decoder().decode(
            TargetReviewDecisionResponse.self,
            from: TargetReviewClientTests.decisionPayload(decision: value)
        )
    }

    private static func targetApproval() throws -> TargetPolicyApprovalResponse {
        try decoder().decode(
            TargetPolicyApprovalResponse.self,
            from: TargetReviewClientTests.targetApprovalPayload
        )
    }

    private static func proteinProposal(
        status: String = "pending"
    ) throws -> ProteinTargetProposalResponse {
        try decoder().decode(
            ProteinTargetProposalResponse.self,
            from: TargetReviewClientTests.proteinProposalPayload(status: status)
        )
    }

    private static func proteinDecision(
        _ decision: String
    ) throws -> ProteinProposalDecisionResponse {
        try decoder().decode(
            ProteinProposalDecisionResponse.self,
            from: TargetReviewClientTests.proteinDecisionPayload(decision: decision)
        )
    }

    private static func targetPolicy(
        versionId: String = "40000000-0000-0000-0000-000000000001",
        policyVersion: String = "owner-target-v1",
        proteinG: String? = nil
    ) -> TargetPolicyLatest {
        var goals = [
            TargetPolicyGoal(
                nutrient: "calories_kcal",
                kind: "target",
                value: "2450.000000",
                weight: "1.250000"
            )
        ]
        if let proteinG {
            goals.append(TargetPolicyGoal(
                nutrient: "protein_g",
                kind: "floor",
                value: proteinG,
                weight: "1"
            ))
        }
        return TargetPolicyLatest(
            versionId: UUID(uuidString: versionId)!,
            policyVersion: policyVersion,
            goals: goals,
            payloadSha256: "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
            approvedAt: WireDate.date(fromISO8601: "2026-08-28T12:00:00Z")
        )
    }
}
