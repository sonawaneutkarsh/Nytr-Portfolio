import Foundation
import Observation

/// Manages explicit Hevy sync plus read-only factual analytics and coaching.
/// Deterministic guidance is returned by history reads; sync never runs automatically.
@MainActor
@Observable
final class TrainingViewModel {
    // MARK: - Phase Enums

    enum RecentPhase: Equatable {
        case idle
        case loading
        case result(TrainingAnalyticsRecentResponse)
        case empty
        case error(String)
        case signedOut
    }

    enum ExerciseIndexPhase: Equatable {
        case idle
        case loading
        case result(ExerciseIndexResponse)
        case empty
        case error(String)
        case signedOut
    }

    enum ExerciseHistoryPhase: Equatable {
        case idle
        case loading
        case result(ExerciseTrainingHistoryResponse)
        case error(String)
        case signedOut
    }

    enum SessionDetailPhase: Equatable {
        case idle
        case loading(String)
        case result(DetailedTrainingSessionResponse)
        case error(String, String)
        case signedOut
    }

    enum HevySyncPhase: Equatable {
        case idle
        case syncing
        case success(String)
        case error(String)
        case signedOut
    }

    // MARK: - Published State

    private(set) var recentPhase: RecentPhase = .idle
    private(set) var indexPhase: ExerciseIndexPhase = .idle
    private(set) var historyPhase: ExerciseHistoryPhase = .idle
    private(set) var sessionDetailPhase: SessionDetailPhase = .idle
    private(set) var hevySyncPhase: HevySyncPhase = .idle

    private(set) var isLoadingRecent = false
    private(set) var isLoadingIndex = false
    private(set) var isLoadingHistory = false
    private(set) var isLoadingSessionDetail = false
    private(set) var isSyncingHevy = false

    // MARK: - Dependencies

    private let backend: any BackendClient
    private let requestDate: () -> Date
    private let timezoneIdentifier: () -> String
    private let onUnauthorized: @MainActor () -> Void
    private var subject: String?
    private var recentOperationID: UUID?
    private var hevySyncOperationID: UUID?

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

    // MARK: - Lifecycle

    func activate(subject: String) async {
        if self.subject != subject {
            self.subject = subject
            recentPhase = .idle
            indexPhase = .idle
            historyPhase = .idle
            sessionDetailPhase = .idle
            hevySyncPhase = .idle
            recentOperationID = nil
            isLoadingRecent = false
            hevySyncOperationID = nil
            isSyncingHevy = false
        }
        if case .idle = recentPhase {
            await loadRecent()
        }
    }

    func resetForSignOut() {
        subject = nil
        recentPhase = .signedOut
        indexPhase = .signedOut
        historyPhase = .signedOut
        sessionDetailPhase = .signedOut
        hevySyncPhase = .signedOut
        isLoadingRecent = false
        isLoadingIndex = false
        isLoadingHistory = false
        isLoadingSessionDetail = false
        recentOperationID = nil
        hevySyncOperationID = nil
        isSyncingHevy = false
    }

    // MARK: - Explicit Hevy Sync

    func syncHevy() async {
        guard let operationSubject = subject,
              !isSyncingHevy,
              !isLoadingRecent
        else { return }

        let operationID = UUID()
        hevySyncOperationID = operationID
        isSyncingHevy = true
        hevySyncPhase = .syncing
        defer {
            if hevySyncOperationID == operationID {
                hevySyncOperationID = nil
                isSyncingHevy = false
            }
        }

        do {
            let response = try await backend.syncHevyTraining()
            guard subject == operationSubject,
                  hevySyncOperationID == operationID
            else { return }

            guard response.status == "synced",
                  response.mode == "incremental",
                  response.checkpointAdvanced,
                  !response.hasMore
            else {
                hevySyncPhase = .error(
                    "Hevy sync did not complete. Existing Training data is unchanged."
                )
                return
            }

            let reloaded = await loadRecent(
                expectedSubject: operationSubject,
                preserveCurrentOnFailure: true
            )
            guard subject == operationSubject,
                  hevySyncOperationID == operationID
            else { return }
            if reloaded {
                hevySyncPhase = .success(Self.syncSuccessMessage(response))
            } else {
                hevySyncPhase = .error(
                    "Hevy synced, but Training could not reload. Pull down to refresh."
                )
            }
        } catch BackendError.unauthorized {
            guard subject == operationSubject,
                  hevySyncOperationID == operationID
            else { return }
            resetForSignOut()
            onUnauthorized()
        } catch {
            guard subject == operationSubject,
                  hevySyncOperationID == operationID
            else { return }
            hevySyncPhase = .error(
                "Hevy sync is temporarily unavailable. Existing Training data is unchanged."
            )
        }
    }

