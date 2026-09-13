import Foundation
import Observation

#if canImport(UserNotifications)
    import UserNotifications
#endif

enum MealGuidanceKind: String, CaseIterable, Equatable, Sendable {
    case lunch, dinner

    var notificationIdentifier: String { "nytr.meal-guidance.\(rawValue)" }
    var deepLink: URL { URL(string: "nutritionhealthcompanion://food/\(rawValue)")! }
    var title: String { self == .lunch ? "Lunch is ready" : "Dinner recommendation available" }
    var body: String {
        self == .lunch
            ? "Nytr has a lunch recommendation based on your current recorded intake."
            : "Open Nytr to review your dinner options."
    }
}

enum MealNotificationPermission: Equatable, Sendable {
    case notDetermined, denied, authorized
}

struct ScheduledMealNotification: Equatable, Sendable {
    let identifier: String
    let title: String
    let body: String
    let deepLink: URL
    let fireDate: Date?
    let timeZoneIdentifier: String?
    let delay: TimeInterval?
}

@MainActor
protocol MealNotificationScheduling: AnyObject {
    func permission() async -> MealNotificationPermission
    func requestPermission() async -> MealNotificationPermission
    func replace(_ notification: ScheduledMealNotification) async throws
    func cancel(identifiers: [String])
}

#if canImport(UserNotifications)
    @MainActor
    final class AppleMealNotificationScheduler: MealNotificationScheduling {
        private let center: UNUserNotificationCenter

        init(center: UNUserNotificationCenter = .current()) {
            self.center = center
        }

        func permission() async -> MealNotificationPermission {
            map(await center.notificationSettings().authorizationStatus)
        }

        func requestPermission() async -> MealNotificationPermission {
            do {
                _ = try await center.requestAuthorization(options: [.alert, .sound])
            } catch {
                return .denied
            }
            return await permission()
        }

        func replace(_ notification: ScheduledMealNotification) async throws {
            center.removePendingNotificationRequests(withIdentifiers: [notification.identifier])
            let content = UNMutableNotificationContent()
            content.title = notification.title
            content.body = notification.body
            content.sound = .default
            content.userInfo = ["nytr_deep_link": notification.deepLink.absoluteString]
            let trigger: UNNotificationTrigger
            if let delay = notification.delay {
                trigger = UNTimeIntervalNotificationTrigger(timeInterval: max(delay, 1), repeats: false)
            } else if let fireDate = notification.fireDate {
                var calendar = Calendar(identifier: .gregorian)
                calendar.timeZone = notification.timeZoneIdentifier.flatMap { TimeZone(identifier: $0) }
                    ?? .current
                var components = calendar.dateComponents(
                    [.year, .month, .day, .hour, .minute, .second],
                    from: fireDate
                )
                components.timeZone = calendar.timeZone
                trigger = UNCalendarNotificationTrigger(dateMatching: components, repeats: false)
            } else {
                throw CocoaError(.validationMissingMandatoryProperty)
            }
            try await center.add(
                UNNotificationRequest(
                    identifier: notification.identifier,
                    content: content,
                    trigger: trigger
                )
            )
        }

        func cancel(identifiers: [String]) {
            center.removePendingNotificationRequests(withIdentifiers: identifiers)
        }

        private func map(_ status: UNAuthorizationStatus) -> MealNotificationPermission {
            switch status {
            case .authorized, .provisional, .ephemeral: .authorized
            case .denied: .denied
            case .notDetermined: .notDetermined
            @unknown default: .denied
            }
        }
    }

    final class NytrNotificationDelegate: NSObject, UNUserNotificationCenterDelegate,
        @unchecked Sendable
    {
        static let shared = NytrNotificationDelegate()
        private let lock = NSLock()
        private var pendingDeepLink: URL?

        func install() {
            UNUserNotificationCenter.current().delegate = self
        }

        func userNotificationCenter(
            _ center: UNUserNotificationCenter,
            willPresent notification: UNNotification
        ) async -> UNNotificationPresentationOptions {
            [.banner, .list, .sound]
        }

        func userNotificationCenter(
            _ center: UNUserNotificationCenter,
            didReceive response: UNNotificationResponse
        ) async {
            guard
                let value = response.notification.request.content.userInfo["nytr_deep_link"] as? String,
                let url = URL(string: value)
            else { return }
            lock.lock()
            pendingDeepLink = url
            lock.unlock()
            await MainActor.run {
                NotificationCenter.default.post(name: .nytrMealNotificationOpened, object: url)
            }
        }

        func takePendingDeepLink() -> URL? {
            lock.lock()
            defer { lock.unlock() }
            let value = pendingDeepLink
            pendingDeepLink = nil
            return value
        }
    }
