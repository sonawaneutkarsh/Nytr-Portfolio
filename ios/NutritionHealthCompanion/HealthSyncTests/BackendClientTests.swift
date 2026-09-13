import XCTest

@testable import NutritionHealthCompanion

/// BackendClient behavior against a URLProtocol stub: URL/method/auth-header/
/// batch-shape, error classification. (401-refresh-retry lives in the
/// AccessTokenProvider; here we verify the client surfaces .unauthorized.)
final class BackendClientTests: XCTestCase {
    private final class StubProtocol: URLProtocol {
        nonisolated(unsafe) static var handler: ((URLRequest) -> (Int, Data))? = nil
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
            valueKgDecimalString: "68.039",
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
            let body =
                request.httpBody ?? request.httpBodyStream.map { stream in
                    var data = Data()
                    stream.open()
                    defer { stream.close() }
                    let size = 8192
                    let buf = UnsafeMutablePointer<UInt8>.allocate(capacity: size)
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
            XCTAssertEqual(added[0]["value"] as? String, "68.039")
            XCTAssertEqual((added[0]["sample_uuid"] as! String).count, 36)
            XCTAssertTrue((added[0]["sample_start"] as! String).hasSuffix("Z"))
            return (
                200,
                Data(
                    """
                    {"accepted_added":1,"duplicate_added":0,"applied_deletions":0,
                     "duplicate_deletions":0,"ingested_at":"2026-08-25T00:00:00Z",
                     "latest_sample":null}
                    """.utf8)
            )
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
            let json =
                try! JSONSerialization.jsonObject(
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
            return (
                200,
                Data(
                    """
                    {"accepted_added":1,"duplicate_added":0,"applied_deletions":0,
                     "duplicate_deletions":0}
                    """.utf8)
            )
        }

        let response = try await client.submitWorkoutBatch(
            added: [
                WorkoutSampleDTO(
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
                )
            ],
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
        } catch BackendError.unauthorized {} catch { XCTFail("wrong error \(error)") }
    }

    func test_503_mapsToRetryable() async {
        let client = makeClient(auth: FakeAccessTokenProvider())
        StubProtocol.handler = { _ in (503, Data()) }
        do {
            _ = try await client.fetchSyncStatus()
            XCTFail("expected error")
        } catch BackendError.retryable {} catch { XCTFail("wrong error \(error)") }
    }