    private static func syncSuccessMessage(_ response: HevySyncResponse) -> String {
        let changed = response.sessionsCreated + response.revisionsAppended
        if changed == 0, response.deletionsRecorded == 0 {
            return "Hevy is up to date. Training was refreshed."
        }
        let noun = changed == 1 ? "workout" : "workouts"
        if response.deletionsRecorded == 0 {
            return "Synced \(changed) \(noun) from Hevy."
        }
        return "Hevy sync complete. Training was refreshed."
    }

    // MARK: - Recent Workouts

    func refreshRecent() async {
        guard let activeSubject = subject, !isSyncingHevy else { return }
        _ = await loadRecent(expectedSubject: activeSubject)
    }

    @discardableResult
    private func loadRecent(
        expectedSubject: String? = nil,
        preserveCurrentOnFailure: Bool = false
    ) async -> Bool {
        guard let activeSubject = expectedSubject ?? subject,
              subject == activeSubject,
              recentOperationID == nil
        else { return false }
        let operationID = UUID()
        let priorPhase = recentPhase
        recentOperationID = operationID
        isLoadingRecent = true
        if !preserveCurrentOnFailure {
            recentPhase = .loading
        }
        defer {
            if recentOperationID == operationID {
                recentOperationID = nil
                isLoadingRecent = false
            }
        }
        do {
            let response = try await backend.fetchRecentTrainingAnalytics(limit: 20)
            guard subject == activeSubject,
                  recentOperationID == operationID
            else { return false }
            if response.sessions.isEmpty {
                recentPhase = .empty
            } else {
                recentPhase = .result(response)
            }
            return true
        } catch BackendError.unauthorized {
            guard subject == activeSubject,
                  recentOperationID == operationID
            else { return false }
            resetForSignOut()
            onUnauthorized()
            return false
        } catch {
            guard subject == activeSubject,
                  recentOperationID == operationID
            else { return false }
            if preserveCurrentOnFailure {
                recentPhase = priorPhase
            } else {
                recentPhase = .error("Training data is temporarily unavailable.")
            }
            return false
        }
    }

    // MARK: - Exercise Index

    func loadExerciseIndex() async {
        guard subject != nil, !isLoadingIndex else { return }
        if case .idle = indexPhase {} else if case .error = indexPhase {} else { return }
        isLoadingIndex = true
        indexPhase = .loading
        defer { isLoadingIndex = false }
        do {
            let response = try await backend.fetchExerciseIndex(
                asOfDate: requestDate(),
                timezone: timezoneIdentifier(),
                limit: 200
            )
            guard subject != nil else { return }
            if response.exercises.isEmpty {
                indexPhase = .empty
            } else {
                indexPhase = .result(response)
            }
        } catch BackendError.unauthorized {
            resetForSignOut()
            onUnauthorized()
        } catch {
            guard subject != nil else { return }
            indexPhase = .error("Exercise index is temporarily unavailable.")
        }
    }

    func refreshExerciseIndex() async {
        guard subject != nil else { return }
        indexPhase = .idle
        await loadExerciseIndex()
    }

    // MARK: - Exercise History

    func loadExerciseHistory(sourceExerciseId: String, sourceSystem: String) async {
        guard subject != nil, !isLoadingHistory else { return }
        isLoadingHistory = true
        historyPhase = .loading
        defer { isLoadingHistory = false }
        do {
            let response = try await backend.fetchExerciseHistory(
                sourceExerciseId: sourceExerciseId,
                sourceSystem: sourceSystem,
                asOfDate: requestDate(),
                timezone: timezoneIdentifier(),
                limit: 50
            )
            guard subject != nil else { return }
            historyPhase = .result(response)
        } catch BackendError.unauthorized {
            resetForSignOut()
            onUnauthorized()
        } catch BackendError.rejected(statusCode: 404, _, _) {
            guard subject != nil else { return }
            historyPhase = .error("No history found for this exercise.")
        } catch {
            guard subject != nil else { return }
            historyPhase = .error("Exercise history is temporarily unavailable.")
        }
    }

    // MARK: - Immutable Session Revision Detail

    func loadSessionDetail(revisionId: String) async {
        guard subject != nil, !isLoadingSessionDetail else { return }
        if case .result(let current) = sessionDetailPhase,
           current.revisionId == revisionId
        {
            return
        }
        isLoadingSessionDetail = true
        sessionDetailPhase = .loading(revisionId)
        defer { isLoadingSessionDetail = false }
        do {
            let response = try await backend.fetchDetailedTrainingSession(
                revisionId: revisionId
            )
            guard subject != nil else { return }
            guard response.revisionId == revisionId else {
                sessionDetailPhase = .error(
                    revisionId,
                    "The detailed workout response did not match the selected revision."
                )
                return
            }
            sessionDetailPhase = .result(response)
        } catch BackendError.unauthorized {
            resetForSignOut()
            onUnauthorized()
        } catch {
            guard subject != nil else { return }
            sessionDetailPhase = .error(
                revisionId,
                "Workout details are temporarily unavailable."
            )
        }
    }
}
