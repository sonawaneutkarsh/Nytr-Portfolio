import SwiftUI

struct TodayView: View {
    @State private var viewModel: TodayViewModel
    @State private var nutritionHistoryViewModel: NutritionHistoryViewModel
    @State private var progressViewModel: ProgressViewModel
    @State private var targetReviewViewModel: TargetReviewViewModel
    @State private var manualFoodViewModel: ManualFoodViewModel
    @State private var aiReviewViewModel: AIReviewViewModel
    @State private var bodyGoalsViewModel: BodyGoalsViewModel
    let healthSyncViewModel: HealthSyncViewModel
    let subject: String
    let onSignOut: () -> Void

    init(
        viewModel: TodayViewModel,
        nutritionHistoryViewModel: NutritionHistoryViewModel,
        progressViewModel: ProgressViewModel,
        targetReviewViewModel: TargetReviewViewModel,
        manualFoodViewModel: ManualFoodViewModel,
        aiReviewViewModel: AIReviewViewModel,
        bodyGoalsViewModel: BodyGoalsViewModel,
        healthSyncViewModel: HealthSyncViewModel,
        subject: String,
        onSignOut: @escaping () -> Void
    ) {
        _viewModel = State(initialValue: viewModel)
        _nutritionHistoryViewModel = State(initialValue: nutritionHistoryViewModel)
        _progressViewModel = State(initialValue: progressViewModel)
        _targetReviewViewModel = State(initialValue: targetReviewViewModel)
        _manualFoodViewModel = State(initialValue: manualFoodViewModel)
        _aiReviewViewModel = State(initialValue: aiReviewViewModel)
        _bodyGoalsViewModel = State(initialValue: bodyGoalsViewModel)
        self.healthSyncViewModel = healthSyncViewModel
        self.subject = subject
        self.onSignOut = onSignOut
    }