    func test_statusRequest_sendsBearerToken() async throws {
        let client = makeClient(auth: FakeAccessTokenProvider(token: "tok-9"))
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.url?.path, "/v1/health/sync-status")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer tok-9")
            return (
                200,
                Data(
                    """
                    {"record_count":3,"tombstone_count":1,"latest_sample":
                     {"sample_uuid":"x","sample_start":"2026-08-21T07:12:00Z","value_kg":"68.039"},
                     "last_ingested_at":"2026-08-22T00:00:00Z"}
                    """.utf8)
            )
        }
        let status = try await client.fetchSyncStatus()
        XCTAssertEqual(status.record_count, 3)
        XCTAssertEqual(status.tombstone_count, 1)
        XCTAssertEqual(status.latest_sample?.value_kg, "68.039")
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
            let query =
                URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
                .queryItems ?? []
            switch request.url!.path {
            case "/v1/training/analytics/recent":
                XCTAssertEqual(query.first(where: { $0.name == "limit" })?.value, "20")
                return (200, Data(#"{"policy_version":"owner-training-analytics.v1","sessions":[]}"#.utf8))
            case "/v1/training/exercises":
                XCTAssertEqual(query.first(where: { $0.name == "as_of_date" })?.value, "2026-09-04")
                XCTAssertEqual(query.first(where: { $0.name == "timezone" })?.value, "America/New_York")
                XCTAssertEqual(query.first(where: { $0.name == "limit" })?.value, "200")
                return (
                    200,
                    Data(
                        #"{"policy_version":"owner-training-analytics.v1","completeness":{"source_bootstrap_complete":true,"query_complete":true,"lifetime_guaranteed":false,"wording":"Analytics cover synced Hevy history; Hevy lifetime completeness is not guaranteed."},"exercises":[]}"#
                            .utf8)
                )
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
            let query =
                URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
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
            let body =
                try! JSONSerialization.jsonObject(
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

    func test_bodyGoalsClientUsesAuthenticatedOwnerRoutesAndExplicitDecision() async throws {
        let client = makeClient(auth: FakeAccessTokenProvider(token: "body-goals-token"))
        let proposalId = UUID(uuidString: "00000000-0000-0000-0000-000000000004")!
        let correctionId = UUID(uuidString: "00000000-0000-0000-0000-000000000003")!
        var paths: [String] = []
        StubProtocol.handler = { request in
            paths.append(request.url!.path)
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"), "Bearer body-goals-token"
            )
            switch request.url!.path {
            case "/v1/body-goals":
                XCTAssertEqual(request.httpMethod, "GET")
                return (200, Data(Self.bodyGoalsJSON.utf8))
            case "/v1/body-goals/profile":
                let body =
                    try! JSONSerialization.jsonObject(
                        with: StubRequestBody.data(from: request)
                    ) as! [String: Any]
                XCTAssertEqual(
                    Set(body.keys),
                    [
                        "height_cm", "date_of_birth", "formula_sex", "activity_level",
                    ])
                return (201, Data(Self.bodyGoalProfileJSON.utf8))
            case "/v1/body-goals/waist":
                let body =
                    try! JSONSerialization.jsonObject(
                        with: StubRequestBody.data(from: request)
                    ) as! [String: Any]
                XCTAssertEqual(body["unit"] as? String, "in")
                XCTAssertEqual(body["corrects_measurement_id"] as? String, correctionId.uuidString.lowercased())
                return (201, Data(Self.waistMeasurementJSON.utf8))
            case "/v1/body-goals/starting-target/proposals":
                XCTAssertEqual(request.httpMethod, "POST")
                return (201, Data(Self.startingProposalJSON.utf8))
            case "/v1/body-goals/starting-target/proposals/\(proposalId.uuidString.lowercased())/decision":
                let body =
                    try! JSONSerialization.jsonObject(
                        with: StubRequestBody.data(from: request)
                    ) as! [String: Any]
                XCTAssertEqual(body["decision"] as? String, "approved")
                XCTAssertNotNil(body["client_event_id"] as? String)
                return (201, Data(Self.startingDecisionJSON.utf8))
            default:
                XCTFail("unexpected path \(request.url!.path)")
                return (404, Data())
            }
        }

        let now = Date(timeIntervalSince1970: 1_789_000_000)
        let summary = try await client.fetchBodyGoals(
            asOfDate: now, timezone: "America/New_York"
        )
        XCTAssertEqual(summary.weight?.authority, "healthkit")
        _ = try await client.saveBodyGoalProfile(
            .init(
                heightCm: "175", dateOfBirth: "1995-01-01", formulaSex: "male",
                activityLevel: "lightly_active", targetWeightKg: nil
            ))
        _ = try await client.addWaistMeasurement(
            .init(
                value: "32", unit: "in", measuredAt: WireDate.string(from: now),
                correctsMeasurementId: correctionId
            ))
        _ = try await client.generateStartingCalorieProposal(
            asOfDate: now, timezone: "America/New_York"
        )
        _ = try await client.decideStartingCalorieProposal(
            proposalId: proposalId, decision: "approved", clientEventId: UUID()
        )
        XCTAssertEqual(paths.count, 5)
    }

    func testSnapshotAndFoodPreviewUseAuthenticatedReadOnlyContracts() async throws {
        let client = makeClient(auth: FakeAccessTokenProvider(token: "fixture-token"))
        let legacy = try JSONSerialization.jsonObject(with: Data(Self.aiReviewJSON.utf8)) as! [String: Any]
        let snapshot = try JSONSerialization.data(withJSONObject: [
            "snapshot": legacy["snapshot"]!,
            "model_input": [
                "goal": "gain", "weight_evidence": "stale", "nutrition_evidence": "recorded_partial",
                "calorie_target_available": true, "protein_target_available": true, "next_meal": "unavailable",
                "includes_estimates": true, "quality_flags": ["selected_sodium_mg_high"],
                "limitation": "Recorded evidence only.",
            ],
        ])
        StubProtocol.handler = { request in
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer fixture-token")
            if request.url?.path == "/v1/review/snapshot" {
                XCTAssertEqual(request.httpMethod, "GET")
                XCTAssertNil(request.httpBody)
                return (200, snapshot)
            }
            XCTAssertEqual(request.url?.path, "/v1/nutrition/food-preview")
            XCTAssertEqual(request.httpMethod, "POST")
            let body = try! JSONSerialization.jsonObject(with: StubRequestBody.data(from: request)) as! [String: Any]
            XCTAssertEqual(body["amount"] as? String, "1.5")
            XCTAssertEqual(body["unit"] as? String, "servings")
            return (
                200,
                Data(
                    #"{"consumed_amount":"45","consumed_unit":"g","portion_factor":"1.5","nutrition":{"calories_kcal":"180","protein_g":"37.5","carbohydrate_g":null,"total_fat_g":null,"fiber_g":null,"sodium_mg":null}}"#
                        .utf8)
            )
        }
        let review = try await client.fetchReviewSnapshot(asOfDate: Date(), timezone: "America/New_York")
        XCTAssertEqual(review.modelInput.qualityFlags, ["selected_sodium_mg_high"])
        let input = try JSONSerialization.jsonObject(with: JSONEncoder().encode(review.modelInput)) as! [String: Any]
        XCTAssertEqual(
            Set(input.keys),
            [
                "goal", "weight_evidence", "nutrition_evidence", "calorie_target_available", "protein_target_available",
                "next_meal", "includes_estimates", "quality_flags", "limitation",
            ])
        let preview = try await client.previewFood(
            FoodPreviewRequest(foodId: UUID(), foodVersionId: UUID(), amount: "1.5", unit: "servings"))
        XCTAssertEqual(preview.consumedAmount, "45")
        XCTAssertEqual(preview.nutrition.proteinG, "37.5")
        XCTAssertNil(preview.nutrition.fiberG)
    }

    func testManualFoodCorrectionAndVoidUseAppendOnlyMutationContracts() async throws {
        let client = makeClient(auth: FakeAccessTokenProvider(token: "fixture-token"))
        let entryId = UUID(uuidString: "00000000-0000-0000-0000-000000000501")!
        let eventId = UUID(uuidString: "00000000-0000-0000-0000-000000000502")!
        var requests: [(String, [String: Any])] = []
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"), "Bearer fixture-token"
            )
            let body = try! JSONSerialization.jsonObject(
                with: StubRequestBody.data(from: request)
            ) as! [String: Any]
            requests.append((request.url!.path, body))
            if request.url!.path.hasSuffix("preview-correction") {
                return (
                    200,
                    Data(
                        #"{"consumed_amount":"1.5","consumed_unit":"serving","portion_factor":"1.5","nutrition":{"calories_kcal":"600","protein_g":"30","carbohydrate_g":null,"total_fat_g":null,"fiber_g":null,"sodium_mg":null}}"#.utf8
                    )
                )
            }
            let kind = request.url!.path.hasSuffix("/void") ? "void" : "correction"
            let replacement = kind == "void" ? "null" : "\"00000000-0000-0000-0000-000000000503\""
            let responseJSON =
                "{\"adjustment_id\":\"00000000-0000-0000-0000-000000000504\","
                + "\"client_event_id\":\"\(eventId.uuidString)\","
                + "\"superseded_entry_id\":\"\(entryId.uuidString)\","
                + "\"replacement_entry_id\":\(replacement),\"kind\":\"\(kind)\","
                + "\"recorded_at\":\"2026-09-12T12:00:00Z\","
                + "\"replacement\":null,\"created\":true}"
            return (
                201,
                Data(responseJSON.utf8)
            )
        }
        let preview = try await client.previewManualFoodCorrection(
            entryId: entryId, amount: "1.5", unit: "serving"
        )
        XCTAssertEqual(preview.nutrition.caloriesKcal, "600")
        _ = try await client.correctManualFood(
            entryId: entryId,
            request: .init(amount: "1.5", unit: "serving", clientEventId: eventId)
        )
        _ = try await client.voidManualFood(entryId: entryId, clientEventId: eventId)
        XCTAssertEqual(requests.count, 3)
        XCTAssertEqual(requests[1].1["client_event_id"] as? String, eventId.uuidString)
        XCTAssertEqual(requests[2].1["client_event_id"] as? String, eventId.uuidString)
    }

    private static let aiReviewJSON = #"""
        {
          "status":"unavailable","prompt_version":"owner-ai-review-prompt.v2",
          "snapshot":{"snapshot_version":"owner-ai-review-snapshot.v2",
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

    private static let bodyGoalsJSON = #"""
        {"weight":{"value_kg":"70","measured_at":"2026-09-12T12:00:00Z","age_days":0,
          "authority":"healthkit"},"profile":null,
          "waist":{"latest":null,"history":[],"trend":{"policy_version":"owner-waist-trend.v1",
            "status":"no_data","latest_measurement_age_days":null,"represented_day_count":0,
            "coverage_span_days":0,"weekly_rate_cm":null}},
          "goal":null,"approved_targets":{"calories_kcal":null,"protein_g":null},
          "starting_calorie_proposal":null,
          "phase_assessment":{"policy_version":"owner-phase-assessment.v1","status":"unavailable",
            "reason_codes":["goal_direction_unavailable"],"recommendation_only":true}}
        """#

    private static let bodyGoalProfileJSON = #"""
        {"profile_id":"00000000-0000-0000-0000-000000000001","height_cm":"175",
          "date_of_birth":"1995-01-01","formula_sex":"male","activity_level":"lightly_active",
          "target_weight_kg":null,"provenance":"owner_entered"}
        """#

    private static let waistMeasurementJSON = #"""
        {"measurement_id":"00000000-0000-0000-0000-000000000002",
          "measured_at":"2026-09-12T12:00:00Z","value_cm":"81.28","entered_value":"32",
          "entered_unit":"in","corrects_measurement_id":"00000000-0000-0000-0000-000000000003"}
        """#

    private static let startingProposalJSON = #"""
        {"proposal_id":"00000000-0000-0000-0000-000000000004",
          "policy_version":"mifflin-st-jeor-starting-target.v1","maintenance_kcal":"2400",
          "goal_adjustment_kcal":"200","proposed_calorie_kcal":"2600","is_estimate":true,
          "requires_explicit_approval":true,"decision_status":"pending"}
        """#

    private static let startingDecisionJSON = #"""
        {"decision":"approved",
          "resulting_target_policy_version_id":"00000000-0000-0000-0000-000000000005"}
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
