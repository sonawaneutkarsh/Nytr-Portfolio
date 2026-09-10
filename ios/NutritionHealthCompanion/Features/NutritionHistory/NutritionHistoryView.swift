import SwiftUI

struct NutritionHistoryView: View {
    @State private var viewModel: NutritionHistoryViewModel
    let subject: String

    init(viewModel: NutritionHistoryViewModel, subject: String) {
        _viewModel = State(initialValue: viewModel)
        self.subject = subject
    }

    var body: some View {
        List {
            switch viewModel.phase {
            case .idle, .loading:
                ProgressView("Loading nutrition history…")
            case .error(let message):
                Section {
                    Text(message).foregroundStyle(.orange)
                    Button("Try Again") { Task { await viewModel.refresh() } }
                }
            case .signedOut:
                Text("Sign in to view nutrition history.")
                    .foregroundStyle(.secondary)
            case .result(let history):
                summary(history.summary)
                ForEach(Array(history.days.reversed()), id: \.localDate) { day in
                    daySection(day)
                }
            }
        }
        .navigationTitle("Nutrition History")
        .refreshable { await viewModel.refresh() }
        .task(id: subject) { await viewModel.activate(subject: subject) }
    }

    @ViewBuilder
    private func summary(_ value: NutritionHistorySummaryResponse) -> some View {
        Section("Last 7 Days") {
            LabeledContent("Days recorded", value: String(value.daysWithConsumption))
            if let calories = value.knownCaloriesTotal {
                LabeledContent("Known calories", value: "\(calories) kcal")
            }
            if let protein = value.knownProteinGTotal {
                LabeledContent("Known protein", value: "\(protein) g")
            }
            if value.daysPartial > 0 || value.daysUnavailable > 0 {
                Text("Some days contain partial or unavailable nutrition evidence.")
                    .foregroundStyle(.orange)
            }
        }
    }

    @ViewBuilder
    private func daySection(_ day: NutritionHistoryDayResponse) -> some View {
        Section(day.localDate) {
            if day.consumedEventCount == 0 {
                Text("No consumption recorded")
                    .foregroundStyle(.secondary)
            } else {
                metric(
                    label: "Calories",
                    known: day.knownCaloriesConsumed,
                    target: day.targetStatus == .available ? day.target?.caloriesKcal : nil,
                    unit: "kcal",
                    partial: day.nutritionCompleteness != .complete
                )
                metric(
                    label: "Protein",
                    known: day.knownProteinGConsumed,
                    target: day.targetStatus == .available ? day.target?.proteinG : nil,
                    unit: "g",
                    partial: day.nutritionCompleteness != .complete
                )
            }
            switch day.targetStatus {
            case .targetChangedDuringDay:
                Text("Target changed during this day; comparison is unavailable.")
                    .foregroundStyle(.orange)
            case .unavailable:
                Text("No full-day approved target was available.")
                    .foregroundStyle(.secondary)
            case .available:
                EmptyView()
            }
            if day.nutritionCompleteness == .partial {
                Text("Known totals are partial.")
                    .foregroundStyle(.orange)
            } else if day.nutritionCompleteness == .unavailable {
                Text("Consumed nutrition is unavailable.")
                    .foregroundStyle(.orange)
            }
        }
    }

    @ViewBuilder
    private func metric(
        label: String,
        known: String?,
        target: String?,
        unit: String,
        partial: Bool
    ) -> some View {
        if let known {
            let prefix = partial ? "\(known)+" : known
            if let target {
                LabeledContent(label, value: "\(prefix) / \(target) \(unit)")
            } else {
                LabeledContent(label, value: "\(prefix) \(unit) known")
            }
        } else {
            LabeledContent(label, value: "Unavailable")
        }
    }
}
