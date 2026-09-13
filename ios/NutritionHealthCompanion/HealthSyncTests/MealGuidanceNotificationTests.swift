import XCTest

@testable import NutritionHealthCompanion

@MainActor
final class MealGuidanceNotificationTests: XCTestCase {
    private final class Scheduler: MealNotificationScheduling {
        var currentPermission: MealNotificationPermission = .authorized
        var requestedPermission: MealNotificationPermission = .authorized
        var requestCount = 0
        var pending: [String: ScheduledMealNotification] = [:]
        var cancelled: [String] = []

        func permission() async -> MealNotificationPermission { currentPermission }
        func requestPermission() async -> MealNotificationPermission {
            requestCount += 1
            currentPermission = requestedPermission
            return requestedPermission
        }
        func replace(_ notification: ScheduledMealNotification) async throws {
            pending[notification.identifier] = notification
        }
        func cancel(identifiers: [String]) {
            identifiers.forEach { pending[$0] = nil }
            cancelled.append(contentsOf: identifiers)
        }
    }

    private let now = ISO8601DateFormatter().date(from: "2026-08-21T15:00:00Z")!

    func testMasterDefaultsOffAndNotDeterminedPermissionIsRequestedOnce() async {
        let scheduler = Scheduler()
        scheduler.currentPermission = .notDetermined
        let viewModel = makeViewModel(scheduler)
        XCTAssertFalse(viewModel.masterEnabled)
        XCTAssertTrue(viewModel.lunchEnabled)
        XCTAssertTrue(viewModel.dinnerEnabled)

        await viewModel.setMasterEnabled(true)
        XCTAssertEqual(scheduler.requestCount, 1)
        XCTAssertEqual(viewModel.permission, .authorized)

        await viewModel.setMasterEnabled(false)
        XCTAssertTrue(scheduler.cancelled.contains(MealGuidanceKind.lunch.notificationIdentifier))
        XCTAssertTrue(scheduler.cancelled.contains(MealGuidanceKind.dinner.notificationIdentifier))
    }

    func testDeniedPermissionDoesNotNagAndShowsRecoveryState() async {
        let scheduler = Scheduler()
        scheduler.currentPermission = .denied
        let viewModel = makeViewModel(scheduler)
        await viewModel.setMasterEnabled(true)
        await viewModel.setMasterEnabled(true)
        XCTAssertEqual(scheduler.requestCount, 0)
        XCTAssertEqual(viewModel.permission, .denied)
        XCTAssertTrue(viewModel.message?.contains("iOS Settings") == true)
    }

    func testAuthorizedPlanSchedulesStablePrivateLunchAndDinnerReminders() async throws {
        let scheduler = Scheduler()
        let viewModel = makeViewModel(scheduler)
        await viewModel.setMasterEnabled(true)
        await viewModel.reconcile(plan: try plan(), consumption: [:], timezone: "America/New_York")
        await viewModel.reconcile(plan: try plan(), consumption: [:], timezone: "America/New_York")

        XCTAssertEqual(Set(scheduler.pending.keys), Set(MealGuidanceKind.allCases.map(\.notificationIdentifier)))
        guard let lunch = scheduler.pending[MealGuidanceKind.lunch.notificationIdentifier] else {
            return XCTFail("Expected lunch reminder")
        }
        XCTAssertEqual(lunch.deepLink, MealGuidanceKind.lunch.deepLink)
        XCTAssertFalse(lunch.title.contains("500"))
        XCTAssertFalse(lunch.body.contains("120"))
        XCTAssertFalse(lunch.body.localizedCaseInsensitiveContains("protein"))
    }

    func testMealToggleStalePlanAndRecordedStatusSuppressScheduling() async throws {
        let scheduler = Scheduler()
        let viewModel = makeViewModel(scheduler)
        await viewModel.setMasterEnabled(true)
        await viewModel.setMeal(.lunch, enabled: false)
        await viewModel.reconcile(plan: try plan(), consumption: [:], timezone: "America/New_York")
        XCTAssertNil(scheduler.pending[MealGuidanceKind.lunch.notificationIdentifier])
        XCTAssertNotNil(scheduler.pending[MealGuidanceKind.dinner.notificationIdentifier])

        let current = try plan()
        guard let lunchItem = current.planItems?.first else {
            return XCTFail("Expected lunch plan item")
        }
        await viewModel.setMeal(.lunch, enabled: true)
        for state in ConsumptionState.allCases {
            let entry = ConsumptionEntryResponse(
                entryId: UUID(), planRunId: current.runId, planVersionId: current.versionId,
                itemId: lunchItem.itemId, state: state, recordedAt: now, clientEventId: UUID()
            )
            await viewModel.reconcile(
                plan: current, consumption: [lunchItem.itemId: [entry]], timezone: "America/New_York"
            )
            XCTAssertNil(
                scheduler.pending[MealGuidanceKind.lunch.notificationIdentifier],
                "\(state) should suppress the lunch reminder"
            )
        }

        let stale = try plan(day: "2026-08-20")
        await viewModel.reconcile(plan: stale, consumption: [:], timezone: "America/New_York")
        XCTAssertTrue(scheduler.pending.isEmpty)
    }

