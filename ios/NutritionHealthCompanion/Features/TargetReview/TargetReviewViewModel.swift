import Foundation
import Observation

@MainActor
@Observable
final class TargetReviewViewModel {
    enum Phase: Equatable {
        case loading
        case noGoalConfigured
        case goalInputError(String)
        case goalConfigured(GoalPolicyResponse)
        case noTargetPolicy
        case evidenceUnavailable(TargetReviewDetail)
        case cooldownHold(TargetReviewDetail)
        case withinBand(TargetReviewDetail)
        case boundHold(TargetReviewDetail)
        case recommendationReady(TargetReviewDetail)
        case approved(TargetReviewDecisionResponse)
        case rejected(TargetReviewDecisionResponse)
        case conflict(TargetReviewDetail?)
        case networkFailure(String, TargetReviewDetail?)
        case signedOut
    }

    enum ProteinPhase: Equatable {
        case loading
        case noProposal
        case proposal(ProteinTargetProposalResponse)
        case evidenceUnavailable(String)
        case networkFailure(String, ProteinTargetProposalResponse?)
        case signedOut
    }

    private struct DecisionKey: Hashable {
        let reviewId: UUID
        let decision: TargetReviewDecisionDTO
    }

    private struct ProteinDecisionKey: Hashable {
        let proposalId: UUID
        let decision: ProteinProposalDecisionDTO
    }

    private let backend: any BackendClient
    private let requestDate: () -> Date
    private let timezoneIdentifier: () -> String
    private let eventId: () -> UUID
    private let onUnauthorized: @MainActor () -> Void
    private let onProteinTargetApproved: @MainActor () async -> Void

    private var subject: String?
    private var pendingGoalPolicyVersion: String?
    private var pendingTargetPolicyVersion: String?
    private var pendingDecisions: [DecisionKey: UUID] = [:]
    private var pendingProteinDecisions: [ProteinDecisionKey: UUID] = [:]
    private var latestGoalPolicy: GoalPolicyResponse?
    private(set) var phase: Phase = .loading
    private(set) var selectedGoalDirection: GoalDirectionDTO = .maintain
    private(set) var isCreatingNewGoalVersion = false
    private(set) var latestTargetPolicy: TargetPolicyLatest?
    private(set) var isTargetPolicyResolved = false
    private(set) var initialTargetMessage: String?
    private(set) var isWorking = false
    private(set) var proteinPhase: ProteinPhase = .loading
    private(set) var proteinApprovalConfirmed = false
    private(set) var proteinDecisionResult: ProteinProposalDecisionResponse?
    private(set) var proteinMessage: String?
    private(set) var isProteinWorking = false

    init(
        backend: any BackendClient,
        requestDate: @escaping () -> Date = { Date() },
        timezoneIdentifier: @escaping () -> String = { TimeZone.current.identifier },
        eventId: @escaping () -> UUID = UUID.init,
        onUnauthorized: @escaping @MainActor () -> Void = {},
        onProteinTargetApproved: @escaping @MainActor () async -> Void = {}
    ) {
        self.backend = backend
        self.requestDate = requestDate
        self.timezoneIdentifier = timezoneIdentifier
        self.eventId = eventId
        self.onUnauthorized = onUnauthorized
        self.onProteinTargetApproved = onProteinTargetApproved
    }

    var currentReview: TargetReviewDetail? {
        switch phase {
        case .evidenceUnavailable(let review), .cooldownHold(let review),
             .withinBand(let review), .boundHold(let review),
             .recommendationReady(let review):
            return review
        case .conflict(let review), .networkFailure(_, let review):
            return review
        default:
            return nil
        }
    }

    var approvedProteinFloor: TargetPolicyGoal? {
        latestTargetPolicy?.goals.first {
            $0.nutrient == "protein_g" && $0.kind == "floor"
        }
    }

    var targetActionsDisabled: Bool { isWorking || isProteinWorking }

    var canApproveProteinProposal: Bool {
        guard case .proposal(let proposal) = proteinPhase else { return false }
        return proposal.isPending
            && proteinDecisionResult == nil
            && proteinApprovalConfirmed
            && !targetActionsDisabled
    }

