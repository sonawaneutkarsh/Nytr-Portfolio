import SwiftUI

// MARK: - Recent Workouts (main Training destination)

struct TrainingView: View {
    @AppStorage(TrainingWeightUnit.preferenceKey) private var weightUnit = "lb"
    private var displayUnit: TrainingWeightUnit { TrainingWeightUnit(rawValue: weightUnit) ?? .lb }
    @State private var viewModel: TrainingViewModel
    let subject: String
    let onShowSettings: () -> Void

    init(
        viewModel: TrainingViewModel, subject: String,
        onShowSettings: @escaping () -> Void = {}
    ) {
        _viewModel = State(initialValue: viewModel)
        self.subject = subject
        self.onShowSettings = onShowSettings
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    NytrScreenHeader(
                        eyebrow: "TRAINING", title: "Build on your last session",
                        subtitle: "Your Hevy records. Nytr’s next-session guidance.",
                        symbol: "figure.strengthtraining.traditional")
                }.listRowBackground(Color.clear)
                Section("Hevy · recorded workouts") {
                    DisclosureGroup("About workout sources") {
                        Text(TrainingSourceAuthorityCopy.training)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                    Button {
                        Task { await viewModel.syncHevy() }
                    } label: {
                        if viewModel.isSyncingHevy {
                            HStack {
                                ProgressView()
                                Text("Syncing Hevy…")
                            }
                        } else {
                            Label("Sync Hevy", systemImage: "arrow.triangle.2.circlepath")
                        }
                    }
                    .disabled(viewModel.isSyncingHevy || viewModel.isLoadingRecent)

                    switch viewModel.hevySyncPhase {
                    case .success(let message):
                        Label(message, systemImage: "checkmark.circle.fill")
                            .font(.caption)
                            .foregroundStyle(.green)
                    case .error(let message):
                        Label(message, systemImage: "exclamationmark.triangle.fill")
                            .font(.caption)
                            .foregroundStyle(.orange)
                    case .idle, .syncing, .signedOut:
                        EmptyView()
                    }
                }
                switch viewModel.recentPhase {
                case .idle, .loading:
                    ProgressView("Loading recent workouts…")
                case .error(let message):
                    Section {
                        Label(message, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                        Button("Try Again") { Task { await viewModel.refreshRecent() } }
                    }
                case .signedOut:
                    Text("Sign in to view training data.")
                        .foregroundStyle(.secondary)
                case .empty:
                    Section {
                        NytrStateView(
                            title: "Your next chapter starts here",
                            message: "Sync a workout from Hevy to see your history and available coaching.",
                            symbol: "dumbbell")
                    }
                case .result(let response):
                    Section("Workout history") {
                        ForEach(response.sessions) { session in
                            NavigationLink {
                                SessionDetailView(
                                    session: session,
                                    viewModel: viewModel,
                                    subject: subject
                                )
                            } label: {
                                RecentSessionRow(session: session)
                            }
                        }

                    }
                    NavigationLink {
                        ExerciseIndexView(viewModel: viewModel, subject: subject)
                    } label: {
                        Label("All Exercises", systemImage: "list.bullet")
                    }
                }
            }
            #if os(iOS)
                .listStyle(.insetGrouped)
            #endif
            .nytrList()
            .navigationTitle("Training")
            .toolbar {
                ToolbarItemGroup(placement: .primaryAction) {
                    TrainingUnitPicker()
                    NytrSettingsToolbarButton(action: onShowSettings)
                }
            }
            .refreshable { await viewModel.refreshRecent() }
        }
        .task(id: subject) { await viewModel.activate(subject: subject) }
    }
}

// MARK: - Recent Session Row

