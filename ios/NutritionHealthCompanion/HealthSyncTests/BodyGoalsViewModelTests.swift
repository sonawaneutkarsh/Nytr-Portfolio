import XCTest

@testable import NutritionHealthCompanion

@MainActor
final class BodyGoalsViewModelTests: XCTestCase {
    private final class Backend: BackendClient, @unchecked Sendable {
        var summaries: [Result<BodyGoalsResponse, Error>] = []
        var proposal: Result<StartingCalorieProposalDTO, Error> = .success(
            BodyGoalsViewModelTests.sampleProposal)
        var decision: Result<StartingTargetDecisionResponse, Error> = .success(
            .init(decision: "approved", resultingTargetPolicyVersionId: UUID(int: 8)))
        private(set) var fetchCount = 0
        private(set) var decisions: [(UUID, String, UUID)] = []
        private(set) var profiles: [SaveBodyGoalProfileRequest] = []
        private(set) var waists: [AddWaistRequest] = []

        func submitBatch(added _: [BodyMassSampleDTO], deletions _: [DeletedSampleDTO])
            async throws -> SyncResponse
        { throw BackendError.retryable("unused") }
        func fetchSyncStatus() async throws -> StatusResponse {
            throw BackendError.retryable("unused")
        }
        func fetchBodyGoals(asOfDate _: Date, timezone _: String) async throws
            -> BodyGoalsResponse
        {
            fetchCount += 1
            return try summaries.removeFirst().get()
        }
        func generateStartingCalorieProposal(asOfDate _: Date, timezone _: String)
            async throws -> StartingCalorieProposalDTO
        { try proposal.get() }
        func saveBodyGoalProfile(_ request: SaveBodyGoalProfileRequest) async throws
            -> BodyGoalProfileDTO
        {
            profiles.append(request)
            return .init(
                profileId: UUID(int: 9), heightCm: request.heightCm,
                dateOfBirth: request.dateOfBirth, formulaSex: request.formulaSex,
                activityLevel: request.activityLevel,
                targetWeightKg: request.targetWeightKg, provenance: "owner_entered"
            )
        }
        func addWaistMeasurement(_ request: AddWaistRequest) async throws
            -> WaistMeasurementDTO
        {
            waists.append(request)
            return .init(
                measurementId: UUID(int: 10), measuredAt: Date(), valueCm: request.value,
                enteredValue: request.value, enteredUnit: request.unit,
                correctsMeasurementId: request.correctsMeasurementId
            )
        }
        func decideStartingCalorieProposal(
            proposalId: UUID, decision: String, clientEventId: UUID
        ) async throws -> StartingTargetDecisionResponse {
            decisions.append((proposalId, decision, clientEventId))
            return try self.decision.get()
        }
    }

    func testActivationLoadsHealthKitAuthorityAndUnavailablePhase() async {
        let backend = Backend()
        backend.summaries = [.success(Self.summary)]
        let viewModel = BodyGoalsViewModel(backend: backend)
        await viewModel.activate(subject: "owner")
        guard case .ready(let response) = viewModel.phase else {
            return XCTFail("expected ready")
        }
        XCTAssertEqual(response.weight?.authority, "healthkit")
        XCTAssertEqual(response.phaseAssessment.status, "unavailable")
        XCTAssertEqual(backend.fetchCount, 1)
    }

    func testProposalApprovalIsExplicitAndRefreshesTargets() async {
        let backend = Backend()
        backend.summaries = [.success(Self.summary), .success(Self.summary)]
        var targetRefreshes = 0
        let event = UUID(int: 7)
        let viewModel = BodyGoalsViewModel(
            backend: backend,
            eventId: { event },
            onTargetsChanged: { targetRefreshes += 1 }
        )
        await viewModel.activate(subject: "owner")
        XCTAssertTrue(backend.decisions.isEmpty)
        await viewModel.decideStartingTarget("approved")
        XCTAssertEqual(backend.decisions.count, 1)
        XCTAssertEqual(backend.decisions[0].1, "approved")
        XCTAssertEqual(backend.decisions[0].2, event)
        XCTAssertEqual(targetRefreshes, 1)
    }

    func testFirstTimeProfileAndWaistEntryAreExplicitAndRefreshHistory() async {
        let backend = Backend()
        backend.summaries = [
            .success(Self.summary), .success(Self.summary), .success(Self.summary),
        ]
        let viewModel = BodyGoalsViewModel(
            backend: backend,
            requestDate: { Date(timeIntervalSince1970: 1_789_000_000) },
            timezone: { "America/New_York" }
        )
        await viewModel.activate(subject: "owner")

        await viewModel.saveProfile(
            heightCm: "175", dateOfBirth: Date(timeIntervalSince1970: 788_918_400),
            formulaSex: "male", activityLevel: "moderately_active", targetWeightKg: "75"
        )
        XCTAssertEqual(backend.profiles.count, 1)
        XCTAssertEqual(backend.profiles[0].heightCm, "175")
        XCTAssertEqual(backend.profiles[0].activityLevel, "moderately_active")

        let correction = UUID(int: 11)
        await viewModel.addWaist(
            value: "32", unit: "in", measuredAt: Date(timeIntervalSince1970: 1_789_000_000),
            correcting: correction
        )
        XCTAssertEqual(backend.waists.count, 1)
        XCTAssertEqual(backend.waists[0].unit, "in")
        XCTAssertEqual(backend.waists[0].correctsMeasurementId, correction)
        XCTAssertEqual(backend.fetchCount, 3)
    }

    private static let sampleProposal = StartingCalorieProposalDTO(
        proposalId: UUID(int: 4), policyVersion: "mifflin-st-jeor-starting-target.v1",
        maintenanceKcal: "2400", goalAdjustmentKcal: "200",
        proposedCalorieKcal: "2600", isEstimate: true,
        requiresExplicitApproval: true, decisionStatus: "pending"
    )

    static let summary = BodyGoalsResponse(
        weight: .init(
            valueKg: "70", measuredAt: Date(timeIntervalSince1970: 1_789_000_000),
            ageDays: 0, authority: "healthkit"
        ),
        profile: nil,
        waist: .init(
            latest: nil, history: [],
            trend: .init(
                policyVersion: "owner-waist-trend.v1", status: "no_data",
                latestMeasurementAgeDays: nil, representedDayCount: 0,
                coverageSpanDays: 0, weeklyRateCm: nil
            )
        ),
        goal: .init(direction: "gain", desiredRateKgPerWeek: "0.25"),
        approvedTargets: .init(caloriesKcal: nil, proteinG: nil),
        startingCalorieProposal: sampleProposal,
        phaseAssessment: .init(
            policyVersion: "owner-phase-assessment.v1", status: "unavailable",
            reasonCodes: ["insufficient_weight_or_waist_evidence"], recommendationOnly: true
        )
    )
}

extension UUID {
    fileprivate init(int: UInt64) {
        self.init(uuidString: String(format: "00000000-0000-0000-0000-%012llu", int))!
    }
}
