import XCTest
@testable import NutritionHealthCompanion

/// BackendClient behavior against a URLProtocol stub: URL/method/auth-header/
/// batch-shape, error classification. (401-refresh-retry lives in the
/// AccessTokenProvider; here we verify the client surfaces .unauthorized.)
final class BackendClientTests: XCTestCase {
    private final class StubProtocol: URLProtocol {
        nonisolated(unsafe) static var handler:
            ((URLRequest) -> (Int, Data))? = nil
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

    private func makeClient(auth: AccessTokenProvider) -> HTTPBackendClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        return HTTPBackendClient(
            baseURL: URL(string: "https://backend.example")!,
            auth: auth,
            session: URLSession(configuration: config)
        )
    }

    private func makeSample(_ n: Int) -> BodyMassSampleDTO {
        BodyMassSampleDTO(
            sampleUUID: UUID(uuidString: "00000000-0000-0000-0000-\(String(format: "%012x", n))")!,
            valueKgDecimalString: "72.000",
            sampleStart: Date(timeIntervalSince1970: 1_760_000_000),
            sampleEnd: Date(timeIntervalSince1970: 1_760_000_000)
        )
    }

    override func tearDown() {
        StubProtocol.handler = nil
        super.tearDown()
    }

    func test_submitBatch_sendsCorrectMethodHeadersAndShape() async throws {
        let auth = FakeAccessTokenProvider(token: "token-abc")
        let client = makeClient(auth: auth)

        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(
                request.url?.path, "/v1/health/body-mass/sync")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"), "Bearer token-abc")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Content-Type"), "application/json")
            let body = request.httpBody ?? request.httpBodyStream.map { stream in
                var data = Data(); stream.open()
                defer { stream.close() }
                let size = 8192; let buf = UnsafeMutablePointer<UInt8>.allocate(capacity: size)
                defer { buf.deallocate() }
                while stream.hasBytesAvailable {
                    let read = stream.read(buf, maxLength: size)
                    if read <= 0 { break }
                    data.append(buf, count: read)
                }
                return data
            } ?? Data()
            let json = try! JSONSerialization.jsonObject(with: body) as! [String: Any]
            // Batch id must be present and string-encoded.
            XCTAssertEqual((json["client_batch_id"] as? String)?.count, 36)
            XCTAssertNotNil(json["added"] as? [[String: Any]])
            let added = json["added"] as! [[String: Any]]
            XCTAssertEqual(added.count, 1)
            XCTAssertEqual(added[0]["value"] as? String, "72.000")
            XCTAssertEqual((added[0]["sample_uuid"] as! String).count, 36)
            XCTAssertTrue((added[0]["sample_start"] as! String).hasSuffix("Z"))
            return (200, Data("""
            {"accepted_added":1,"duplicate_added":0,"applied_deletions":0,
             "duplicate_deletions":0,"ingested_at":"2026-08-25T00:00:00Z",
             "latest_sample":null}
            """.utf8))
        }

        _ = try await client.submitBatch(
            added: [makeSample(1)], deletions: [])
    }

    func test_submitWorkoutBatch_sendsSourceIdentityDecimalsAndOptionalProvenance() async throws {
        let client = makeClient(auth: FakeAccessTokenProvider(token: "workout-token"))
        let sourceID = UUID(uuidString: "00000000-0000-0000-0000-000000000077")!
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/v1/health/workouts/sync")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"),
                "Bearer workout-token"
            )
            let json = try! JSONSerialization.jsonObject(
                with: StubRequestBody.data(from: request)
            ) as! [String: Any]
            let added = json["added"] as! [[String: Any]]
            XCTAssertEqual(added.count, 1)
            XCTAssertEqual(added[0]["source_system"] as? String, "healthkit")
            XCTAssertEqual(added[0]["source_record_id"] as? String, sourceID.uuidString.lowercased())
            XCTAssertEqual(added[0]["active_duration_seconds"] as? String, "1800.000")
            XCTAssertEqual(added[0]["active_energy_kcal"] as? String, "210.250")
            XCTAssertEqual(added[0]["timezone_identifier"] as? String, "America/New_York")
            XCTAssertEqual((json["client_batch_id"] as? String)?.count, 36)
            return (200, Data("""
            {"accepted_added":1,"duplicate_added":0,"applied_deletions":0,
             "duplicate_deletions":0}
            """.utf8))
        }

        let response = try await client.submitWorkoutBatch(
            added: [WorkoutSampleDTO(
                sourceRecordID: sourceID,
                activityType: "37",
                startedAt: Date(timeIntervalSince1970: 1_760_000_000),
                endedAt: Date(timeIntervalSince1970: 1_760_001_800),
                activeDurationSecondsDecimalString: "1800.000",
                activeEnergyKcalDecimalString: "210.250",
                timezoneIdentifier: "America/New_York",
                sourceName: "Apple Watch",
                sourceBundleID: "com.apple.health",
                sourceRevision: "26.0"
            )],
            deletions: []
        )
        XCTAssertEqual(response.accepted_added, 1)
    }

    func test_400_mapsToRejectedPermanent() async {
        let client = makeClient(auth: FakeAccessTokenProvider())
        StubProtocol.handler = { _ in (400, Data("batch_rejected".utf8)) }
        do {
            _ = try await client.submitBatch(added: [makeSample(1)], deletions: [])
            XCTFail("expected error")
        } catch BackendError.rejectedPermanent {
            // correct classification
        } catch {
            XCTFail("wrong error \(error)")
        }
    }

    func test_401_mapsToUnauthorized() async {
        let client = makeClient(auth: FakeAccessTokenProvider())
        StubProtocol.handler = { _ in (401, Data()) }
        do {
            _ = try await client.submitBatch(added: [], deletions: [])
            XCTFail("expected error")
        } catch BackendError.unauthorized {}
        catch { XCTFail("wrong error \(error)") }
    }

    func test_503_mapsToRetryable() async {
        let client = makeClient(auth: FakeAccessTokenProvider())
        StubProtocol.handler = { _ in (503, Data()) }
        do {
            _ = try await client.fetchSyncStatus()
            XCTFail("expected error")
        } catch BackendError.retryable {}
        catch { XCTFail("wrong error \(error)") }
    }

    func test_statusRequest_sendsBearerToken() async throws {
        let client = makeClient(auth: FakeAccessTokenProvider(token: "tok-9"))
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path, "/v1/health/sync-status")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer tok-9")
            return (200, Data("""
            {"record_count":3,"tombstone_count":1,"latest_sample":
             {"sample_uuid":"x","sample_start":"2026-08-21T07:12:00Z","value_kg":"72.000"},
             "last_ingested_at":"2026-08-22T00:00:00Z"}
            """.utf8))
        }
        let status = try await client.fetchSyncStatus()
        XCTAssertEqual(status.record_count, 3)
        XCTAssertEqual(status.tombstone_count, 1)
        XCTAssertEqual(status.latest_sample?.value_kg, "72.000")
    }

    func test_trainingAnalyticsRequestsUseAuthenticatedBoundedReadRoutes() async throws {
        let client = makeClient(auth: FakeAccessTokenProvider(token: "training-token"))
        var requestedPaths: [String] = []
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"),
                "Bearer training-token"
            )
            requestedPaths.append(request.url!.path)
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
                .queryItems ?? []
            switch request.url!.path {
            case "/v1/training/analytics/recent":
                XCTAssertEqual(query.first(where: { $0.name == "limit" })?.value, "20")
                return (200, Data(#"{"policy_version":"owner-training-analytics.v1","sessions":[]}"#.utf8))
            case "/v1/training/exercises":
                XCTAssertEqual(query.first(where: { $0.name == "as_of_date" })?.value, "2026-09-04")
                XCTAssertEqual(query.first(where: { $0.name == "timezone" })?.value, "America/New_York")
                XCTAssertEqual(query.first(where: { $0.name == "limit" })?.value, "200")
                return (200, Data(#"{"policy_version":"owner-training-analytics.v1","completeness":{"source_bootstrap_complete":true,"query_complete":true,"lifetime_guaranteed":false,"wording":"Analytics cover synced Hevy history; Hevy lifetime completeness is not guaranteed."},"exercises":[]}"#.utf8))
            default:
                XCTFail("unexpected route \(request.url!.path)")
                return (500, Data())
            }
        }

        _ = try await client.fetchRecentTrainingAnalytics(limit: 20)
        _ = try await client.fetchExerciseIndex(
            asOfDate: Date(timeIntervalSince1970: 1_788_537_600),
            timezone: "America/New_York",
            limit: 200
        )

        XCTAssertEqual(
            requestedPaths,
            ["/v1/training/analytics/recent", "/v1/training/exercises"]
        )
    }

    func test_hevySyncUsesAuthenticatedBodylessPostAndDecodesSafeSummary() async throws {
        let client = makeClient(auth: FakeAccessTokenProvider(token: "training-token"))
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/v1/training/hevy/sync")
            XCTAssertNil(request.url?.query)
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"),
                "Bearer training-token"
            )
            XCTAssertNil(request.value(forHTTPHeaderField: "api-key"))
            XCTAssertNil(request.httpBody)
            XCTAssertNil(request.httpBodyStream)
            return (200, Data(Self.hevySyncJSON.utf8))
        }

        let response = try await client.syncHevyTraining()

        XCTAssertEqual(response.status, "synced")
        XCTAssertEqual(response.mode, "incremental")
        XCTAssertEqual(response.sessionsCreated, 1)
        XCTAssertEqual(response.revisionsAppended, 0)
        XCTAssertEqual(response.deletionsRecorded, 0)
        XCTAssertFalse(response.hasMore)
        XCTAssertTrue(response.checkpointAdvanced)
    }

    func test_hevySyncClassifiesAuthenticationAndBackendFailures() async {
        let client = makeClient(auth: FakeAccessTokenProvider(token: "training-token"))

        StubProtocol.handler = { _ in (401, Data()) }
        do {
            _ = try await client.syncHevyTraining()
            XCTFail("expected unauthorized")
        } catch BackendError.unauthorized {
            // Expected.
        } catch {
            XCTFail("wrong error \(error)")
        }

        StubProtocol.handler = { _ in
            (503, Data(#"{"error":{"code":"hevy_unavailable","detail":"temporarily unavailable"}}"#.utf8))
        }
        do {
            _ = try await client.syncHevyTraining()
            XCTFail("expected retryable backend failure")
        } catch BackendError.retryableHTTP(let statusCode, let code, _) {
            XCTAssertEqual(statusCode, 503)
            XCTAssertEqual(code, "hevy_unavailable")
        } catch {
            XCTFail("wrong error \(error)")
        }
    }

    func test_trainingHistoryAndImmutableSessionDetailPreserveSourceIdentityAndDecimals() async throws {
        let client = makeClient(auth: FakeAccessTokenProvider(token: "training-token"))
        StubProtocol.handler = { request in
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
                .queryItems ?? []
            if request.url!.path == "/v1/training/exercises/fixture-row/history" {
                XCTAssertEqual(query.first(where: { $0.name == "source_system" })?.value, "hevy")
                XCTAssertEqual(query.first(where: { $0.name == "limit" })?.value, "50")
                return (200, Data(Self.trainingHistoryJSON.utf8))
            }
            XCTAssertEqual(request.url!.path, "/v1/training/sessions/revision-id")
            return (200, Data(Self.trainingDetailJSON.utf8))
        }

        let history = try await client.fetchExerciseHistory(
            sourceExerciseId: "fixture-row",
            sourceSystem: "hevy",
            asOfDate: Date(timeIntervalSince1970: 1_788_537_600),
            timezone: "UTC",
            limit: 50
        )
        let detail = try await client.fetchDetailedTrainingSession(revisionId: "revision-id")

        XCTAssertEqual(history.sourceExerciseId, "fixture-row")
        XCTAssertEqual(history.latest.topLoadSet?.loadKg, "36.28743275485118")
        XCTAssertEqual(history.coaching.policyVersion, "owner-training-coaching.v1")
        XCTAssertEqual(history.coaching.status, "unavailable")
        XCTAssertEqual(history.coaching.reasonCodes, ["insufficient_history"])
        XCTAssertEqual(detail.revisionId, "revision-id")
        XCTAssertEqual(detail.exercises[0].sets[0].load?.value, "36.28743275485118")
        XCTAssertEqual(detail.exercises[0].sets[0].rpe, "8.5")
    }

    func test_aiReviewUsesAuthenticatedPostWithDateAndTimezoneOnly() async throws {
        let client = makeClient(auth: FakeAccessTokenProvider(token: "review-token"))
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/v1/review/current")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"),
                "Bearer review-token"
            )
            let body = try! JSONSerialization.jsonObject(
                with: StubRequestBody.data(from: request)
            ) as! [String: Any]
            XCTAssertEqual(Set(body.keys), ["as_of_date", "timezone"])
            XCTAssertEqual(body["as_of_date"] as? String, "2026-09-08")
            XCTAssertEqual(body["timezone"] as? String, "America/New_York")
            return (200, Data(Self.aiReviewJSON.utf8))
        }

        let response = try await client.generateAIReview(
            asOfDate: Date(timeIntervalSince1970: 1_788_854_400),
            timezone: "America/New_York"
        )

        XCTAssertEqual(response.status, "unavailable")
        XCTAssertEqual(response.failureCode, "ai_not_configured")
        XCTAssertEqual(response.snapshot.todayRecorded.completeness, "partial")
    }

    private static let aiReviewJSON = #"""
    {
      "status":"unavailable","prompt_version":"owner-ai-review-prompt.v1",
      "snapshot":{"snapshot_version":"owner-ai-review-snapshot.v1",
        "as_of_date":"2026-09-08","timezone":"America/New_York",
        "goal":{"mode":"gain","band_status":"unavailable"},
        "targets":{"calories_kcal":"2200","calories_kind":"target",
          "protein_g":"120","protein_kind":"floor"},
        "today_recorded":{"item_count":1,"completeness":"partial",
          "calories_kcal":"450","protein_g":null,
          "reason_codes":["protein_unknown"],"authorities":["partial"]},
        "body_trend":{"status":"stale","latest_measurement_age_days":15,
          "represented_day_count":6,"coverage_span_days":18},
        "recorded_nutrition_progress":{"days_with_records_7d":2,
          "days_with_records_28d":4,"calorie_quantified_days_7d":1,
          "protein_quantified_days_7d":0,"includes_estimates_7d":true},
        "next_meal":{"status":"unavailable",
          "reason_codes":["nutrition_evidence_incomplete"]},
        "limitations":["recorded_events_do_not_prove_complete_intake"]},
      "review":null,"failure_code":"ai_not_configured",
      "authority_notice":"AI explanation; deterministic calculations remain authoritative."
    }
    """#

    private static let hevySyncJSON = #"""
    {
      "status":"synced","mode":"incremental","sessions_created":1,
      "revisions_appended":0,"deletions_recorded":0,"events_replayed":1,
      "tombstone_blocked":0,"pages_fetched":1,"has_more":false,
      "source_event_watermark":"2026-09-07T15:41:54Z",
      "logical_requests":1,"attempts_made":1,"retries":0,
      "checkpoint_advanced":true
    }
    """#

    private static let trainingHistoryJSON = #"""
    {
      "policy_version":"owner-training-analytics.v1",
      "source_system":"hevy","source_exercise_id":"fixture-row",
      "latest_display_name":"Cable Row",
      "history":[{"revision_id":"revision-id","source_session_id":"session-id",
        "source_revision":"source-revision","session_title":"Pull 1",
        "started_at":"2026-09-04T12:00:00Z","display_name":"Cable Row",
        "occurrence_count":1,"metric_family":"rep_load","recorded_set_count":1,
        "working_set_count":1,"warmup_set_count":0,"unsupported_set_count":0,
        "rep_total":10,"max_load_kg":"36.28743275485118",
        "top_load_set":{"set_identity":"0:0","set_index":0,"set_type":"normal",
          "reps":10,"load_kg":"36.28743275485118","rpe":"8.5"},
        "volume_kg_reps":"362.87432754851180","max_rpe":"8.5",
        "metric_completeness":"complete"}],
      "latest":{"revision_id":"revision-id","source_session_id":"session-id",
        "source_revision":"source-revision","session_title":"Pull 1",
        "started_at":"2026-09-04T12:00:00Z","display_name":"Cable Row",
        "occurrence_count":1,"metric_family":"rep_load","recorded_set_count":1,
        "working_set_count":1,"warmup_set_count":0,"unsupported_set_count":0,
        "rep_total":10,"max_load_kg":"36.28743275485118",
        "top_load_set":{"set_identity":"0:0","set_index":0,"set_type":"normal",
          "reps":10,"load_kg":"36.28743275485118","rpe":"8.5"},
        "volume_kg_reps":"362.87432754851180","max_rpe":"8.5",
        "metric_completeness":"complete"},
      "previous":null,
      "comparison":{"latest_revision_id":"revision-id","previous_revision_id":null,
        "working_set_count_delta":null,"top_load_delta_kg":null,
        "reps_at_same_top_load_delta":null,"volume_delta_kg_reps":null,
        "reason_codes":["no_previous_session"]},
      "frequency":{"timezone":"UTC","as_of_date":"2026-09-04",
        "sessions_last_7_days":1,"sessions_last_28_days":1,"days_since_last_performance":0},
      "pr_evidence":[],
      "completeness":{"source_bootstrap_complete":true,"query_complete":true,
        "lifetime_guaranteed":false,
        "wording":"PR within synced Hevy history; Hevy lifetime completeness is not guaranteed."},
      "coaching":{"policy_version":"owner-training-coaching.v1",
        "analytics_policy_version":"owner-training-analytics.v1",
        "timezone":"UTC","as_of_date":"2026-09-04","status":"unavailable",
        "action":null,"metric_family":"rep_load","latest_revision_id":"revision-id",
        "previous_revision_id":null,"latest_started_at":"2026-09-04T12:00:00Z",
        "previous_started_at":null,"target":null,
        "reason_codes":["insufficient_history"],"limitations":["advisory_only"]}
    }
    """#

    private static let trainingDetailJSON = #"""
    {
      "revision_id":"revision-id","source_system":"hevy",
      "source_session_id":"session-id","source_revision":"source-revision",
      "title":"Pull 1","description":null,"routine_id":null,
      "started_at":"2026-09-04T12:00:00Z","ended_at":"2026-09-04T13:00:00Z",
      "source_created_at":"2026-09-04T12:00:00Z",
      "source_updated_at":"2026-09-04T13:00:00Z",
      "parser_version":"hevy-public-api.v1","source_payload_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "ingested_at":"2026-09-04T13:01:00Z",
      "exercises":[{"occurrence_identity":"fixture-row:0",
        "source_exercise_id":"fixture-row","display_name":"Cable Row",
        "exercise_order":0,"notes":null,"superset_id":null,
        "sets":[{"set_identity":"0:0","source_set_id":"set-id","set_index":0,
          "set_type":"normal","reps":10,
          "load":{"value":"36.28743275485118","unit":"kg"},
          "distance":null,"duration_seconds":null,"rpe":"8.5","custom_metric":null}]}]
    }
    """#
}
