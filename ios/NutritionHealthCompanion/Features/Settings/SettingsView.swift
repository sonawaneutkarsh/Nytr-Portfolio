import SwiftUI

struct SettingsView: View {
    let healthSyncViewModel: HealthSyncViewModel
    let onSignOut: () -> Void

    var body: some View {
        List {
            Section("Apple Health") {
                NavigationLink("Health Sync") {
                    HealthSyncView(viewModel: healthSyncViewModel)
                }
            }
            Section("Account") {
                Button("Sign Out", role: .destructive, action: onSignOut)
            }
        }
        .navigationTitle("Settings")
    }
}
