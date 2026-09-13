import SwiftUI

struct AIReviewView: View {
    @State private var viewModel: AIReviewViewModel
    let subject: String

    init(viewModel: AIReviewViewModel, subject: String) {
        _viewModel = State(initialValue: viewModel)
        self.subject = subject
    }

    var body: some View {
        List {
            Section {
                NytrScreenHeader(
                    eyebrow: "INSIGHT", title: "Understand your day",
                    subtitle: "Nytr analysis first. An optional, private second opinion.", symbol: "sparkles")
            }.listRowBackground(Color.clear)
            if let evidence = viewModel.evidence {
                facts(evidence.snapshot)
                if !evidence.modelInput.qualityFlags.isEmpty {
                    Section("Selected meal · nutrient signals") {
                        ForEach(evidence.modelInput.qualityFlags, id: \.self) { flag in
                            Text(
                                flag.replacingOccurrences(of: "selected_", with: "").replacingOccurrences(
                                    of: "_", with: " "))
                        }
                        Text("FDA daily-reference contributions for the selected quantity. Not personal limits.").font(
                            .caption
                        ).foregroundStyle(.secondary)
                    }
                }
            }
            if case .error(let message) = viewModel.phase {
                Section {
                    NytrStateView(title: "Evidence unavailable", message: message, symbol: "wifi.exclamationmark")
                }
            }
            Section {
                if viewModel.isRequesting {
                    ProgressView("Preparing review…")
                    if viewModel.isGenerating {
                        Button("Cancel AI Review") { viewModel.cancelLocalReview() }
                    }
                } else {
                    generateButton
                }
            }
            Section("AI second opinion · on device") {
                if viewModel.modelAvailable {
                    Label("Private to this device", systemImage: "lock.shield")
                    Text(
                        "Apple’s on-device model explains only Nytr’s minimized evidence. No AI provider request or API cost."
                    )
                    .font(.subheadline).foregroundStyle(.secondary)
                    Button("Explain with Apple Intelligence") { Task { await viewModel.explainOnDevice() } }
                        .buttonStyle(.borderedProminent).tint(NytrDesign.buttonFill).controlSize(.large)
                        .disabled(viewModel.evidence == nil || viewModel.isRequesting)
                } else {
                    NytrStateView(
                        title: "AI Review requires Apple Intelligence",
                        message: viewModel.modelUnavailableMessage,
                        symbol: "apple.intelligence")
                }
                if let message = viewModel.localMessage { Text(message).foregroundStyle(.secondary) }
            }
            if case .result(let response) = viewModel.phase { aiExplanation(response) }
        }
        .nytrList()
        .navigationTitle("Nytr Review")
        .task(id: subject) { viewModel.activate(subject: subject) }
    }

    private var generateButton: some View {
        Button("Review current evidence") { Task { await viewModel.generate() } }
            .buttonStyle(.borderedProminent).tint(NytrDesign.buttonFill)
            .controlSize(.large)
            .disabled(viewModel.isRequesting)
    }

    @ViewBuilder
    private func facts(_ snapshot: AIReviewSnapshotDTO) -> some View {
        Section("Nytr analysis · deterministic") {
            NytrMetricRow(
                "Goal",
                value: snapshot.goal.mode?.capitalized ?? "Unavailable"
            )
            NytrMetricRow("Goal band", value: label(snapshot.goal.bandStatus))
            NytrMetricRow(
                "Active targets",
                value: targetText(snapshot.targets)
            )
            NytrMetricRow(
                "Recorded nutrition today",
                value: recordedText(snapshot.todayRecorded)
            )
            NytrMetricRow("Weight evidence", value: label(snapshot.bodyTrend.status))
            if let age = snapshot.bodyTrend.latestMeasurementAgeDays {
                NytrMetricRow("Latest weight age", value: "\(age) days")
            }
            NytrMetricRow(
                "Recorded days",
                value: "\(snapshot.recordedNutritionProgress.daysWithRecords7d) of 7 recent"
            )
            NytrMetricRow("Next Meal", value: label(snapshot.nextMeal.status))
        }
    }

