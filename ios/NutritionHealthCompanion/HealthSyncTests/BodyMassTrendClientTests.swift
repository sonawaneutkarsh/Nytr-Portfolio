import XCTest
@testable import NutritionHealthCompanion

final class BodyMassTrendClientTests: XCTestCase {
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

    private let day = WireDate.date(fromISO8601: "2026-08-28T00:00:00Z")!

    override func tearDown() {
        StubProtocol.handler = nil
        super.tearDown()
    }

    func test_fetchTrendUsesAuthenticatedGETEncodedIanaTimezoneAndLosslessStrings() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path, "/v1/health/body-mass/trend")
            let query = Dictionary(uniqueKeysWithValues:
                URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
                    .queryItems!.map { ($0.name, $0.value!) })
            XCTAssertEqual(query["as_of_date"], "2026-08-28")
            XCTAssertEqual(query["timezone"], "Etc/GMT+5")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"), "Bearer m9-token")
            return (200, Self.payload(
                status: "ready", average: "68.4000000000000000001", rate: "0.180000"))
        }

        let response = try await makeClient().fetchBodyMassTrend(
            asOfDate: day, timezone: "Etc/GMT+5")

        XCTAssertEqual(response.status, .ready)
        XCTAssertEqual(response.asOfDate, "2026-08-28")
        XCTAssertEqual(response.timezone, "Etc/GMT+5")
        XCTAssertEqual(response.trailing7dAverageKg, "68.4000000000000000001")
        XCTAssertEqual(response.weeklyRateKg, "0.180000")
        XCTAssertEqual(response.formattedTrailingAverageKg, "68.40")
        XCTAssertEqual(response.formattedWeeklyRateKg, "+0.18")
    }

    func test_allFourStatusesDecodeAndReadyAllowsNullableAverage() async throws {
        for status in ["no_data", "insufficient", "stale", "ready"] {
            StubProtocol.handler = { _ in
                (200, Self.payload(status: status, average: nil, rate: "-0.12"))
            }
            let response = try await makeClient().fetchBodyMassTrend(
                asOfDate: day, timezone: "America/New_York")
            XCTAssertEqual(response.status.rawValue, status)
            XCTAssertNil(response.trailing7dAverageKg)
            XCTAssertNil(response.formattedTrailingAverageKg)
        }
    }

    func test_ratePresentationPreservesPositiveNegativeAndZeroSigns() throws {
        for (raw, expected) in [
            ("0.181", "+0.18"),
            ("-0.124", "-0.12"),
            ("0", "0.00"),
        ] {
            let response = try JSONDecoder().decode(
                BodyMassTrendResponse.self,
                from: Self.payload(status: "ready", average: nil, rate: raw)
            )
            XCTAssertEqual(response.formattedWeeklyRateKg, expected)
        }
    }

    func test_unknownStatusInvalidDateAndInvalidDecimalFailClosed() async {
        for body in [
            Self.payload(status: "future", average: nil, rate: "0.1"),
            Self.payload(
                status: "ready", average: "68", rate: "0.1", asOfDate: "2026-02-30"),
            Self.payload(status: "ready", average: "1abc", rate: "0.1"),
        ] {
            StubProtocol.handler = { _ in (200, body) }
            do {
                _ = try await makeClient().fetchBodyMassTrend(asOfDate: day, timezone: "UTC")
                XCTFail("expected invalid trend response to fail")
            } catch BackendError.retryable {
                // Existing client convention: malformed 200 responses fail safely.
            } catch {
                XCTFail("wrong error \(error)")
            }
        }
    }

    private func makeClient() -> HTTPBackendClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        return HTTPBackendClient(
            baseURL: URL(string: "https://backend.example")!,
            auth: FakeAccessTokenProvider(token: "m9-token"),
            session: URLSession(configuration: config)
        )
    }

    private static func payload(
        status: String,
        average: String?,
        rate: String?,
        asOfDate: String = "2026-08-28"
    ) -> Data {
        let averageJSON = average.map { "\"\($0)\"" } ?? "null"
        let rateJSON = rate.map { "\"\($0)\"" } ?? "null"
        return Data("""
        {"status":"\(status)","as_of_date":"\(asOfDate)","timezone":"Etc/GMT+5",
         "algorithm_version":"body-mass-trend-v1",
         "input_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
         "represented_day_count":7,"coverage_span_days":20,
         "first_measurement_date":"2026-08-08","last_measurement_date":"2026-08-28",
         "latest_measurement_date":"2026-08-28","latest_measurement_age_days":0,
         "trailing_7d_average_kg":\(averageJSON),"weekly_rate_kg":\(rateJSON)}
        """.utf8)
    }
}
