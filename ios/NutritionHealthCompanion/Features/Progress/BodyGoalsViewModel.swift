import Foundation
import Observation

@MainActor
@Observable
final class BodyGoalsViewModel {
    enum Phase: Equatable {
        case loading
        case ready(BodyGoalsResponse)
        case error(String)
        case signedOut
    }

    private let backend: any BackendClient
    private let requestDate: () -> Date
    private let timezone: () -> String
    private let eventId: () -> UUID
    private let onUnauthorized: @MainActor () -> Void
    private let onTargetsChanged: @MainActor () async -> Void
    private var subject: String?
    private var pendingDecisionEvent: UUID?

    private(set) var phase: Phase = .loading
    private(set) var isWorking = false
    private(set) var message: String?

    init(
        backend: any BackendClient,
        requestDate: @escaping () -> Date = { WireDay.localRequestDate() },
        timezone: @escaping () -> String = { TimeZone.current.identifier },
        eventId: @escaping () -> UUID = UUID.init,
        onUnauthorized: @escaping @MainActor () -> Void = {},
        onTargetsChanged: @escaping @MainActor () async -> Void = {}
    ) {
        self.backend = backend
        self.requestDate = requestDate
        self.timezone = timezone
        self.eventId = eventId
        self.onUnauthorized = onUnauthorized
        self.onTargetsChanged = onTargetsChanged
    }

    var current: BodyGoalsResponse? {
        guard case .ready(let value) = phase else { return nil }
        return value
    }

    func activate(subject: String) async {
        guard self.subject != subject else { return }
        self.subject = subject
        await refresh()
    }

    func refresh() async {
        guard subject != nil else { phase = .signedOut; return }
        phase = .loading
        do {
            phase = .ready(try await backend.fetchBodyGoals(
                asOfDate: requestDate(), timezone: timezone()
            ))
        } catch BackendError.unauthorized {
            signedOut()
        } catch {
            phase = .error("Body & Goals could not be loaded. Try again.")
        }
    }

    func saveProfile(
        heightCm: String,
        dateOfBirth: Date,
        formulaSex: String,
        activityLevel: String,
        targetWeightKg: String
    ) async {
        guard subject != nil, !isWorking else { return }
        let height = heightCm.trimmingCharacters(in: .whitespacesAndNewlines)
        let target = targetWeightKg.trimmingCharacters(in: .whitespacesAndNewlines)
        guard Self.positiveDecimal(height), target.isEmpty || Self.positiveDecimal(target) else {
            message = "Enter valid positive measurements."
            return
        }
        isWorking = true
        defer { isWorking = false }
        do {
            _ = try await backend.saveBodyGoalProfile(SaveBodyGoalProfileRequest(
                heightCm: height,
                dateOfBirth: WireDay.string(from: dateOfBirth),
                formulaSex: formulaSex,
                activityLevel: activityLevel,
                targetWeightKg: target.isEmpty ? nil : target
            ))
            message = "Profile evidence saved. Changed values create a new version."
            await refresh()
        } catch BackendError.unauthorized {
            signedOut()
        } catch {
            message = "Profile evidence could not be saved."
        }
    }

    func addWaist(value: String, unit: String, measuredAt: Date, correcting: UUID?) async {
        guard subject != nil, !isWorking else { return }
        let value = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard Self.positiveDecimal(value) else { message = "Enter a valid waist measurement."; return }
        isWorking = true
        defer { isWorking = false }
        do {
            _ = try await backend.addWaistMeasurement(AddWaistRequest(
                value: value, unit: unit, measuredAt: WireDate.string(from: measuredAt),
                correctsMeasurementId: correcting
            ))
            message = correcting == nil ? "Waist evidence recorded." : "Correction appended; history was retained."
            await refresh()
        } catch BackendError.unauthorized {
            signedOut()
        } catch {
            message = "Waist evidence could not be recorded."
        }
    }

    func generateStartingTarget() async {
        guard subject != nil, !isWorking else { return }
        isWorking = true
        defer { isWorking = false }
        do {
            _ = try await backend.generateStartingCalorieProposal(
                asOfDate: requestDate(), timezone: timezone()
            )
            message = "Starting estimate generated. Review it before approval."
            await refresh()
        } catch BackendError.unauthorized {
            signedOut()
        } catch BackendError.rejected(_, _, let detail) {
            message = detail ?? "Complete the required profile, goal, and HealthKit weight first."
        } catch {
            message = "Starting estimate could not be generated."
        }
    }

    func decideStartingTarget(_ decision: String) async {
        guard subject != nil, !isWorking, let proposal = current?.startingCalorieProposal else { return }
        let event = pendingDecisionEvent ?? eventId()
        pendingDecisionEvent = event
        isWorking = true
        defer { isWorking = false }
        do {
            _ = try await backend.decideStartingCalorieProposal(
                proposalId: proposal.proposalId, decision: decision, clientEventId: event
            )
            pendingDecisionEvent = nil
            message = decision == "approved" ? "Starting calorie target approved." : "Starting estimate rejected; no target changed."
            await onTargetsChanged()
            await refresh()
        } catch BackendError.unauthorized {
            signedOut()
        } catch {
            message = "The decision could not be recorded. Try again."
        }
    }

    func resetForSignOut() {
        subject = nil
        pendingDecisionEvent = nil
        phase = .signedOut
        message = nil
        isWorking = false
    }

    private func signedOut() {
        resetForSignOut()
        onUnauthorized()
    }

    private static func positiveDecimal(_ value: String) -> Bool {
        guard let decimal = Decimal(string: value, locale: Locale(identifier: "en_US_POSIX")) else {
            return false
        }
        return decimal > 0
    }
}
