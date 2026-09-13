import Foundation
import Observation

@MainActor
@Observable
final class NutritionHistoryViewModel {
    enum Phase: Equatable {
        case idle
        case loading
        case result(NutritionHistory7DayResponse)
        case error(String)
        case signedOut
    }

    private let backend: any BackendClient
    private let requestDate: () -> Date
    private let timezoneIdentifier: () -> String
    private let onUnauthorized: @MainActor () -> Void
    private var subject: String?

    private(set) var phase: Phase = .idle
    private(set) var isLoading = false

    init(
        backend: any BackendClient,
        requestDate: @escaping () -> Date = { WireDay.localRequestDate() },
        timezoneIdentifier: @escaping () -> String = { TimeZone.current.identifier },
        onUnauthorized: @escaping @MainActor () -> Void = {}
    ) {
        self.backend = backend
        self.requestDate = requestDate
        self.timezoneIdentifier = timezoneIdentifier
        self.onUnauthorized = onUnauthorized
    }

    func activate(subject: String) async {
        if self.subject != subject {
            self.subject = subject
            phase = .idle
        }
        if case .idle = phase {
            await load()
        }
    }

    func refresh() async {
        guard subject != nil else { return }
        await load()
    }

    func resetForSignOut() {
        subject = nil
        phase = .signedOut
        isLoading = false
    }

    private func load() async {
        guard subject != nil, !isLoading else { return }
        isLoading = true
        phase = .loading
        defer { isLoading = false }
        do {
            let history = try await backend.fetchNutritionHistory(
                endDate: requestDate(),
                timezone: timezoneIdentifier()
            )
            guard subject != nil else { return }
            phase = .result(history)
        } catch BackendError.unauthorized {
            resetForSignOut()
            onUnauthorized()
        } catch {
            guard subject != nil else { return }
            phase = .error("Nutrition history is temporarily unavailable.")
        }
    }
}
