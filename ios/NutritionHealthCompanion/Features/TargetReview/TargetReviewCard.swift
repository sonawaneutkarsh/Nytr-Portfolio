import SwiftUI

struct TargetReviewCard: View {
    @State private var viewModel: TargetReviewViewModel
    @State private var desiredRate = "0"
    @State private var showingProteinRejection = false

    init(viewModel: TargetReviewViewModel) {
        _viewModel = State(initialValue: viewModel)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Goal & calorie review").font(.headline)
            content
            Divider()
            proteinContent
        }
        .font(.subheadline)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 6)
        .confirmationDialog(
            "Reject this protein proposal?",
            isPresented: $showingProteinRejection,
            titleVisibility: .visible
        ) {
            Button("Reject proposal", role: .destructive) {
                Task { await viewModel.decideProteinProposal(.rejected) }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Rejection is recorded permanently and does not change the active target.")
        }
    }

    @ViewBuilder
    private var content: some View {
        if viewModel.isCreatingNewGoalVersion {
            goalSetup
            Text("Your previous goals remain in history.")
                .font(.caption)
                .foregroundStyle(.secondary)
            if case .goalInputError(let message) = viewModel.phase {
                Text(message).foregroundStyle(.red)
            }
            if case .networkFailure(let message, _) = viewModel.phase {
                Text(message).foregroundStyle(.orange)
            }
        } else {
            phaseContent
        }
    }

    @ViewBuilder
    private var phaseContent: some View {
        switch viewModel.phase {
        case .loading:
            ProgressView("Loading goal…").controlSize(.small)
        case .noGoalConfigured, .goalInputError:
            goalSetup
            if case .goalInputError(let message) = viewModel.phase {
                Text(message).foregroundStyle(.red)
            }
        case .goalConfigured(let goal):
            Text(
                "Goal: \(goal.direction.rawValue), "
                    + "\(NytrNumberFormat.detail(goal.desiredRateKgPerWeek, maximumFractionDigits: 2) ?? goal.desiredRateKgPerWeek) kg/week"
            )
            Button("Set new goal") { viewModel.beginNewGoalVersion() }
                .buttonStyle(.bordered)
                .disabled(viewModel.targetActionsDisabled)
            reviewButton
        case .noTargetPolicy:
            initialTargetSetup
        case .evidenceUnavailable(let review):
            reviewEvidence(review)
            Text("There is not enough current weight evidence for an adjustment.")
                .foregroundStyle(.secondary)
        case .cooldownHold(let review):
            reviewEvidence(review)
            Text("The current target is still in its review cooldown.")
                .foregroundStyle(.secondary)
        case .withinBand(let review):
            reviewEvidence(review)
            Text("The observed trend is within the configured goal band.")
                .foregroundStyle(.secondary)
        case .boundHold(let review):
            reviewEvidence(review)
            Text("The configured target bound prevents this adjustment.")
                .foregroundStyle(.secondary)
        case .recommendationReady(let review):
            reviewEvidence(review)
            proposal(review)
            VStack(alignment: .leading, spacing: 8) {
                Button("Approve") { Task { await viewModel.decide(.approved) } }
                    .buttonStyle(.borderedProminent).tint(NytrDesign.buttonFill)
                    .controlSize(.large)
                Button("Reject") { Task { await viewModel.decide(.rejected) } }
                    .buttonStyle(.bordered)
            }
            .disabled(viewModel.targetActionsDisabled)
        case .approved:
            Text("Approved. Your target was updated to the reviewed proposal.")
                .foregroundStyle(.green)
            reviewButton
        case .rejected:
            Text("Rejected. Your target was not changed.")
                .foregroundStyle(.secondary)
            reviewButton
        case .conflict:
            Text("This review is stale or already has a different decision. Refresh it.")
                .foregroundStyle(.orange)
            reviewButton
        case .networkFailure(let message, let review):
            Text(message).foregroundStyle(.orange)
            if review != nil {
                Button("Return to review") { viewModel.restoreReviewAfterNetworkFailure() }
                    .buttonStyle(.bordered)
            } else {
                Button("Try again") { Task { await viewModel.retryGoalLoad() } }
                    .buttonStyle(.bordered)
            }
        case .signedOut:
            Text("Sign in to review your target.").foregroundStyle(.secondary)
        }
    }

    private var initialTargetSetup: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("No approved calorie target")
                .font(.subheadline.bold())
            Text(
                "Save your profile below, then create a starting estimate. Review and approve it before it becomes active."
            )
            .foregroundStyle(.secondary)
            Text("You decide when a target becomes active.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var goalSetup: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Set a body-weight direction and desired weekly rate.")
                .foregroundStyle(.secondary)
            Picker(
                "Direction",
                selection: Binding(
                    get: { viewModel.selectedGoalDirection },
                    set: { viewModel.selectGoalDirection($0) }
                )
            ) {
                ForEach(GoalDirectionDTO.allCases, id: \.self) { value in
                    Text(value.rawValue.capitalized).tag(value)
                }
            }
            .pickerStyle(.segmented)
            Text("Selected direction: \(viewModel.selectedGoalDirection.rawValue.capitalized)")
                .font(.caption)
                .foregroundStyle(.secondary)
                .accessibilityIdentifier("goal-direction-selection")
            NytrNumberField(title: "Desired change (kg/week)", text: $desiredRate)
                .textFieldStyle(.roundedBorder)
            Button("Save goal") {
                Task {
                    await viewModel.createGoal(
                        desiredRateKgPerWeek: desiredRate
                    )
                }
            }
            .buttonStyle(.borderedProminent).tint(NytrDesign.buttonFill)
            .controlSize(.large)
            .disabled(viewModel.targetActionsDisabled)
        }
    }

    private var reviewButton: some View {
        Button("Review current target") { Task { await viewModel.requestReview() } }
            .buttonStyle(.bordered)
            .disabled(viewModel.targetActionsDisabled)
    }

    @ViewBuilder
    private var proteinContent: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Protein target").font(.subheadline.bold())
            if viewModel.isTargetPolicyResolved {
                if let floor = viewModel.approvedProteinFloor {
                    NytrMetricRow(
                        "Active protein floor",
                        value: "\(NytrNumberFormat.whole(floor.value) ?? floor.value) g/day"
                    )
                    if let policy = viewModel.latestTargetPolicy {
                        DisclosureGroup("Target details") {
                            NytrMetricRow("Target version", value: policy.policyVersion)
                        }
                    }
                } else {
                    Text("No approved protein floor is active.")
                        .foregroundStyle(.secondary)
                }
            } else {
                Text("Checking the active protein target…")
                    .foregroundStyle(.secondary)
            }

            switch viewModel.proteinPhase {
            case .loading:
                ProgressView("Loading protein proposal…").controlSize(.small)
            case .noProposal:
                Text("No protein proposal has been generated.")
                    .foregroundStyle(.secondary)
                generateProteinButton("Generate protein proposal")
            case .proposal(let proposal):
                proteinProposalDetails(proposal)
                proteinDecisionControls(proposal)
            case .evidenceUnavailable(let detail):
                Text("A proposal was not created.").foregroundStyle(.orange)
                Text(detail).foregroundStyle(.secondary)
                Text(
                    "Sync a recent weight from Apple Health, then check again."
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                generateProteinButton("Check for protein proposal")
            case .networkFailure(let message, let previous):
                if let previous { proteinProposalDetails(previous) }
                Text(message).foregroundStyle(.orange)
                Button("Try again") { Task { await viewModel.retryProteinLoad() } }
                    .buttonStyle(.bordered)
                    .disabled(viewModel.targetActionsDisabled)
            case .signedOut:
                Text("Sign in to review your protein target.")
                    .foregroundStyle(.secondary)
            }

            if let message = viewModel.proteinMessage {
                Text(message).font(.caption).foregroundStyle(.orange)
            }
        }
    }

    private func proteinProposalDetails(_ proposal: ProteinTargetProposalResponse) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Protein floor proposal").font(.subheadline.bold())
            NytrMetricRow(
                "Proposed",
                value: "\(NytrNumberFormat.whole(proposal.proposedProteinG) ?? proposal.proposedProteinG) g/day"
            )
            NytrMetricRow("Goal type", value: "Minimum protein floor")
            NytrMetricRow(
                "Weight evidence",
                value: "\(NytrNumberFormat.detail(proposal.bodyMassKg) ?? proposal.bodyMassKg) kg"
            )
            NytrMetricRow(
                "Measured",
                value: proposal.calculation.bodyMass.measuredAt.formatted(
                    date: .abbreviated, time: .shortened)
            )
            DisclosureGroup("Calculation details") {
                NytrMetricRow("Policy", value: proposal.policyVersion)
                NytrMetricRow("Evidence source", value: proposal.provenance)
            }
            Text(
                "Based on your synced Apple Health weight. This proposal is not active until approved. Rejecting it leaves your target unchanged."
            )
            .font(.caption)
            .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func proteinDecisionControls(_ proposal: ProteinTargetProposalResponse) -> some View {
        let acknowledged =
            viewModel.proteinDecisionResult?.proposalId == proposal.proposalId
            ? viewModel.proteinDecisionResult?.decision
            : proposal.decisionStatus
        switch acknowledged {
        case .approved:
            Label("Approved", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
            Text("Your approved protein target is active.")
                .font(.caption)
                .foregroundStyle(.secondary)
            generateProteinButton("Check for updated proposal")
        case .rejected:
            Label("Rejected", systemImage: "xmark.circle")
                .foregroundStyle(.secondary)
            Text("The active target was not changed.")
                .font(.caption)
                .foregroundStyle(.secondary)
            generateProteinButton("Check for updated proposal")
        case nil:
            Text("Review the weight and proposed amount before approving.")
                .font(.caption)
                .foregroundStyle(.secondary)
            Toggle(
                "I reviewed the evidence and want to approve this protein floor",
                isOn: Binding(
                    get: { viewModel.proteinApprovalConfirmed },
                    set: { viewModel.setProteinApprovalConfirmed($0) }
                )
            )
            .toggleStyle(.switch)
            VStack(alignment: .leading, spacing: 8) {
                Button("Approve protein floor") {
                    Task { await viewModel.decideProteinProposal(.approved) }
                }
                .buttonStyle(.borderedProminent).tint(NytrDesign.buttonFill)
                .controlSize(.large)
                .disabled(!viewModel.canApproveProteinProposal)
                Button("Reject proposal") { showingProteinRejection = true }
                    .buttonStyle(.bordered)
                    .disabled(viewModel.targetActionsDisabled)
            }
        }
    }

    private func generateProteinButton(_ label: String) -> some View {
        Button(label) { Task { await viewModel.generateProteinProposal() } }
            .buttonStyle(.bordered)
            .disabled(viewModel.targetActionsDisabled)
    }

    private func reviewEvidence(_ review: TargetReviewDetail) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            NytrMetricRow(
                "Observed trend",
                value: review.trend.weeklyRateKg.map {
                    "\(NytrNumberFormat.detail($0, maximumFractionDigits: 2) ?? $0) kg/week"
                } ?? "Unavailable"
            )
            NytrMetricRow(
                "Desired rate",
                value:
                    "\(NytrNumberFormat.detail(review.goalPolicy.desiredRateKgPerWeek, maximumFractionDigits: 2) ?? review.goalPolicy.desiredRateKgPerWeek) kg/week"
            )
            NytrMetricRow(
                "Acceptable band",
                value:
                    "\(NytrNumberFormat.detail(review.goalPolicy.acceptableRateLowerKgPerWeek, maximumFractionDigits: 2) ?? review.goalPolicy.acceptableRateLowerKgPerWeek) to "
                    + "\(NytrNumberFormat.detail(review.goalPolicy.acceptableRateUpperKgPerWeek, maximumFractionDigits: 2) ?? review.goalPolicy.acceptableRateUpperKgPerWeek) kg/week"
            )
            NytrMetricRow(
                "Current target",
                value:
                    "\(NytrNumberFormat.whole(review.targetPolicy.currentCaloriesKcal) ?? review.targetPolicy.currentCaloriesKcal) kcal/day"
            )
        }
    }

    private func proposal(_ review: TargetReviewDetail) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Review this exact change").font(.subheadline.bold())
            NytrMetricRow(
                "Proposed",
                value: review.targetPolicy.proposedCaloriesKcal.map {
                    "\(NytrNumberFormat.whole($0) ?? $0) kcal/day"
                } ?? "Unavailable"
            )
            NytrMetricRow(
                "Change",
                value: review.targetPolicy.calorieDelta.map {
                    "\(NytrNumberFormat.whole($0) ?? $0) kcal/day"
                } ?? "Unavailable"
            )
            Text("This is a deterministic target review, not medical advice.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}
