import SwiftUI

struct BodyGoalsView: View {
    @State private var viewModel: BodyGoalsViewModel
    let subject: String
    @State private var height = ""
    @State private var targetWeight = ""
    @State private var waist = ""
    @State private var waistUnit = "cm"

    init(viewModel: BodyGoalsViewModel, subject: String) {
        _viewModel = State(initialValue: viewModel)
        self.subject = subject
    }

    var body: some View {
        List {
            profileSection
            targetSection
            waistSection
            progressSection
        }
        .navigationTitle("Body & Goals")
        .task(id: subject) { await viewModel.activate(subject: subject) }
    }

    private var profileSection: some View {
        Section("Profile") {
            Text("HealthKit weight is the factual current-weight source. Height is owner-entered profile data.")
                .font(.caption).foregroundStyle(.secondary)
            if case .loaded(let response) = viewModel.phase {
                let weight = response.currentWeight.valueKg.map { "\($0) kg" } ?? "Unavailable"
                Text("Current weight: \(weight) · HealthKit")
                if let freshness = response.currentWeight.freshnessDays {
                    Text("Weight age: \(freshness) days").font(.caption).foregroundStyle(.secondary)
                }
                if let profile = response.profile { Text("Height: \(profile.heightCm) cm") }
            }
            TextField("Height (cm)", text: $height)
                #if os(iOS)
                .keyboardType(.decimalPad)
                #endif
            TextField("Optional target weight (kg)", text: $targetWeight)
                #if os(iOS)
                .keyboardType(.decimalPad)
                #endif
            Button("Save profile") { Task { await viewModel.saveProfile(heightCm: height, targetWeightKg: targetWeight) } }
                .disabled(height.isEmpty || viewModel.isWorking)
        }
    }

    private var targetSection: some View {
        Section("Starting calorie target") {
            if case .loaded(let response) = viewModel.phase {
                if let target = response.target, let calories = target.caloriesKcal {
                    Text("Approved target: \(calories) kcal")
                    Text("Approved targets remain unchanged until an explicit Target Review decision.")
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Text("No approved calorie target yet.")
                    Button("Generate starting estimate") { Task { await viewModel.generateEstimate() } }
                        .buttonStyle(.borderedProminent).disabled(viewModel.isWorking)
                }
            }
            if let estimate = viewModel.estimate {
                Text("Starting estimate: \(estimate.estimateKcal) kcal")
                    .font(.headline)
                Text(estimate.rationale).font(.caption).foregroundStyle(.secondary)
                Text("This estimate is not approved. Approve it in Target Review to make it active.")
                    .font(.caption).foregroundStyle(.orange)
            }
        }
    }

    private var waistSection: some View {
        Section("Waist evidence (optional)") {
            Text("Owner-measured progress evidence; never used alone to infer body fat or switch phases.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                TextField("Waist", text: $waist)
                    #if os(iOS)
                    .keyboardType(.decimalPad)
                    #endif
                Picker("Unit", selection: $waistUnit) { Text("cm").tag("cm"); Text("in").tag("in") }
                    .pickerStyle(.segmented).frame(width: 120)
            }
            Button("Record waist") { Task { await viewModel.recordWaist(value: waist, unit: waistUnit) } }
                .disabled(waist.isEmpty || viewModel.isWorking)
            if case .loaded(let response) = viewModel.phase, let latest = response.latestWaist {
                Text("Latest: \(latest.waistCm) cm · \(latest.measuredAt)")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private var progressSection: some View {
        Section("Phase progress") {
            if case .loaded(let response) = viewModel.phase, let progress = response.progress {
                Text(progress.status.replacingOccurrences(of: "_", with: " ").capitalized)
                    .font(.headline)
                Text(progress.reason).foregroundStyle(.secondary)
                Text("Weight and waist are evidence for review only; Nytr never switches gain, maintain, or lose automatically.")
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                Text("Choose a goal in Target Review to evaluate phase progress.")
                    .foregroundStyle(.secondary)
            }
            if case .error(let message) = viewModel.phase { Text(message).foregroundStyle(.orange) }
        }
    }
}