private struct RecentSessionRow: View {
    @AppStorage(TrainingWeightUnit.preferenceKey) private var weightUnit = "lb"
    private var displayUnit: TrainingWeightUnit { TrainingWeightUnit(rawValue: weightUnit) ?? .lb }
    let session: TrainingSessionAnalyticsDTO

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(session.title)
                .font(.headline)
            VStack(alignment: .leading, spacing: 4) {
                Text(Self.formatDate(session.startedAt))
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                Text("\(session.exerciseCount) exercise\(session.exerciseCount == 1 ? "" : "s")")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(setLabel(session))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let volume = session.volumeKgReps {
                    Text("Eligible volume: \(displayUnit.display(volume, volume: true))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }

    private func setLabel(_ session: TrainingSessionAnalyticsDTO) -> String {
        if session.workingSetCount > 0 {
            return "\(session.workingSetCount) working / \(session.recordedSetCount) recorded sets"
        } else {
            return "\(session.recordedSetCount) recorded sets"
        }
    }

    private static func formatDate(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return formatter.string(from: date)
    }
}

// MARK: - Session Detail

struct SessionDetailView: View {
    @AppStorage(TrainingWeightUnit.preferenceKey) private var weightUnit = "lb"
    private var displayUnit: TrainingWeightUnit { TrainingWeightUnit(rawValue: weightUnit) ?? .lb }
    let session: TrainingSessionAnalyticsDTO
    let viewModel: TrainingViewModel
    let subject: String

    var body: some View {
        List {
            Section("Summary") {
                NytrMetricRow("Duration", value: Self.formatDuration(session.sessionDurationSeconds))
                NytrMetricRow("Exercises", value: String(session.exerciseCount))
                NytrMetricRow("Working Sets", value: String(session.workingSetCount))
                NytrMetricRow("Recorded Sets", value: String(session.recordedSetCount))
                if session.warmupSetCount > 0 {
                    NytrMetricRow("Warmup Sets", value: String(session.warmupSetCount))
                }
                if session.unsupportedSetCount > 0 {
                    NytrMetricRow("Other Sets", value: String(session.unsupportedSetCount))
                }
                if let reps = session.repTotal {
                    NytrMetricRow("Total Reps", value: String(reps))
                }
                if let volume = session.volumeKgReps {
                    NytrMetricRow("Eligible Load Volume", value: "\(displayUnit.display(volume, volume: true))")
                }
            }

            detailContent
        }
        #if os(iOS)
            .listStyle(.insetGrouped)
        #endif
        .nytrList()
        .navigationTitle(session.title)
        .toolbar { ToolbarItem(placement: .primaryAction) { TrainingUnitPicker() } }
        .task(id: "\(subject):\(session.revisionId)") {
            await viewModel.loadSessionDetail(revisionId: session.revisionId)
        }
    }

    @ViewBuilder
    private var detailContent: some View {
        switch viewModel.sessionDetailPhase {
        case .idle, .loading:
            Section { ProgressView("Loading recorded sets…") }
        case .signedOut:
            Section {
                Text("Sign in to view workout details.")
                    .foregroundStyle(.secondary)
            }
        case .error(let revisionId, let message):
            if revisionId == session.revisionId {
                Section {
                    NytrStatusLabel(title: message, systemImage: "exclamationmark.triangle")
                    Button("Try Again") {
                        Task { await viewModel.loadSessionDetail(revisionId: session.revisionId) }
                    }
                }
            }
        case .result(let detail):
            if detail.revisionId == session.revisionId {
                ForEach(detail.exercises) { exercise in
                    Section {
                        NavigationLink("View exercise history") {
                            ExerciseHistoryView(
                                viewModel: viewModel,
                                sourceExerciseId: exercise.sourceExerciseId,
                                sourceSystem: detail.sourceSystem,
                                displayName: exercise.displayName,
                                subject: subject
                            )
                        }
                        ForEach(exercise.sets) { trainingSet in
                            DetailedTrainingSetRow(trainingSet: trainingSet)
                        }
                        if let notes = exercise.notes, !notes.isEmpty {
                            Text(notes)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    } header: {
                        Text(exercise.displayName)
                    }
                }
            } else {
                Section { ProgressView("Loading recorded sets…") }
            }
        }
    }

    private static func formatDuration(_ secondsString: String) -> String {
        guard let totalSeconds = Double(secondsString) else { return secondsString }
        let hours = Int(totalSeconds) / 3600
        let minutes = (Int(totalSeconds) % 3600) / 60
        if hours > 0 {
            return "\(hours)h \(minutes)m"
        } else {
            return "\(minutes)m"
        }
    }
}

private struct DetailedTrainingSetRow: View {
    @AppStorage(TrainingWeightUnit.preferenceKey) private var weightUnit = "lb"
    private var displayUnit: TrainingWeightUnit { TrainingWeightUnit(rawValue: weightUnit) ?? .lb }
    let trainingSet: DetailedTrainingSetDTO

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("Set \(trainingSet.setIndex + 1)")
                    .font(.subheadline)
                Text(trainingSet.setType.capitalized)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                if let rpe = trainingSet.rpe {
                    Text("RPE \(rpe)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            VStack(alignment: .leading, spacing: 4) {
                if let reps = trainingSet.reps {
                    Text("\(reps) reps")
                }
                if let load = trainingSet.load {
                    Text("\(displayUnit.display(load.value, sourceUnit: load.unit))")
                }
                if let distance = trainingSet.distance {
                    Text("\(distance.value) \(distance.unit)")
                }
                if let duration = trainingSet.durationSeconds {
                    Text("\(duration) sec")
                }
                if let custom = trainingSet.customMetric {
                    Text("Custom: \(custom)")
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Exercise Occurrence within a Session

private struct ExerciseOccurrenceView: View {
    @AppStorage(TrainingWeightUnit.preferenceKey) private var weightUnit = "lb"
    private var displayUnit: TrainingWeightUnit { TrainingWeightUnit(rawValue: weightUnit) ?? .lb }
    let exercise: ExerciseOccurrenceAnalyticsDTO

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(familyLabel(exercise.metricFamily))
                    .font(.caption)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(.quaternary)
                    .clipShape(Capsule())
                Spacer()
                metricSummary
            }

            if let topSet = exercise.topLoadSet {
                topSetView(topSet)
            }

            metricDetails
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var metricSummary: some View {
        HStack(spacing: 8) {
            Text("\(exercise.workingSetCount) working")
                .font(.caption)
                .foregroundStyle(.secondary)
            if exercise.warmupSetCount > 0 {
                Text("\(exercise.warmupSetCount) warmup")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private func topSetView(_ topSet: TopLoadSetEvidenceDTO) -> some View {
        HStack(spacing: 4) {
            Image(systemName: "trophy.fill")
                .font(.caption2)
                .foregroundStyle(.yellow)
            Text("Top: \(displayUnit.display(topSet.loadKg)) × \(topSet.reps) reps")
                .font(.caption)
            if let rpe = topSet.rpe {
                Text("@ RPE \(rpe)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Text("(\(topSet.setType))")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private var metricDetails: some View {
        HStack(spacing: 12) {
            if let reps = exercise.repTotal {
                Text("\(reps) reps")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let volume = exercise.volumeKgReps {
                Text("\(displayUnit.display(volume, volume: true))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let duration = exercise.durationSeconds {
                Text(Self.formatDuration(duration))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let distance = exercise.distanceMeters {
                Text("\(distance) m")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private static func formatDuration(_ secondsString: String) -> String {
        guard let totalSeconds = Double(secondsString) else { return secondsString }
        let minutes = Int(totalSeconds) / 60
        let seconds = Int(totalSeconds) % 60
        return "\(minutes)m \(seconds)s"
    }

    private func familyLabel(_ family: String) -> String {
        switch family {
        case "rep_load": return "Rep + Load"
        case "bodyweight_rep": return "Bodyweight"
        case "assisted_rep": return "Assisted"
        case "duration": return "Duration"
        case "distance": return "Distance"
        case "other": return "Other"
        default: return family
        }
    }
}

// MARK: - Exercise History

struct ExerciseHistoryView: View {
    @AppStorage(TrainingWeightUnit.preferenceKey) private var weightUnit = "lb"
    private var displayUnit: TrainingWeightUnit { TrainingWeightUnit(rawValue: weightUnit) ?? .lb }
    let viewModel: TrainingViewModel
    let sourceExerciseId: String
    let sourceSystem: String
    let displayName: String
    let subject: String

    var body: some View {
        List {
            switch viewModel.historyPhase {
            case .idle, .loading:
                ProgressView("Loading exercise history…")
            case .error(let message):
                Section {
                    NytrStatusLabel(title: message, systemImage: "exclamationmark.triangle")
                    Button("Try Again") {
                        Task {
                            await viewModel.loadExerciseHistory(
                                sourceExerciseId: sourceExerciseId,
                                sourceSystem: sourceSystem
                            )
                        }
                    }
                }
            case .signedOut:
                Text("Sign in to view exercise history.")
                    .foregroundStyle(.secondary)
            case .result(let response):
                coachingSection(response.coaching)
                comparisonSection(response)
                prSection(response)
                frequencySection(response.frequency)
                completenessSection(response.completeness)

                Section("Recorded sessions") {
                    ForEach(response.history) { point in
                        historyPointRow(point)
                    }
                }
            }
        }
        #if os(iOS)
            .listStyle(.insetGrouped)
        #endif
        .nytrList()
        .navigationTitle(displayName)
        .toolbar { ToolbarItem(placement: .primaryAction) { TrainingUnitPicker() } }
        .task(id: "\(subject):\(sourceExerciseId)") {
            await viewModel.loadExerciseHistory(
                sourceExerciseId: sourceExerciseId,
                sourceSystem: sourceSystem
            )
        }
    }

    @ViewBuilder
    private func coachingSection(_ coaching: ExerciseCoachingGuidanceDTO) -> some View {
        Section("Nytr coaching · next session") {
            Label {
                Text(coachingStatusLabel(coaching.status))
            } icon: {
                Image(systemName: coachingStatusIcon(coaching.status))
            }
            .foregroundStyle(coaching.status == "progress" ? .green : .secondary)

            Text(coachingInstruction(coaching))
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(NytrDesign.accent.opacity(0.08), in: RoundedRectangle(cornerRadius: 16))
                .font(.title3.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)

            if let target = coaching.target {
                NytrMetricRow("Working Sets", value: String(target.workingSetCount))
            }

            Text(
                "Advisory only. Nytr cannot assess form, recovery, pain, or your program's rep range. It never changes Hevy."
            )
            .font(.caption)
            .foregroundStyle(.secondary)
        }
    }

    private func coachingInstruction(_ coaching: ExerciseCoachingGuidanceDTO) -> String {
        guard let action = coaching.action, let target = coaching.target else {
            return unavailableCoachingMessage(coaching.reasonCodes.first)
        }
        switch action {
        case "add_one_top_set_rep_same_load":
            guard let load = target.topLoadKg, let reps = target.topSetReps else {
                return "Guidance is unavailable because its exact target is incomplete."
            }
            return "Keep the top-set load at \(displayUnit.display(load)) and aim for \(reps) reps."
        case "add_one_total_rep_bodyweight":
            guard let reps = target.totalReps else {
                return "Guidance is unavailable because its exact target is incomplete."
            }
            return "Keep this bodyweight movement and aim for \(reps) total reps across the same working sets."
        case "add_one_total_rep_same_assistance":
            guard let reps = target.totalReps else {
                return "Guidance is unavailable because its exact target is incomplete."
            }
            return
                "Keep every assistance setting unchanged and aim for \(reps) total reps across the same working sets."
        case "repeat_latest_top_set":
            guard let load = target.topLoadKg, let reps = target.topSetReps else {
                return "Hold the latest performance; its exact target is incomplete."
            }
            return "Hold: repeat the latest top set at \(displayUnit.display(load)) for \(reps) reps."
        case "repeat_latest_rep_total":
            guard let reps = target.totalReps else {
                return "Hold the latest performance; its exact target is incomplete."
            }
            if target.keepAssistanceConstant {
                return "Hold: keep every assistance setting unchanged and repeat \(reps) total reps."
            }
            return "Hold: repeat \(reps) total bodyweight reps across the same working sets."
        default:
            return "Guidance is not available for this performance."
        }
    }

    private func unavailableCoachingMessage(_ reason: String?) -> String {
        switch reason {
        case "history_incomplete":
            return "Sync your Hevy history before reviewing the next session."
        case "insufficient_history":
            return "Guidance needs two comparable performances of this exact exercise."
        case "stale_latest_performance":
            return "A more recent performance is needed for guidance."
        case "future_evidence":
            return "The latest workout is dated in the future. Check its date in Hevy."
        case "assistance_configuration_changed":
            return "Assistance changed between sessions, so Nytr will not infer a progression target."
        case "working_set_structure_changed", "repeated_exercise_occurrence", "metric_family_changed":
            return "The exercise or working sets changed, so these sessions cannot be compared."
        case "unsupported_metric_family":
            return "This exercise type does not support progressive-overload guidance."
        case "incomplete_metrics", "unsupported_sets_present", "source_evidence_unavailable",
            "assistance_configuration_incomplete", "top_set_unavailable", "rep_total_unavailable":
            return "Some workout details needed for guidance are missing."
        case "rep_target_out_of_bounds":
            return "The rep target is outside the supported range for guidance."
        default:
            return "Guidance is not available for this performance."
        }
    }

    private func coachingStatusLabel(_ status: String) -> String {
        switch status {
        case "progress": return "Progress one rep"
        case "hold": return "Hold current performance"
        default: return "Guidance unavailable"
        }
    }

    private func coachingStatusIcon(_ status: String) -> String {
        switch status {
        case "progress": return "arrow.up.circle.fill"
        case "hold": return "pause.circle.fill"
        default: return "info.circle.fill"
        }
    }

    @ViewBuilder
    private func comparisonSection(_ response: ExerciseTrainingHistoryResponse) -> some View {
        let comparison = response.comparison
        Section("Hevy history · latest vs previous") {
            if comparison.previousRevisionId == nil {
                Text("No previous session for comparison.")
                    .foregroundStyle(.secondary)
            } else {
                if let loadDelta = comparison.topLoadDeltaKg {
                    NytrMetricRow("Top Load Change", value: deltaLoad(loadDelta))
                }
                if let repDelta = comparison.repsAtSameTopLoadDelta {
                    NytrMetricRow("Reps at Same Load", value: deltaInt(repDelta))
                }
                if let volumeDelta = comparison.volumeDeltaKgReps {
                    NytrMetricRow("Volume Change", value: deltaLoad(volumeDelta, volume: true))
                }
                if let setDelta = comparison.workingSetCountDelta {
                    NytrMetricRow("Working Sets", value: deltaInt(setDelta))
                }
            }
        }
    }

    @ViewBuilder
    private func prSection(_ response: ExerciseTrainingHistoryResponse) -> some View {
        if !response.prEvidence.isEmpty {
            Section("Personal Records") {
                ForEach(response.prEvidence) { pr in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack {
                            Image(systemName: "star.fill")
                                .foregroundStyle(.yellow)
                                .font(.caption2)
                            Text(prTypeLabel(pr.prType))
                                .font(.subheadline)
                        }
                        prValueText(pr)
                        Text(prScopeLabel(pr.scope))
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 2)
                }
            }
        }
    }

    @ViewBuilder
    private func frequencySection(_ frequency: ExerciseFrequencyDTO) -> some View {
        Section("Frequency") {
            NytrMetricRow(
                "Last 7 Days",
                value: "\(frequency.sessionsLast7Days) session\(frequency.sessionsLast7Days == 1 ? "" : "s")")
            NytrMetricRow(
                "Last 28 Days",
                value: "\(frequency.sessionsLast28Days) session\(frequency.sessionsLast28Days == 1 ? "" : "s")")
            if let days = frequency.daysSinceLastPerformance {
                NytrMetricRow("Days Since Last", value: String(days))
            }
        }
    }

    @ViewBuilder
    private func completenessSection(_ completeness: TrainingHistoryCompletenessDTO) -> some View {
        Section {
            Text(completeness.wording)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func historyPointRow(_ point: ExerciseHistoryPointDTO) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            VStack(alignment: .leading, spacing: 4) {
                Text(point.sessionTitle)
                    .font(.subheadline)
                Text(Self.formatDate(point.startedAt))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            HStack(spacing: 12) {
                Text("\(point.workingSetCount) working sets")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let reps = point.repTotal {
                    Text("\(reps) reps")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            if let topSet = point.topLoadSet {
                Text("Top: \(displayUnit.display(topSet.loadKg)) × \(topSet.reps)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let volume = point.volumeKgReps {
                Text("Volume: \(displayUnit.display(volume, volume: true))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }

    // MARK: - Helpers

    private func deltaLoad(_ value: String, volume: Bool = false) -> String {
        (value.hasPrefix("-") ? "" : "+") + displayUnit.display(value, volume: volume)
    }

    private func deltaString(_ value: String, unit: String) -> String {
        if value.hasPrefix("-") {
            return "\(value) \(unit)"
        } else {
            return "+\(value) \(unit)"
        }
    }

    private func deltaInt(_ value: Int) -> String {
        if value > 0 { return "+\(value)" }
        return String(value)
    }

    private func prTypeLabel(_ type: String) -> String {
        switch type {
        case "highest_load_for_exercise": return "Highest Load"
        case "most_reps_at_same_load": return "Most Reps at Same Load"
        case "highest_valid_volume_session": return "Highest Eligible Volume"
        default: return type
        }
    }

    @ViewBuilder
    private func prValueText(_ pr: TrainingPREvidenceDTO) -> some View {
        HStack(spacing: 8) {
            if let load = pr.loadKg {
                Text("\(displayUnit.display(load))")
                    .font(.caption)
            }
            if let reps = pr.reps {
                Text("\(reps) reps")
                    .font(.caption)
            }
            if let volume = pr.volumeKgReps {
                Text("\(displayUnit.display(volume, volume: true))")
                    .font(.caption)
            }
        }
    }

    private func prScopeLabel(_ scope: String) -> String {
        switch scope {
        case "within_synced_hevy_history": return "Within synced Hevy history"
        case "within_bounded_synced_hevy_history": return "Within the Hevy history synced to Nytr"
        default: return scope
        }
    }

    private static func formatDate(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return formatter.string(from: date)
    }
}

// MARK: - Exercise Index

struct ExerciseIndexView: View {
    @AppStorage(TrainingWeightUnit.preferenceKey) private var weightUnit = "lb"
    private var displayUnit: TrainingWeightUnit { TrainingWeightUnit(rawValue: weightUnit) ?? .lb }
    let viewModel: TrainingViewModel
    let subject: String

    var body: some View {
        List {
            switch viewModel.indexPhase {
            case .idle, .loading:
                ProgressView("Loading exercise index…")
            case .error(let message):
                Section {
                    NytrStatusLabel(title: message, systemImage: "exclamationmark.triangle")
                    Button("Try Again") { Task { await viewModel.refreshExerciseIndex() } }
                }
            case .signedOut:
                Text("Sign in to view exercises.")
                    .foregroundStyle(.secondary)
            case .empty:
                Section {
                    Text("No exercises recorded yet.")
                        .foregroundStyle(.secondary)
                }
            case .result(let response):
                Section {
                    Text(response.completeness.wording)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                ForEach(response.exercises) { entry in
                    NavigationLink {
                        ExerciseHistoryView(
                            viewModel: viewModel,
                            sourceExerciseId: entry.sourceExerciseId,
                            sourceSystem: entry.sourceSystem,
                            displayName: entry.latestDisplayName,
                            subject: subject
                        )
                    } label: {
                        exerciseIndexRow(entry)
                    }
                }
            }
        }
        #if os(iOS)
            .listStyle(.insetGrouped)
        #endif
        .navigationTitle("All Exercises")
        .refreshable { await viewModel.refreshExerciseIndex() }
        .task(id: subject) { await viewModel.loadExerciseIndex() }
    }

    @ViewBuilder
    private func exerciseIndexRow(_ entry: ExerciseIndexEntryDTO) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(entry.latestDisplayName)
                .font(.subheadline)
            HStack(spacing: 12) {
                Text("\(entry.sessionCount) session\(entry.sessionCount == 1 ? "" : "s")")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(familyLabel(entry.latestMetricFamily))
                    .font(.caption)
                    .padding(.horizontal, 4)
                    .padding(.vertical, 1)
                    .background(.quaternary)
                    .clipShape(Capsule())
            }
            if let topSet = entry.latestTopLoadSet {
                Text("Latest top: \(displayUnit.display(topSet.loadKg)) × \(topSet.reps) reps")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            HStack(spacing: 12) {
                Text("7d: \(entry.frequency.sessionsLast7Days)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Text("28d: \(entry.frequency.sessionsLast28Days)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }

    private func familyLabel(_ family: String) -> String {
        switch family {
        case "rep_load": return "Rep + Load"
        case "bodyweight_rep": return "Bodyweight"
        case "assisted_rep": return "Assisted"
        case "duration": return "Duration"
        case "distance": return "Distance"
        case "other": return "Other"
        default: return family
        }
    }
}
