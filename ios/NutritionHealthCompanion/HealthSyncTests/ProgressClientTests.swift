import XCTest
@testable import NutritionHealthCompanion

final class ProgressClientTests: XCTestCase {
    private final class StubProtocol: URLProtocol {
        nonisolated(unsafe) static var handler: ((URLRequest) -> (Int, Data))?

        override class func canInit(with request: URLRequest) -> Bool { true }
        override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
        override func startLoading() {
            guard let handler = Self.handler else { return }
            let (status, data) = handler(request)
            let response = HTTPURLResponse(
                url: request.url!, statusCode: status,
                httpVersion: "HTTP/1.1", headerFields: nil)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        }
        override func stopLoading() {}
    }

    private let day = WireDate.date(fromISO8601: "2026-09-08T00:00:00Z")!

    override func tearDown() {
        StubProtocol.handler = nil
        super.tearDown()
    }

    func test_fetchProgressUsesAuthenticatedFixedWindowGETAndDecodesEvidenceStates() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path, "/v1/analytics/progress")
            let query = Dictionary(uniqueKeysWithValues:
                URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
                    .queryItems!.map { ($0.name, $0.value!) })
            XCTAssertEqual(query, [
                "as_of_date": "2026-09-08",
                "timezone": "America/New_York",
            ])
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"), "Bearer progress-token")
            return (200, Self.payload)
        }

        let response = try await makeClient().fetchProgress(
            asOfDate: day, timezone: "America/New_York")

        XCTAssertEqual(response.policyVersion, "owner-longitudinal-progress.v1")
        XCTAssertEqual(response.body.windowDays, 90)
        XCTAssertEqual(response.body.trend28d.status, .stale)
        XCTAssertEqual(response.body.dailyMedians[0].medianKg, "70.125")
        XCTAssertEqual(response.goal.status, .unavailable)
        XCTAssertEqual(response.nutrition.days[0].recordedCalories.state, .quantified)
        XCTAssertEqual(response.nutrition.days[0].recordedProtein.state, .partial)
        XCTAssertTrue(response.nutrition.summary7d.includesEstimates)
        XCTAssertEqual(response.nutrition.targetChanges[0].targetPolicyVersion, "target.v2")
        XCTAssertEqual(response.limitations.codes.count, 3)
    }

    private func makeClient() -> HTTPBackendClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        return HTTPBackendClient(
            baseURL: URL(string: "https://backend.example")!,
            auth: FakeAccessTokenProvider(token: "progress-token"),
            session: URLSession(configuration: config)
        )
    }

    static let payload = Data("""
    {
      "policy_version":"owner-longitudinal-progress.v1",
      "as_of_date":"2026-09-08",
      "timezone":"America/New_York",
      "body":{
        "window_days":90,"start_date":"2026-06-11","end_date":"2026-09-08",
        "daily_medians":[{"local_date":"2026-09-08","median_kg":"70.125","observation_count":2}],
        "trend_28d":{
          "status":"stale","as_of_date":"2026-09-08","timezone":"America/New_York",
          "algorithm_version":"body-mass-trend-v1",
          "input_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "latest_measurement_date":"2026-08-20","latest_measurement_age_days":19,
          "first_measurement_date":"2026-08-01","last_measurement_date":"2026-08-20",
          "represented_day_count":7,"coverage_span_days":20,
          "trailing_7d_average_kg":"70.2","weekly_rate_kg":null
        }
      },
      "goal":{
        "status":"unavailable","mode":"gain","desired_rate_kg_per_week":"0.2",
        "observed_rate_kg_per_week":null,"acceptable_rate_lower_kg_per_week":"0.15",
        "acceptable_rate_upper_kg_per_week":"0.25"
      },
      "nutrition":{
        "window_days":28,"start_date":"2026-08-12","end_date":"2026-09-08",
        "days":[{
          "local_date":"2026-09-08","has_recorded_events":true,"recorded_event_count":2,
          "recorded_calories":{"state":"quantified","value_kcal":"500.25"},
          "recorded_protein":{"state":"partial","value_g":"30"},
          "recorded_calorie_target_comparison":"below_target",
          "recorded_protein_target_comparison":"unavailable",
          "target_status":"available",
          "target":{"policy_version_id":"00000000-0000-0000-0000-000000000002","policy_version":"target.v2","calories_kcal":"2200","calories_goal_kind":"target","protein_g":"150","protein_goal_kind":"floor"},
          "authority_event_counts":{"estimated":1,"official":1},
          "source_event_counts":{"manual_custom":1,"plan":1},"includes_estimates":true
        }],
        "summary_7d":\(summary(window: 7, start: "2026-09-02")),
        "summary_28d":\(summary(window: 28, start: "2026-08-12")),
        "target_changes":[{
          "effective_at":"2026-09-02T04:00:00+00:00","effective_local_date":"2026-09-02",
          "target_policy_version_id":"00000000-0000-0000-0000-000000000002",
          "target_policy_version":"target.v2","calories_kcal":"2200",
          "calories_goal_kind":"target","protein_g":"150","protein_goal_kind":"floor"
        }]
      },
      "limitations":{
        "codes":["recorded_events_do_not_prove_complete_intake","consumption_is_attributed_by_server_recorded_time","nutrition_and_body_weight_are_descriptive_not_causal"],
        "logging_coverage":"Nytr summarizes recorded events only; unlogged food cannot be inferred.",
        "record_time_attribution":"Nutrition is attributed to the local day of its server-recorded time.",
        "causality":"Nutrition and body weight are displayed descriptively, not causally."
      }
    }
    """.utf8)

    private static func summary(window: Int, start: String) -> String {
        """
        {"window_days":\(window),"start_date":"\(start)","end_date":"2026-09-08",
         "coverage":{"days_with_recorded_events":1,"days_without_recorded_events":\(window - 1)},
         "recorded_calories":{"quantified_recorded_days":1,"partial_recorded_days":0,"unavailable_recorded_days":0,"average_recorded_kcal":"500.25","average_denominator_days":1},
         "recorded_protein":{"quantified_recorded_days":0,"partial_recorded_days":1,"unavailable_recorded_days":0,"average_recorded_g":null,"average_denominator_days":0},
         "recorded_calorie_target_comparisons":{"eligible_day_count":1,"below":1,"at":0,"above":0,"unavailable":\(window - 1)},
         "recorded_protein_target_comparisons":{"eligible_day_count":0,"below":0,"at_or_above":0,"unavailable":\(window)},
         "authority_event_counts":{"estimated":1,"official":1},"source_event_counts":{"manual_custom":1,"plan":1},"includes_estimates":true}
        """
    }
}
