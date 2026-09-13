import SwiftUI

struct BodyGoalsView: View {
    @State private var viewModel: BodyGoalsViewModel
    @State private var targetReviewViewModel: TargetReviewViewModel
    let subject: String

    @State private var heightCm = ""
    @State private var birthDate = Calendar.current.date(byAdding: .year, value: -25, to: Date())!
    @State private var formulaSex = "male"
    @State private var activityLevel = "sedentary"
    @State private var targetWeightKg = ""
    @State private var waistValue = ""
    @State private var waistUnit = "cm"
    @State private var waistDate = Date()
    @State private var correctionId: UUID?

    init(
        viewModel: BodyGoalsViewModel,
        targetReviewViewModel: TargetReviewViewModel,
        subject: String
    ) {
        _viewModel = State(initialValue: viewModel)
        _targetReviewViewModel = State(initialValue: targetReviewViewModel)
        self.subject = subject
    }

    var body: some View {
        List {
            Section {
                NytrScreenHeader(
                    eyebrow: "BODY & GOALS", title: "Your direction, your pace",
                    subtitle: "Current evidence, clear goals, and targets you approve.", symbol: "figure.stand")
            }.listRowBackground(Color.clear)
            switch viewModel.phase {
            case .loading:
                ProgressView("Loading Body & Goals…")
            case .error(let message):
                Section {
                    NytrStatusLabel(title: message, systemImage: "exclamationmark.triangle")
                    retryButton
                }
            case .signedOut:
                Section { Text("Sign in to view Body & Goals.") }
            case .ready(let bodyGoals):
                weightSection(bodyGoals)
                approvedTargetsSection(bodyGoals)
                Section("Goal & target review") { TargetReviewCard(viewModel: targetReviewViewModel) }
                profileSection(bodyGoals)
                startingTargetSection(bodyGoals)
                waistSection(bodyGoals)
                phaseSection(bodyGoals)
            }
            if let message = viewModel.message {
                Section { Text(message).font(.caption).foregroundStyle(.secondary) }
            }
        }
        .nytrList()
        .navigationTitle("Body & Goals")
        .refreshable { await reload() }
        .task(id: subject) {
            async let body: Void = viewModel.activate(subject: subject)
            async let target: Void = targetReviewViewModel.activate(subject: subject)
            _ = await (body, target)
            prefill()
        }
        .onChange(of: viewModel.current?.profile) { _, _ in prefill() }
    }

    @ViewBuilder
    private func weightSection(_ response: BodyGoalsResponse) -> some View {
        Section("Current body evidence") {
            if let weight = response.weight {
                NytrMetricRow(
                    "Current weight",
                    value: "\(NytrNumberFormat.detail(weight.valueKg) ?? weight.valueKg) kg"
                ).font(.title.weight(.semibold))
                NytrMetricRow("Measured", value: weight.measuredAt.formatted(date: .abbreviated, time: .shortened))
                NytrMetricRow("Freshness", value: weight.ageDays == 0 ? "Today" : "\(weight.ageDays) days old")
                Text("Weight synced from Apple Health.").font(.caption).foregroundStyle(.secondary)
            } else {
                Text("No weight available. Record a weight in Apple Health, then run Health Sync.")
                    .foregroundStyle(.orange)
            }
            NytrMetricRow(
                "Height",
                value: response.profile.map {
                    "\(NytrNumberFormat.detail($0.heightCm) ?? $0.heightCm) cm"
                } ?? "Not recorded"
            )
            NytrMetricRow(
                "Latest waist",
                value: response.waist.latest.map {
                    "\(NytrNumberFormat.detail($0.valueCm) ?? $0.valueCm) cm"
                } ?? "Not recorded"
            )
        }
    }