    func activate(subject: String) async {
        guard self.subject != subject else { return }
        self.subject = subject
        pendingGoalPolicyVersion = nil
        pendingTargetPolicyVersion = nil
        pendingDecisions = [:]
        pendingProteinDecisions = [:]
        latestGoalPolicy = nil
        selectedGoalDirection = .maintain
        isCreatingNewGoalVersion = false
        latestTargetPolicy = nil
        initialTargetMessage = nil
        resetProteinState()
        async let goal: Void = loadLatestGoal(expectedSubject: subject)
        async let protein: Void = loadProteinState(expectedSubject: subject)
        _ = await (goal, protein)
    }

    func selectGoalDirection(_ direction: GoalDirectionDTO) {
        selectedGoalDirection = direction
    }

    func beginNewGoalVersion() {
        guard subject != nil, !targetActionsDisabled else { return }
        pendingGoalPolicyVersion = nil
        selectedGoalDirection = .maintain
        isCreatingNewGoalVersion = true
    }

    func createGoal(desiredRateKgPerWeek rawRate: String) async {
        guard subject != nil, !targetActionsDisabled else { return }
        let rate = rawRate.trimmingCharacters(in: .whitespacesAndNewlines)
        guard Self.goalRateIsValid(rate, for: selectedGoalDirection) else {
            phase = .goalInputError(Self.goalValidationMessage(for: selectedGoalDirection))
            return
        }
        let policyVersion = pendingGoalPolicyVersion ?? "ios-goal-\(eventId().uuidString.lowercased())"
        pendingGoalPolicyVersion = policyVersion
        isWorking = true
        defer { isWorking = false }
        do {
            let goal = try await backend.createGoalPolicy(
                policyVersion: policyVersion,
                direction: selectedGoalDirection,
                desiredRateKgPerWeek: rate
            )
            pendingGoalPolicyVersion = nil
            isCreatingNewGoalVersion = false
            latestGoalPolicy = goal
            phase = .goalConfigured(goal)
        } catch BackendError.unauthorized {
            transitionToSignedOut()
        } catch BackendError.rejected(_, let code, _) where code == "duplicate_goal_policy" {
            // A prior ambiguous response may have committed. Resolve the
            // authoritative latest value instead of inventing a replacement.
            await loadLatestGoal(expectedSubject: subject)
        } catch {
            phase = .networkFailure("The goal could not be saved. Try again.", nil)
        }
    }

    func requestReview() async {
        guard subject != nil, !targetActionsDisabled else { return }
        isWorking = true
        defer { isWorking = false }
        do {
            let response = try await backend.createTargetReview(
                asOfDate: requestDate(),
                timezone: timezoneIdentifier()
            )
            switch response {
            case .noGoalPolicy:
                phase = .noGoalConfigured
            case .noTargetPolicy:
                initialTargetMessage = nil
                phase = .noTargetPolicy
            case .review(let review):
                phase = Self.phase(for: review)
            }
        } catch BackendError.unauthorized {
            transitionToSignedOut()
        } catch {
            phase = .networkFailure("The review could not be loaded. Try again.", currentReview)
        }
    }

    func approveInitialCalorieTarget(
        caloriesKcal rawCalories: String,
        calorieWeight rawWeight: String,
        rationale rawRationale: String
    ) async {
        guard subject != nil, case .noTargetPolicy = phase, !targetActionsDisabled else { return }
        guard let goal = latestGoalPolicy else {
            phase = .noGoalConfigured
            return
        }
        let calories = rawCalories.trimmingCharacters(in: .whitespacesAndNewlines)
        let weight = rawWeight.trimmingCharacters(in: .whitespacesAndNewlines)
        let rationale = rawRationale.trimmingCharacters(in: .whitespacesAndNewlines)
        guard Self.positiveDecimalIsValid(calories) else {
            initialTargetMessage = "Enter a positive daily calorie target."
            return
        }
        guard Self.positiveDecimalIsValid(weight) else {
            initialTargetMessage = "Enter a positive calorie scoring weight."
            return
        }
        guard !rationale.isEmpty else {
            initialTargetMessage = "Explain why you are approving this initial target."
            return
        }

        let policyVersion = pendingTargetPolicyVersion
            ?? "ios-target-\(eventId().uuidString.lowercased())"
        pendingTargetPolicyVersion = policyVersion
        initialTargetMessage = nil
        isWorking = true
        defer { isWorking = false }
        do {
            _ = try await backend.approveInitialCalorieTarget(
                policyVersion: policyVersion,
                caloriesKcal: calories,
                calorieWeight: weight,
                rationale: rationale
            )
            try await finishInitialTargetApproval(goal: goal, expectedPolicyVersion: nil)
        } catch BackendError.unauthorized {
            transitionToSignedOut()
        } catch BackendError.rejected(_, let code, _)
            where code == "duplicate_target_policy_version"
        {
            // A prior ambiguous response may have committed. Resolve only the
            // exact pending immutable version; never treat another latest
            // policy as this request's success.
            do {
                try await finishInitialTargetApproval(
                    goal: goal, expectedPolicyVersion: policyVersion)
            } catch BackendError.unauthorized {
                transitionToSignedOut()
            } catch {
                initialTargetMessage = "That target version already exists. Refresh and try again."
            }
        } catch {
            initialTargetMessage = "The initial target could not be approved. Try again."
        }
    }

