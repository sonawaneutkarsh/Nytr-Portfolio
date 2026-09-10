import XCTest
@testable import NutritionHealthCompanion

final class TargetReviewClientTests: XCTestCase {
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
    private let reviewId = UUID(uuidString: "10000000-0000-0000-0000-000000000001")!
    private let eventId = UUID(uuidString: "20000000-0000-0000-0000-000000000001")!

    override func tearDown() {
        StubProtocol.handler = nil
        super.tearDown()
    }

    func test_goalEndpointsPreserveLosslessRateAndExactAuthenticatedPayload() async throws {
        var calls = 0
        StubProtocol.handler = { request in
            calls += 1
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer m10-token")
            if request.httpMethod == "GET" {
                XCTAssertEqual(request.url?.path, "/v1/goal-policies/latest")
            } else {
                XCTAssertEqual(request.httpMethod, "POST")
                XCTAssertEqual(request.url?.path, "/v1/goal-policies")
                let body = try! JSONSerialization.jsonObject(with: StubRequestBody.data(from: request)) as! [String: Any]
                XCTAssertEqual(Set(body.keys), Set([
                    "policy_version", "direction", "desired_rate_kg_per_week",
                ]))
                XCTAssertEqual(body["desired_rate_kg_per_week"] as? String, "-0.250000")
                XCTAssertEqual(body["direction"] as? String, "lose")
            }
            return (request.httpMethod == "GET" ? 200 : 201, Self.goalPayload)
        }

        let client = makeClient()
        let latest = try await client.fetchLatestGoalPolicy()
        let created = try await client.createGoalPolicy(
            policyVersion: "owner-goal-v1",
            direction: .lose,
            desiredRateKgPerWeek: "-0.250000"
        )

        XCTAssertEqual(calls, 2)
        XCTAssertEqual(latest?.desiredRateKgPerWeek, "-0.250000")
        XCTAssertEqual(created.direction, .lose)
    }