    @ViewBuilder
    private func aiExplanation(_ response: AIReviewResponse) -> some View {
        if let review = response.review {
            Section {
                VStack(alignment: .leading, spacing: 10) {
                    Label("AI explanation", systemImage: "sparkles")
                        .font(.headline)
                        .foregroundStyle(Color.accentColor)
                    Text(response.authorityNotice)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(review.summary)
                    textGroup("Key findings", review.attentionItems)
                    textGroup("Considerations", review.evidenceNotes)
                    textGroup("Limitations", review.limitations)
                }
                .padding(.vertical, 4)
                .accessibilityElement(children: .contain)
            }
            .listRowBackground(Color.accentColor.opacity(0.06))
        }
    }

    @ViewBuilder
    private func textGroup(_ title: String, _ items: [String]) -> some View {
        if !items.isEmpty {
            Text(title).font(.subheadline.weight(.semibold))
            ForEach(items, id: \.self) { item in
                Text("• \(item)").font(.subheadline)
            }
        }
    }

    private func targetText(_ targets: AIReviewTargetsDTO) -> String {
        let calories =
            targets.caloriesKcal.map { "\(NytrNumberFormat.whole($0) ?? $0) kcal target" }
            ?? "calorie target unavailable"
        let proteinLabel = targets.proteinKind == "floor" ? "protein floor" : "protein target"
        let protein =
            targets.proteinG.map { "\(NytrNumberFormat.whole($0) ?? $0) g \(proteinLabel)" }
            ?? "\(proteinLabel) unavailable"
        return "\(calories) · \(protein)"
    }

    private func recordedText(_ recorded: AIReviewTodayRecordedDTO) -> String {
        let calories = recorded.caloriesKcal.map {
            "\(NytrNumberFormat.whole($0) ?? $0) kcal"
        } ?? "calories unavailable"
        let protein = recorded.proteinG.map {
            "\(NytrNumberFormat.whole($0) ?? $0) g protein"
        } ?? "protein unavailable"
        return "\(calories) · \(protein) · \(label(recorded.completeness))"
    }

    private func label(_ value: String) -> String {
        value.replacingOccurrences(of: "_", with: " ").capitalized
    }
}

struct AIReviewFailurePresentation: Equatable {
    let title: String
    let message: String
    let allowsRetry: Bool
    var isPrivacyChoice: Bool = false

    static func forCode(_ code: String?) -> Self {
        switch code {
        case "ai_disabled_for_privacy":
            return Self(
                title: "AI Review is disabled for privacy",
                message:
                    "Your personal health and nutrition evidence stays out of AI providers. Nytr’s deterministic analysis remains available.",
                allowsRetry: false,
                isPrivacyChoice: true
            )
        case "ai_not_configured":
            return Self(
                title: "AI review not configured",
                message: "AI review is not configured. Nytr's other features remain available.",
                allowsRetry: false
            )
        case "ai_rate_limited":
            return Self(
                title: "AI review rate limited",
                message: "The provider is rate limited. Retry explicitly later.",
                allowsRetry: true
            )
        case "ai_timeout":
            return Self(
                title: "AI provider temporarily unavailable",
                message: "The provider did not respond in time.",
                allowsRetry: true
            )
        case "ai_provider_refused":
            return Self(
                title: "AI review unavailable",
                message: "The provider could not produce this review.",
                allowsRetry: true
            )
        case "ai_provider_invalid":
            return Self(
                title: "AI response could not be validated",
                message: "The provider returned an invalid review.",
                allowsRetry: true
            )
        default:
            return Self(
                title: "AI provider temporarily unavailable",
                message: "The provider is temporarily unavailable.",
                allowsRetry: true
            )
        }
    }
}
