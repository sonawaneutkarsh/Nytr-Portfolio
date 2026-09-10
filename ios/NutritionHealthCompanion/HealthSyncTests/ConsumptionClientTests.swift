import XCTest
@testable import NutritionHealthCompanion

final class ConsumptionClientTests: XCTestCase {
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

    private let runId = UUID(uuidString: "10000000-0000-0000-0000-000000000001")!
    private let versionId = UUID(uuidString: "20000000-0000-0000-0000-000000000001")!
    private let itemId = UUID(uuidString: "30000000-0000-0000-0000-000000000001")!
    private let eventId = UUID(uuidString: "40000000-0000-0000-0000-000000000001")!

    private func makeClient() -> HTTPBackendClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        return HTTPBackendClient(
            baseURL: URL(string: "https://backend.example")!,
            auth: FakeAccessTokenProvider(token: "consumption-token"),
            session: URLSession(configuration: config)
        )
    }

    override func tearDown() {
        StubProtocol.handler = nil
        super.tearDown()
    }

    func test_recordConsumption_firstWrite201SendsExactEventAndDecodes() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(
                request.url?.path,
                "/v1/plans/10000000-0000-0000-0000-000000000001/consumption")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"),
                "Bearer consumption-token")
            let body = try! JSONSerialization.jsonObject(with: StubRequestBody.data(from: request))
                as! [String: String]
            XCTAssertEqual(body["plan_version_id"], self.versionId.uuidString.lowercased())
            XCTAssertEqual(body["item_id"], self.itemId.uuidString.lowercased())
            XCTAssertEqual(body["state"], "eaten")
            XCTAssertEqual(body["client_event_id"], self.eventId.uuidString.lowercased())
            return (201, Self.entryData(state: "eaten"))
        }

        let entry = try await makeClient().recordConsumption(
            runId: runId,
            planVersionId: versionId,
            itemId: itemId,
            state: .eaten,
            clientEventId: eventId
        )
        XCTAssertEqual(entry.planRunId, runId)
        XCTAssertEqual(entry.clientEventId, eventId)
        XCTAssertEqual(entry.state, .eaten)
        XCTAssertEqual(
            entry.recordedAt,
            WireDate.date(fromISO8601: "2026-08-21T12:00:00.654321+00:00"))
    }

    func test_recordConsumption_exactReplay200DecodesOriginalMetadata() async throws {
        StubProtocol.handler = { _ in (200, Self.entryData(state: "eaten")) }
        let entry = try await makeClient().recordConsumption(
            runId: runId, planVersionId: versionId, itemId: itemId,
            state: .eaten, clientEventId: eventId)
        XCTAssertEqual(
            entry.entryId,
            UUID(uuidString: "50000000-0000-0000-0000-000000000001"))
        XCTAssertEqual(entry.clientEventId, eventId)
    }

    func test_recordConsumption_nilEventOmitsClientEventKey() async throws {
        StubProtocol.handler = { request in
            let body = try! JSONSerialization.jsonObject(with: StubRequestBody.data(from: request))
                as! [String: Any]
            XCTAssertNil(body["client_event_id"])
            return (201, Self.entryData(state: "skipped"))
        }
        let entry = try await makeClient().recordConsumption(
            runId: runId, planVersionId: versionId, itemId: itemId,
            state: .skipped, clientEventId: nil)
        XCTAssertEqual(entry.state, .skipped)
    }

    func test_listConsumption_sendsAuthenticatedGETAndDecodesEntries() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(
                request.url?.path,
                "/v1/plans/10000000-0000-0000-0000-000000000001/consumption")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"),
                "Bearer consumption-token")
            return (200, Data("{\"entries\":[\(String(decoding: Self.entryData(state: "alternative"), as: UTF8.self))]}".utf8))
        }
        let response = try await makeClient().listConsumption(runId: runId)
        XCTAssertEqual(response.entries.count, 1)
        XCTAssertEqual(response.entries[0].state, .alternative)
    }

    func test_allFourConsumptionStatesDecodeAndRoundTripRawValues() async throws {
        XCTAssertEqual(
            ConsumptionState.allCases.map(\.rawValue),
            ["eaten", "skipped", "unavailable", "alternative"])
        for state in ConsumptionState.allCases {
            StubProtocol.handler = { _ in (201, Self.entryData(state: state.rawValue)) }
            let entry = try await makeClient().recordConsumption(
                runId: runId, planVersionId: versionId, itemId: itemId,
                state: state, clientEventId: eventId)
            XCTAssertEqual(entry.state, state)
        }
    }

    func test_targetNotFound404PreservesBackendCode() async {
        StubProtocol.handler = { _ in Self.error(
            status: 404, code: "consumption_target_not_found") }
        do {
            _ = try await makeClient().recordConsumption(
                runId: runId, planVersionId: versionId, itemId: itemId,
                state: .eaten, clientEventId: eventId)
            XCTFail("expected rejection")
        } catch BackendError.rejected(let status, let code, _) {
            XCTAssertEqual(status, 404)
            XCTAssertEqual(code, "consumption_target_not_found")
        } catch {
            XCTFail("wrong error \(error)")
        }
    }

    func test_consumptionConflict409PreservesBackendCode() async {
        StubProtocol.handler = { _ in Self.error(status: 409, code: "consumption_conflict") }
        do {
            _ = try await makeClient().recordConsumption(
                runId: runId, planVersionId: versionId, itemId: itemId,
                state: .eaten, clientEventId: eventId)
            XCTFail("expected rejection")
        } catch BackendError.rejected(let status, let code, _) {
            XCTAssertEqual(status, 409)
            XCTAssertEqual(code, "consumption_conflict")
        } catch {
            XCTFail("wrong error \(error)")
        }
    }

    private static func error(status: Int, code: String) -> (Int, Data) {
        (status, Data("{\"error\":{\"code\":\"\(code)\",\"detail\":\"safe\"}}".utf8))
    }

    private static func entryData(state: String) -> Data {
        Data("""
        {"entry_id":"50000000-0000-0000-0000-000000000001",
         "plan_run_id":"10000000-0000-0000-0000-000000000001",
         "plan_version_id":"20000000-0000-0000-0000-000000000001",
         "item_id":"30000000-0000-0000-0000-000000000001",
         "state":"\(state)","recorded_at":"2026-08-21T12:00:00.654321+00:00",
         "client_event_id":"40000000-0000-0000-0000-000000000001"}
        """.utf8)
    }
}