    func decide(_ decision: TargetReviewDecisionDTO) async {
        guard case .recommendationReady(let review) = phase, !targetActionsDisabled else { return }
        let key = DecisionKey(reviewId: review.reviewId, decision: decision)
        let idempotencyKey = pendingDecisions[key] ?? eventId()
        pendingDecisions[key] = idempotencyKey
        isWorking = true
        defer { isWorking = false }
        do {
            let result = try await backend.decideTargetReview(
                reviewId: review.reviewId,
                decision: decision,
                idempotencyKey: idempotencyKey
            )
            pendingDecisions[key] = nil
            if result.decision == .approved {
                phase = .approved(result)
                do {
                    latestTargetPolicy = try await backend.fetchLatestTargetPolicy()
                    isTargetPolicyResolved = true
                } catch BackendError.unauthorized {
                    transitionToSignedOut()
                } catch {
                    // The terminal decision is already authoritative. A
                    // secondary refresh failure must not disguise success.
                    latestTargetPolicy = nil
                    isTargetPolicyResolved = false
                }
            } else {
                phase = .rejected(result)
            }
        } catch BackendError.unauthorized {
            transitionToSignedOut()
        } catch BackendError.rejected(let status, _, _) where status == 409 {
            pendingDecisions[key] = nil
            phase = .conflict(review)
        } catch {
            // Keep this logical decision's UUID for an explicit retry.
            phase = .networkFailure("The decision was not recorded. Try again.", review)
        }
    }

    func resetForSignOut() {
        subject = nil
        pendingGoalPolicyVersion = nil
        pendingTargetPolicyVersion = nil
        pendingDecisions = [:]
        pendingProteinDecisions = [:]
        latestGoalPolicy = nil
        selectedGoalDirection = .maintain
        isCreatingNewGoalVersion = false
        latestTargetPolicy = nil
        isTargetPolicyResolved = false
        initialTargetMessage = nil
        isWorking = false
        resetProteinState()
        proteinPhase = .signedOut
        phase = .signedOut
    }

    func restoreReviewAfterNetworkFailure() {
        guard case .networkFailure(_, let review?) = phase else { return }
        phase = Self.phase(for: review)
    }

    func retryGoalLoad() async {
        guard let expectedSubject = subject, !targetActionsDisabled else { return }
        await loadLatestGoal(expectedSubject: expectedSubject)
    }

    func setProteinApprovalConfirmed(_ confirmed: Bool) {
        guard case .proposal(let proposal) = proteinPhase,
              proposal.isPending,
              proteinDecisionResult == nil,
              !targetActionsDisabled
        else {
            proteinApprovalConfirmed = false
            return
        }
        proteinApprovalConfirmed = confirmed
    }

