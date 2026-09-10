import Foundation
import Observation

@MainActor
@Observable
final class TodayViewModel {
    enum Phase: Equatable {
        case loading
        case cached(plan: CompletedDayPlan, cachedAt: Date)
        case completed(CompletedDayPlan)
        case noPlan(NoPlanDay)
        case notGenerated
        case generating
        case noApprovedPolicy
        case menuDataUnavailable
        case offline(plan: CompletedDayPlan?, cachedAt: Date?)
        case error(String)
        case signedOut
    }

    enum TrendPhase: Equatable {
        case loading
        case result(BodyMassTrendResponse)
        case error(String)
        case signedOut
    }

    enum LedgerPhase: Equatable {
        case loading
        case result(DailyNutritionLedgerResponse)
        case error(String)
        case signedOut
    }

    enum NextMealPhase: Equatable {
        case loading
        case notGenerated
        case generating
        case result(NextMealRecommendationResponse)
        case error(String)
        case signedOut
    }

    private struct ConsumptionActionKey: Hashable {
        let itemId: UUID
        let state: ConsumptionState
    }

    private let backend: any BackendClient
    private let cache: any DayPlanCaching
    private let requestDate: @MainActor () -> Date
    private let timezoneIdentifier: () -> String
    private let eventId: () -> UUID
    private let onUnauthorized: @MainActor () -> Void
    private let onNextMealConsumptionRecorded: @MainActor () async -> Void

    private var subject: String?
    private var pendingEvents: [ConsumptionActionKey: UUID] = [:]
    private var pendingNextMealRequestId: UUID?
    private var pendingNextMealConsumptionEventId: UUID?
    private(set) var phase: Phase = .loading
    private(set) var trendPhase: TrendPhase = .loading
    private(set) var ledgerPhase: LedgerPhase = .loading
    private(set) var nextMealPhase: NextMealPhase = .loading
    private(set) var nextMealConsumption: NextMealConsumptionResponse?
    private(set) var nextMealConsumptionConfirmed = false
    private(set) var isNextMealConsumptionStatusResolved = false
    private(set) var isRecordingNextMealConsumption = false
    private(set) var nextMealConsumptionMessage: String?
    private(set) var consumptionByItem: [UUID: [ConsumptionEntryResponse]] = [:]
    private(set) var consumptionMessage: String?
    private(set) var isLoading = false
    private(set) var isRegenerating = false
    private(set) var regenerationMessage: String?

    init(
        backend: any BackendClient,
        cache: any DayPlanCaching,
        requestDate: @escaping @MainActor () -> Date = TodayViewModel.localTodayRequestDate,
        timezoneIdentifier: @escaping () -> String = { TimeZone.current.identifier },
        eventId: @escaping () -> UUID = UUID.init,
        onUnauthorized: @escaping @MainActor () -> Void = {},
        onNextMealConsumptionRecorded: @escaping @MainActor () async -> Void = {}
    ) {
        self.backend = backend
        self.cache = cache
        self.requestDate = requestDate
        self.timezoneIdentifier = timezoneIdentifier
        self.eventId = eventId
        self.onUnauthorized = onUnauthorized
        self.onNextMealConsumptionRecorded = onNextMealConsumptionRecorded
    }

    var canMutate: Bool {
        if case .completed = phase { return !isRegenerating }
        return false
    }

    var showsRegenerateAction: Bool {
        displayedPlan != nil
    }

    var canRegenerate: Bool {
        subject != nil && showsRegenerateAction && !isLoading && !isRegenerating
    }

    var displayedPlan: CompletedDayPlan? {
        switch phase {
        case .completed(let plan), .cached(let plan, _), .offline(let plan?, _):
            return plan
        default:
            return nil
        }
    }

    var canRecordNextMealConsumption: Bool {
        guard case .result(let response) = nextMealPhase else { return false }
        return response.status == .recommended
            && nextMealConsumption == nil
            && isNextMealConsumptionStatusResolved
            && nextMealConsumptionConfirmed
            && !isRecordingNextMealConsumption
    }

    func activate(subject: String) async {
        if self.subject != subject {
            self.subject = subject
            consumptionByItem = [:]
            consumptionMessage = nil
            regenerationMessage = nil
            pendingEvents = [:]
            phase = .loading
            trendPhase = .loading
            ledgerPhase = .loading
            nextMealPhase = .loading
            resetNextMealConsumptionState()
        }
        await load()
    }

    func refresh() async {
        guard subject != nil else { return }
        await load()
    }