#else
    @MainActor
    final class AppleMealNotificationScheduler: MealNotificationScheduling {
        func permission() async -> MealNotificationPermission { .denied }
        func requestPermission() async -> MealNotificationPermission { .denied }
        func replace(_ notification: ScheduledMealNotification) async throws {}
        func cancel(identifiers: [String]) {}
    }
#endif

extension Notification.Name {
    static let nytrMealNotificationOpened = Notification.Name("nytrMealNotificationOpened")
}

enum MealGuidanceDeepLink {
    static func destination(from url: URL) -> MealGuidanceKind? {
        guard url.scheme == "nutritionhealthcompanion", url.host == "food" else { return nil }
        return MealGuidanceKind(rawValue: url.path.trimmingCharacters(in: CharacterSet(charactersIn: "/")))
    }
}

@MainActor
@Observable
final class MealGuidanceNotificationViewModel {
    private enum Key {
        static let master = "nytr.notifications.mealGuidance"
        static let lunch = "nytr.notifications.lunch"
        static let dinner = "nytr.notifications.dinner"
    }

    private let scheduler: any MealNotificationScheduling
    private let defaults: UserDefaults
    private let now: () -> Date
    private(set) var permission: MealNotificationPermission = .notDetermined
    private(set) var message: String?
    private(set) var masterEnabled: Bool
    private(set) var lunchEnabled: Bool
    private(set) var dinnerEnabled: Bool
    private var latestPlan: CompletedDayPlan?
    private var latestConsumption: [UUID: [ConsumptionEntryResponse]] = [:]
    private var latestTimezone = TimeZone.current.identifier

    init(
        scheduler: (any MealNotificationScheduling)? = nil,
        defaults: UserDefaults = .standard,
        now: @escaping () -> Date = Date.init
    ) {
        self.scheduler = scheduler ?? AppleMealNotificationScheduler()
        self.defaults = defaults
        self.now = now
        masterEnabled = defaults.bool(forKey: Key.master)
        lunchEnabled = defaults.object(forKey: Key.lunch) as? Bool ?? true
        dinnerEnabled = defaults.object(forKey: Key.dinner) as? Bool ?? true
    }

    func refreshPermission() async {
        permission = await scheduler.permission()
        if permission == .authorized {
            await reconcileCurrentPlan()
        } else {
            cancelAll()
        }
    }

    func setMasterEnabled(_ enabled: Bool) async {
        masterEnabled = enabled
        defaults.set(enabled, forKey: Key.master)
        message = nil
        guard enabled else {
            cancelMealGuidance()
            return
        }
        permission = await scheduler.permission()
        if permission == .notDetermined {
            permission = await scheduler.requestPermission()
        }
        guard permission == .authorized else {
            cancelAll()
            message = "Notifications are disabled in iOS Settings."
            return
        }
        await reconcileCurrentPlan()
    }

    func setMeal(_ kind: MealGuidanceKind, enabled: Bool) async {
        switch kind {
        case .lunch:
            lunchEnabled = enabled
            defaults.set(enabled, forKey: Key.lunch)
        case .dinner:
            dinnerEnabled = enabled
            defaults.set(enabled, forKey: Key.dinner)
        }
        if !enabled { scheduler.cancel(identifiers: [kind.notificationIdentifier]) }
        else { await reconcileCurrentPlan() }
    }

