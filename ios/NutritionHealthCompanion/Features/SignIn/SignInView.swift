import SwiftUI

struct SignInView: View {
    @Bindable var viewModel: SignInViewModel
    let callbackError: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack(spacing: 16) {
                        NytrBrandMark(size: 72)
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Nytr")
                                .font(.system(.largeTitle, design: .rounded, weight: .bold))
                            Text("Nutrition and training, grounded in your records.")
                                .font(.title3.weight(.medium))
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .accessibilityElement(children: .combine)
                }
                Section {
                    TextField("Email", text: $viewModel.email)
                        .textContentType(.emailAddress)
                        .autocorrectionDisabled()
                        #if os(iOS)
                            .keyboardType(.emailAddress)
                            .textInputAutocapitalization(.never)
                        #endif

                    Button("Email sign-in link") {
                        Task { await viewModel.sendMagicLink() }
                    }
                    .buttonStyle(.borderedProminent).tint(NytrDesign.buttonFill)
                    .controlSize(.large)
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
                if case .failed(let message) = viewModel.phase {
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