    func generateNextMeal() async {
        guard let expectedSubject = subject else { return }
        let requestId = pendingNextMealRequestId ?? eventId()
        pendingNextMealRequestId = requestId
        nextMealPhase = .generating
        do {
            let response = try await backend.generateNextMeal(
                date: requestDate(),
                timezone: timezoneIdentifier(),
                clientRequestId: requestId
            )
            guard subject == expectedSubject else { return }
            pendingNextMealRequestId = nil
            await applyNextMealResponse(response, expectedSubject: expectedSubject)
        } catch BackendError.unauthorized {
            guard subject == expectedSubject else { return }
            pendingNextMealRequestId = nil
            transitionToSignedOut()
        } catch BackendError.rejected(_, _, _) {
            guard subject == expectedSubject else { return }
            pendingNextMealRequestId = nil
            nextMealPhase = .error("The next-meal request was rejected. Refresh and try again.")
        } catch {
            guard subject == expectedSubject else { return }
            nextMealPhase = .error("A next-meal recommendation could not be generated.")
        }
    }

    func setNextMealConsumptionConfirmed(_ confirmed: Bool) {
        guard case .result(let response) = nextMealPhase,
            response.status == .recommended,
            nextMealConsumption == nil,
            isNextMealConsumptionStatusResolved,
            !isRecordingNextMealConsumption
        else {
            nextMealConsumptionConfirmed = false
            return
        }
        nextMealConsumptionConfirmed = confirmed
    }

    @discardableResult
    func recordNextMealConsumption() async -> Bool {
        guard canRecordNextMealConsumption,
            case .result(let response) = nextMealPhase,
            let expectedSubject = subject
        else { return false }
        let event = pendingNextMealConsumptionEventId ?? eventId()
        pendingNextMealConsumptionEventId = event
        isRecordingNextMealConsumption = true
        nextMealConsumptionMessage = nil
        defer { isRecordingNextMealConsumption = false }
        do {
            let entry = try await backend.recordNextMealConsumption(
                recommendationId: response.recommendationId,
                clientEventId: event
            )
            guard subject == expectedSubject else { return false }
            pendingNextMealConsumptionEventId = nil
            nextMealConsumptionConfirmed = false
            nextMealConsumption = entry
            isNextMealConsumptionStatusResolved = true
            nextMealConsumptionMessage = "Recorded as eaten."
            await fetchLedger(for: requestDate())
            await onNextMealConsumptionRecorded()
            return true
        } catch BackendError.rejected(_, let code, _)
            where code == "next_meal_consumption_conflict"
        {
            guard subject == expectedSubject else { return false }
            pendingNextMealConsumptionEventId = nil
            await fetchNextMealConsumption(
                recommendationId: response.recommendationId,
                expectedSubject: expectedSubject
            )
            if nextMealConsumption == nil {
                nextMealConsumptionMessage = "That consumption event conflicts with history."
            }
        } catch BackendError.rejected(_, let code, _)
            where code == "next_meal_not_consumable" || code == "next_meal_not_found"
        {
            guard subject == expectedSubject else { return false }
            pendingNextMealConsumptionEventId = nil
            nextMealConsumptionConfirmed = false
            nextMealConsumptionMessage = "This recommendation cannot be recorded as eaten."
        } catch BackendError.unauthorized {
            guard subject == expectedSubject else { return false }
            pendingNextMealConsumptionEventId = nil
            transitionToSignedOut()
        } catch {
            guard subject == expectedSubject else { return false }
            nextMealConsumptionMessage = "The meal was not recorded. Try again when online."
        }
        return false
    }

    func generate() async {
        guard subject != nil, phase == .notGenerated else { return }
        phase = .generating
        do {
            _ = try await backend.generateDayPlan(
                date: requestDate(),
                timezone: timezoneIdentifier()
            )
            await fetchCanonicalPlan()
        } catch BackendError.rejected(_, let code, _) where code == "concurrent_generation" {
            // One canonical refresh only; no polling loop.
            await fetchCanonicalPlan()
        } catch BackendError.rejected(_, let code, _)
            where code == "no_approved_target_policy"
        {
            phase = .noApprovedPolicy
        } catch BackendError.retryableHTTP(_, let code, _)
            where code == "menu_data_unavailable"
        {
            phase = .menuDataUnavailable
        } catch BackendError.unauthorized {
            transitionToSignedOut()
        } catch {
            phase = .error("The plan could not be generated. Please try again.")
        }
    }

