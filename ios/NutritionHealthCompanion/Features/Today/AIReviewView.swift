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
                Text("Generate a concise AI explanation of Nytr’s current deterministic state.")
                    .foregroundStyle(.secondary)
                Text("The explanation cannot change targets, recommendations, or recorded consumption.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            switch viewModel.phase {
            case .idle:
                Section { generateButton }
            case .loading:
                Section {
                    ProgressView("Generating Nytr Review…")
                        .accessibilityLabel("Generating Nytr Review")
                }
            case .result(let response):
                facts(response.snapshot)
                aiExplanation(response)
                Section { generateButton }
            case .unavailable(let response):
                facts(response.snapshot)
                Section("AI unavailable") {
                    Label(
                        unavailableMessage(response.failureCode),
                        systemImage: "exclamationmark.triangle.fill"
                    )
                    .foregroundStyle(.orange)
                    Button("Try Again") { Task { await viewModel.generate() } }
                }
            case .error(let message):
                Section("AI unavailable") {
                    Label(message, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                    Button("Try Again") { Task { await viewModel.generate() } }
                }
            case .signedOut:
                Section { Text("Sign in to generate a review.").foregroundStyle(.secondary) }
            }
        }
        .navigationTitle("Nytr Review")
        .task(id: subject) { viewModel.activate(subject: subject) }
    }

    private var generateButton: some View {
        Button("Generate Review") { Task { await viewModel.generate() } }
            .buttonStyle(.borderedProminent)
            .disabled(viewModel.isRequesting)
    }

    @ViewBuilder
    private func facts(_ snapshot: AIReviewSnapshotDTO) -> some View {
        Section("Nytr facts") {
            LabeledContent(
                "Goal",
                value: snapshot.goal.mode?.capitalized ?? "Unavailable"
            )
            LabeledContent("Goal band", value: label(snapshot.goal.bandStatus))
            LabeledContent(
                "Active targets",
                value: targetText(snapshot.targets)
            )
            LabeledContent(
                "Recorded nutrition today",
                value: recordedText(snapshot.todayRecorded)
            )
            LabeledContent("Weight evidence", value: label(snapshot.bodyTrend.status))
            if let age = snapshot.bodyTrend.latestMeasurementAgeDays {
                LabeledContent("Latest weight age", value: "\(age) days")
            }
            LabeledContent(
                "Recorded days",
                value: "\(snapshot.recordedNutritionProgress.daysWithRecords7d) of 7 recent"
            )
            LabeledContent("Next Meal", value: label(snapshot.nextMeal.status))
        }
    }

    @ViewBuilder
    private func aiExplanation(_ response: AIReviewResponse) -> some View {
        if let review = response.review {
            Section {
                VStack(alignment: .leading, spacing: 10) {
                    Label("AI explanation", systemImage: "sparkles")
                        .font(.headline)
                        .foregroundStyle(.purple)
                    Text(response.authorityNotice)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(review.summary)
                    textGroup("Attention", review.attentionItems)
                    textGroup("Evidence", review.evidenceNotes)
                    textGroup("Limitations", review.limitations)
                }
                .padding(.vertical, 4)
                .accessibilityElement(children: .contain)
            }
            .listRowBackground(Color.purple.opacity(0.08))
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
        let calories = targets.caloriesKcal.map { "\($0) kcal target" }
            ?? "calorie target unavailable"
        let proteinLabel = targets.proteinKind == "floor" ? "protein floor" : "protein target"
        let protein = targets.proteinG.map { "\($0) g \(proteinLabel)" }
            ?? "\(proteinLabel) unavailable"
        return "\(calories) · \(protein)"
    }

    private func recordedText(_ recorded: AIReviewTodayRecordedDTO) -> String {
        let calories = recorded.caloriesKcal.map { "\($0) kcal" } ?? "calories unavailable"
        let protein = recorded.proteinG.map { "\($0) g protein" } ?? "protein unavailable"
        return "\(calories) · \(protein) · \(label(recorded.completeness))"
    }

    private func unavailableMessage(_ code: String?) -> String {
        switch code {
        case "ai_not_configured": return "AI review is not configured. Nytr's other features remain available."
        case "ai_rate_limited": return "The provider is rate limited. Retry explicitly later."
        case "ai_timeout": return "The provider did not respond in time."
        case "ai_provider_refused": return "The provider could not produce this review."
        case "ai_provider_invalid": return "The provider returned an invalid review."
        case "ai_authentication_failed": return "The AI provider could not authenticate. Nytr's other features remain available."
        case "ai_invalid_request": return "The AI review request was rejected. Nytr's other features remain available."
        default: return "The provider is temporarily unavailable."
        }
    }

    private func label(_ value: String) -> String {
        value.replacingOccurrences(of: "_", with: " ").capitalized
    }
}
