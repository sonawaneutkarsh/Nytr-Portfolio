import SwiftUI

#if os(iOS)
    import UIKit
#endif

struct SettingsView: View {
    @Binding var appearance: NytrAppearance
    let healthSyncViewModel: HealthSyncViewModel
    @State private var notificationViewModel: MealGuidanceNotificationViewModel
    let onSignOut: () -> Void

    init(
        appearance: Binding<NytrAppearance>,
        healthSyncViewModel: HealthSyncViewModel,
        notificationViewModel: MealGuidanceNotificationViewModel,
        onSignOut: @escaping () -> Void
    ) {
        _appearance = appearance
        self.healthSyncViewModel = healthSyncViewModel
        _notificationViewModel = State(initialValue: notificationViewModel)
        self.onSignOut = onSignOut
    }

    var body: some View {
        List {
            Section("Appearance") {
                Picker("Appearance", selection: $appearance) {
                    ForEach(NytrAppearance.allCases) { option in
                        Text(option.label).tag(option)
                    }
                }
                .pickerStyle(.segmented)
                Text("System follows your device. Light and Dark override it for Nytr.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("Apple Health") {
                NavigationLink("Health Sync") {
                    HealthSyncView(viewModel: healthSyncViewModel)
                }
            }
            Section {
                Toggle(
                    "Meal guidance notifications",
                    isOn: Binding(
                        get: { notificationViewModel.masterEnabled },
                        set: { enabled in
                            Task { await notificationViewModel.setMasterEnabled(enabled) }
                        }
                    )
                )
                Text("Get reminders when Nytr has a lunch or dinner recommendation ready.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Toggle(
                    "Lunch recommendations",
                    isOn: Binding(
                        get: { notificationViewModel.lunchEnabled },
                        set: { enabled in
                            Task { await notificationViewModel.setMeal(.lunch, enabled: enabled) }
                        }
                    )
                )
                .disabled(!notificationViewModel.masterEnabled)
                Toggle(
                    "Dinner recommendations",
                    isOn: Binding(
                        get: { notificationViewModel.dinnerEnabled },
                        set: { enabled in
                            Task { await notificationViewModel.setMeal(.dinner, enabled: enabled) }
                        }
                    )
                )
                .disabled(!notificationViewModel.masterEnabled)
                if notificationViewModel.permission == .denied {
                    Text("Notifications are denied for Nytr in iOS Settings.")
                        .foregroundStyle(.orange)
                    #if os(iOS)
                        Button("Open Settings") {
                            if let url = URL(string: UIApplication.openSettingsURLString) {
                                UIApplication.shared.open(url)
                            }
                        }
                    #endif
                }
                if notificationViewModel.permission == .authorized {
                    Button("Send test notification") {
                        Task { await notificationViewModel.sendTestNotification() }
                    }
                }
                if let message = notificationViewModel.message {
                    Text(message).font(.caption).foregroundStyle(.secondary)
                }
            } header: {
                Text("Notifications")
            } footer: {
                Text("Reminders are generic convenience alerts. They never record food or generate recommendations.")
            }
            Section("Account") {
                Button("Sign Out", role: .destructive, action: onSignOut)
            }
            Section("About") {
                HStack(spacing: 12) {
                    NytrBrandMark(size: 42)
                    VStack(alignment: .leading) {
                        Text("Nytr").font(.headline)
                        Text("Nutrition and training grounded in your records.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .navigationTitle("Settings")
        .task { await notificationViewModel.refreshPermission() }
    }
}

struct NytrSettingsToolbarButton: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "gearshape")
        }
        .accessibilityLabel("Settings")
        .accessibilityHint("Opens Nytr settings")
    }
}
