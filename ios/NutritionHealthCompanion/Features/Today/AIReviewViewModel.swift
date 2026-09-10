import Foundation
import Observation

@MainActor
@Observable
final class AIReviewViewModel {
    enum Phase: Equatable {
        case idle
        case loading
        case result(AIReviewResponse)
        case unavailable(AIReviewResponse)
        case error(String)
        case signedOut
    }

    private(set) var phase: Phase = .idle
    private(set) var isRequesting = false

    private let backend: any BackendClient
    private let requestDate: () -> Date
    private let timezoneIdentifier: () -> String
    private let onUnauthorized: @MainActor () -> Void
    private var subject: String?
    private var requestToken: UUID?

    init(
        backend: any BackendClient,
        requestDate: @escaping () -> Date = Date.init,
        timezoneIdentifier: @escaping () -> String = { TimeZone.current.identifier },
        onUnauthorized: @escaping @MainActor () -> Void = {}
    ) {
        self.backend = backend
        self.requestDate = requestDate
        self.timezoneIdentifier = timezoneIdentifier
        self.onUnauthorized = onUnauthorized
    }

    /// Establishes owner isolation only. Opening the screen never calls AI.
    func activate(subject: String) {
        if self.subject != subject {
            self.subject = subject
            phase = .idle
            requestToken = nil
            isRequesting = false
        }
    }

    func generate() async {
        guard let expectedSubject = subject, !isRequesting else { return }
        let expectedToken = UUID()
        requestToken = expectedToken
        isRequesting = true
        phase = .loading
        defer {
            if requestToken == expectedToken {
                requestToken = nil
                isRequesting = false
            }
        }
        do {
            let response = try await backend.generateAIReview(
                asOfDate: requestDate(),
                timezone: timezoneIdentifier()
            )
            guard subject == expectedSubject, requestToken == expectedToken else { return }
            if response.status == "available", response.review != nil {
                phase = .result(response)
            } else {
                phase = .unavailable(response)
            }
        } catch BackendError.unauthorized {
            guard subject == expectedSubject, requestToken == expectedToken else { return }
            resetForSignOut()
            onUnauthorized()
        } catch {
            guard subject == expectedSubject, requestToken == expectedToken else { return }
            phase = .error("Nytr Review is temporarily unavailable.")
        }
    }

    func resetForSignOut() {
        subject = nil
        requestToken = nil
        phase = .signedOut
        isRequesting = false
    }
}