    func reconcile(
        plan: CompletedDayPlan?,
        consumption: [UUID: [ConsumptionEntryResponse]],
        timezone: String
    ) async {
        latestPlan = plan
        latestConsumption = consumption
        latestTimezone = timezone
        if masterEnabled { permission = await scheduler.permission() }
        await reconcileCurrentPlan()
    }

    func sendTestNotification() async {
        permission = await scheduler.permission()
        guard permission == .authorized else {
            message = "Allow notifications in iOS Settings before sending a test."
            return
        }
        do {
            try await scheduler.replace(
                ScheduledMealNotification(
                    identifier: "nytr.notification-test",
                    title: "Nytr test",
                    body: "Notifications are working.",
                    deepLink: MealGuidanceKind.lunch.deepLink,
                    fireDate: nil,
                    timeZoneIdentifier: nil,
                    delay: 5
                )
            )
            message = "Test notification scheduled for about 5 seconds from now."
        } catch {
            message = "The test notification could not be scheduled."
        }
    }

    func resetForSignOut() {
        latestPlan = nil
        latestConsumption = [:]
        cancelAll()
        message = nil
    }

    private func reconcileCurrentPlan() async {
        guard masterEnabled, permission == .authorized, let plan = latestPlan else {
            cancelMealGuidance()
            return
        }
        for kind in MealGuidanceKind.allCases {
            guard isEnabled(kind) else {
                scheduler.cancel(identifiers: [kind.notificationIdentifier])
                continue
            }
            guard let notification = notification(for: kind, plan: plan) else {
                scheduler.cancel(identifiers: [kind.notificationIdentifier])
                continue
            }
            do {
                try await scheduler.replace(notification)
            } catch {
                message = "A meal reminder could not be scheduled. Nytr still works normally."
            }
        }
    }

    private func notification(
        for kind: MealGuidanceKind,
        plan: CompletedDayPlan
    ) -> ScheduledMealNotification? {
        guard let timeZone = TimeZone(identifier: latestTimezone) else { return nil }
        let current = now()
        let currentDay = WireDay.string(
            from: WireDay.localRequestDate(from: current, timeZone: timeZone)
        )
        guard plan.planDate == currentDay else {
            return nil
        }
        let matching = plan.plan.slots.enumerated().first { _, slot in
            kind == .lunch
                ? ["lunch", "post_workout_lunch"].contains(slot.context)
                : slot.context == "dinner"
        }
        guard let (slotIndex, slot) = matching,
            let primary = plan.presentation(forSlotAt: slotIndex)?.primary,
            let reference = primary.itemReference,
            latestConsumption[reference.itemId]?.last == nil,
            slot.status == "ok",
            !slot.candidates.isEmpty,
            let start = localDate(plan.planDate, time: slot.window.first, timeZone: timeZone),
            let end = localDate(plan.planDate, time: slot.window.dropFirst().first, timeZone: timeZone),
            current < end
        else { return nil }
        let fireDate = max(start, current.addingTimeInterval(60))
        guard fireDate <= end else { return nil }
        return ScheduledMealNotification(
            identifier: kind.notificationIdentifier,
            title: kind.title,
            body: kind.body,
            deepLink: kind.deepLink,
            fireDate: fireDate,
            timeZoneIdentifier: latestTimezone,
            delay: nil
        )
    }

    private func localDate(_ day: String, time: String?, timeZone: TimeZone) -> Date? {
        guard let time else { return nil }
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return formatter.date(from: "\(day) \(time)")
    }

    private func isEnabled(_ kind: MealGuidanceKind) -> Bool {
        kind == .lunch ? lunchEnabled : dinnerEnabled
    }

    private func cancelAll() {
        scheduler.cancel(identifiers: ["nytr.notification-test"])
        cancelMealGuidance()
    }

    private func cancelMealGuidance() {
        scheduler.cancel(
            identifiers: MealGuidanceKind.allCases.map(\.notificationIdentifier)
        )
    }
}