    func regenerate() async {
        guard canRegenerate, let expectedSubject = subject else { return }
        let previousPhase = phase
        isRegenerating = true
        regenerationMessage = nil
        defer { isRegenerating = false }

        do {
            _ = try await backend.generateDayPlan(
                date: requestDate(),
                timezone: timezoneIdentifier()
            )
            await fetchRegeneratedCanonicalPlan(
                expectedSubject: expectedSubject,
                previousPhase: previousPhase
            )
        } catch BackendError.rejected(_, let code, _) where code == "concurrent_generation" {
            // The server has already resolved the idempotent write race. Read
            // the one canonical result without polling or issuing another POST.
            await fetchRegeneratedCanonicalPlan(
                expectedSubject: expectedSubject,
                previousPhase: previousPhase
            )
        } catch BackendError.rejected(_, let code, _)
            where code == "no_approved_target_policy"
        {
            restoreAfterFailedRegeneration(
                expectedSubject: expectedSubject,
                previousPhase: previousPhase,
                message: "An approved target policy is required. Your existing plan was kept."
            )
        } catch BackendError.retryableHTTP(_, let code, _)
            where code == "menu_data_unavailable"
        {
            restoreAfterFailedRegeneration(
                expectedSubject: expectedSubject,
                previousPhase: previousPhase,
                message: "Today’s validated menu is unavailable. Your existing plan was kept."
            )
        } catch BackendError.unauthorized {
            transitionToSignedOut()
        } catch {
            restoreAfterFailedRegeneration(
                expectedSubject: expectedSubject,
                previousPhase: previousPhase,
                message: "The plan could not be regenerated. Your existing plan was kept. "
                    + "Try again when online."
            )
        }
    }

    func recordConsumption(item: PlanItemReference, state: ConsumptionState) async {
        guard canMutate, let plan = displayedPlan else { return }
        guard plan.itemReference(
            slotIndex: item.slotIndex,
            rank: item.rank,
            candidateId: item.candidateId
        )?.itemId == item.itemId else {
            consumptionMessage = "This plan item is unavailable. Refresh the plan."
            return
        }

        let key = ConsumptionActionKey(itemId: item.itemId, state: state)
        let clientEventId = pendingEvents[key] ?? eventId()
        pendingEvents[key] = clientEventId
        consumptionMessage = nil
        do {
            let entry = try await backend.recordConsumption(
                runId: plan.runId,
                planVersionId: plan.versionId,
                itemId: item.itemId,
                state: state,
                clientEventId: clientEventId
            )
            merge(entry)
            pendingEvents[key] = nil
            await fetchLedger(for: requestDate())
        } catch BackendError.rejected(_, let code, _) where code == "consumption_conflict" {
            pendingEvents[key] = nil
            consumptionMessage = "That event conflicts with an existing record."
            await reloadConsumption(for: plan.runId)
        } catch BackendError.rejected(_, let code, _)
            where code == "consumption_target_not_found"
        {
            pendingEvents[key] = nil
            consumptionMessage = "This plan is stale or inconsistent. Refresh before recording."
        } catch BackendError.unauthorized {
            pendingEvents[key] = nil
            transitionToSignedOut()
        } catch {
            // Keep the event UUID so an explicit retry of this logical action
            // receives the backend's immutable replay semantics.
            consumptionMessage = "The event was not recorded. Try again when online."
        }
    }

    func latestConsumption(for itemId: UUID) -> ConsumptionEntryResponse? {
        consumptionByItem[itemId]?.last
    }

    func resetForSignOut() {
        subject = nil
        phase = .signedOut
        trendPhase = .signedOut
        ledgerPhase = .signedOut
        nextMealPhase = .signedOut
        consumptionByItem = [:]
        consumptionMessage = nil
        regenerationMessage = nil
        isRegenerating = false
        pendingEvents = [:]
        pendingNextMealRequestId = nil
        pendingNextMealConsumptionEventId = nil
        nextMealConsumption = nil
        isNextMealConsumptionStatusResolved = false
        nextMealConsumptionConfirmed = false
        isRecordingNextMealConsumption = false
        nextMealConsumptionMessage = nil
    }

    func clearForSignOut() {
        cache.clearAll()
        resetForSignOut()
    }

