import SwiftUI

/// The single screen of the M5 companion (plan §12). Deliberately minimal:
/// status, latest synchronized weight, last successful sync, Sync Now, and
/// permission-neutral empty-state copy. No dashboards/graphs/editors.
struct HealthSyncView: View {
    @State private var viewModel: HealthSyncViewModel

    init(viewModel: HealthSyncViewModel) {
        _viewModel = State(initialValue: viewModel)
    }

    var body: some View {
        NavigationStack {
            List {
                Section("Apple Health") {
                    healthKitStatusRow
                    authorizationHintRow
                    Text(TrainingSourceAuthorityCopy.healthSync)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("Synchronization") {
                    latestWeightRow
                    lastSyncRow
                    syncStatusRow
                    syncNowButton
                }
            }
            .navigationTitle("Health Sync")
        }
        .task { await viewModel.syncNow() }
    }

    @ViewBuilder
    private var healthKitStatusRow: some View {
        if viewModel.phase == .unavailable {
            LabeledContent("HealthKit", value: "Unavailable on this device")
        } else {
            LabeledContent("HealthKit", value: "Available")
        }
    }

    @ViewBuilder
    private var authorizationHintRow: some View {
        switch viewModel.phase {
        case .needsAuthorization:
            Text("Tap Sync Now to grant body-weight and workout read access.")
                .foregroundStyle(.secondary)
        case .needsSignIn:
            Text("Sign in to your account to upload samples.")
                .foregroundStyle(.secondary)
        default:
            EmptyView()
        }
    }

    @ViewBuilder
    private var latestWeightRow: some View {
        switch viewModel.phase {
        case .synced(let kg, let date), .stale(let kg, let date):
            LabeledContent("Latest weight") {
                VStack(alignment: .trailing) {
                    if let kg {
                        Text("\(kg) kg").monospacedDigit()
                    }
                    if let date {
                        Text(date, style: .date)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        case .noReadableSamples:
            // Permission-neutral copy (plan §4): never claims to know whether
            // access was granted; describes only what was observed.
            VStack(alignment: .leading, spacing: 4) {
                Text("No body-weight or workout samples were found to sync.")
                Text("If you expected data, check Settings → Health → Data Access & Devices.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        default:
            LabeledContent("Latest weight", value: "—")
        }
    }

    private var lastSyncRow: some View {
        LabeledContent("Last successful sync") {
            if let last = viewModel.lastSuccessfulSync {
                Text(last, style: .relative)
                    .foregroundStyle(viewModel.phase.isStale ? .orange : .primary)
            } else {
                Text("never")
            }
        }
    }

    @ViewBuilder
    private var syncStatusRow: some View {
        switch viewModel.phase {
        case .idle:
            LabeledContent("Status", value: "Ready")
        case .authorizing, .syncing:
            HStack {
                Text("Status")
                Spacer()
                ProgressView()
            }
        case .unavailable:
            LabeledContent("Status", value: "HealthKit unavailable")
        case .needsAuthorization:
            LabeledContent("Status", value: "Authorization needed")
        case .needsSignIn:
            LabeledContent("Status", value: "Sign-in required")
        case .noReadableSamples:
            LabeledContent("Status", value: "Nothing to sync")
        case .synced:
            LabeledContent("Status", value: "Up to date")
        case .stale:
            LabeledContent("Status", value: "Stale — open app to refresh")
        case .failed(let reason):
            LabeledContent("Status", value: "Error: \(reason)")
        }
    }

    private var syncNowButton: some View {
        Button {
            Task { await viewModel.syncNow() }
        } label: {
            if viewModel.isSyncingNow {
                ProgressView().controlSize(.regular)
            } else {
                Text("Sync Now")
            }
        }
        .disabled(viewModel.isSyncingNow || viewModel.phase == .unavailable)
    }
}

extension HealthSyncViewModel.Phase {
    var isStale: Bool {
        if case .stale = self { return true }
        return false
    }
}
