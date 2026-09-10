import Foundation
import Observation

@MainActor
@Observable
final class BodyGoalsViewModel {
    enum Phase: Equatable {
        case loading
        case loaded(BodyGoalsResponse)
        case error(String)
        case signedOut
    }

    private(set) var phase: Phase = .loading
    private(set) var estimate: StartingCalorieEstimateDTO?
    private(set) var isWorking = false
    private let backend: any BackendClient
    private let requestDate: () -> Date
    private let timezoneIdentifier: () -> String
    private let onUnauthorized: @MainActor () -> Void
    private var subject: String?

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

    func activate(subject: String) async {
        guard self.subject != subject else { return }
        self.subject = subject
        await refresh()
    }

    func refresh() async {
        guard subject != nil else { return }
        do {
            phase = .loaded(try await backend.fetchBodyGoals(
                asOfDate: requestDate(), timezone: timezoneIdentifier()
            ))
        } catch BackendError.unauthorized {
            resetForSignOut()
            onUnauthorized()
        } catch {
            phase = .error("Body & Goals is temporarily unavailable.")
        }
    }

    func generateEstimate() async {
        guard subject != nil, !isWorking else { return }
        isWorking = true
        defer { isWorking = false }
        do {
            estimate = try await backend.fetchStartingCalorieEstimate(
                asOfDate: requestDate(), timezone: timezoneIdentifier()
            )
        } catch BackendError.unauthorized {
            resetForSignOut()
            onUnauthorized()
        } catch {
            phase = .error("A fresh HealthKit weight is required for a starting estimate.")
        }
    }

    func saveProfile(heightCm: String, targetWeightKg: String?) async {
        guard subject != nil, !isWorking else { return }
        isWorking = true
        defer { isWorking = false }
        do {
            _ = try await backend.saveBodyProfile(
                heightCm: heightCm,
                targetWeightKg: targetWeightKg?.isEmpty == true ? nil : targetWeightKg
            )
            await refresh()
        } catch BackendError.unauthorized {
            resetForSignOut()
            onUnauthorized()
        } catch {
            phase = .error("Profile could not be saved. Check the measurements and try again.")
        }
    }

    func recordWaist(value: String, unit: String) async {
        guard subject != nil, !isWorking else { return }
        isWorking = true
        defer { isWorking = false }
        do {
            _ = try await backend.recordWaist(value: value, unit: unit, measuredAt: requestDate())
            await refresh()
        } catch BackendError.unauthorized {
            resetForSignOut()
            onUnauthorized()
        } catch {
            phase = .error("Waist measurement could not be saved.")
        }
    }

    func resetForSignOut() {
        subject = nil
        phase = .signedOut
        estimate = nil
        isWorking = false
    }
}
