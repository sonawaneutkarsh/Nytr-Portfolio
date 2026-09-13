import SwiftUI

/// Food-tab presentation of the existing canonical daily plan. No planning,
/// ranking, nutrition arithmetic, or consumption state is duplicated here.
struct MealDiscoverySections: View {
    @State private var viewModel: TodayViewModel

    init(viewModel: TodayViewModel) {
        _viewModel = State(initialValue: viewModel)
    }

    var body: some View {
        switch viewModel.phase {
        case .completed(let plan):
            mealSections(plan, notice: nil)
        case .cached(let plan, _):
            mealSections(plan, notice: "Refreshing this saved plan…")
        case .offline(let plan?, _):
            mealSections(plan, notice: "Offline saved plan · view only")
        case .loading:
            boundedSection("Lunch", message: "Loading today’s plan…", progress: true)
            boundedSection("Dinner", message: "Loading today’s plan…", progress: true)
        case .notGenerated:
            boundedSection(
                "Lunch",
                message: "No plan has been generated for today.",
                action: "Generate today’s plan"
            )
            boundedSection("Dinner", message: "No plan has been generated for today.")
        case .generating:
            boundedSection("Lunch", message: "Generating today’s plan…", progress: true)
            boundedSection("Dinner", message: "Generating today’s plan…", progress: true)
        case .noApprovedPolicy:
            boundedSection("Lunch", message: "Approved nutrition targets are required.")
            boundedSection("Dinner", message: "Approved nutrition targets are required.")
        case .menuDataUnavailable:
            boundedSection("Lunch", message: "Validated Stacks menu evidence is unavailable.")
            boundedSection("Dinner", message: "Validated Stacks menu evidence is unavailable.")
        case .noPlan(let value):
            let message = value.reasonCodes.first.map(friendlyReason)
                ?? "No eligible plan is available."
            boundedSection("Lunch", message: message)
            boundedSection("Dinner", message: message)
        case .offline(nil, _):
            boundedSection("Lunch", message: "Offline · no saved plan is available.")
            boundedSection("Dinner", message: "Offline · no saved plan is available.")
        case .error(let message):
            boundedSection("Lunch", message: message, action: "Try Again")
            boundedSection("Dinner", message: message)
        case .signedOut:
            boundedSection("Lunch", message: "Sign in to view meal guidance.")
            boundedSection("Dinner", message: "Sign in to view meal guidance.")
        }
    }

    @ViewBuilder
    private func mealSections(_ plan: CompletedDayPlan, notice: String?) -> some View {
        mealSection(title: "Lunch", contexts: ["lunch", "post_workout_lunch"], plan: plan, notice: notice)
        mealSection(title: "Dinner", contexts: ["dinner"], plan: plan, notice: notice)
    }

