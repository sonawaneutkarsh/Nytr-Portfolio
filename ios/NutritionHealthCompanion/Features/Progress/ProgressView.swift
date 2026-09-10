import Charts
import SwiftUI

struct OwnerProgressView: View {
    @State private var viewModel: ProgressViewModel
    let subject: String

    init(viewModel: ProgressViewModel, subject: String) {
        _viewModel = State(initialValue: viewModel)
        self.subject = subject
    }

    var body: some View {
        List {
            switch viewModel.phase {
            case .idle, .loading:
                ProgressView("Loading progress…")
            case .error(let message):
                Section {
                    Label(message, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                    Button("Try Again") { Task { await viewModel.refresh() } }
                }
            case .signedOut:
                Text("Sign in to view progress.")
                    .foregroundStyle(.secondary)
            case .result(let response):
                weightSection(response)
                nutritionSection(response)
                limitationsSection(response.limitations)
            }
        }
        .navigationTitle("Progress")
        .refreshable { await viewModel.refresh() }
        .task(id: subject) { await viewModel.activate(subject: subject) }
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
                .accessibilityElement(children: .contain)
                .accessibilityLabel("Body-weight trend chart")
                .accessibilityValue(
                    "\(viewModel.selectedBodyPoints.count) represented days; missing days are gaps"
                )

                if let latest = viewModel.selectedBodyPoints.last {
                    LabeledContent("Latest in window", value: "\(latest.medianKg) kg")
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
        LabeledContent("28-day represented days", value: String(trend.representedDayCount))
        LabeledContent("28-day coverage span", value: "\(trend.coverageSpanDays) days")
        if let age = trend.latestMeasurementAgeDays {
            LabeledContent("Latest measurement age", value: "\(age) days")
        }
        if let average = trend.formattedTrailingAverageKg {
            LabeledContent("Existing 7-day average", value: "\(average) kg")
        }
        if let rate = trend.formattedWeeklyRateKg {
            LabeledContent("Existing 28-day trend", value: "\(rate) kg/week")
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
            LabeledContent("Current goal", value: mode.rawValue.capitalized)
        } else {
            LabeledContent("Current goal", value: "Unavailable")
        }
        if let desired = goal.desiredRateKgPerWeek {
            LabeledContent("Desired rate", value: "\(desired) kg/week")
        }
        LabeledContent("Goal-band interpretation", value: goalBandLabel(goal.status))
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
                LabeledContent(
                    "Days with records",
                    value: "\(summary.coverage.daysWithRecordedEvents) / \(summary.windowDays)"
                )
                LabeledContent(
                    "Average recorded calories",
                    value: average(
                        summary.recordedCalories.averageRecordedKcal,
                        unit: "kcal",
                        denominator: summary.recordedCalories.averageDenominatorDays
                    )
                )
                LabeledContent(
                    "Average recorded protein",
                    value: average(
                        summary.recordedProtein.averageRecordedG,
                        unit: "g",
                        denominator: summary.recordedProtein.averageDenominatorDays
                    )
                )
                LabeledContent(
                    "Calorie evidence",
                    value: evidenceCounts(
                        quantified: summary.recordedCalories.quantifiedRecordedDays,
                        partial: summary.recordedCalories.partialRecordedDays,
                        unavailable: summary.recordedCalories.unavailableRecordedDays
                    )
                )
                LabeledContent(
                    "Protein evidence",
                    value: evidenceCounts(
                        quantified: summary.recordedProtein.quantifiedRecordedDays,
                        partial: summary.recordedProtein.partialRecordedDays,
                        unavailable: summary.recordedProtein.unavailableRecordedDays
                    )
                )
                LabeledContent(
                    "Calorie recorded-total comparison",
                    value: calorieComparisons(summary.recordedCalorieTargetComparisons)
                )
                LabeledContent(
                    "Protein recorded-total comparison",
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
            Text(limitations.loggingCoverage)
            Text(limitations.recordTimeAttribution)
            Text(limitations.causality)
        }
        .font(.caption)
        .foregroundStyle(.secondary)
    }

    private func average(_ value: String?, unit: String, denominator: Int) -> String {
        guard let value else { return "Unavailable (0 quantified days)" }
        return "\(value) \(unit) across \(denominator) quantified days"
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
        let calories = value.caloriesKcal.map { "\($0) kcal" } ?? "calories unavailable"
        let protein = value.proteinG.map { "\($0) g protein" } ?? "protein unavailable"
        return "\(value.targetPolicyVersion): \(calories), \(protein)"
    }

    private func goalBandLabel(_ value: ProgressGoalBandStatus) -> String {
        switch value {
        case .unavailable: "Unavailable"
        case .belowBand: "Below configured band"
        case .withinBand: "Within configured band"
        case .aboveBand: "Above configured band"
        }
    }
}
