import Foundation
import Observation

@MainActor
@Observable
final class ProgressViewModel {
    enum Phase: Equatable {
        case idle
        case loading
        case result(OwnerProgressResponse)
        case error(String)
        case signedOut
    }

    enum BodyWindow: Int, CaseIterable, Identifiable {
        case days28 = 28
        case days90 = 90

        var id: Int { rawValue }
        var label: String { "\(rawValue) days" }
    }

    enum NutritionWindow: Int, CaseIterable, Identifiable {
        case days7 = 7
        case days28 = 28

        var id: Int { rawValue }
        var label: String { "\(rawValue) days" }
    }

    struct BodyChartPoint: Identifiable, Equatable {
        let localDate: String
        let date: Date
        let medianKg: String
        let displayKg: Double
        let observationCount: Int

        var id: String { localDate }
    }

    private let backend: any BackendClient
    private let requestDate: () -> Date
    private let timezoneIdentifier: () -> String
    private let onUnauthorized: @MainActor () -> Void
    private var subject: String?

    private(set) var phase: Phase = .idle
    private(set) var isLoading = false
    var bodyWindow: BodyWindow = .days28
    var nutritionWindow: NutritionWindow = .days7

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

    var selectedSummary: ProgressNutritionSummary? {
        guard case .result(let response) = phase else { return nil }
        switch nutritionWindow {
        case .days7: return response.nutrition.summary7d
        case .days28: return response.nutrition.summary28d
        }
    }

    var selectedTargetChanges: [ProgressTargetChange] {
        guard case .result(let response) = phase,
              let start = selectedSummary?.startDate
        else { return [] }
        return response.nutrition.targetChanges.filter { $0.effectiveLocalDate >= start }
    }

    var selectedBodyPoints: [BodyChartPoint] {
        guard case .result(let response) = phase,
              let asOf = Self.localDate(response.asOfDate)
        else { return [] }
        let calendar = Self.calendar
        guard let start = calendar.date(
            byAdding: .day, value: -(bodyWindow.rawValue - 1), to: asOf
        ) else { return [] }
        return response.body.dailyMedians.compactMap { point in
            guard let date = Self.localDate(point.localDate),
                  date >= start,
                  date <= asOf,
                  let value = Double(point.medianKg),
                  value.isFinite
            else { return nil }
            return BodyChartPoint(
                localDate: point.localDate,
                date: date,
                medianKg: point.medianKg,
                displayKg: value,
                observationCount: point.observationCount
            )
        }
        .sorted { ($0.date, $0.localDate) < ($1.date, $1.localDate) }
    }

    /// Each line segment contains only consecutive local days. Point marks are
    /// still shown for every represented day, so missing measurements stay gaps.
    var bodyChartSegments: [[BodyChartPoint]] {
        selectedBodyPoints.reduce(into: [[BodyChartPoint]]()) { segments, point in
            guard let previous = segments.last?.last else {
                segments.append([point])
                return
            }
            let next = Self.calendar.date(byAdding: .day, value: 1, to: previous.date)
            if next == point.date {
                segments[segments.count - 1].append(point)
            } else {
                segments.append([point])
            }
        }
    }

    private func load() async {
        guard let requestedSubject = subject, !isLoading else { return }
        isLoading = true
        phase = .loading
        defer { isLoading = false }
        do {
            let response = try await backend.fetchProgress(
                asOfDate: requestDate(),
                timezone: timezoneIdentifier()
            )
            guard subject == requestedSubject else { return }
            phase = .result(response)
        } catch BackendError.unauthorized {
            guard subject == requestedSubject else { return }
            resetForSignOut()
            onUnauthorized()
        } catch {
            guard subject == requestedSubject else { return }
            phase = .error("Progress is temporarily unavailable.")
        }
    }

    private static var calendar: Calendar {
        var value = Calendar(identifier: .gregorian)
        value.timeZone = TimeZone(secondsFromGMT: 0)!
        return value
    }

    private static func localDate(_ raw: String) -> Date? {
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.isLenient = false
        return formatter.date(from: raw)
    }
}