    func test_initialTargetApprovalUsesExactOwnerAuthoredValues() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer m10-token")
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/v1/target-policies")
            let body = try! JSONSerialization.jsonObject(
                with: StubRequestBody.data(from: request)) as! [String: Any]
            XCTAssertEqual(Set(body.keys), Set(["policy_version", "rationale", "goals"]))
            XCTAssertEqual(body["policy_version"] as? String, "owner-target-v1")
            XCTAssertEqual(body["rationale"] as? String, "owner selected baseline")
            let goals = body["goals"] as! [[String: String]]
            XCTAssertEqual(goals, [[
                "nutrient": "calories_kcal",
                "kind": "target",
                "value": "2450.000000",
                "weight": "1.250000",
            ]])
            return (200, Self.targetApprovalPayload)
        }

        let result = try await makeClient().approveInitialCalorieTarget(
            policyVersion: "owner-target-v1",
            caloriesKcal: "2450.000000",
            calorieWeight: "1.250000",
            rationale: "owner selected baseline"
        )

        XCTAssertEqual(result.policyVersion, "owner-target-v1")
        XCTAssertEqual(
            result.payloadSha256,
            "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        )
    }

    func test_reviewAndDecisionUseOnlyAuthoritativeIdentifiersAndLosslessStrings() async throws {
        var calls = 0
        StubProtocol.handler = { request in
            calls += 1
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer m10-token")
            let body = try! JSONSerialization.jsonObject(with: StubRequestBody.data(from: request)) as! [String: Any]
            if request.url?.path == "/v1/target-reviews" {
                XCTAssertEqual(Set(body.keys), Set(["as_of_date", "timezone"]))
                XCTAssertEqual(body["as_of_date"] as? String, "2026-08-28")
                XCTAssertEqual(body["timezone"] as? String, "America/New_York")
                return (201, Self.reviewPayload(status: "recommendation_ready"))
            }
            XCTAssertEqual(
                request.url?.path,
                "/v1/target-reviews/\(self.reviewId.uuidString.lowercased())/decision"
            )
            XCTAssertEqual(Set(body.keys), Set(["decision", "idempotency_key"]))
            XCTAssertEqual(body["decision"] as? String, "approved")
            XCTAssertEqual(body["idempotency_key"] as? String, self.eventId.uuidString.lowercased())
            return (201, Self.decisionPayload(decision: "approved"))
        }

        let client = makeClient()
        let review = try await client.createTargetReview(
            asOfDate: day, timezone: "America/New_York")
        guard case .review(let detail) = review else {
            XCTFail("expected review")
            return
        }
        XCTAssertEqual(detail.targetPolicy.currentCaloriesKcal, "2500.000000")
        XCTAssertEqual(detail.targetPolicy.proposedCaloriesKcal, "2600.000000")
        XCTAssertEqual(detail.targetPolicy.calorieDelta, "100.000000")

        let result = try await client.decideTargetReview(
            reviewId: reviewId, decision: .approved, idempotencyKey: eventId)
        XCTAssertEqual(result.decision, .approved)
        XCTAssertEqual(calls, 2)
    }

    func test_typedUnavailableStatesMalformedProposalAndConflictFailClosed() async throws {
        let client = makeClient()
        for (state, expected) in [
            ("no_goal_policy", TargetReviewResponse.noGoalPolicy),
            ("no_target_policy", TargetReviewResponse.noTargetPolicy),
        ] {
            StubProtocol.handler = { _ in (200, Data("{\"state\":\"\(state)\"}".utf8)) }
            let response = try await client.createTargetReview(asOfDate: day, timezone: "UTC")
            XCTAssertEqual(response, expected)
        }

        StubProtocol.handler = { _ in
            (200, Self.reviewPayload(status: "recommendation_ready", includeProposal: false))
        }
        do {
            _ = try await client.createTargetReview(asOfDate: day, timezone: "UTC")
            XCTFail("inconsistent review should fail")
        } catch BackendError.retryable {
            // Malformed success payloads fail closed.
        }

        StubProtocol.handler = { _ in
            (409, Data("{\"error\":{\"code\":\"target_review_stale\",\"detail\":\"refresh\"}}".utf8))
        }
        do {
            _ = try await client.decideTargetReview(
                reviewId: reviewId, decision: .approved, idempotencyKey: eventId)
            XCTFail("conflict should be preserved")
        } catch BackendError.rejected(let status, let code, _) {
            XCTAssertEqual(status, 409)
            XCTAssertEqual(code, "target_review_stale")
        }
    }

    func test_proteinProposalLatestAndGenerationUseAuthenticatedBackendEvidence() async throws {
        var calls = 0
        StubProtocol.handler = { request in
            calls += 1
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer m10-token")
            XCTAssertEqual(
                request.url?.path,
                request.httpMethod == "GET"
                    ? "/v1/target-policies/protein-proposals/latest"
                    : "/v1/target-policies/protein-proposals"
            )
            XCTAssertTrue(request.httpMethod == "GET" || request.httpMethod == "POST")
            return (request.httpMethod == "GET" ? 200 : 201, Self.proteinProposalPayload())
        }

        let client = makeClient()
        let latest = try await client.fetchLatestProteinProposal()
        let generated = try await client.generateProteinProposal()

        XCTAssertEqual(calls, 2)
        XCTAssertEqual(latest?.decisionStatus, nil)
        XCTAssertTrue(latest?.requiresExplicitApproval == true)
        XCTAssertEqual(latest?.targetKind, .floor)
        XCTAssertEqual(latest?.proposedProteinG, "116")
        XCTAssertEqual(latest?.bodyMassKg, "65.8")
        XCTAssertEqual(
            latest?.calculation.bodyMass.measuredAt,
            WireDate.date(fromISO8601: "2026-09-04T11:30:00Z")
        )
        XCTAssertEqual(generated.policyVersion, "owner-protein-target.v1")
        XCTAssertEqual(generated.provenance, "HealthKit body-mass sample persisted by iOS")
    }

    func test_proteinProposalApprovalAndRejectionSendOnlyDecisionIntent() async throws {
        var calls = 0
        StubProtocol.handler = { request in
            calls += 1
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(
                request.url?.path,
                "/v1/target-policies/protein-proposals/"
                    + "70000000-0000-0000-0000-000000000001/decision"
            )
            let body = try! JSONSerialization.jsonObject(
                with: StubRequestBody.data(from: request)) as! [String: Any]
            XCTAssertEqual(Set(body.keys), Set(["decision", "client_event_id", "rationale"]))
            XCTAssertEqual(
                body["client_event_id"] as? String,
                self.eventId.uuidString.lowercased()
            )
            XCTAssertNil(body["user_id"])
            XCTAssertNil(body["body_mass_kg"])
            XCTAssertNil(body["proposed_protein_g"])
            let decision = body["decision"] as! String
            return (201, Self.proteinDecisionPayload(decision: decision))
        }

        let client = makeClient()
        let approved = try await client.decideProteinProposal(
            proposalId: Self.proteinProposalId,
            decision: .approved,
            clientEventId: eventId,
            rationale: "Owner approved after review."
        )
        let rejected = try await client.decideProteinProposal(
            proposalId: Self.proteinProposalId,
            decision: .rejected,
            clientEventId: eventId,
            rationale: "Owner rejected after review."
        )

        XCTAssertEqual(calls, 2)
        XCTAssertEqual(approved.decision, .approved)
        XCTAssertNotNil(approved.resultingTargetPolicyVersionId)
        XCTAssertEqual(rejected.decision, .rejected)
        XCTAssertNil(rejected.resultingTargetPolicyVersionId)
    }

    func test_proteinProposalTerminalStatesAndAPIErrorsFailClosed() async throws {
        let client = makeClient()
        for status in ["pending", "approved", "rejected"] {
            StubProtocol.handler = { _ in (200, Self.proteinProposalPayload(status: status)) }
            let proposal = try await client.fetchLatestProteinProposal()
            XCTAssertEqual(proposal?.decisionStatus?.rawValue ?? "pending", status)
        }

        StubProtocol.handler = { _ in
            (404, Data("{\"error\":{\"code\":\"protein_proposal_not_found\"}}".utf8))
        }
        let missing = try await client.fetchLatestProteinProposal()
        XCTAssertNil(missing)

        StubProtocol.handler = { _ in
            (409, Data("{\"error\":{\"code\":\"protein_proposal_evidence_unavailable\",\"detail\":\"recent evidence required\"}}".utf8))
        }
        do {
            _ = try await client.generateProteinProposal()
            XCTFail("typed evidence failure should be preserved")
        } catch BackendError.rejected(let status, let code, _) {
            XCTAssertEqual(status, 409)
            XCTAssertEqual(code, "protein_proposal_evidence_unavailable")
        }

        StubProtocol.handler = { _ in
            (200, Self.proteinProposalPayload(status: "unexpected"))
        }
        do {
            _ = try await client.fetchLatestProteinProposal()
            XCTFail("unknown terminal state should fail closed")
        } catch BackendError.retryable {
            // Malformed success payloads never become an actionable proposal.
        }
    }

    private func makeClient() -> HTTPBackendClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        return HTTPBackendClient(
            baseURL: URL(string: "https://backend.example")!,
            auth: FakeAccessTokenProvider(token: "m10-token"),
            session: URLSession(configuration: config)
        )
    }

    private static let goalPayload = Data("""
    {"version_id":"30000000-0000-0000-0000-000000000001",
     "policy_version":"owner-goal-v1","direction":"lose",
     "desired_rate_kg_per_week":"-0.250000",
     "payload_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
     "created_at":"2026-08-28T12:00:00Z"}
    """.utf8)

    static let targetApprovalPayload = Data("""
    {"version_id":"40000000-0000-0000-0000-000000000001",
     "policy_version":"owner-target-v1",
     "payload_sha256":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
     "approved_at":"2026-08-28T12:00:00Z"}
    """.utf8)

    static let proteinProposalId = UUID(
        uuidString: "70000000-0000-0000-0000-000000000001")!

    static func proteinProposalPayload(status: String = "pending") -> Data {
        let requiresApproval = status == "pending" ? "true" : "false"
        let resultingTarget = status == "approved"
            ? "\"80000000-0000-0000-0000-000000000001\"" : "null"
        let decision = status == "pending" ? "null" : """
        {"client_event_id":"20000000-0000-0000-0000-000000000001",
         "decided_at":"2026-09-05T15:00:00Z",
         "decision_id":"90000000-0000-0000-0000-000000000001",
         "rationale":"Owner reviewed the evidence.",
         "resulting_target_policy_version_id":\(resultingTarget)}
        """
        return Data("""
        {"proposal_id":"70000000-0000-0000-0000-000000000001",
         "prior_target_policy_version_id":"40000000-0000-0000-0000-000000000001",
         "body_mass_sample_uuid":"71000000-0000-0000-0000-000000000001",
         "policy_version":"owner-protein-target.v1","target_kind":"floor",
         "body_mass_kg":"65.8","grams_per_pound":"0.8",
         "proposed_protein_g":"116",
         "evidence_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
         "calculation":{
          "body_mass":{"measured_at":"2026-09-04T11:30:00Z",
           "sample_uuid":"71000000-0000-0000-0000-000000000001","value_kg":"65.8"},
          "formula":"body_mass_kg / kilograms_per_pound * grams_per_pound",
          "grams_per_pound":"0.8","kilograms_per_pound":"0.45359237",
          "max_evidence_age_days":7,
          "prior_target_payload_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
          "prior_target_policy_version_id":"40000000-0000-0000-0000-000000000001",
          "proposed_protein_g":"116","rounding":"ROUND_HALF_EVEN",
          "rounding_quantum_g":"1","target_kind":"floor",
          "target_policy":"owner-protein-target.v1"},
         "rationale":"Deterministic protein floor proposal; not a medical diagnosis.",
         "provenance":"HealthKit body-mass sample persisted by iOS",
         "generated_at":"2026-09-05T14:00:00Z","decision_status":"\(status)",
         "requires_explicit_approval":\(requiresApproval),"decision":\(decision)}
        """.utf8)
    }

    static func proteinDecisionPayload(decision: String) -> Data {
        let resultingTarget = decision == "approved"
            ? "\"80000000-0000-0000-0000-000000000001\"" : "null"
        return Data("""
        {"created":true,
         "decision_id":"90000000-0000-0000-0000-000000000001",
         "proposal_id":"70000000-0000-0000-0000-000000000001",
         "decision":"\(decision)",
         "client_event_id":"20000000-0000-0000-0000-000000000001",
         "resulting_target_policy_version_id":\(resultingTarget),
         "decided_at":"2026-09-05T15:00:00Z"}
        """.utf8)
    }

    static func reviewPayload(
        status: String,
        includeProposal: Bool = true
    ) -> Data {
        let proposal = includeProposal ? "\"2600.000000\"" : "null"
        let delta = includeProposal ? "\"100.000000\"" : "null"
        return Data("""
        {"state":"review","created":true,
         "review_id":"10000000-0000-0000-0000-000000000001",
         "created_at":"2026-08-28T12:00:00Z","status":"\(status)",
         "reason_codes":["observed_below_band"],"as_of_date":"2026-08-28",
         "timezone":"America/New_York",
         "trend":{"status":"ready","algorithm_version":"body-mass-trend-v1",
          "input_digest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
          "weekly_rate_kg":"0.100000"},
         "goal_policy":{"version_id":"30000000-0000-0000-0000-000000000001",
          "policy_version":"owner-goal-v1","direction":"gain",
          "desired_rate_kg_per_week":"0.250000",
          "payload_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
          "created_at":"2026-08-01T12:00:00Z",
          "acceptable_rate_lower_kg_per_week":"0.150000",
          "acceptable_rate_upper_kg_per_week":"0.350000"},
         "target_policy":{"version_id":"40000000-0000-0000-0000-000000000001",
          "policy_version":"target-v1",
          "payload_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
          "approved_at":"2026-08-01T12:00:00Z",
          "current_calories_kcal":"2500.000000",
          "proposed_calories_kcal":\(proposal),"calorie_delta":\(delta)},
         "review_policy":{"policy_version":"target-review-v1",
          "deadband_kg_per_week":"0.10","adjustment_step_kcal":"100",
          "cooldown_days":14,"lower_calorie_bound":null,"upper_calorie_bound":null},
         "recommendation_digest":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"}
        """.utf8)
    }

    static func decisionPayload(decision: String) -> Data {
        let target = decision == "approved"
            ? "\"50000000-0000-0000-0000-000000000001\"" : "null"
        return Data("""
        {"created":true,
         "target_review_id":"10000000-0000-0000-0000-000000000001",
         "target_review_decision_id":"60000000-0000-0000-0000-000000000001",
         "decision":"\(decision)",
         "client_event_id":"20000000-0000-0000-0000-000000000001",
         "resulting_target_policy_id":\(target),"decided_at":"2026-08-28T13:00:00Z"}
        """.utf8)
    }
}