    @ViewBuilder
    private func mealSection(
        title: String,
        contexts: Set<String>,
        plan: CompletedDayPlan,
        notice: String?
    ) -> some View {
        let matches = plan.plan.slots.enumerated().filter { contexts.contains($0.element.context) }
        Section(title) {
            if let notice { Text(notice).font(.caption).foregroundStyle(.orange) }
            if matches.isEmpty {
                Text("No \(title.lowercased()) opportunity is covered by today’s plan.")
                    .foregroundStyle(.secondary)
            }
            ForEach(matches, id: \.offset) { slotIndex, slot in
                if let presentation = plan.presentation(forSlotAt: slotIndex),
                    let primary = presentation.primary
                {
                    mealCard(primary)
                    if !presentation.alternatives.isEmpty {
                        DisclosureGroup("Alternatives") {
                            ForEach(presentation.alternatives, id: \.rank) { alternative in
                                mealCard(alternative)
                            }
                        }
                    }
                } else {
                    let reasons = slot.failureReasons.isEmpty
                        ? ["No eligible meal is available for this period."]
                        : slot.failureReasons.map(friendlyReason)
                    ForEach(reasons, id: \.self) { reason in
                        Label(reason, systemImage: "info.circle")
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func mealCard(_ presentation: PlanCandidatePresentation) -> some View {
        let candidate = presentation.candidate
        let calories = candidate.totals.quantities["calories_kcal"]
        let protein = candidate.totals.quantities["protein_g"]
        VStack(alignment: .leading, spacing: 10) {
            Text(presentation.mealName.isEmpty ? "Stacks meal" : presentation.mealName)
                .font(.headline)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 16) {
                Label(
                    calories.map { "\(NytrNumberFormat.whole($0) ?? $0) kcal" } ?? "Calories unknown",
                    systemImage: "bolt"
                )
                Label(
                    protein.map { "\(NytrNumberFormat.whole($0) ?? $0) g protein" } ?? "Protein unknown",
                    systemImage: "leaf"
                )
            }
            .font(.subheadline)
            .foregroundStyle(.secondary)

            if candidate.configurableEstimate != nil {
                Label("Estimated portions", systemImage: "info.circle")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.orange)
            }

            DisclosureGroup("Nutrition and source details") {
                ForEach(candidate.totals.quantities.keys.sorted(), id: \.self) { nutrient in
                    if let value = candidate.totals.quantities[nutrient] {
                        let display = nutrientDisplay(nutrient, value: value)
                        NytrMetricRow(display.label, value: display.value)
                    }
                }
                Text("Nutrition confidence: \(candidate.totals.confidence.replacingOccurrences(of: "_", with: " "))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if !candidate.totals.declaredUnavailable.isEmpty {
                    Text("Unknown values: \(candidate.totals.declaredUnavailable.joined(separator: ", "))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            if let reference = presentation.itemReference {
                if let recorded = viewModel.latestConsumption(for: reference.itemId) {
                    Label(
                        "Status: \(recorded.state.rawValue.capitalized)",
                        systemImage: "checkmark.circle"
                    )
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
                Menu("Update meal status") {
                    ForEach(ConsumptionState.allCases, id: \.self) { state in
                        Button(state.rawValue.capitalized) {
                            Task { await viewModel.recordConsumption(item: reference, state: state) }
                        }
                    }
                }
                .buttonStyle(.bordered)
                .disabled(!viewModel.canMutate)
                .accessibilityHint("Records an explicit status for this recommended plan item")
            } else {
                Text("Consumption action unavailable for this plan item.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 6)
    }

    @ViewBuilder
    private func boundedSection(
        _ title: String,
        message: String,
        progress: Bool = false,
        action: String? = nil
    ) -> some View {
        Section(title) {
            if progress { ProgressView(message) } else { Text(message).foregroundStyle(.secondary) }
            if let action {
                Button(action) {
                    Task {
                        if viewModel.phase == .notGenerated { await viewModel.generate() }
                        else { await viewModel.refresh() }
                    }
                }
            }
        }
    }

    private func friendlyReason(_ value: String) -> String {
        switch value.lowercased() {
        case "menu_data_unavailable", "menu_unavailable":
            "Validated Stacks menu evidence is unavailable."
        case "menu_stale", "stale_menu_data":
            "Validated Stacks menu evidence is stale."
        case "empty_menu_period":
            "Stacks published no items for this meal period."
        case "no_eligible_candidate":
            "No meal passed the current nutrition and dietary rules."
        default:
            value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private func nutrientDisplay(_ key: String, value: String) -> (label: String, value: String) {
        let amount = NytrNumberFormat.detail(value) ?? value
        switch key {
        case "calories_kcal": return ("Calories", "\(NytrNumberFormat.whole(value) ?? value) kcal")
        case "protein_g": return ("Protein", "\(amount) g")
        case "carbohydrate_g": return ("Carbohydrate", "\(amount) g")
        case "total_fat_g": return ("Total fat", "\(amount) g")
        case "saturated_fat_g": return ("Saturated fat", "\(amount) g")
        case "trans_fat_g": return ("Trans fat", "\(amount) g")
        case "fiber_g": return ("Fiber", "\(amount) g")
        case "sugars_g": return ("Sugars", "\(amount) g")
        case "added_sugars_g": return ("Added sugars", "\(amount) g")
        case "sodium_mg": return ("Sodium", "\(NytrNumberFormat.whole(value) ?? value) mg")
        case "cholesterol_mg": return ("Cholesterol", "\(NytrNumberFormat.whole(value) ?? value) mg")
        default: return (key.replacingOccurrences(of: "_", with: " ").capitalized, amount)
        }
    }
}
