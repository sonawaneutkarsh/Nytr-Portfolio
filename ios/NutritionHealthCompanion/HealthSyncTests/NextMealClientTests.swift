import XCTest
@testable import NutritionHealthCompanion

final class NextMealClientTests: XCTestCase {
    private final class StubProtocol: URLProtocol {
        nonisolated(unsafe) static var handler: ((URLRequest) -> (Int, Data))?
        override class func canInit(with request: URLRequest) -> Bool { true }
        override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
        override func startLoading() {
            let (status, data) = Self.handler!(request)
            let response = HTTPURLResponse(
                url: request.url!, statusCode: status,
                httpVersion: "HTTP/1.1", headerFields: nil)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        }
        override func stopLoading() {}
    }

    private let day = WireDate.date(fromISO8601: "2026-09-05T00:00:00Z")!
    private let requestId = UUID(uuidString: "20000000-0000-0000-0000-000000000001")!

    override func tearDown() {
        StubProtocol.handler = nil
        super.tearDown()
    }

    func testExplicitGenerationSendsOnlyFactualRequestContextAndDecodesTypedState() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/v1/recommendations/next-meal")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer token")
            let body = try! JSONSerialization.jsonObject(
                with: StubRequestBody.data(from: request)) as! [String: String]
            XCTAssertEqual(Set(body.keys), Set(["local_date", "timezone", "client_request_id"]))
            XCTAssertEqual(body["local_date"], "2026-09-05")
            XCTAssertEqual(body["timezone"], "America/New_York")
            XCTAssertEqual(body["client_request_id"], self.requestId.uuidString.uppercased())
            return (201, Self.failureResponse)
        }

        let value = try await makeClient().generateNextMeal(
            date: day, timezone: "America/New_York", clientRequestId: requestId)
        XCTAssertEqual(value.status, .noRemainingMealOpportunity)
        XCTAssertEqual(value.artifact.nextMealPolicyVersion, "next-meal.remaining-opportunities.v1")
    }

    func testLatestIsRetrievalOnly() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path, "/v1/recommendations/next-meal/latest")
            XCTAssertNil(request.httpBody)
            return (200, Self.failureResponse)
        }
        _ = try await makeClient().fetchLatestNextMeal()
    }

    func testExplicitConsumptionSendsOnlyClientEventIdentity() async throws {
        let recommendationId = UUID(
            uuidString: "10000000-0000-0000-0000-000000000001")!
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(
                request.url?.path,
                "/v1/recommendations/next-meal/"
                    + "\(recommendationId.uuidString.lowercased())/consumption"
            )
            let body = try! JSONSerialization.jsonObject(
                with: StubRequestBody.data(from: request)) as! [String: String]
            XCTAssertEqual(Set(body.keys), Set(["client_event_id"]))
            XCTAssertEqual(body["client_event_id"], self.requestId.uuidString.uppercased())
            return (201, Self.consumptionResponse)
        }

        let value = try await makeClient().recordNextMealConsumption(
            recommendationId: recommendationId,
            clientEventId: requestId
        )
        XCTAssertEqual(value.state, "eaten")
        XCTAssertEqual(value.caloriesKcal, "640")
    }

    func testConsumptionStatusIsRetrievalOnly() async throws {
        let recommendationId = UUID(
            uuidString: "10000000-0000-0000-0000-000000000001")!
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(
                request.url?.path,
                "/v1/recommendations/next-meal/"
                    + "\(recommendationId.uuidString.lowercased())/consumption"
            )
            XCTAssertNil(request.httpBody)
            return (200, Self.consumptionResponse)
        }
        _ = try await makeClient().fetchNextMealConsumption(
            recommendationId: recommendationId
        )
    }

    private func makeClient() -> HTTPBackendClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        return HTTPBackendClient(
            baseURL: URL(string: "https://backend.example")!,
            auth: FakeAccessTokenProvider(token: "token"),
            session: URLSession(configuration: config)
        )
    }

    private static let failureResponse = Data("""
    {
      "recommendation_id":"10000000-0000-0000-0000-000000000001",
      "client_request_id":"20000000-0000-0000-0000-000000000001",
      "local_date":"2026-09-05","timezone":"America/New_York",
      "decision_at":"2026-09-06T03:00:00Z",
      "status":"no_remaining_meal_opportunity",
      "reason_codes":["all_stacks_windows_elapsed"],
      "inputs_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "artifact_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "artifact":{
        "artifact_kind":"next_meal_recommendation","artifact_version":"m16a.v1",
        "decision_at":"2026-09-06T03:00:00Z","local_date":"2026-09-05",
        "timezone":"America/New_York","status":"no_remaining_meal_opportunity",
        "reason_codes":["all_stacks_windows_elapsed"],
        "next_meal_policy_version":"next-meal.remaining-opportunities.v1"
      },"created":true
    }
    """.utf8)

    private static let consumptionResponse = Data("""
    {
      "entry_id":"70000000-0000-0000-0000-000000000001",
      "recommendation_id":"10000000-0000-0000-0000-000000000001",
      "client_event_id":"20000000-0000-0000-0000-000000000001",
      "state":"eaten","local_date":"2026-09-05","timezone":"America/New_York",
      "recorded_at":"2026-09-05T17:00:00Z",
      "recommendation_artifact_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "next_meal_policy_version":"next-meal.remaining-opportunities.v1",
      "meal_context":"lunch","menu_period":"Lunch","candidate_id":"lunch-01",
      "item_name":"Chicken bowl","serving_description":"1 × Chicken bowl",
      "configuration_summary":null,"nutrition_authority":"official",
      "nutrition_confidence":"official_published","calories_kcal":"640",
      "protein_g":"44.5","unknown_nutrients":[],
      "selected_candidate_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
      "created":true
    }
    """.utf8)
}
