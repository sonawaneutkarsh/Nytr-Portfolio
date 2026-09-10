import XCTest
@testable import NutritionHealthCompanion

final class DayPlanClientTests: XCTestCase {
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

    private let day = WireDate.date(fromISO8601: "2026-08-21T00:00:00Z")!

    private func makeClient(token: String = "m7-token") -> HTTPBackendClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        return HTTPBackendClient(
            baseURL: URL(string: "https://backend.example")!,
            auth: FakeAccessTokenProvider(token: token),
            session: URLSession(configuration: config)
        )
    }

    override func tearDown() {
        StubProtocol.handler = nil
        super.tearDown()
    }

    func test_fetchDayPlan_sendsAuthenticatedGETAndDecodesCompletedArtifact() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path, "/v1/plans/day")
            XCTAssertEqual(request.url?.query, "date=2026-08-21")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"), "Bearer m7-token")
            return (200, Self.completedData(policy: true, generatedAt: true))
        }

        let response = try await makeClient().fetchDayPlan(date: day)
        guard case .completed(let completed) = response else {
            return XCTFail("expected completed")
        }
        XCTAssertEqual(completed.planDate, "2026-08-21")
        XCTAssertEqual(completed.plan.slots[0].candidates[0].caloriesKcal, "901.2300")
        XCTAssertEqual(
            completed.plan.slots[0].candidates[0].totals.quantities["protein_g"],
            "50.500")
        XCTAssertEqual(completed.plan.slots[0].candidates[0].lines[0].servings, "1.250")
        XCTAssertEqual(completed.planItems?.count, 1)
        XCTAssertEqual(
            completed.planItems?[0].itemId,
            UUID(uuidString: "50000000-0000-0000-0000-000000000001")
        )
        XCTAssertEqual(completed.planItems?[0].candidateId, "candidate-1")
        XCTAssertEqual(completed.planItems?[0].slotIndex, 0)
        XCTAssertEqual(completed.planItems?[0].rank, 1)
        XCTAssertEqual(
            completed.itemReference(slotIndex: 0, rank: 1, candidateId: "candidate-1")?.itemId,
            UUID(uuidString: "50000000-0000-0000-0000-000000000001")
        )
    }

    func test_fetchDayPlan_decodesNoPlanWithoutInventingGenerationFields() async throws {
        StubProtocol.handler = { _ in
            (200, Data("""
            {"state":"no_plan","requested_date":"2026-08-21",
             "reason_codes":["empty_menu_period"],"inputs_fingerprint":"fp"}
            """.utf8))
        }
        let response = try await makeClient().fetchDayPlan(date: day)
        guard case .noPlan(let value) = response else { return XCTFail("expected no_plan") }
        XCTAssertEqual(value.requestedDate, "2026-08-21")
        XCTAssertEqual(value.reasonCodes, ["empty_menu_period"])
        XCTAssertNil(value.planDate)
        XCTAssertNil(value.runId)
    }

    func test_fetchDayPlan_decodesNotGeneratedExactShape() async throws {
        StubProtocol.handler = { _ in
            (200, Data("""
            {"state":"not_generated","requested_date":"2026-08-21"}
            """.utf8))
        }
        let response = try await makeClient().fetchDayPlan(date: day)
        XCTAssertEqual(response, .notGenerated(NotGeneratedDay(requestedDate: "2026-08-21")))
    }

    func test_completedTargetPolicyNullDecodesAsNil() async throws {
        StubProtocol.handler = { _ in
            (200, Self.completedData(policy: false, generatedAt: true))
        }
        let response = try await makeClient().fetchDayPlan(date: day)
        guard case .completed(let value) = response else { return XCTFail("expected completed") }
        XCTAssertNil(value.targetPolicy)
    }

    func test_completedHistoricalPolicyAndGeneratedAtDecode() async throws {
        StubProtocol.handler = { _ in
            (200, Self.completedData(policy: true, generatedAt: true))
        }
        let response = try await makeClient().fetchDayPlan(date: day)
        guard case .completed(let value) = response else { return XCTFail("expected completed") }
        XCTAssertEqual(value.targetPolicy?.policyVersion, "policy-p1")
        XCTAssertEqual(value.targetPolicy?.payloadSha256, "policy-sha")
        XCTAssertEqual(
            value.targetPolicy?.approvedAt,
            WireDate.date(fromISO8601: "2026-08-20T10:15:30+00:00"))
        XCTAssertEqual(
            value.generatedAt,
            WireDate.date(fromISO8601: "2026-08-21T12:00:00.123456+00:00"))
    }

    func test_unknownStateFailsClosed() async {
        StubProtocol.handler = { _ in (200, Data("{\"state\":\"future\"}".utf8)) }
        do {
            _ = try await makeClient().fetchDayPlan(date: day)
            XCTFail("expected decoding failure")
        } catch BackendError.retryable {
            // Safe success-payload failure classification.
        } catch {
            XCTFail("wrong error \(error)")
        }
    }

    func test_fetchCompletedMissingRequiredPlanItemsFailsSafely() async {
        StubProtocol.handler = { _ in
            (200, Self.completedData(policy: true, generatedAt: true, planItems: false))
        }
        do {
            _ = try await makeClient().fetchDayPlan(date: day)
            XCTFail("expected missing metadata failure")
        } catch BackendError.retryable {
            // Canonical GET guarantees plan_items.
        } catch {
            XCTFail("wrong error \(error)")
        }
    }

    func test_generateDayPlan_sendsOnlyDateAndCallerTimezone() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/v1/plans/day/generate")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"), "Bearer m7-token")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Content-Type"), "application/json")
            let object = try! JSONSerialization.jsonObject(with: StubRequestBody.data(from: request))
                as! [String: String]
            XCTAssertEqual(Set(object.keys), Set(["date", "timezone"]))
            XCTAssertEqual(object["date"], "2026-08-21")
            XCTAssertEqual(object["timezone"], "America/New_York")
            return (200, Self.completedData(
                policy: nil, generatedAt: false, planItems: false))
        }

        let response = try await makeClient().generateDayPlan(
            date: day, timezone: "America/New_York")
        guard case .completed(let value) = response else { return XCTFail("expected completed") }
        XCTAssertNil(value.targetPolicy)
        XCTAssertNil(value.generatedAt)
        XCTAssertNil(value.planItems)
    }

    func test_generateDayPlan_decodesNoPlanGenerationShape() async throws {
        StubProtocol.handler = { _ in
            (200, Data("""
            {"state":"no_plan","requested_date":"2026-08-21","plan_date":"2026-08-21",
             "reason_codes":["no_candidate"],"inputs_fingerprint":"fp",
             "run_id":"10000000-0000-0000-0000-000000000001"}
            """.utf8))
        }
        let response = try await makeClient().generateDayPlan(date: day, timezone: "UTC")
        guard case .noPlan(let value) = response else { return XCTFail("expected no_plan") }
        XCTAssertEqual(value.planDate, "2026-08-21")
        XCTAssertEqual(
            value.runId, UUID(uuidString: "10000000-0000-0000-0000-000000000001"))
    }

    func test_latestTargetPolicyUsesAuthenticatedPathAndPreservesDecimalStrings() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path, "/v1/target-policies/latest")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"), "Bearer m7-token")
            return (200, Data("""
            {"version_id":"90000000-0000-0000-0000-000000000001",
             "policy_version":"policy-p2","goals":[{"nutrient":"calories_kcal",
             "kind":"target","value":"901.2300","weight":"1.000"}],
             "payload_sha256":"sha","approved_at":"2026-08-21T12:00:00+00:00"}
            """.utf8))
        }
        let policy = try await makeClient().fetchLatestTargetPolicy()
        XCTAssertEqual(policy?.goals[0].value, "901.2300")
        XCTAssertEqual(policy?.goals[0].weight, "1.000")
    }

    func test_dailyNutritionLedgerUsesAuthenticatedLocalDayAndPreservesExactValues() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path, "/v1/nutrition/daily-ledger")
            let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)
            let query = Dictionary(uniqueKeysWithValues: (components?.queryItems ?? []).map {
                ($0.name, $0.value)
            })
            XCTAssertEqual(query["date"]!, "2026-08-21")
            XCTAssertEqual(query["timezone"]!, "America/New_York")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"), "Bearer m7-token")
            return (200, Data("""
            {"local_date":"2026-08-21","timezone":"America/New_York",
             "target":{"policy_version_id":"90000000-0000-0000-0000-000000000001",
             "policy_version":"target.v1","calories_kcal":"2500.00",
             "calories_goal_kind":"target","protein_g":"150.0","protein_goal_kind":"floor"},
             "consumed_item_count":1,"known_calories_consumed":"604.27781682500",
             "known_protein_g_consumed":"45.7386516982500",
             "remaining_known_calories":"1895.72218317500",
             "remaining_known_protein_g":"104.2613483017500",
             "nutrition_completeness":"partial","nutrition_authorities":["partial"],
             "unknown_nutrients":["sodium_mg"],"reason_codes":["nutrition_partial"],
             "consumed_items":[{"entry_id":"70000000-0000-0000-0000-000000000001",
             "recorded_at":"2026-08-21T16:00:00Z",
             "plan_run_id":"10000000-0000-0000-0000-000000000001",
             "plan_version_id":"20000000-0000-0000-0000-000000000001",
             "plan_item_id":"50000000-0000-0000-0000-000000000001",
             "meal_context":"post_workout_lunch","candidate_id":"candidate-1",
             "item_name":"CYO Halal Bowl","configuration_summary":"no sauce",
             "nutrition_authority":"partial","confidence":"partial",
             "calories_kcal":"604.27781682500","protein_g":"45.7386516982500",
             "unknown_nutrients":["sodium_mg"],
             "provenance_summary":"Owner-observed configuration with external reference nutrition"}]}
            """.utf8))
        }

        let ledger = try await makeClient().fetchDailyNutritionLedger(
            date: day, timezone: "America/New_York")

        XCTAssertEqual(ledger.knownCaloriesConsumed, "604.27781682500")
        XCTAssertEqual(ledger.target?.caloriesKcal, "2500.00")
        XCTAssertEqual(ledger.nutritionCompleteness, .partial)
        XCTAssertEqual(ledger.consumedItems[0].planItemId,
            UUID(uuidString: "50000000-0000-0000-0000-000000000001"))
        XCTAssertEqual(ledger.consumedItems[0].nutritionAuthority, .partial)
    }

    func test_nutritionHistoryUsesAuthenticatedEndDayAndPreservesTargetStatus() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path, "/v1/nutrition/history")
            let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)
            let query = Dictionary(uniqueKeysWithValues: (components?.queryItems ?? []).map {
                ($0.name, $0.value)
            })
            XCTAssertEqual(query["end_date"]!, "2026-08-21")
            XCTAssertEqual(query["timezone"]!, "America/New_York")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"), "Bearer m7-token")
            return (200, Data("""
            {"start_date":"2026-08-15","end_date":"2026-08-21",
             "timezone":"America/New_York","days":[
              {"local_date":"2026-08-21","timezone":"America/New_York","target":null,
               "consumed_event_count":0,"known_calories_consumed":"0",
               "known_protein_g_consumed":"0","remaining_known_calories":null,
               "remaining_known_protein_g":null,"nutrition_completeness":"complete",
               "nutrition_authorities":[],"unknown_nutrients":[],"consumed_items":[],
               "reason_codes":["target_changed_during_day"],
               "target_status":"target_changed_during_day",
               "calorie_adherence":"unavailable","protein_adherence":"unavailable"}],
             "summary":{"days_with_consumption":0,"days_complete":7,"days_partial":0,
              "days_unavailable":0,"days_target_available":6,"days_target_changed":1,
              "known_calories_total":"0","known_protein_g_total":"0"}}
            """.utf8))
        }

        let history = try await makeClient().fetchNutritionHistory(
            endDate: day, timezone: "America/New_York")

        XCTAssertEqual(history.days[0].targetStatus, .targetChangedDuringDay)
        XCTAssertEqual(history.days[0].calorieAdherence, .unavailable)
        XCTAssertEqual(history.summary.daysTargetChanged, 1)
        XCTAssertEqual(history.summary.knownCaloriesTotal, "0")
    }

    func test_latestTargetPolicyNullResponseDecodesAsNil() async throws {
        StubProtocol.handler = { _ in (200, Data("null".utf8)) }
        let policy = try await makeClient().fetchLatestTargetPolicy()
        XCTAssertNil(policy)
    }

    func test_m7UnauthorizedMapsToExistingUnauthorized() async {
        StubProtocol.handler = { _ in (401, Data()) }
        do {
            _ = try await makeClient().fetchDayPlan(date: day)
            XCTFail("expected unauthorized")
        } catch BackendError.unauthorized {
        } catch {
            XCTFail("wrong error \(error)")
        }
    }

    func test_generationConflictCodesArePreserved() async {
        for code in ["concurrent_generation", "no_approved_target_policy"] {
            StubProtocol.handler = { _ in Self.error(status: 409, code: code) }
            do {
                _ = try await makeClient().generateDayPlan(date: day, timezone: "UTC")
                XCTFail("expected rejection")
            } catch BackendError.rejected(let status, let actualCode, _) {
                XCTAssertEqual(status, 409)
                XCTAssertEqual(actualCode, code)
            } catch {
                XCTFail("wrong error \(error)")
            }
        }
    }

    func test_422UsesGenericRejectedMapping() async {
        StubProtocol.handler = { _ in (422, Data("{\"detail\":[]}".utf8)) }
        do {
            _ = try await makeClient().generateDayPlan(date: day, timezone: "UTC")
            XCTFail("expected rejection")
        } catch BackendError.rejected(let status, let code, _) {
            XCTAssertEqual(status, 422)
            XCTAssertNil(code)
        } catch {
            XCTFail("wrong error \(error)")
        }
    }

    func test_503PreservesRetryableBackendCode() async {
        StubProtocol.handler = { _ in Self.error(status: 503, code: "storage_unavailable") }
        do {
            _ = try await makeClient().fetchDayPlan(date: day)
            XCTFail("expected retryable failure")
        } catch BackendError.retryableHTTP(let status, let code, _) {
            XCTAssertEqual(status, 503)
            XCTAssertEqual(code, "storage_unavailable")
        } catch {
            XCTFail("wrong error \(error)")
        }
    }

    private static func error(status: Int, code: String) -> (Int, Data) {
        (status, Data("{\"error\":{\"code\":\"\(code)\",\"detail\":\"safe\"}}".utf8))
    }

    /// A minimal but structurally complete copy of the canonical M6 artifact.
    private static func completedData(
        policy: Bool?, generatedAt: Bool, planItems: Bool = true
    ) -> Data {
        let policyJSON: String
        if policy == true {
            policyJSON = """
            ,"target_policy":{"version_id":"90000000-0000-0000-0000-000000000001",
            "policy_version":"policy-p1","payload_sha256":"policy-sha",
            "approved_at":"2026-08-20T10:15:30+00:00"}
            """
        } else if policy == false {
            policyJSON = ",\"target_policy\":null"
        } else {
            policyJSON = ""
        }
        let generatedJSON = generatedAt
            ? ",\"generated_at\":\"2026-08-21T12:00:00.123456+00:00\"" : ""
        let planItemsJSON = planItems
            ? """
            ,"plan_items":[
              {"item_id":"50000000-0000-0000-0000-000000000001",
               "slot_index":0,"rank":1,"candidate_id":"candidate-1"}]
            """
            : ""
        return Data("""
        {"state":"completed","requested_date":"2026-08-21","plan_date":"2026-08-21",
         "plan_sha256":"plan-sha","inputs_fingerprint":"fingerprint",
         "run_id":"10000000-0000-0000-0000-000000000001",
         "version_id":"20000000-0000-0000-0000-000000000001",
         "plan":{"artifact_kind":"daily_plan","plan_date":"2026-08-21",
          "policy_versions":{"engine":"engine.v1","planner":"planner.v1",
          "schedule":"schedule.v1","target":"target.v1"},
          "menu_snapshot_sha256":"menu-sha","status":"ok","slots":[{
           "context":"lunch","menu_period":"Lunch","status":"ok",
           "window":["12:00:00","13:00:00"],"failure_reasons":[],
           "rejection_counts":{},"rejection_details":[],"candidates":[{
            "candidate_id":"candidate-1","calories_kcal":"901.2300",
            "category_names":["Entree"],"dietary_tags":[],"lines":[{
             "category_name":"Entree","food_id":"30000000-0000-0000-0000-000000000001",
             "name_normalized":"Example meal","occurrence_ordinal":0,
             "offering_id":"40000000-0000-0000-0000-000000000001",
             "parser_version":"parser.v1","profile_content_sha256":"profile-sha",
             "servings":"1.250","source_mid":"mid-1"}],
            "provenance":{"offering_ids":["40000000-0000-0000-0000-000000000001"],
             "food_ids":["30000000-0000-0000-0000-000000000001"],
             "profile_content_sha256s":["profile-sha"]},
            "score":{"breakdown":{"protein_g:target":"-0.125"},"total":"-0.125"},
            "totals":{"confidence":"official_published","declared_unavailable":[],
             "presences":{"protein_g":"known_value"},"published_zero":[],
             "quantities":{"protein_g":"50.500","calories_kcal":"901.2300"}}}] }]}
         \(planItemsJSON)\(policyJSON)\(generatedJSON)}
        """.utf8)
    }
}
