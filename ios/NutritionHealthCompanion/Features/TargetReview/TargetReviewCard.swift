import SwiftUI

struct TargetReviewCard: View {
    @State private var viewModel: TargetReviewViewModel
    @State private var desiredRate = "0"
    @State private var initialCalories = ""
    @State private var calorieWeight = "1"
    @State private var targetRationale = ""
    @State private var showingProteinRejection = false

    init(viewModel: TargetReviewViewModel) {
        _viewModel = State(initialValue: viewModel)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Target Review").font(.headline)
            content
            Divider()
            proteinContent
        }
        .font(.subheadline)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(Color.secondary.opacity(0.08))
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
            Text("Saving creates a new goal version; existing goal history is retained.")
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
            Text("Goal: \(goal.direction.rawValue), \(goal.desiredRateKgPerWeek) kg/week")
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
                    .buttonStyle(.borderedProminent)
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
            Text("Approve the first calorie target")
                .font(.subheadline.bold())
            Text(
                "Enter a target you chose. Nytr will not calculate it from your goal or weight."
            )
            .foregroundStyle(.secondary)
            TextField("Daily calories (kcal)", text: $initialCalories)
                .textFieldStyle(.roundedBorder)
            TextField("Calorie scoring weight", text: $calorieWeight)
                .textFieldStyle(.roundedBorder)
            TextField("Approval rationale", text: $targetRationale)
                .textFieldStyle(.roundedBorder)
            Button("Approve initial target") {
                Task {
                    await viewModel.approveInitialCalorieTarget(
                        caloriesKcal: initialCalories,
                        calorieWeight: calorieWeight,
                        rationale: targetRationale
                    )
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(viewModel.targetActionsDisabled)
            if let message = viewModel.initialTargetMessage {
                Text(message).foregroundStyle(.orange)
            }
            Text("Approval creates an immutable target version and keeps its rationale.")
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
            TextField("kg/week", text: $desiredRate)
                .textFieldStyle(.roundedBorder)
            Button("Save goal") {
                Task {
                    await viewModel.createGoal(
                        desiredRateKgPerWeek: desiredRate
                    )
                }
            }
            .buttonStyle(.borderedProminent)
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
                    LabeledContent("Active protein floor", value: "\(floor.value) g/day")
                    if let policy = viewModel.latestTargetPolicy {
                        LabeledContent("Target version", value: policy.policyVersion)
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
                    "Nytr will not fabricate a proposal. If evidence is missing, sync a current HealthKit body-weight measurement, then check again."
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
            LabeledContent("Proposed", value: "\(proposal.proposedProteinG) g/day")
            LabeledContent("Goal type", value: "Minimum protein floor")
            LabeledContent("Weight evidence", value: "\(proposal.bodyMassKg) kg")
            LabeledContent(
                "Measured",
                value: proposal.calculation.bodyMass.measuredAt.formatted(
                    date: .abbreviated, time: .shortened)
            )
            LabeledContent("Policy", value: proposal.policyVersion)
            Text("Evidence source: \(proposal.provenance)")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(
                "Weight evidence comes from persisted HealthKit data. This deterministic proposal is not a medical diagnosis and is not applied automatically. Approval creates a new active target-policy version; rejection leaves the active target unchanged. Existing target history remains preserved."
            )
            .font(.caption)
            .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func proteinDecisionControls(_ proposal: ProteinTargetProposalResponse) -> some View {
        let acknowledged = viewModel.proteinDecisionResult?.proposalId == proposal.proposalId
            ? viewModel.proteinDecisionResult?.decision
            : proposal.decisionStatus
        switch acknowledged {
        case .approved:
            Label("Approved", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
            Text("Approval created a new immutable target-policy version.")
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
            Text("Review the evidence before making this durable nutrition decision.")
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
                .buttonStyle(.borderedProminent)
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
            LabeledContent(
                "Observed trend",
                value: review.trend.weeklyRateKg.map { "\($0) kg/week" } ?? "Unavailable"
            )
            LabeledContent(
                "Desired rate",
                value: "\(review.goalPolicy.desiredRateKgPerWeek) kg/week"
            )
            LabeledContent(
                "Acceptable band",
                value: "\(review.goalPolicy.acceptableRateLowerKgPerWeek) to \(review.goalPolicy.acceptableRateUpperKgPerWeek)"
            )
            LabeledContent(
                "Current target",
                value: "\(review.targetPolicy.currentCaloriesKcal) kcal/day"
            )
        }
    }

    private func proposal(_ review: TargetReviewDetail) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Review this exact change").font(.subheadline.bold())
            LabeledContent(
                "Proposed",
                value: "\(review.targetPolicy.proposedCaloriesKcal ?? "Unavailable") kcal/day"
            )
            LabeledContent(
                "Change",
                value: "\(review.targetPolicy.calorieDelta ?? "Unavailable") kcal/day"
            )
            Text("This is a deterministic target review, not medical advice.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}
