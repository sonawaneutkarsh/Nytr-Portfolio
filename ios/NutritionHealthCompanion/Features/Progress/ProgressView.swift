import Charts
import SwiftUI

struct OwnerProgressView: View {
    @State private var viewModel: ProgressViewModel
    @State private var bodyGoalsViewModel: BodyGoalsViewModel
    let subject: String
    let targetReviewViewModel: TargetReviewViewModel
    let onShowSettings: () -> Void

    init(
        viewModel: ProgressViewModel, bodyGoalsViewModel: BodyGoalsViewModel,
        targetReviewViewModel: TargetReviewViewModel, subject: String,
        onShowSettings: @escaping () -> Void = {}
    ) {
        _viewModel = State(initialValue: viewModel)
        _bodyGoalsViewModel = State(initialValue: bodyGoalsViewModel)
        self.subject = subject
        self.targetReviewViewModel = targetReviewViewModel
        self.onShowSettings = onShowSettings
    }

    var body: some View {
        List {
            Section {
                NytrScreenHeader(
                    eyebrow: "PROGRESS", title: "See the longer view",
                    subtitle: "Trends grounded in the days you have recorded.", symbol: "chart.xyaxis.line")
                NavigationLink("Body & Goals") {
                    BodyGoalsView(
                        viewModel: bodyGoalsViewModel, targetReviewViewModel: targetReviewViewModel, subject: subject)
                }
            }.listRowBackground(Color.clear)
            switch viewModel.phase {
            case .idle, .loading:
                ProgressView("Loading progress…")
            case .error(let message):
                Section {
                    Label(message, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                    Button("Try Again") { Task { await refresh() } }
                }
            case .signedOut:
                Text("Sign in to view progress.")
                    .foregroundStyle(.secondary)
            case .result(let response):
                weightSection(response)
                bodyEvidenceSections
                nutritionSection(response)
                limitationsSection(response.limitations)
            }
        }
        .nytrList()
        .navigationTitle("Progress")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                NytrSettingsToolbarButton(action: onShowSettings)
            }
        }
        .refreshable { await refresh() }
        .task(id: subject) {
            async let progress: Void = viewModel.activate(subject: subject)
            async let body: Void = bodyGoalsViewModel.activate(subject: subject)
            _ = await (progress, body)
        }
    }

    private func refresh() async {
        async let progress: Void = viewModel.refresh()
        async let body: Void = bodyGoalsViewModel.refresh()
        _ = await (progress, body)
    }

    @ViewBuilder
    private var bodyEvidenceSections: some View {
        switch bodyGoalsViewModel.phase {
        case .loading:
            Section { ProgressView("Loading waist and phase…") }
        case .error(let message):
            Section("Waist & phase") {
                NytrStatusLabel(title: message, systemImage: "exclamationmark.triangle")
                Button("Try again") { Task { await bodyGoalsViewModel.refresh() } }
            }
        case .signedOut:
            Section { Text("Sign in to view waist and phase evidence.") }
        case .ready(let response):
            Section("Waist trend") {
                if let latest = response.waist.latest {
                    NytrMetricRow(
                        "Latest",
                        value: "\(NytrNumberFormat.detail(latest.valueCm) ?? latest.valueCm) cm"
                    )
                    NytrMetricRow("Measured", value: latest.measuredAt.formatted(date: .abbreviated, time: .omitted))
                }
                NytrWaistTrend(trend: response.waist.trend)
            }
            NytrPhaseAssessmentSection(assessment: response.phaseAssessment)
        }
    }

    @ViewBuilder
    private func weightSection(_ response: OwnerProgressResponse) -> some View {
        Section("Body weight") {
            Picker("Weight window", selection: $viewModel.bodyWindow) {
                ForEach(ProgressViewModel.BodyWindow.allCases) { window in
                    Text(window.label).tag(window)
                }
            }
            .pickerStyle(.segmented)

            if viewModel.selectedBodyPoints.isEmpty {
                Text("No body-weight measurements in this window.")
                    .foregroundStyle(.secondary)
            } else {
                if viewModel.selectedBodyPoints.count > 1 {
                    Chart {
                        ForEach(Array(viewModel.bodyChartSegments.enumerated()), id: \.offset) {
                            segmentIndex, segment in
                            if segment.count > 1 {
                                ForEach(segment) { point in
                                    LineMark(
                                        x: .value("Date", point.date),
                                        y: .value("Daily median (kg)", point.displayKg),
                                        series: .value("Segment", segmentIndex)
                                    )
                                }
                            }
                        }
                        ForEach(viewModel.selectedBodyPoints) { point in
                            PointMark(
                                x: .value("Date", point.date),
                                y: .value("Daily median (kg)", point.displayKg)
                            )
                            .accessibilityLabel("\(point.localDate) daily median")
                            .accessibilityValue("\(point.medianKg) kilograms")
                        }
                    }
                    .frame(height: 190)
                    .chartYAxisLabel("kg")
                    .chartYScale(domain: .automatic(includesZero: false))
                    .accessibilityElement(children: .contain)
                    .accessibilityLabel("Body-weight trend chart")
                    .accessibilityValue(
                        "\(viewModel.selectedBodyPoints.count) represented days; missing days are gaps"
                    )
                } else {
                    NytrStateView(
                        title: "A starting point",
                        message: "One recorded day is available. More measurements are needed to show a trend.",
                        symbol: "chart.xyaxis.line")
                }

                if let latest = viewModel.selectedBodyPoints.last {
                    NytrMetricRow("Latest in window", value: "\(latest.medianKg) kg")
                    if latest.observationCount > 1 {
                        Text("Daily median of \(latest.observationCount) measurements")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            trendDetails(response.body.trend28d)
            goalDetails(response.goal)
            Text("Missing measurement days remain gaps; values are not interpolated.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func trendDetails(_ trend: BodyMassTrendResponse) -> some View {
        NytrMetricRow("Measurement days", value: String(trend.representedDayCount))
        NytrMetricRow("Measurement span", value: "\(trend.coverageSpanDays) days")
        if let age = trend.latestMeasurementAgeDays {
            NytrMetricRow("Latest measurement age", value: "\(age) days")
        }
        if let average = trend.formattedTrailingAverageKg {
            NytrMetricRow("7-day average", value: "\(average) kg")
        }
        if let rate = trend.formattedWeeklyRateKg {
            NytrMetricRow("28-day trend", value: "\(rate) kg/week")
        }
        switch trend.status {
        case .ready:
            Text("Weight trend evidence is ready.").foregroundStyle(.secondary)
        case .stale:
            Text("Weight trend evidence is stale.").foregroundStyle(.orange)
        case .insufficient:
            Text("More represented measurement days are needed for a weight trend.")
                .foregroundStyle(.secondary)
        case .noData:
            Text("No weight trend evidence is available.").foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func goalDetails(_ goal: ProgressGoalResponse) -> some View {
        if let mode = goal.mode {
            NytrMetricRow("Current goal", value: mode.rawValue.capitalized)
        } else {
            NytrMetricRow("Current goal", value: "Unavailable")
        }
        if let desired = goal.desiredRateKgPerWeek {
            NytrMetricRow("Desired rate", value: "\(desired) kg/week")
        }
        NytrMetricRow("Progress toward goal", value: goalBandLabel(goal.status))
    }

    @ViewBuilder
    private func nutritionSection(_ response: OwnerProgressResponse) -> some View {
        Section("Recorded nutrition") {
            Picker("Nutrition window", selection: $viewModel.nutritionWindow) {
                ForEach(ProgressViewModel.NutritionWindow.allCases) { window in
                    Text(window.label).tag(window)
                }
            }
            .pickerStyle(.segmented)

            if let summary = viewModel.selectedSummary {
                NytrMetricRow(
                    "Days with records",
                    value: "\(summary.coverage.daysWithRecordedEvents) / \(summary.windowDays)"
                )
                NytrMetricRow(
                    "Average recorded calories",
                    value: average(
                        summary.recordedCalories.averageRecordedKcal,
                        unit: "kcal",
                        denominator: summary.recordedCalories.averageDenominatorDays
                    )
                )
                NytrMetricRow(
                    "Average recorded protein",
                    value: average(
                        summary.recordedProtein.averageRecordedG,
                        unit: "g",
                        denominator: summary.recordedProtein.averageDenominatorDays
                    )
                )
                NytrMetricRow(
                    "Calorie evidence",
                    value: evidenceCounts(
                        quantified: summary.recordedCalories.quantifiedRecordedDays,
                        partial: summary.recordedCalories.partialRecordedDays,
                        unavailable: summary.recordedCalories.unavailableRecordedDays
                    )
                )
                NytrMetricRow(
                    "Protein evidence",
                    value: evidenceCounts(
                        quantified: summary.recordedProtein.quantifiedRecordedDays,
                        partial: summary.recordedProtein.partialRecordedDays,
                        unavailable: summary.recordedProtein.unavailableRecordedDays
                    )
                )
                NytrMetricRow(
                    "Calories vs target",
                    value: calorieComparisons(summary.recordedCalorieTargetComparisons)
                )
                NytrMetricRow(
                    "Protein vs minimum",
                    value: proteinComparisons(summary.recordedProteinTargetComparisons)
                )

                if summary.includesEstimates
                    || summary.recordedCalories.partialRecordedDays > 0
                    || summary.recordedProtein.partialRecordedDays > 0
                    || summary.recordedCalories.unavailableRecordedDays > 0
                    || summary.recordedProtein.unavailableRecordedDays > 0
                {
                    Label(
                        "This window includes estimated, partial, or unavailable evidence.",
                        systemImage: "exclamationmark.triangle.fill"
                    )
                    .font(.caption)
                    .foregroundStyle(.orange)
                }
            }

            if viewModel.selectedTargetChanges.isEmpty {
                Text("No target changes in this window.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(viewModel.selectedTargetChanges, id: \.targetPolicyVersionId) { change in
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Target changed \(change.effectiveLocalDate)")
                        Text(targetChangeDetails(change))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private func limitationsSection(_ limitations: ProgressLimitations) -> some View {
        Section("About these summaries") {
            DisclosureGroup("Evidence & limitations") {
                Text(limitations.loggingCoverage)
                Text(limitations.recordTimeAttribution)
                Text(limitations.causality)
            }
        }
        .font(.caption)
        .foregroundStyle(.secondary)
    }

    private func average(_ value: String?, unit: String, denominator: Int) -> String {
        guard let value else { return "Unavailable (0 quantified days)" }
        return "\(NytrNumberFormat.whole(value) ?? value) \(unit) across \(denominator) quantified days"
    }

    private func evidenceCounts(quantified: Int, partial: Int, unavailable: Int) -> String {
        "\(quantified) quantified, \(partial) partial, \(unavailable) unavailable"
    }

    private func calorieComparisons(_ value: ProgressCalorieComparisonCounts) -> String {
        "\(value.below) below, \(value.at) at, \(value.above) above, "
            + "\(value.unavailable) unavailable "
            + "(\(value.eligibleDayCount) eligible)"
    }

    private func proteinComparisons(_ value: ProgressProteinComparisonCounts) -> String {
        "\(value.below) below, \(value.atOrAbove) at/above, "
            + "\(value.unavailable) unavailable "
            + "(\(value.eligibleDayCount) eligible)"
    }

    private func targetChangeDetails(_ value: ProgressTargetChange) -> String {
        let calories = value.caloriesKcal.map {
            "\(NytrNumberFormat.whole($0) ?? $0) kcal"
        } ?? "calories unavailable"
        let protein = value.proteinG.map {
            "\(NytrNumberFormat.whole($0) ?? $0) g protein"
        } ?? "protein unavailable"
        return "\(calories), \(protein)"
    }

    private func goalBandLabel(_ value: ProgressGoalBandStatus) -> String {
        switch value {
        case .unavailable: "Unavailable"
        case .belowBand: "Below goal range"
        case .withinBand: "Within goal range"
        case .aboveBand: "Above goal range"
        }
    }
}
