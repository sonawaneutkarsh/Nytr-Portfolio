import Foundation
import Observation

@MainActor
@Observable
final class SignInViewModel {
    enum Phase: Equatable {
        case idle
        case sending
        case checkEmail
        case failed(message: String)
    }

    private let requester: any MagicLinkRequesting
    private let redirectURL: URL

    var email = ""
    private(set) var phase: Phase = .idle

    init(
        requester: any MagicLinkRequesting,
        redirectURL: URL = URL(string: "nutritionhealthcompanion://auth-callback")!
    ) {
        self.requester = requester
        self.redirectURL = redirectURL
    }

    func sendMagicLink() async {
        guard phase != .sending else { return }
        let normalizedEmail = email.trimmingCharacters(in: .whitespacesAndNewlines)
        guard Self.looksLikeEmail(normalizedEmail) else {
            phase = .failed(message: "Enter a valid email address.")
            return
        }

        phase = .sending
        do {
            try await requester.requestMagicLink(email: normalizedEmail, redirectTo: redirectURL)
            phase = .checkEmail
        } catch {
            phase = .failed(message: "We couldn't send the sign-in link. Please try again.")
        }
    }

    private static func looksLikeEmail(_ value: String) -> Bool {
        let parts = value.split(separator: "@", omittingEmptySubsequences: false)
        return parts.count == 2 && parts[0].isEmpty == false && parts[1].contains(".")
    }
}
