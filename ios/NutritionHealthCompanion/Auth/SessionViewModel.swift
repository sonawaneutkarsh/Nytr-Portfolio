import Foundation
import Observation

@MainActor
@Observable
final class SessionViewModel {
    enum Phase: Equatable {
        case checking
        case signedOut
        case signedIn(subject: String)
    }

    private let auth: any AppAuthSession
    private(set) var phase: Phase = .checking
    private(set) var callbackError: String?
    private var suppressForegroundSyncForCallbackActivation = false

    init(auth: any AppAuthSession) {
        self.auth = auth
    }

    func checkSession() async {
        phase = .checking
        do {
            phase = .signedIn(subject: try await auth.currentSubject())
        } catch {
            phase = .signedOut
        }
    }

    @discardableResult
    func handleOpenURL(_ url: URL) -> Bool {
        do {
            let subject = try auth.completeMagicLinkCallback(url)
            callbackError = nil
            phase = .signedIn(subject: subject)
            // Opening the magic link also activates the scene. Let Today
            // render before any HealthKit authorization-capable foreground
            // sync; the next ordinary foreground activation still syncs.
            suppressForegroundSyncForCallbackActivation = true
            return true
        } catch {
            callbackError = "The sign-in link is invalid or expired. Request a new link."
            if case .signedIn = phase {
                // An unrelated or stale callback must not destroy a valid session.
            } else {
                phase = .signedOut
            }
            return false
        }
    }

    /// Returns whether an ordinary foreground activation should start the
    /// existing HealthKit catch-up path. A successful auth callback consumes
    /// exactly one same-activation trigger so its system authorization sheet
    /// cannot obscure the transition to Today.
    func shouldStartForegroundSync() -> Bool {
        guard case .signedIn = phase else { return false }
        if suppressForegroundSyncForCallbackActivation {
            suppressForegroundSyncForCallbackActivation = false
            return false
        }
        return true
    }

    /// If scene activation was observed before its callback, clear the pending
    /// one-shot suppression when that activation ends. The next real
    /// foreground entry must retain the existing catch-up behavior.
    func sceneDidLeaveActiveState() {
        suppressForegroundSyncForCallbackActivation = false
    }

    func signOut(clearingLocalState: () -> Void = {}) {
        try? auth.signOut()
        clearingLocalState()
        callbackError = nil
        suppressForegroundSyncForCallbackActivation = false
        phase = .signedOut
    }
}