    private func load() async {
        guard !isLoading, let subject else { return }
        isLoading = true
        defer { isLoading = false }
        let date = requestDate()
        let expectedDay = WireDay.string(from: date)
        let cached = matchingCachedPlan(subject: subject, expectedDay: expectedDay)
        if let cached {
            phase = .cached(plan: cached.plan, cachedAt: cached.cachedAt)
        } else {
            phase = .loading
        }
        trendPhase = .loading
        ledgerPhase = .loading
        nextMealPhase = .loading
        await fetchCanonicalPlan(fallback: cached)
        guard self.subject != nil else { return }
        async let trend: Void = fetchTrend(asOfDate: date)
        async let ledger: Void = fetchLedger(for: date)
        async let nextMeal: Void = fetchLatestNextMeal(expectedSubject: subject, for: date)
        _ = await (trend, ledger, nextMeal)
    }

    private func fetchLatestNextMeal(expectedSubject: String, for date: Date) async {
        do {
            let response = try await backend.fetchLatestNextMeal()
            guard subject == expectedSubject else { return }
            if response.localDate == WireDay.string(from: date)
                && response.timezone == timezoneIdentifier()
            {
                await applyNextMealResponse(response, expectedSubject: expectedSubject)
            } else {
                nextMealPhase = .notGenerated
                resetNextMealConsumptionState()
            }
        } catch BackendError.rejected(let status, _, _) where status == 404 {
            guard subject == expectedSubject else { return }
            nextMealPhase = .notGenerated
            resetNextMealConsumptionState()
        } catch BackendError.unauthorized {
            guard subject == expectedSubject else { return }
            transitionToSignedOut()
        } catch {
            guard subject == expectedSubject else { return }
            nextMealPhase = .error("The latest next-meal recommendation is unavailable.")
        }
    }

    private func applyNextMealResponse(
        _ response: NextMealRecommendationResponse,
        expectedSubject: String
    ) async {
        guard subject == expectedSubject else { return }
        let previousId: UUID? = {
            if case .result(let previous) = nextMealPhase {
                return previous.recommendationId
            }
            return nil
        }()
        if previousId != response.recommendationId {
            resetNextMealConsumptionState()
        }
        nextMealPhase = .result(response)
        if response.status == .recommended {
            await fetchNextMealConsumption(
                recommendationId: response.recommendationId,
                expectedSubject: expectedSubject
            )
        } else {
            resetNextMealConsumptionState()
        }
    }

    private func fetchNextMealConsumption(
        recommendationId: UUID,
        expectedSubject: String
    ) async {
        do {
            let entry = try await backend.fetchNextMealConsumption(
                recommendationId: recommendationId
            )
            guard subject == expectedSubject else { return }
            nextMealConsumption = entry
            isNextMealConsumptionStatusResolved = true
            nextMealConsumptionConfirmed = false
            pendingNextMealConsumptionEventId = nil
        } catch BackendError.rejected(let status, _, _) where status == 404 {
            guard subject == expectedSubject else { return }
            nextMealConsumption = nil
            isNextMealConsumptionStatusResolved = true
        } catch BackendError.unauthorized {
            guard subject == expectedSubject else { return }
            transitionToSignedOut()
        } catch {
            guard subject == expectedSubject else { return }
            nextMealConsumptionMessage = "Consumption status is temporarily unavailable."
        }
    }

    private func resetNextMealConsumptionState() {
        pendingNextMealConsumptionEventId = nil
        nextMealConsumption = nil
        isNextMealConsumptionStatusResolved = false
        nextMealConsumptionConfirmed = false
        isRecordingNextMealConsumption = false
        nextMealConsumptionMessage = nil
    }

    private func fetchTrend(asOfDate: Date) async {
        do {
            let trend = try await backend.fetchBodyMassTrend(
                asOfDate: asOfDate,
                timezone: timezoneIdentifier()
            )
            trendPhase = .result(trend)
        } catch BackendError.unauthorized {
            transitionToSignedOut()
        } catch {
            trendPhase = .error("Weight trend is temporarily unavailable.")
        }
    }

    private func fetchLedger(for date: Date) async {
        do {
            let ledger = try await backend.fetchDailyNutritionLedger(
                date: date,
                timezone: timezoneIdentifier()
            )
            guard subject != nil else { return }
            ledgerPhase = .result(ledger)
        } catch BackendError.unauthorized {
            transitionToSignedOut()
        } catch {
            guard subject != nil else { return }
            ledgerPhase = .error("Today’s nutrition totals are temporarily unavailable.")
        }
    }

