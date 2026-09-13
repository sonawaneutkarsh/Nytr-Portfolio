import SwiftUI

struct TodayView: View {
    @State private var viewModel: TodayViewModel
    @State private var nutritionHistoryViewModel: NutritionHistoryViewModel
    @State private var progressViewModel: ProgressViewModel
    @State private var targetReviewViewModel: TargetReviewViewModel
    @State private var bodyGoalsViewModel: BodyGoalsViewModel
    @State private var manualFoodViewModel: ManualFoodViewModel
    @State private var aiReviewViewModel: AIReviewViewModel
    private let notificationViewModel: MealGuidanceNotificationViewModel
    let healthSyncViewModel: HealthSyncViewModel
    let subject: String
    let onSignOut: () -> Void
    let onShowSettings: () -> Void

    init(
        viewModel: TodayViewModel,
        nutritionHistoryViewModel: NutritionHistoryViewModel,
        progressViewModel: ProgressViewModel,
        targetReviewViewModel: TargetReviewViewModel,
        bodyGoalsViewModel: BodyGoalsViewModel,
        manualFoodViewModel: ManualFoodViewModel,
        aiReviewViewModel: AIReviewViewModel,
        notificationViewModel: MealGuidanceNotificationViewModel? = nil,
        healthSyncViewModel: HealthSyncViewModel,
        subject: String,
        onSignOut: @escaping () -> Void,
        onShowSettings: @escaping () -> Void = {}
    ) {
        _viewModel = State(initialValue: viewModel)
        _nutritionHistoryViewModel = State(initialValue: nutritionHistoryViewModel)
        _progressViewModel = State(initialValue: progressViewModel)
        _targetReviewViewModel = State(initialValue: targetReviewViewModel)
        _bodyGoalsViewModel = State(initialValue: bodyGoalsViewModel)
        _manualFoodViewModel = State(initialValue: manualFoodViewModel)
        _aiReviewViewModel = State(initialValue: aiReviewViewModel)
        self.notificationViewModel = notificationViewModel ?? MealGuidanceNotificationViewModel()
        self.healthSyncViewModel = healthSyncViewModel
        self.subject = subject
        self.onSignOut = onSignOut
        self.onShowSettings = onShowSettings
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    if case .result(let ledger) = viewModel.ledgerPhase {
                        NytrDailySummary(ledger: ledger)
                    } else {
                        ledgerCard
                    }
                }.listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 4, trailing: 0))
                    .listRowBackground(Color.clear)
                Section {
                    nextMealCard
                }
                Section("Food log") {
                    NavigationLink {
                        ManualFoodView(viewModel: manualFoodViewModel)
                    } label: {
                        Label("Add Food", systemImage: "plus.circle.fill")
                    }
                    NavigationLink("Nutrition History") {
                        NutritionHistoryView(viewModel: nutritionHistoryViewModel, subject: subject)
                    }
                }
                Section("Your progress") {
                    NavigationLink("Body & Goals") {
                        BodyGoalsView(
                            viewModel: bodyGoalsViewModel,
                            targetReviewViewModel: targetReviewViewModel,
                            subject: subject
                        )
                    }
                    NavigationLink("Progress") {
                        OwnerProgressView(
                            viewModel: progressViewModel, bodyGoalsViewModel: bodyGoalsViewModel,
                            targetReviewViewModel: targetReviewViewModel, subject: subject)
                    }
                    NavigationLink("Nytr Review") {
                        AIReviewView(viewModel: aiReviewViewModel, subject: subject)
                    }
                }
                Section {
                    DisclosureGroup("Weight trend") { trendCard }
                }
            }
            #if os(iOS)
                .listStyle(.insetGrouped)
            #endif
            .refreshable { await viewModel.refresh() }
            .nytrList()
            .navigationTitle("Today")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    NytrSettingsToolbarButton(action: onShowSettings)
                }
            }
        }
        .task(id: subject) {
            async let plan: Void = viewModel.activate(subject: subject)
            async let bodyGoals: Void = bodyGoalsViewModel.activate(subject: subject)
            _ = await (plan, bodyGoals)
        }
    }

    @ViewBuilder
    private var nextMealCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("NEXT MEAL", systemImage: "fork.knife").font(.caption.weight(.bold)).tracking(1).foregroundStyle(
                NytrDesign.accent)
            switch viewModel.nextMealPhase {
            case .loading:
                ProgressView("Loading latest recommendation…").controlSize(.small)
            case .notGenerated:
                Text("Find a meal that fits today’s food log and remaining meal times.")
                    .foregroundStyle(.secondary)
                nextMealButton
            case .generating:
                ProgressView("Checking remaining opportunities…").controlSize(.small)
            case .error(let message):
                NytrStatusLabel(title: message, systemImage: "exclamationmark.triangle")
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
        .padding(.vertical, 6)
    }

    private var nextMealButton: some View {
        Button("Recommend next meal") {
            Task { await viewModel.generateNextMeal() }
        }
        .buttonStyle(.bordered)
        .controlSize(.large)
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
            NytrMetricRow(
                "Remaining from records",
                value: "\(NytrNumberFormat.whole(ledger.remainingCalories) ?? "unknown") kcal · "
                    + "\(NytrNumberFormat.whole(ledger.remainingProteinG) ?? "unknown") g protein"
            )
            NytrMetricRow(
                "This opportunity",
                value: "\(NytrNumberFormat.whole(allocated.caloriesKcal) ?? allocated.caloriesKcal) kcal · "
                    + "\(NytrNumberFormat.whole(allocated.proteinG) ?? allocated.proteinG) g protein"
            )
            let mealCalories = candidate.totals.quantities["calories_kcal"]
            let mealProtein = candidate.totals.quantities["protein_g"]
            if let mealCalories, let mealProtein {
                NytrMetricRow(
                    "Recommended meal",
                    value: "\(NytrNumberFormat.whole(mealCalories) ?? mealCalories) kcal · "
                        + "\(NytrNumberFormat.whole(mealProtein) ?? mealProtein) g protein"
                )
            } else if let mealCalories {
                NytrMetricRow(
                    "Recommended meal",
                    value: "\(NytrNumberFormat.whole(mealCalories) ?? mealCalories) kcal"
                )
            } else if let mealProtein {
                NytrMetricRow(
                    "Recommended meal",
                    value: "\(NytrNumberFormat.whole(mealProtein) ?? mealProtein) g protein"
                )
            }
            if candidate.configurableEstimate != nil {
                Text("Estimated nutrition — review portions and caveats.")
                    .foregroundStyle(.orange)
            } else {
                Text("Based on the accepted Stacks menu.")
                    .foregroundStyle(.secondary)
            }
            Text("Nutrition: \(candidate.totals.confidence.replacingOccurrences(of: "_", with: " "))")
                .font(.caption)
                .foregroundStyle(.secondary)
            if !allocated.proteinScoringActive {
                Text("Your protein goal is met. This recommendation prioritizes your remaining needs.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text("Only meals you record as eaten count toward your totals.")
                .font(.caption)
                .foregroundStyle(.secondary)
            if artifact.ledger?.unknownNutrients?.contains(where: {
                !["calories_kcal", "protein_g", "carbohydrate_g", "total_fat_g"]
                    .contains($0)
            }) == true {
                Text("Some micronutrient evidence is unavailable.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let alternatives = artifact.alternatives, !alternatives.isEmpty {
                DisclosureGroup("Alternatives") {
                    ForEach(alternatives, id: \.candidateId) { alternative in
                        Text(nextMealName(alternative))
                    }
                }
            }
            if let quality = response.nutritionQuality {
                ForEach(quality.findings.filter { $0.band == "high" && $0.nutrient != "fiber_g" }) { finding in
                    Label(
                        "\(finding.label): "
                            + "\(NytrNumberFormat.detail(finding.dailyValuePercent) ?? finding.dailyValuePercent)% of the daily reference",
                        systemImage: "info.circle"
                    )
                    .font(.subheadline).foregroundStyle(.secondary)
                }
                DisclosureGroup("Beyond calories & protein") {
                    ForEach(quality.findings) { finding in
                        NytrMetricRow(
                            finding.label,
                            value:
                                "\(NytrNumberFormat.detail(finding.amount) ?? finding.amount) \(finding.unit) · "
                                + "\(NytrNumberFormat.detail(finding.dailyValuePercent) ?? finding.dailyValuePercent)% DV · \(finding.band)"
                        )
                    }
                    Text(quality.notice).font(.caption).foregroundStyle(.secondary)
                    if !quality.missingNutrients.isEmpty {
                        Text("Some nutrient evidence is missing; this is not a complete quality assessment.").font(
                            .caption)
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
            "Generated \(response.decisionAt.formatted())"
        )
        .font(.caption)
        .foregroundStyle(.secondary)
        DisclosureGroup("Recommendation details") {
            NytrMetricRow("Calculation version", value: artifact.nextMealPolicyVersion)
            ForEach(response.reasonCodes, id: \.self) { reason in
                Text(reason).font(.caption)
            }
        }
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
                "I ate this meal",
                isOn: Binding(
                    get: { viewModel.nextMealConsumptionConfirmed },
                    set: { viewModel.setNextMealConsumptionConfirmed($0) }
                )
            )
            .toggleStyle(.switch)
            if viewModel.isRecordingNextMealConsumption {
                ProgressView("Recording meal…").controlSize(.small)
            } else {
                Button("Record as eaten") {
                    Task { await viewModel.recordNextMealConsumption() }
                }
                .buttonStyle(.borderedProminent).tint(NytrDesign.buttonFill)
                .controlSize(.large)
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
                NytrStatusLabel(title: message, systemImage: "exclamationmark.triangle")
                Button("Try Again") { Task { await viewModel.retryLedger() } }
                    .controlSize(.large)
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
                Text("Only logged food is included.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .font(.subheadline)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 6)
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
            let displayedConsumed = NytrNumberFormat.whole(consumed) ?? consumed
            if let target {
                NytrMetricRow(
                    label,
                    value: "\(displayedConsumed) / \(NytrNumberFormat.whole(target) ?? target) \(unit)"
                ).font(.title3.weight(.semibold))
            } else {
                NytrMetricRow(label, value: "\(displayedConsumed) \(unit)").font(.title3.weight(.semibold))
            }
        } else {
            NytrMetricRow(label, value: "Unavailable")
        }
        if let remaining {
            if remaining.hasPrefix("-") {
                let magnitude = String(remaining.dropFirst())
                Text("\(NytrNumberFormat.whole(magnitude) ?? magnitude) \(unit) over target")
                    .foregroundStyle(.secondary)
            } else {
                Text("\(NytrNumberFormat.whole(remaining) ?? remaining) \(unit) remaining from logged food")
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
                title: "Approved targets needed",
                detail: "Approve your targets in Body & Goals before creating a plan.",
                action: "Refresh",
                handler: { Task { await viewModel.refresh() } }
            )
        case .menuDataUnavailable:
            messageView(
                title: "Today’s menu isn’t available",
                detail: "Today’s Stacks menu has not been verified yet. Refresh to check again.",
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
                NytrStatusLabel(title: message, systemImage: "exclamationmark.triangle")
                Button("Try Again") { Task { await viewModel.refresh() } }
                    .controlSize(.large)
            case .signedOut:
                Text("Sign in to view your trend.").foregroundStyle(.secondary)
            case .result(let trend):
                trendResult(trend)
            }
        }
        .font(.subheadline)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 6)
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
        VStack(alignment: .leading, spacing: 12) {
            NytrStatusLabel(title: title, systemImage: "info.circle")
            Text(detail).foregroundStyle(.secondary)
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
            NytrMetricRow("Date", value: plan.planDate)
            if let policy = plan.targetPolicy {
                DisclosureGroup("Calculation details") {
                    NytrMetricRow("Target version", value: policy.policyVersion)
                }
            }
            if let generatedAt = plan.generatedAt {
                NytrMetricRow("Generated", value: generatedAt.formatted())
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
                    NytrStatusLabel(title: message, systemImage: "exclamationmark.triangle")
                }
            }
        }
        if let message = viewModel.consumptionMessage {
            Section { NytrStatusLabel(title: message, systemImage: "exclamationmark.triangle") }
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
                    NytrMetricRow("Estimated calories", value: calories)
                }
                if let protein = candidate.totals.quantities["protein_g"] {
                    NytrMetricRow("Estimated protein (g)", value: protein)
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
                                    "\(component.componentId): "
                                        + "\(NytrNumberFormat.detail(portion.amount) ?? portion.amount) \(portion.unit)"
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
                        NytrMetricRow(display.label, value: display.value)
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