    var body: some View {
        NavigationStack {
            List {
                TargetReviewCard(viewModel: targetReviewViewModel)
                ledgerCard
                nextMealCard
                NavigationLink("Add Food") {
                    ManualFoodView(viewModel: manualFoodViewModel)
                }
                NavigationLink("Nutrition History") {
                    NutritionHistoryView(
                        viewModel: nutritionHistoryViewModel,
                        subject: subject
                    )
                }
                NavigationLink("Progress") {
                    OwnerProgressView(viewModel: progressViewModel, subject: subject)
                }
                NavigationLink("Nytr Review") {
                    AIReviewView(viewModel: aiReviewViewModel, subject: subject)
                }
                NavigationLink("Body & Goals") {
                    BodyGoalsView(viewModel: bodyGoalsViewModel, subject: subject)
                }
                trendCard
                planContent
            }
            #if os(iOS)
            .listStyle(.insetGrouped)
            #endif
            .refreshable { await viewModel.refresh() }
            .navigationTitle("Today")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    NavigationLink("Settings") {
                        SettingsView(
                            healthSyncViewModel: healthSyncViewModel,
                            onSignOut: onSignOut
                        )
                    }
                }
            }
        }
        .task(id: subject) {
            async let plan: Void = viewModel.activate(subject: subject)
            async let review: Void = targetReviewViewModel.activate(subject: subject)
            _ = await (plan, review)
        }
    }

    @ViewBuilder
    private var nextMealCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Next Meal").font(.headline)
            switch viewModel.nextMealPhase {
            case .loading:
                ProgressView("Loading latest recommendation…").controlSize(.small)
            case .notGenerated:
                Text("Generate from today’s recorded nutrition and remaining meal opportunities.")
                    .foregroundStyle(.secondary)
                nextMealButton
            case .generating:
                ProgressView("Checking remaining opportunities…").controlSize(.small)
            case .error(let message):
                Text(message).foregroundStyle(.orange)
                nextMealButton
            case .signedOut:
                Text("Sign in to request a recommendation.").foregroundStyle(.secondary)
            case .result(let response):
                nextMealResult(response)
                nextMealButton
            }
        }
        .font(.subheadline)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(Color.secondary.opacity(0.08))
    }

    private var nextMealButton: some View {
        Button("Recommend next meal") {
            Task { await viewModel.generateNextMeal() }
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.small)
    }

    @ViewBuilder
    private func nextMealResult(_ response: NextMealRecommendationResponse) -> some View {
        let artifact = response.artifact
        if response.status == .recommended,
            let candidate = artifact.selected,
            let ledger = artifact.ledger,
            let allocated = artifact.allocatedTargets
        {
            Text(nextMealName(candidate)).font(.headline)
            if let opportunity = artifact.selectedOpportunity {
                Text(
                    "For \(opportunity.context.replacingOccurrences(of: "_", with: " "))"
                )
                    .foregroundStyle(.secondary)
            }
            LabeledContent(
                "Remaining from records",
                value: "\(ledger.remainingCalories ?? "unknown") kcal · "
                    + "\(ledger.remainingProteinG ?? "unknown") g protein"
            )
            LabeledContent(
                "This opportunity",
                value: "\(allocated.caloriesKcal) kcal · \(allocated.proteinG) g protein"
            )
            let mealCalories = candidate.totals.quantities["calories_kcal"]
            let mealProtein = candidate.totals.quantities["protein_g"]
            if let mealCalories, let mealProtein {
                LabeledContent(
                    "Recommended meal",
                    value: "\(mealCalories) kcal · \(mealProtein) g protein"
                )
            } else if let mealCalories {
                LabeledContent("Recommended meal", value: "\(mealCalories) kcal")
            } else if let mealProtein {
                LabeledContent("Recommended meal", value: "\(mealProtein) g protein")
            }
            if candidate.configurableEstimate != nil {
                Text("Estimated nutrition — review portions and caveats.")
                    .foregroundStyle(.orange)
            } else {
                Text("Nutrition pinned to accepted Stacks menu evidence.")
                    .foregroundStyle(.secondary)
            }
            Text("Nutrition confidence: \(candidate.totals.confidence)")
                .font(.caption)
                .foregroundStyle(.secondary)
            if !allocated.proteinScoringActive {
                Text("Protein goal already satisfied; ranking did not chase more protein.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text("A recommendation is not consumption and does not change recorded totals.")
                .font(.caption)
                .foregroundStyle(.secondary)
            if let alternatives = artifact.alternatives, !alternatives.isEmpty {
                DisclosureGroup("Alternatives") {
                    ForEach(alternatives, id: \.candidateId) { alternative in
                        Text(nextMealName(alternative))
                    }
                }
            }
            nextMealConsumptionControls(response)
        } else {
            Text(nextMealStateTitle(response.status)).font(.headline)
            Text(response.reasonCodes.map(friendlyReason).joined(separator: " · "))
                .foregroundStyle(.secondary)
        }
        Text(
            "Generated \(response.decisionAt.formatted()) · \(artifact.nextMealPolicyVersion)"
        )
            .font(.caption2)
            .foregroundStyle(.secondary)
    }

    @ViewBuilder
    private func nextMealConsumptionControls(_ response: NextMealRecommendationResponse)
        -> some View
    {
        if let consumed = viewModel.nextMealConsumption,
            consumed.recommendationId == response.recommendationId
        {
            Label("Recorded as eaten", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
            Text("Recorded \(consumed.recordedAt.formatted())")
                .font(.caption)
                .foregroundStyle(.secondary)
        } else {
            Toggle(
                "I confirm I ate this recommendation",
                isOn: Binding(
                    get: { viewModel.nextMealConsumptionConfirmed },
                    set: { viewModel.setNextMealConsumptionConfirmed($0) }
                )
            )
            .toggleStyle(.switch)
            if viewModel.isRecordingNextMealConsumption {
                ProgressView("Recording factual consumption…").controlSize(.small)
            } else {
                Button("Record as eaten") {
                    Task { await viewModel.recordNextMealConsumption() }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .disabled(!viewModel.canRecordNextMealConsumption)
            }
        }
        if let message = viewModel.nextMealConsumptionMessage {
            Text(message)
                .font(.caption)
                .foregroundStyle(viewModel.nextMealConsumption == nil ? .orange : .secondary)
        }
    }

    private func nextMealName(_ candidate: PlanCandidate) -> String {
        if let estimated = candidate.configurableEstimate {
            return estimated.definition.configurationSummary
        }
        let names = candidate.lines.map(\.nameNormalized)
        return names.isEmpty ? "Stacks meal" : names.joined(separator: " + ")
    }

    private func nextMealStateTitle(_ status: NextMealStatusDTO) -> String {
        switch status {
        case .recommended: return "Recommendation ready"
        case .noApprovedTargetPolicy: return "Approved targets required"
        case .noApprovedCalorieTarget: return "Approved calorie target required"
        case .noApprovedProteinTarget: return "Approved protein target required"
        case .unsupportedTargetSemantics: return "Targets need review"
        case .incompleteLedgerNutrition: return "Consumed nutrition is incomplete"
        case .noRemainingMealOpportunity: return "No Stacks opportunity remains"
        case .dailyCalorieTargetMet: return "Calorie target already met"
        case .menuDataUnavailable: return "Validated menu unavailable"
        case .staleMenuData: return "Validated menu is stale"
        case .noEligibleCandidate: return "No eligible meal found"
        }
    }

    @ViewBuilder
    private var ledgerCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Today’s nutrition").font(.headline)
            switch viewModel.ledgerPhase {
            case .loading:
                ProgressView("Loading recorded totals…").controlSize(.small)
            case .error(let message):
                Text(message).foregroundStyle(.orange)
                Button("Try Again") { Task { await viewModel.refresh() } }
                    .controlSize(.small)
            case .signedOut:
                Text("Sign in to view recorded totals.").foregroundStyle(.secondary)
            case .result(let ledger):
                ledgerMetric(
                    label: "Recorded calories",
                    consumed: ledger.knownCaloriesConsumed,
                    target: ledger.target?.caloriesKcal,
                    remaining: ledger.remainingKnownCalories,
                    unit: "kcal"
                )
                ledgerMetric(
                    label: "Recorded protein",
                    consumed: ledger.knownProteinGConsumed,
                    target: ledger.target?.proteinG,
                    remaining: ledger.remainingKnownProteinG,
                    unit: "g"
                )
                Text(
                    "Based on \(ledger.consumedItemCount) recorded item"
                        + (ledger.consumedItemCount == 1 ? "." : "s.")
                )
                    .foregroundStyle(.secondary)
                if ledger.nutritionCompleteness != .complete {
                    Text(
                        ledger.nutritionCompleteness == .partial
                            ? "Recorded totals are partial; some nutrition values are unavailable."
                            : "Recorded nutrition totals are unavailable."
                    )
                    .foregroundStyle(.orange)
                } else if ledger.nutritionAuthorities.contains(.estimated)
                    || ledger.nutritionAuthorities.contains(.partial)
                {
                    Text("Totals include estimated nutrition.")
                        .foregroundStyle(.orange)
                }
                Text("Recorded nutrition reflects logged consumption only; unlogged intake cannot be inferred.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .font(.subheadline)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(Color.secondary.opacity(0.08))
    }

    @ViewBuilder
    private func ledgerMetric(
        label: String,
        consumed: String?,
        target: String?,
        remaining: String?,
        unit: String
    ) -> some View {
        if let consumed {
            if let target {
                LabeledContent(label, value: "\(consumed) / \(target) \(unit)")
            } else {
                LabeledContent(label, value: "\(consumed) \(unit)")
            }
        } else {
            LabeledContent(label, value: "Unavailable")
        }
        if let remaining {
            if remaining.hasPrefix("-") {
                Text("\(String(remaining.dropFirst())) \(unit) over target")
                    .foregroundStyle(.secondary)
            } else {
                Text("\(remaining) \(unit) remaining against recorded nutrition")
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private var planContent: some View {
        switch viewModel.phase {
        case .loading:
            ProgressView("Loading today’s plan…")
        case .cached(let plan, let cachedAt):
            planList(plan, banner: "Showing cached plan from \(cachedAt.formatted()). Refreshing…")
        case .completed(let plan):
            planList(plan, banner: nil)
        case .offline(let plan?, let cachedAt):
            planList(
                plan,
                banner: "Offline — cached \(cachedAt?.formatted() ?? "previously"). View only."
            )
        case .offline(nil, _):
            messageView(
                title: "Offline",
                detail: "No cached plan is available for today.",
                action: "Try Again",
                handler: { Task { await viewModel.refresh() } }
            )
        case .notGenerated:
            messageView(
                title: "No plan generated",
                detail: "Generate today’s plan when you’re ready.",
                action: "Generate today’s plan",
                handler: { Task { await viewModel.generate() } }
            )
        case .generating:
            ProgressView("Generating today’s plan…")
        case .noApprovedPolicy:
            messageView(
                title: "Target policy required",
                detail: "Approve a target policy before generating a plan.",
                action: "Refresh",
                handler: { Task { await viewModel.refresh() } }
            )
        case .menuDataUnavailable:
            messageView(
                title: "Today’s menu isn’t available",
                detail: "Nytr has no validated Stacks menu data for today, "
                    + "so it will not fabricate a plan.",
                action: "Refresh",
                handler: { Task { await viewModel.refresh() } }
            )
        case .noPlan(let value):
            Section("No plan") {
                Text(value.requestedDate)
                ForEach(value.reasonCodes, id: \.self) { reason in
                    Label(friendlyReason(reason), systemImage: "info.circle")
                }
            }
        case .error(let message):
            messageView(
                title: "Plan unavailable",
                detail: message,
                action: "Try Again",
                handler: { Task { await viewModel.refresh() } }
            )
        case .signedOut:
            ProgressView("Returning to sign in…")
        }
    }

    @ViewBuilder
    private var trendCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Weight trend").font(.headline)
            switch viewModel.trendPhase {
            case .loading:
                ProgressView("Loading trend…").controlSize(.small)
            case .error(let message):
                Text(message).foregroundStyle(.orange)
                Button("Try Again") { Task { await viewModel.refresh() } }
                    .controlSize(.small)
            case .signedOut:
                Text("Sign in to view your trend.").foregroundStyle(.secondary)
            case .result(let trend):
                trendResult(trend)
            }
        }
        .font(.subheadline)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(Color.secondary.opacity(0.08))
    }

    @ViewBuilder
    private func trendResult(_ trend: BodyMassTrendResponse) -> some View {
        switch trend.status {
        case .noData:
            Text("No recent body-weight data is available.")
                .foregroundStyle(.secondary)
        case .insufficient:
            Text("More measurements are needed before a reliable trend is available.")
                .foregroundStyle(.secondary)
            Text("\(trend.representedDayCount) days represented")
                .foregroundStyle(.secondary)
        case .stale:
            Text("The most recent weight measurement is stale.")
                .foregroundStyle(.secondary)
            if let age = trend.latestMeasurementAgeDays {
                Text("Latest measurement: \(age) days ago")
                    .foregroundStyle(.secondary)
            }
        case .ready:
            if let average = trend.formattedTrailingAverageKg {
                Text("7-day avg: \(average) kg")
            } else {
                Text("7-day average unavailable").foregroundStyle(.secondary)
            }
            if let rate = trend.formattedWeeklyRateKg {
                Text("Trend: \(rate) kg/week")
            } else {
                Text("Weekly rate unavailable").foregroundStyle(.secondary)
            }
            Text(
                "\(trend.representedDayCount) days across \(trend.coverageSpanDays) calendar days"
            )
            .foregroundStyle(.secondary)
        }
    }

    private func messageView(
        title: String,
        detail: String,
        action: String,
        handler: @escaping () -> Void
    ) -> some View {
        VStack(spacing: 16) {
            Text(title).font(.title2)
            Text(detail).foregroundStyle(.secondary).multilineTextAlignment(.center)
            Button(action, action: handler)
        }
        .padding()
    }

    @ViewBuilder
    private func planList(_ plan: CompletedDayPlan, banner: String?) -> some View {
        if let banner {
            Section { Text(banner).foregroundStyle(.orange) }
        }
        Section("Plan") {
            LabeledContent("Date", value: plan.planDate)
            if let policy = plan.targetPolicy {
                LabeledContent("Target policy", value: policy.policyVersion)
            }
            if let generatedAt = plan.generatedAt {
                LabeledContent("Generated", value: generatedAt.formatted())
            }
        }
        if viewModel.showsRegenerateAction {
            Section {
                Button {
                    Task { await viewModel.regenerate() }
                } label: {
                    if viewModel.isRegenerating {
                        HStack {
                            ProgressView().controlSize(.small)
                            Text("Regenerating Today…")
                        }
                    } else {
                        Text("Regenerate Today")
                    }
                }
                .disabled(!viewModel.canRegenerate)
                if let message = viewModel.regenerationMessage {
                    Text(message).foregroundStyle(.orange)
                }
            }
        }
        if let message = viewModel.consumptionMessage {
            Section { Text(message).foregroundStyle(.orange) }
        }
        ForEach(Array(plan.plan.slots.enumerated()), id: \.offset) { slotIndex, slot in
            Section("\(friendlyContext(slot.context)) · \(slot.menuPeriod)") {
                if slot.candidates.isEmpty {
                    ForEach(slot.failureReasons, id: \.self) { reason in
                        Label(friendlyReason(reason), systemImage: "info.circle")
                    }
                }
                if let presentation = plan.presentation(forSlotAt: slotIndex),
                    let primary = presentation.primary
                {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Recommended")
                            .font(.subheadline)
                            .fontWeight(.semibold)
                            .foregroundStyle(.secondary)
                        candidateView(primary)
                    }
                    if !presentation.alternatives.isEmpty {
                        DisclosureGroup("Alternatives (\(presentation.alternatives.count))") {
                            ForEach(presentation.alternatives, id: \.rank) { alternative in
                                candidateView(alternative)
                            }
                        }
                    }
                }
            }
        }
    }

    private func candidateView(
        _ presentation: PlanCandidatePresentation
    ) -> some View {
        let candidate = presentation.candidate
        let reference = presentation.itemReference
        return VStack(alignment: .leading, spacing: 8) {
            Text(presentation.mealName)
                .font(.headline)
            if let estimated = candidate.configurableEstimate {
                Text("Estimated nutrition")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(.orange.opacity(0.16), in: Capsule())
                    .foregroundStyle(.orange)
                Text(estimated.definition.configurationSummary)
                    .font(.subheadline)
                if let calories = candidate.totals.quantities["calories_kcal"] {
                    LabeledContent("Estimated calories", value: calories)
                }
                if let protein = candidate.totals.quantities["protein_g"] {
                    LabeledContent("Estimated protein (g)", value: protein)
                }
                if !estimated.definition.estimate.unknownNutrients.isEmpty {
                    Text(
                        "Some nutrients are unknown: "
                            + estimated.definition.estimate.unknownNutrients.joined(separator: ", ")
                    )
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
                Text("Owner-observed portions with external reference nutrition")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                DisclosureGroup("Estimate details") {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(
                            Array(estimated.definition.selectedComponents.enumerated()),
                            id: \.offset
                        ) { _, component in
                            if let portion = component.portion {
                                Text(
                                    "\(component.componentId): \(portion.amount) \(portion.unit)"
                                )
                            } else {
                                Text("\(component.componentId): quantity unknown")
                            }
                        }
                        ForEach(estimated.definition.estimate.caveats, id: \.self) { caveat in
                            Text(caveat)
                        }
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
            } else {
                ForEach(candidate.totals.quantities.keys.sorted(), id: \.self) { nutrient in
                    if let value = candidate.totals.quantities[nutrient] {
                        let display = nutrientDisplay(nutrient, value: value)
                        LabeledContent(display.label, value: display.value)
                    }
                }
            }
            if let reference {
                if let recorded = viewModel.latestConsumption(for: reference.itemId) {
                    Text("Recorded consumption: \(recorded.state.rawValue.capitalized)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 108))], spacing: 8) {
                    ForEach(ConsumptionState.allCases, id: \.self) { state in
                        Button(state.rawValue.capitalized) {
                            Task { await viewModel.recordConsumption(item: reference, state: state) }
                        }
                        .buttonStyle(.bordered)
                        .frame(maxWidth: .infinity, minHeight: 44)
                        .disabled(!viewModel.canMutate)
                    }
                }
            } else {
                Text("Consumption target unavailable")
                    .font(.caption)
                    .foregroundStyle(.red)
            }
        }
        .padding(.vertical, 4)
    }

    private func friendlyContext(_ value: String) -> String {
        switch value {
        case "post_workout_lunch": return "Post-workout lunch"
        case "lunch": return "Lunch"
        case "dinner": return "Dinner"
        default: return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private func friendlyReason(_ value: String) -> String {
        switch value.lowercased() {
        case "menu_data_unavailable", "menu_unavailable":
            return "Validated Stacks menu evidence is unavailable."
        case "menu_stale", "stale_menu_data":
            return "Validated Stacks menu evidence is stale."
        case "empty_menu_period":
            return "Stacks published no items for this meal period."
        case "no_resolved_meal_slots", "no_remaining_meal_opportunity":
            return "No remaining Stacks meal opportunity is available."
        case "no_eligible_candidate":
            return "No meal passed the current nutrition and dietary rules."
        case "no_approved_target_policy":
            return "Approve nutrition targets before requesting a recommendation."
        case "no_approved_calorie_target":
            return "An approved calorie target is required."
        case "no_approved_protein_target":
            return "An approved protein floor or target is required."
        case "incomplete_ledger_nutrition":
            return "Recorded nutrition is too incomplete for this calculation."
        case "daily_calorie_target_met":
            return "The recorded calorie target has already been met."
        default:
            return value.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private func nutrientDisplay(_ key: String, value: String) -> (label: String, value: String) {
        switch key {
        case "calories_kcal": return ("Calories", "\(value) kcal")
        case "protein_g": return ("Protein", "\(value) g")
        case "carbohydrate_g": return ("Carbohydrate", "\(value) g")
        case "total_fat_g": return ("Total fat", "\(value) g")
        case "saturated_fat_g": return ("Saturated fat", "\(value) g")
        case "trans_fat_g": return ("Trans fat", "\(value) g")
        case "fiber_g": return ("Fiber", "\(value) g")
        case "sugars_g": return ("Sugars", "\(value) g")
        case "added_sugars_g": return ("Added sugars", "\(value) g")
        case "sodium_mg": return ("Sodium", "\(value) mg")
        case "cholesterol_mg": return ("Cholesterol", "\(value) mg")
        default: return (key.replacingOccurrences(of: "_", with: " ").capitalized, value)
        }
    }
}