    private func fetchCanonicalPlan(
        fallback: (plan: CompletedDayPlan, cachedAt: Date)? = nil
    ) async {
        guard let subject else {
            transitionToSignedOut()
            return
        }
        do {
            let response = try await backend.fetchDayPlan(date: requestDate())
            switch response {
            case .completed(let plan):
                guard plan.planItems != nil else {
                    phase = .error("The plan response is incomplete. Please try again.")
                    return
                }
                regenerationMessage = nil
                _ = cache.save(.completed(plan), forSubject: subject)
                phase = .completed(plan)
                await reloadConsumption(for: plan.runId)
            case .noPlan(let value):
                regenerationMessage = nil
                consumptionByItem = [:]
                phase = .noPlan(value)
            case .notGenerated:
                regenerationMessage = nil
                consumptionByItem = [:]
                phase = .notGenerated
            }
        } catch BackendError.unauthorized {
            transitionToSignedOut()
        } catch {
            let cached = fallback ?? matchingCachedPlan(
                subject: subject,
                expectedDay: WireDay.string(from: requestDate())
            )
            phase = .offline(plan: cached?.plan, cachedAt: cached?.cachedAt)
        }
    }

    private func fetchRegeneratedCanonicalPlan(
        expectedSubject: String,
        previousPhase: Phase
    ) async {
        guard subject == expectedSubject else { return }
        do {
            let response = try await backend.fetchDayPlan(date: requestDate())
            guard subject == expectedSubject else { return }
            switch response {
            case .completed(let plan):
                guard plan.planItems != nil else {
                    restoreAfterFailedRegeneration(
                        expectedSubject: expectedSubject,
                        previousPhase: previousPhase,
                        message: "The regenerated plan response was incomplete. "
                            + "Your existing plan was kept."
                    )
                    return
                }
                _ = cache.save(.completed(plan), forSubject: expectedSubject)
                regenerationMessage = nil
                consumptionMessage = nil
                phase = .completed(plan)
                await reloadConsumption(for: plan.runId)
            case .noPlan(let value):
                cache.clear(forSubject: expectedSubject)
                regenerationMessage = nil
                consumptionByItem = [:]
                phase = .noPlan(value)
            case .notGenerated:
                restoreAfterFailedRegeneration(
                    expectedSubject: expectedSubject,
                    previousPhase: previousPhase,
                    message: "The server did not return a regenerated plan. "
                        + "Your existing plan was kept."
                )
            }
        } catch BackendError.unauthorized {
            transitionToSignedOut()
        } catch {
            restoreAfterFailedRegeneration(
                expectedSubject: expectedSubject,
                previousPhase: previousPhase,
                message: "The regenerated plan could not be refreshed. "
                    + "Your existing plan was kept. Try again when online."
            )
        }
    }

    private func restoreAfterFailedRegeneration(
        expectedSubject: String,
        previousPhase: Phase,
        message: String
    ) {
        guard subject == expectedSubject else { return }
        phase = previousPhase
        regenerationMessage = message
    }

    private func reloadConsumption(for runId: UUID) async {
        do {
            let response = try await backend.listConsumption(runId: runId)
            consumptionByItem = Dictionary(grouping: response.entries, by: \.itemId)
        } catch BackendError.unauthorized {
            transitionToSignedOut()
        } catch {
            consumptionMessage = "Consumption history is temporarily unavailable."
        }
    }

    private func merge(_ entry: ConsumptionEntryResponse) {
        var entries = consumptionByItem[entry.itemId] ?? []
        if let index = entries.firstIndex(where: { $0.entryId == entry.entryId }) {
            entries[index] = entry
        } else {
            entries.append(entry)
        }
        entries.sort { ($0.recordedAt, $0.entryId) < ($1.recordedAt, $1.entryId) }
        consumptionByItem[entry.itemId] = entries
    }

    private func matchingCachedPlan(
        subject: String,
        expectedDay: String
    ) -> (plan: CompletedDayPlan, cachedAt: Date)? {
        guard let cached = cache.load(forSubject: subject),
              cached.planDate == expectedDay,
              case .completed(let plan) = cached.response
        else {
            return nil
        }
        return (plan, cached.cachedAt)
    }

    private func transitionToSignedOut() {
        cache.clearAll()
        resetForSignOut()
        onUnauthorized()
    }

    private static func localTodayRequestDate() -> Date {
        let local = Calendar.current.dateComponents([.year, .month, .day], from: Date())
        var utc = Calendar(identifier: .gregorian)
        utc.timeZone = TimeZone(secondsFromGMT: 0)!
        return utc.date(from: local) ?? Date()
    }
}
