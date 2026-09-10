import SwiftUI

struct SignInView: View {
    @Bindable var viewModel: SignInViewModel
    let callbackError: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Email", text: $viewModel.email)
                        .textContentType(.emailAddress)

                    Button("Send magic link") {
                        Task { await viewModel.sendMagicLink() }
                    }
                    .disabled(viewModel.phase == .sending)
                } header: {
                    Text("Sign in")
                } footer: {
                    Text("We'll email you a secure link to open in this app.")
                }

                if viewModel.phase == .sending {
                    ProgressView("Sending…")
                }
                if viewModel.phase == .checkEmail {
                    Text("Check your email, then open the sign-in link on this device.")
                        .foregroundStyle(.secondary)
                }
                if case let .failed(message) = viewModel.phase {
                    Label(message, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red)
                }
                if let callbackError {
                    Label(callbackError, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red)
                }
            }
            .navigationTitle("Nytr")
        }
    }
}