    func generateProteinProposal() async {
        guard let expectedSubject = subject, !targetActionsDisabled else { return }
        isProteinWorking = true
        proteinMessage = nil
        defer { isProteinWorking = false }
        do {
            let proposal = try await backend.generateProteinProposal()
            guard subject == expectedSubject else { return }
            proteinPhase = .proposal(proposal)
            proteinApprovalConfirmed = false
            proteinDecisionResult = nil
        } catch BackendError.unauthorized {
            guard subject == expectedSubject else { return }
            transitionToSignedOut()
        } catch BackendError.rejected(_, let code, let detail)
            where code == "protein_proposal_evidence_unavailable"
        {
            guard subject == expectedSubject else { return }
            proteinApprovalConfirmed = false
            proteinDecisionResult = nil
            proteinPhase = .evidenceUnavailable(
                detail ?? "A current persisted HealthKit body-weight sample is required."
            )
        } catch {
            guard subject == expectedSubject else { return }
            proteinMessage = "The proposal could not be generated. Try again when online."
        }
    }

    func decideProteinProposal(_ decision: ProteinProposalDecisionDTO) async {
        guard let expectedSubject = subject,
              case .proposal(let proposal) = proteinPhase,
              proposal.isPending,
              proteinDecisionResult == nil,
              !targetActionsDisabled,
              decision != .approved || proteinApprovalConfirmed
        else { return }

        let key = ProteinDecisionKey(proposalId: proposal.proposalId, decision: decision)
        let clientEventId = pendingProteinDecisions[key] ?? eventId()
        pendingProteinDecisions[key] = clientEventId
        isProteinWorking = true
        proteinMessage = nil
        defer { isProteinWorking = false }
        let rationale = decision == .approved
            ? "Owner approved the protein floor in iOS after reviewing its evidence."
            : "Owner rejected the protein floor in iOS after reviewing its evidence."
        do {
            let result = try await backend.decideProteinProposal(
                proposalId: proposal.proposalId,
                decision: decision,
                clientEventId: clientEventId,
                rationale: rationale
            )
            guard subject == expectedSubject else { return }
            pendingProteinDecisions[key] = nil
            proteinApprovalConfirmed = false
            proteinDecisionResult = result
            await refreshProposalAfterDecision(
                original: proposal,
                result: result,
                expectedSubject: expectedSubject
            )
        } catch BackendError.unauthorized {
            guard subject == expectedSubject else { return }
            pendingProteinDecisions[key] = nil
            transitionToSignedOut()
        } catch BackendError.rejected(let status, _, _) where status == 409 {
            guard subject == expectedSubject else { return }
            pendingProteinDecisions[key] = nil
            proteinApprovalConfirmed = false
            await loadProteinState(expectedSubject: expectedSubject)
            guard subject == expectedSubject else { return }
            proteinMessage = "This proposal is stale or already has a different decision."
        } catch {
            guard subject == expectedSubject else { return }
            proteinMessage = "The decision was not recorded. Try again when online."
        }
    }

    func retryProteinLoad() async {
        guard let expectedSubject = subject, !targetActionsDisabled else { return }
        await loadProteinState(expectedSubject: expectedSubject)
    }

    private func loadLatestGoal(expectedSubject: String? = nil) async {
        guard let expectedSubject = expectedSubject ?? subject,
              subject == expectedSubject
        else { return }
        isCreatingNewGoalVersion = false
        phase = .loading
        do {
            let goal = try await backend.fetchLatestGoalPolicy()
            guard subject == expectedSubject else { return }
            if let goal {
                latestGoalPolicy = goal
                phase = .goalConfigured(goal)
            } else {
                latestGoalPolicy = nil
                phase = .noGoalConfigured
            }
        } catch BackendError.unauthorized {
            guard subject == expectedSubject else { return }
            transitionToSignedOut()
        } catch {
            guard subject == expectedSubject else { return }
            phase = .networkFailure("Goal settings are temporarily unavailable.", nil)
        }
    }

    private func loadProteinState(expectedSubject: String) async {
        guard subject == expectedSubject else { return }
        proteinPhase = .loading
        proteinMessage = nil
        proteinApprovalConfirmed = false
        proteinDecisionResult = nil
        isTargetPolicyResolved = false

        async let targetRequest = backend.fetchLatestTargetPolicy()
        async let proposalRequest = backend.fetchLatestProteinProposal()

        do {
            let target = try await targetRequest
            guard subject == expectedSubject else { return }
            latestTargetPolicy = target
            isTargetPolicyResolved = true
        } catch BackendError.unauthorized {
            guard subject == expectedSubject else { return }
            transitionToSignedOut()
            return
        } catch {
            guard subject == expectedSubject else { return }
            latestTargetPolicy = nil
            isTargetPolicyResolved = false
            proteinMessage = "The current protein target is temporarily unavailable."
        }

        do {
            let proposal = try await proposalRequest
            guard subject == expectedSubject else { return }
            proteinPhase = proposal.map(ProteinPhase.proposal) ?? .noProposal
        } catch BackendError.unauthorized {
            guard subject == expectedSubject else { return }
            transitionToSignedOut()
        } catch {
            guard subject == expectedSubject else { return }
            proteinPhase = .networkFailure(
                "The latest protein proposal is temporarily unavailable.", nil)
        }
    }