    func testTestNotificationDeepLinksAndSignOutCancelsPending() async {
        let scheduler = Scheduler()
        let viewModel = makeViewModel(scheduler)
        await viewModel.refreshPermission()
        await viewModel.sendTestNotification()
        let test = scheduler.pending["nytr.notification-test"]
        XCTAssertEqual(test?.title, "Nytr test")
        XCTAssertEqual(test?.body, "Notifications are working.")
        XCTAssertEqual(test?.delay, 5)
        await viewModel.reconcile(plan: nil, consumption: [:], timezone: "America/New_York")
        XCTAssertNotNil(scheduler.pending["nytr.notification-test"])
        XCTAssertEqual(
            MealGuidanceDeepLink.destination(from: MealGuidanceKind.lunch.deepLink), .lunch
        )
        XCTAssertEqual(
            MealGuidanceDeepLink.destination(from: MealGuidanceKind.dinner.deepLink), .dinner
        )
        XCTAssertNil(MealGuidanceDeepLink.destination(from: URL(string: "https://example.com")!))
        viewModel.resetForSignOut()
        XCTAssertTrue(scheduler.pending.isEmpty)
    }

    private func makeViewModel(_ scheduler: Scheduler) -> MealGuidanceNotificationViewModel {
        let suite = "MealGuidanceNotificationTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)
        return MealGuidanceNotificationViewModel(
            scheduler: scheduler,
            defaults: defaults,
            now: { self.now }
        )
    }

    private func plan(day: String = "2026-08-21") throws -> CompletedDayPlan {
        let data = Data(
            """
            {"state":"completed","requested_date":"\(day)","plan_date":"\(day)",
             "plan_sha256":"sha","inputs_fingerprint":"fingerprint",
             "run_id":"10000000-0000-0000-0000-000000000001",
             "version_id":"20000000-0000-0000-0000-000000000001",
             "plan":{"artifact_kind":"daily_plan","plan_date":"\(day)",
              "policy_versions":{"engine":"e","planner":"p","schedule":"s","target":"t"},
              "menu_snapshot_sha256":"menu","status":"ok","slots":[
               {"context":"lunch","menu_period":"Lunch","status":"ok",
                "window":["12:00:00","13:00:00"],"failure_reasons":[],"rejection_counts":{},
                "rejection_details":[],"candidates":[\(candidate(id: "lunch"))]},
               {"context":"dinner","menu_period":"Dinner","status":"ok",
                "window":["17:00:00","19:00:00"],"failure_reasons":[],"rejection_counts":{},
                "rejection_details":[],"candidates":[\(candidate(id: "dinner"))]}]},
             "plan_items":[
              {"item_id":"50000000-0000-0000-0000-000000000001","slot_index":0,"rank":1,"candidate_id":"lunch"},
              {"item_id":"50000000-0000-0000-0000-000000000002","slot_index":1,"rank":1,"candidate_id":"dinner"}]}
            """.utf8
        )
        guard case .completed(let plan) = try JSONDecoder().decode(DayPlanResponse.self, from: data)
        else { throw CocoaError(.coderInvalidValue) }
        return plan
    }

    private func candidate(id: String) -> String {
        """
        {"candidate_id":"\(id)","calories_kcal":"500","category_names":[],"dietary_tags":[],
         "lines":[],"provenance":{"offering_ids":[],"food_ids":[],"profile_content_sha256s":[]},
         "score":{"breakdown":{},"total":"0"},"totals":{"confidence":"official_published",
         "declared_unavailable":[],"presences":{"calories_kcal":"known_value","protein_g":"known_value"},
         "published_zero":[],"quantities":{"calories_kcal":"500","protein_g":"30"}}}
        """
    }
}