    private func profileSection(_ response: BodyGoalsResponse) -> some View {
        Section("Profile for starting estimate") {
            if let profile = response.profile {
                NytrMetricRow(
                    "Usual activity", value: profile.activityLevel.replacingOccurrences(of: "_", with: " ").capitalized)
            }
            DisclosureGroup(response.profile == nil ? "Set up your profile" : "Edit profile") {
                NytrNumberField(title: "Height (cm)", text: $heightCm)
                DatePicker("Date of birth", selection: $birthDate, in: ...Date(), displayedComponents: .date)
                Picker("Formula", selection: $formulaSex) {
                    Text("Male equation").tag("male")
                    Text("Female equation").tag("female")
                }
                Picker("Activity", selection: $activityLevel) {
                    Text("Sedentary").tag("sedentary")
                    Text("Lightly active").tag("lightly_active")
                    Text("Moderately active").tag("moderately_active")
                    Text("Very active").tag("very_active")
                }
                NytrNumberField(title: "Target weight (kg, optional)", text: $targetWeightKg)
                Button(response.profile == nil ? "Save profile" : "Save profile changes") {
                    Task {
                        await viewModel.saveProfile(
                            heightCm: heightCm, dateOfBirth: birthDate, formulaSex: formulaSex,
                            activityLevel: activityLevel, targetWeightKg: targetWeightKg
                        )
                    }
                }.disabled(viewModel.isWorking)
                Text(
                    "Choose your usual activity level. The formula category is used only for the Mifflin-St Jeor estimate."
                )
                .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func approvedTargetsSection(_ response: BodyGoalsResponse) -> some View {
        Section("Approved targets") {
            if response.approvedTargets.caloriesKcal != nil || response.approvedTargets.proteinG != nil {
                NytrStatusLabel(title: "Active · approved by you", systemImage: "checkmark.circle")
            }
            NytrMetricRow(
                "Calories", value: response.approvedTargets.caloriesKcal.map {
                    "\(NytrNumberFormat.whole($0) ?? $0) kcal/day"
                } ?? "Not approved yet")
            NytrMetricRow(
                "Protein minimum", value: response.approvedTargets.proteinG.map {
                    "\(NytrNumberFormat.whole($0) ?? $0) g/day"
                } ?? "Not approved yet")
        }.listRowBackground(NytrDesign.accent.opacity(0.08))
    }

    @ViewBuilder
    private func startingTargetSection(_ response: BodyGoalsResponse) -> some View {
        Section("Estimate · requires your approval") {
            if response.approvedTargets.caloriesKcal != nil {
                Text("Your calorie target is active. Use Target Review for future adjustments.")
                    .foregroundStyle(.secondary)
            } else if let proposal = response.startingCalorieProposal {
                NytrStatusLabel(title: "Estimated · not an active target", systemImage: "function")
                NytrMetricRow(
                    "Proposed calories",
                    value: "\(NytrNumberFormat.whole(proposal.proposedCalorieKcal) ?? proposal.proposedCalorieKcal) kcal/day"
                )
                    .font(.title3.weight(.semibold))
                DisclosureGroup("How this was estimated") {
                    NytrMetricRow(
                        "Estimated maintenance",
                        value: "\(NytrNumberFormat.whole(proposal.maintenanceKcal) ?? proposal.maintenanceKcal) kcal/day"
                    )
                    NytrMetricRow(
                        "Goal adjustment",
                        value: "\(NytrNumberFormat.whole(proposal.goalAdjustmentKcal) ?? proposal.goalAdjustmentKcal) kcal/day"
                    )
                    Text(
                        "Mifflin-St Jeor estimate using your Apple Health weight and profile. Exercise calories are not added back."
                    )
                    .font(.subheadline).foregroundStyle(.secondary)
                }
                if proposal.requiresExplicitApproval {
                    Text("This proposal becomes active only when you approve it.")
                        .foregroundStyle(.secondary)
                    Button("Approve starting target") { Task { await viewModel.decideStartingTarget("approved") } }
                        .buttonStyle(.borderedProminent).tint(NytrDesign.buttonFill)
                        .controlSize(.large)
                        .disabled(viewModel.isWorking)
                    Button("Reject estimate", role: .destructive) {
                        Task { await viewModel.decideStartingTarget("rejected") }
                    }
                    .disabled(viewModel.isWorking)
                } else {
                    NytrMetricRow(
                        "Decision", value: proposal.decisionStatus.replacingOccurrences(of: "_", with: " ").capitalized)
                }
            } else {
                Text("Create an estimate to review. It will not activate a target.")
                    .foregroundStyle(.secondary)
                Button("Create starting estimate") { Task { await viewModel.generateStartingTarget() } }
                    .buttonStyle(.borderedProminent).tint(NytrDesign.buttonFill)
                    .controlSize(.large).disabled(viewModel.isWorking)
                Text("Requires a recent Apple Health weight, a saved profile, and a saved goal. Waist is optional.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }.listRowBackground(Color.orange.opacity(0.08))
    }

    private func waistSection(_ response: BodyGoalsResponse) -> some View {
        Section("Waist · optional") {
            if let latest = response.waist.latest {
                NytrMetricRow(
                    "Latest",
                    value: "\(NytrNumberFormat.detail(latest.valueCm) ?? latest.valueCm) cm"
                )
                NytrMetricRow("Measured", value: latest.measuredAt.formatted(date: .abbreviated, time: .omitted))
            }
            NytrWaistTrend(trend: response.waist.trend)
            NytrNumberField(title: "Waist measurement (\(waistUnit))", text: $waistValue)
            Picker("Unit", selection: $waistUnit) {
                Text("cm").tag("cm")
                Text("in").tag("in")
            }
            .pickerStyle(.segmented)
            DatePicker("Measured", selection: $waistDate, in: ...Date(), displayedComponents: .date)
            if !response.waist.history.isEmpty {
                Picker("Record type", selection: $correctionId) {
                    Text("New measurement").tag(UUID?.none)
                    ForEach(response.waist.history, id: \.measurementId) { item in
                        Text("Correct \(item.measuredAt.formatted(date: .abbreviated, time: .omitted))")
                            .tag(Optional(item.measurementId))
                    }
                }
            }
            Button(correctionId == nil ? "Record waist" : "Save correction") {
                Task {
                    await viewModel.addWaist(
                        value: waistValue, unit: waistUnit, measuredAt: waistDate, correcting: correctionId)
                }
            }.disabled(viewModel.isWorking)
            Text(
                "Corrections keep the original history. Waist is used for phase assessment, not calorie or body-fat estimates."
            )
            .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func phaseSection(_ response: BodyGoalsResponse) -> some View {
        NytrPhaseAssessmentSection(assessment: response.phaseAssessment)
    }

    private var retryButton: some View {
        Button("Try again") { Task { await reload() } }
    }

    private func reload() async {
        async let body: Void = viewModel.refresh()
        async let target: Void = targetReviewViewModel.retryGoalLoad()
        _ = await (body, target)
    }

    private func prefill() {
        guard let profile = viewModel.current?.profile else { return }
        heightCm = profile.heightCm
        formulaSex = profile.formulaSex
        activityLevel = profile.activityLevel
        targetWeightKg = profile.targetWeightKg ?? ""
        if let date = ISO8601DateFormatter.day.date(from: profile.dateOfBirth) { birthDate = date }
    }
}

extension ISO8601DateFormatter {
    fileprivate static let day: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()
}