    private func refreshProposalAfterDecision(
        original: ProteinTargetProposalResponse,
        result: ProteinProposalDecisionResponse,
        expectedSubject: String
    ) async {
        do {
            if let latest = try await backend.fetchLatestProteinProposal() {
                guard subject == expectedSubject else { return }
                proteinPhase = .proposal(latest)
            }
        } catch BackendError.unauthorized {
            guard subject == expectedSubject else { return }
            transitionToSignedOut()
            return
        } catch {
            guard subject == expectedSubject else { return }
            proteinPhase = .proposal(original)
            proteinMessage = "The decision was recorded, but its latest details could not reload."
        }

        guard result.decision == .approved else { return }
        do {
            let target = try await backend.fetchLatestTargetPolicy()
            guard subject == expectedSubject else { return }
            if target?.versionId == result.resultingTargetPolicyVersionId {
                latestTargetPolicy = target
                isTargetPolicyResolved = true
            } else {
                proteinMessage = "Approval was recorded, but the active target could not be confirmed."
            }
        } catch BackendError.unauthorized {
            guard subject == expectedSubject else { return }
            transitionToSignedOut()
            return
        } catch {
            guard subject == expectedSubject else { return }
            proteinMessage = "Approval was recorded, but the active target could not reload."
        }
        guard subject == expectedSubject else { return }
        await onProteinTargetApproved()
    }

    private func resetProteinState() {
        pendingProteinDecisions = [:]
        proteinPhase = .loading
        proteinApprovalConfirmed = false
        proteinDecisionResult = nil
        proteinMessage = nil
        isProteinWorking = false
    }

    private func transitionToSignedOut() {
        resetForSignOut()
        onUnauthorized()
    }

    private func finishInitialTargetApproval(
        goal: GoalPolicyResponse,
        expectedPolicyVersion: String?
    ) async throws {
        guard let latest = try await backend.fetchLatestTargetPolicy(),
              expectedPolicyVersion == nil || latest.policyVersion == expectedPolicyVersion
        else {
            throw BackendError.retryable("approved target could not be resolved")
        }
        latestTargetPolicy = latest
        isTargetPolicyResolved = true
        pendingTargetPolicyVersion = nil
        initialTargetMessage = nil
        phase = .goalConfigured(goal)
    }

    private static func phase(for review: TargetReviewDetail) -> Phase {
        switch review.status {
        case .evidenceUnavailable: .evidenceUnavailable(review)
        case .cooldownHold: .cooldownHold(review)
        case .withinBand: .withinBand(review)
        case .boundHold: .boundHold(review)
        case .recommendationReady: .recommendationReady(review)
        }
    }

    private static func goalRateIsValid(_ raw: String, for direction: GoalDirectionDTO) -> Bool {
        guard let rate = Decimal(
            string: raw,
            locale: Locale(identifier: "en_US_POSIX")
        ), !rate.isNaN else { return false }
        switch direction {
        case .gain: return rate > 0
        case .lose: return rate < 0
        case .maintain: return rate == 0
        }
    }

    private static func positiveDecimalIsValid(_ raw: String) -> Bool {
        guard let value = Decimal(
            string: raw,
            locale: Locale(identifier: "en_US_POSIX")
        ), !value.isNaN else { return false }
        return value > 0
    }

    private static func goalValidationMessage(for direction: GoalDirectionDTO) -> String {
        switch direction {
        case .gain: "A gain goal needs a positive kg/week rate."
        case .lose: "A loss goal needs a negative kg/week rate."
        case .maintain: "A maintenance goal needs a 0 kg/week rate."
        }
    }
}
