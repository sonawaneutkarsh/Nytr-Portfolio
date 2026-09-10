import Foundation

protocol BackendClient: Sendable {
    /// Submits one batch. Resolves the bearer token via AccessTokenProvider.
    func submitBatch(
        added: [BodyMassSampleDTO], deletions: [DeletedSampleDTO]
    ) async throws -> SyncResponse

    func submitWorkoutBatch(
        added: [WorkoutSampleDTO], deletions: [DeletedWorkoutDTO]
    ) async throws -> WorkoutSyncResponse

    func fetchSyncStatus() async throws -> StatusResponse
    func fetchBodyMassTrend(asOfDate: Date, timezone: String) async throws
        -> BodyMassTrendResponse
    func fetchDailyNutritionLedger(date: Date, timezone: String) async throws
        -> DailyNutritionLedgerResponse
    func fetchNutritionHistory(endDate: Date, timezone: String) async throws
        -> NutritionHistory7DayResponse
    func fetchProgress(asOfDate: Date, timezone: String) async throws
        -> OwnerProgressResponse
    func generateAIReview(asOfDate: Date, timezone: String) async throws
        -> AIReviewResponse
    func fetchBodyGoals(asOfDate: Date, timezone: String) async throws -> BodyGoalsResponse
    func saveBodyProfile(heightCm: String, targetWeightKg: String?) async throws -> BodyProfileDTO
    func recordWaist(value: String, unit: String, measuredAt: Date) async throws -> WaistMeasurementDTO
    func fetchStartingCalorieEstimate(asOfDate: Date, timezone: String) async throws
        -> StartingCalorieEstimateDTO
    func fetchCustomFoods() async throws -> [CustomFoodVersionDTO]
    func createCustomFood(_ request: CreateCustomFoodRequestDTO) async throws
        -> CustomFoodVersionDTO
    func recordManualFood(_ request: ManualFoodConsumptionRequestDTO) async throws
        -> ManualFoodConsumptionResponse
    func lookupBarcodeFood(barcode: String) async throws -> BarcodeProductDTO
    func importBarcodeFood(barcode: String, expectedPayloadSha256: String) async throws
        -> ImportBarcodeFoodResponseDTO

    func fetchDayPlan(date: Date) async throws -> DayPlanResponse
    func generateDayPlan(date: Date, timezone: String) async throws -> DayPlanResponse
    func fetchLatestTargetPolicy() async throws -> TargetPolicyLatest?
    func approveInitialCalorieTarget(
        policyVersion: String,
        caloriesKcal: String,
        calorieWeight: String,
        rationale: String
    ) async throws -> TargetPolicyApprovalResponse
    func recordConsumption(
        runId: UUID,
        planVersionId: UUID,
        itemId: UUID,
        state: ConsumptionState,
        clientEventId: UUID?
    ) async throws -> ConsumptionEntryResponse
    func listConsumption(runId: UUID) async throws -> ConsumptionListResponse
    func fetchLatestGoalPolicy() async throws -> GoalPolicyResponse?
    func createGoalPolicy(
        policyVersion: String,
        direction: GoalDirectionDTO,
        desiredRateKgPerWeek: String
    ) async throws -> GoalPolicyResponse
    func createTargetReview(asOfDate: Date, timezone: String) async throws
        -> TargetReviewResponse
    func decideTargetReview(
        reviewId: UUID,
        decision: TargetReviewDecisionDTO,
        idempotencyKey: UUID
    ) async throws -> TargetReviewDecisionResponse
    func fetchLatestProteinProposal() async throws -> ProteinTargetProposalResponse?
    func generateProteinProposal() async throws -> ProteinTargetProposalResponse
    func decideProteinProposal(
        proposalId: UUID,
        decision: ProteinProposalDecisionDTO,
        clientEventId: UUID,
        rationale: String
    ) async throws -> ProteinProposalDecisionResponse
    func fetchLatestNextMeal() async throws -> NextMealRecommendationResponse
    func generateNextMeal(
        date: Date,
        timezone: String,
        clientRequestId: UUID
    ) async throws -> NextMealRecommendationResponse
    func fetchNextMealConsumption(recommendationId: UUID) async throws
        -> NextMealConsumptionResponse
    func recordNextMealConsumption(
        recommendationId: UUID,
        clientEventId: UUID
    ) async throws -> NextMealConsumptionResponse

    // MARK: - Hevy sync and M14C Training Analytics
    func syncHevyTraining() async throws -> HevySyncResponse
    func fetchRecentTrainingAnalytics(limit: Int) async throws
        -> TrainingAnalyticsRecentResponse
    func fetchExerciseIndex(asOfDate: Date, timezone: String, limit: Int) async throws
        -> ExerciseIndexResponse
    func fetchExerciseHistory(
        sourceExerciseId: String,
        sourceSystem: String,
        asOfDate: Date,
        timezone: String,
        limit: Int
    ) async throws -> ExerciseTrainingHistoryResponse
    func fetchDetailedTrainingSession(revisionId: String) async throws
        -> DetailedTrainingSessionResponse
}

/// M5 test doubles only exercise the frozen health surface. Defaults keep
/// those focused doubles source-compatible while new M7 doubles can override
/// just the methods they need.
extension BackendClient {
    func fetchCustomFoods() async throws -> [CustomFoodVersionDTO] {
        throw BackendError.retryable("manual-food API is not implemented by this client")
    }

    func createCustomFood(_ request: CreateCustomFoodRequestDTO) async throws
        -> CustomFoodVersionDTO
    {
        _ = request
        throw BackendError.retryable("manual-food API is not implemented by this client")
    }

    func recordManualFood(_ request: ManualFoodConsumptionRequestDTO) async throws
        -> ManualFoodConsumptionResponse
    {
        _ = request
        throw BackendError.retryable("manual-food API is not implemented by this client")
    }
    func lookupBarcodeFood(barcode: String) async throws -> BarcodeProductDTO {
        _ = barcode
        throw BackendError.retryable("barcode-food API is not implemented by this client")
    }
    func importBarcodeFood(barcode: String, expectedPayloadSha256: String) async throws
        -> ImportBarcodeFoodResponseDTO
    {
        _ = (barcode, expectedPayloadSha256)
        throw BackendError.retryable("barcode-food API is not implemented by this client")
    }
    func submitWorkoutBatch(
        added: [WorkoutSampleDTO], deletions: [DeletedWorkoutDTO]
    ) async throws -> WorkoutSyncResponse {
        _ = (added, deletions)
        throw BackendError.retryable("workout sync API is not implemented by this client")
    }

    func fetchBodyMassTrend(asOfDate: Date, timezone: String) async throws
        -> BodyMassTrendResponse
    {
        _ = (asOfDate, timezone)
        throw BackendError.retryable("body-mass trend API is not implemented by this client")
    }

    func fetchDailyNutritionLedger(date: Date, timezone: String) async throws
        -> DailyNutritionLedgerResponse
    {
        _ = (date, timezone)
        throw BackendError.retryable("daily nutrition ledger API is not implemented by this client")
    }

    func fetchNutritionHistory(endDate: Date, timezone: String) async throws
        -> NutritionHistory7DayResponse
    {
        _ = (endDate, timezone)
        throw BackendError.retryable("nutrition history API is not implemented by this client")
    }

    func fetchProgress(asOfDate: Date, timezone: String) async throws
        -> OwnerProgressResponse
    {
        _ = (asOfDate, timezone)
        throw BackendError.retryable("progress API is not implemented by this client")
    }

    func generateAIReview(asOfDate: Date, timezone: String) async throws
        -> AIReviewResponse
    {
        _ = (asOfDate, timezone)
        throw BackendError.retryable("AI review API is not implemented by this client")
    }

    func fetchBodyGoals(asOfDate: Date, timezone: String) async throws -> BodyGoalsResponse {
        _ = (asOfDate, timezone)
        throw BackendError.retryable("body-goals API is not implemented by this client")
    }
    func saveBodyProfile(heightCm: String, targetWeightKg: String?) async throws -> BodyProfileDTO {
        _ = (heightCm, targetWeightKg)
        throw BackendError.retryable("body-goals API is not implemented by this client")
    }
    func recordWaist(value: String, unit: String, measuredAt: Date) async throws -> WaistMeasurementDTO {
        _ = (value, unit, measuredAt)
        throw BackendError.retryable("body-goals API is not implemented by this client")
    }
    func fetchStartingCalorieEstimate(asOfDate: Date, timezone: String) async throws
        -> StartingCalorieEstimateDTO
    {
        _ = (asOfDate, timezone)
        throw BackendError.retryable("body-goals API is not implemented by this client")
    }

    func fetchDayPlan(date: Date) async throws -> DayPlanResponse {
        _ = date
        throw BackendError.retryable("daily-plan API is not implemented by this client")
    }

    func generateDayPlan(date: Date, timezone: String) async throws -> DayPlanResponse {
        _ = (date, timezone)
        throw BackendError.retryable("daily-plan API is not implemented by this client")
    }

    func fetchLatestTargetPolicy() async throws -> TargetPolicyLatest? {
        throw BackendError.retryable("target-policy API is not implemented by this client")
    }

    func approveInitialCalorieTarget(
        policyVersion: String,
        caloriesKcal: String,
        calorieWeight: String,
        rationale: String
    ) async throws -> TargetPolicyApprovalResponse {
        _ = (policyVersion, caloriesKcal, calorieWeight, rationale)
        throw BackendError.retryable("target-policy API is not implemented by this client")
    }

    func recordConsumption(
        runId: UUID,
        planVersionId: UUID,
        itemId: UUID,
        state: ConsumptionState,
        clientEventId: UUID?
    ) async throws -> ConsumptionEntryResponse {
        _ = (runId, planVersionId, itemId, state, clientEventId)
        throw BackendError.retryable("consumption API is not implemented by this client")
    }

    func listConsumption(runId: UUID) async throws -> ConsumptionListResponse {
        _ = runId
        throw BackendError.retryable("consumption API is not implemented by this client")
    }

    func fetchLatestGoalPolicy() async throws -> GoalPolicyResponse? {
        throw BackendError.retryable("goal-policy API is not implemented by this client")
    }

    func createGoalPolicy(
        policyVersion: String,
        direction: GoalDirectionDTO,
        desiredRateKgPerWeek: String
    ) async throws -> GoalPolicyResponse {
        _ = (policyVersion, direction, desiredRateKgPerWeek)
        throw BackendError.retryable("goal-policy API is not implemented by this client")
    }

    func createTargetReview(asOfDate: Date, timezone: String) async throws
        -> TargetReviewResponse
    {
        _ = (asOfDate, timezone)
        throw BackendError.retryable("target-review API is not implemented by this client")
    }

    func decideTargetReview(
        reviewId: UUID,
        decision: TargetReviewDecisionDTO,
        idempotencyKey: UUID
    ) async throws -> TargetReviewDecisionResponse {
        _ = (reviewId, decision, idempotencyKey)
        throw BackendError.retryable("target-review decision API is not implemented by this client")
    }

    func fetchLatestProteinProposal() async throws -> ProteinTargetProposalResponse? {
        throw BackendError.retryable("protein-proposal API is not implemented by this client")
    }

    func generateProteinProposal() async throws -> ProteinTargetProposalResponse {
        throw BackendError.retryable("protein-proposal API is not implemented by this client")
    }

    func decideProteinProposal(
        proposalId: UUID,
        decision: ProteinProposalDecisionDTO,
        clientEventId: UUID,
        rationale: String
    ) async throws -> ProteinProposalDecisionResponse {
        _ = (proposalId, decision, clientEventId, rationale)
        throw BackendError.retryable("protein-proposal decision API is not implemented by this client")
    }

    func fetchLatestNextMeal() async throws -> NextMealRecommendationResponse {
        throw BackendError.retryable("next-meal API is not implemented by this client")
    }

    func generateNextMeal(
        date: Date,
        timezone: String,
        clientRequestId: UUID
    ) async throws -> NextMealRecommendationResponse {
        _ = (date, timezone, clientRequestId)
        throw BackendError.retryable("next-meal API is not implemented by this client")
    }

    func fetchNextMealConsumption(recommendationId: UUID) async throws
        -> NextMealConsumptionResponse
    {
        _ = recommendationId
        throw BackendError.retryable(
            "next-meal consumption API is not implemented by this client"
        )
    }

    func recordNextMealConsumption(
        recommendationId: UUID,
        clientEventId: UUID
    ) async throws -> NextMealConsumptionResponse {
        _ = (recommendationId, clientEventId)
        throw BackendError.retryable(
            "next-meal consumption API is not implemented by this client"
        )
    }

    func fetchRecentTrainingAnalytics(limit: Int) async throws
        -> TrainingAnalyticsRecentResponse
    {
        _ = limit
        throw BackendError.retryable("training analytics API is not implemented by this client")
    }

    func syncHevyTraining() async throws -> HevySyncResponse {
        throw BackendError.retryable("Hevy sync API is not implemented by this client")
    }

    func fetchExerciseIndex(asOfDate: Date, timezone: String, limit: Int) async throws
        -> ExerciseIndexResponse
    {
        _ = (asOfDate, timezone, limit)
        throw BackendError.retryable("training analytics API is not implemented by this client")
    }

    func fetchExerciseHistory(
        sourceExerciseId: String,
        sourceSystem: String,
        asOfDate: Date,
        timezone: String,
        limit: Int
    ) async throws -> ExerciseTrainingHistoryResponse {
        _ = (sourceExerciseId, sourceSystem, asOfDate, timezone, limit)
        throw BackendError.retryable("training analytics API is not implemented by this client")
    }

    func fetchDetailedTrainingSession(revisionId: String) async throws
        -> DetailedTrainingSessionResponse
    {
        _ = revisionId
        throw BackendError.retryable("training detail API is not implemented by this client")
    }
}

/// URLSession implementation of the frozen /v1/health contract.
/// HTTPS-only (ATS defaults), 30 s timeout, NO transport-layer retries —
/// retry policy belongs to HealthSyncCoordinator.
struct HTTPBackendClient: BackendClient {
    private let baseURL: URL
    private let auth: AccessTokenProvider
    private let session: URLSession

    init(baseURL: URL, auth: AccessTokenProvider, session: URLSession? = nil) {
        self.baseURL = baseURL
        self.auth = auth
        if let session {
            self.session = session
        } else {
            let config = URLSessionConfiguration.default
            config.timeoutIntervalForRequest = 30
            self.session = URLSession(configuration: config)
        }
    }

    func submitBatch(
        added: [BodyMassSampleDTO], deletions: [DeletedSampleDTO]
    ) async throws -> SyncResponse {
        let payload = SyncRequestPayload(
            client_batch_id: UUID().uuidString.lowercased(),
            added: added.map { sample in
                .init(
                    sample_uuid: sample.sampleUUID.uuidString.lowercased(),
                    value: sample.valueKgDecimalString,
                    sample_start: WireDate.string(from: sample.sampleStart),
                    sample_end: WireDate.string(from: sample.sampleEnd),
                    source_name: sample.sourceName,
                    source_bundle_id: sample.sourceBundleID
                )
            },
            deleted: deletions.map { .init(sample_uuid: $0.sampleUUID.uuidString.lowercased()) }
        )
        return try await post(path: "v1/health/body-mass/sync", body: payload)
    }

    func submitWorkoutBatch(
        added: [WorkoutSampleDTO], deletions: [DeletedWorkoutDTO]
    ) async throws -> WorkoutSyncResponse {
        let payload = WorkoutSyncRequestPayload(
            client_batch_id: UUID().uuidString.lowercased(),
            added: added.map { sample in
                .init(
                    source_system: "healthkit",
                    source_record_id: sample.sourceRecordID.uuidString.lowercased(),
                    activity_type: sample.activityType,
                    started_at: WireDate.string(from: sample.startedAt),
                    ended_at: WireDate.string(from: sample.endedAt),
                    active_duration_seconds: sample.activeDurationSecondsDecimalString,
                    active_energy_kcal: sample.activeEnergyKcalDecimalString,
                    timezone_identifier: sample.timezoneIdentifier,
                    source_name: sample.sourceName,
                    source_bundle_id: sample.sourceBundleID,
                    source_revision: sample.sourceRevision
                )
            },
            deleted: deletions.map { deletion in
                .init(
                    source_system: "healthkit",
                    source_record_id: deletion.sourceRecordID.uuidString.lowercased()
                )
            }
        )
        return try await post(path: "v1/health/workouts/sync", body: payload)
    }

    func fetchSyncStatus() async throws -> StatusResponse {
        let token = try await auth.validAccessToken()
        var request = URLRequest(url: baseURL.appending(path: "v1/health/sync-status"))
        request.httpMethod = "GET"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        do {
            let (data, response) = try await session.data(for: request)
            return try Self.decode(StatusResponse.self, data: data, response: response)
        } catch let error as BackendError {
            throw error
        } catch is AuthProviderError {
            throw BackendError.unauthorized
        } catch {
            throw BackendError.retryable(String(describing: error))
        }
    }

    func fetchBodyMassTrend(asOfDate: Date, timezone: String) async throws
        -> BodyMassTrendResponse
    {
        try await requestM7(
            method: "GET",
            path: "v1/health/body-mass/trend",
            queryItems: [
                URLQueryItem(name: "as_of_date", value: WireDay.string(from: asOfDate)),
                URLQueryItem(name: "timezone", value: timezone),
            ],
            body: nil,
            successStatusCodes: [200]
        )
    }

    func fetchDailyNutritionLedger(date: Date, timezone: String) async throws
        -> DailyNutritionLedgerResponse
    {
        try await requestM7(
            method: "GET",
            path: "v1/nutrition/daily-ledger",
            queryItems: [
                URLQueryItem(name: "date", value: WireDay.string(from: date)),
                URLQueryItem(name: "timezone", value: timezone),
            ],
            body: nil,
            successStatusCodes: [200]
        )
    }

    func fetchNutritionHistory(endDate: Date, timezone: String) async throws
        -> NutritionHistory7DayResponse
    {
        try await requestM7(
            method: "GET",
            path: "v1/nutrition/history",
            queryItems: [
                URLQueryItem(name: "end_date", value: WireDay.string(from: endDate)),
                URLQueryItem(name: "timezone", value: timezone),
            ],
            body: nil,
            successStatusCodes: [200]
        )
    }

    func fetchProgress(asOfDate: Date, timezone: String) async throws
        -> OwnerProgressResponse
    {
        try await requestM7(
            method: "GET",
            path: "v1/analytics/progress",
            queryItems: [
                URLQueryItem(name: "as_of_date", value: WireDay.string(from: asOfDate)),
                URLQueryItem(name: "timezone", value: timezone),
            ],
            body: nil,
            successStatusCodes: [200]
        )
    }

    func generateAIReview(asOfDate: Date, timezone: String) async throws
        -> AIReviewResponse
    {
        let payload = GenerateAIReviewRequestDTO(
            asOfDate: WireDay.string(from: asOfDate),
            timezone: timezone
        )
        return try await requestM7(
            method: "POST",
            path: "v1/review/current",
            body: try JSONEncoder().encode(payload),
            successStatusCodes: [200]
        )
    }

    func fetchBodyGoals(asOfDate: Date, timezone: String) async throws -> BodyGoalsResponse {
        try await requestM7(
            method: "GET",
            path: "v1/body-goals",
            queryItems: [
                URLQueryItem(name: "as_of_date", value: WireDay.string(from: asOfDate)),
                URLQueryItem(name: "timezone", value: timezone),
            ],
            body: nil,
            successStatusCodes: [200]
        )
    }

    func saveBodyProfile(heightCm: String, targetWeightKg: String?) async throws -> BodyProfileDTO {
        let payload = BodyProfileRequestDTO(heightCm: heightCm, targetWeightKg: targetWeightKg)
        return try await requestM7(
            method: "PUT", path: "v1/body-goals/profile",
            body: try JSONEncoder().encode(payload), successStatusCodes: [200]
        )
    }

    func recordWaist(value: String, unit: String, measuredAt: Date) async throws -> WaistMeasurementDTO {
        let payload = WaistMeasurementRequestDTO(
            value: value, unit: unit, measuredAt: WireDate.iso8601UTC.string(from: measuredAt)
        )
        return try await requestM7(
            method: "POST", path: "v1/body-goals/waist",
            body: try JSONEncoder().encode(payload), successStatusCodes: [201]
        )
    }

    func fetchStartingCalorieEstimate(asOfDate: Date, timezone: String) async throws
        -> StartingCalorieEstimateDTO
    {
        let payload = StartingCalorieEstimateRequestDTO(
            asOfDate: WireDay.string(from: asOfDate), timezone: timezone
        )
        return try await requestM7(
            method: "POST", path: "v1/body-goals/starting-estimate",
            body: try JSONEncoder().encode(payload), successStatusCodes: [200]
        )
    }

    func fetchCustomFoods() async throws -> [CustomFoodVersionDTO] {
        let response: CustomFoodsResponse = try await requestM7(
            method: "GET", path: "v1/nutrition/custom-foods", body: nil,
            successStatusCodes: [200]
        )
        return response.foods
    }

    func createCustomFood(_ request: CreateCustomFoodRequestDTO) async throws
        -> CustomFoodVersionDTO
    {
        try await requestM7(
            method: "POST", path: "v1/nutrition/custom-foods",
            body: try JSONEncoder().encode(request), successStatusCodes: [201]
        )
    }

    func recordManualFood(_ request: ManualFoodConsumptionRequestDTO) async throws
        -> ManualFoodConsumptionResponse
    {
        try await requestM7(
            method: "POST", path: "v1/nutrition/manual-consumption",
            body: try JSONEncoder().encode(request), successStatusCodes: [200, 201]
        )
    }

    func lookupBarcodeFood(barcode: String) async throws -> BarcodeProductDTO {
        try await requestM7(
            method: "GET", path: "v1/nutrition/barcodes/\(barcode)", body: nil,
            successStatusCodes: [200]
        )
    }

    func importBarcodeFood(barcode: String, expectedPayloadSha256: String) async throws
        -> ImportBarcodeFoodResponseDTO
    {
        let payload = ImportBarcodeFoodRequestDTO(
            expectedPayloadSha256: expectedPayloadSha256
        )
        return try await requestM7(
            method: "POST", path: "v1/nutrition/barcodes/\(barcode)/import",
            body: try JSONEncoder().encode(payload), successStatusCodes: [200, 201]
        )
    }

    func fetchDayPlan(date: Date) async throws -> DayPlanResponse {
        let response: DayPlanResponse = try await requestM7(
            method: "GET",
            path: "v1/plans/day",
            queryItems: [URLQueryItem(name: "date", value: WireDay.string(from: date))],
            body: nil,
            successStatusCodes: [200]
        )
        if case .completed(let completed) = response, completed.planItems == nil {
            throw BackendError.retryable("completed plan is missing plan_items metadata")
        }
        return response
    }

    func generateDayPlan(date: Date, timezone: String) async throws -> DayPlanResponse {
        let payload = GenerateDayPlanRequest(date: WireDay.string(from: date), timezone: timezone)
        return try await requestM7(
            method: "POST",
            path: "v1/plans/day/generate",
            body: try JSONEncoder().encode(payload),
            successStatusCodes: [200]
        )
    }

    func fetchLatestTargetPolicy() async throws -> TargetPolicyLatest? {
        try await requestM7(
            method: "GET",
            path: "v1/target-policies/latest",
            body: nil,
            successStatusCodes: [200]
        )
    }

    func approveInitialCalorieTarget(
        policyVersion: String,
        caloriesKcal: String,
        calorieWeight: String,
        rationale: String
    ) async throws -> TargetPolicyApprovalResponse {
        let payload = ApproveInitialTargetPolicyRequest(
            policyVersion: policyVersion,
            rationale: rationale,
            goals: [
                TargetPolicyGoal(
                    nutrient: "calories_kcal",
                    kind: "target",
                    value: caloriesKcal,
                    weight: calorieWeight
                )
            ]
        )
        return try await requestM7(
            method: "POST",
            path: "v1/target-policies",
            body: try JSONEncoder().encode(payload),
            successStatusCodes: [200]
        )
    }

    func recordConsumption(
        runId: UUID,
        planVersionId: UUID,
        itemId: UUID,
        state: ConsumptionState,
        clientEventId: UUID?
    ) async throws -> ConsumptionEntryResponse {
        let payload = RecordConsumptionRequest(
            planVersionId: planVersionId,
            itemId: itemId,
            state: state,
            clientEventId: clientEventId
        )
        return try await requestM7(
            method: "POST",
            path: "v1/plans/\(runId.uuidString.lowercased())/consumption",
            body: try JSONEncoder().encode(payload),
            successStatusCodes: [200, 201]
        )
    }

    func listConsumption(runId: UUID) async throws -> ConsumptionListResponse {
        try await requestM7(
            method: "GET",
            path: "v1/plans/\(runId.uuidString.lowercased())/consumption",
            body: nil,
            successStatusCodes: [200]
        )
    }

    func fetchLatestGoalPolicy() async throws -> GoalPolicyResponse? {
        try await requestM7(
            method: "GET",
            path: "v1/goal-policies/latest",
            body: nil,
            successStatusCodes: [200]
        )
    }

    func createGoalPolicy(
        policyVersion: String,
        direction: GoalDirectionDTO,
        desiredRateKgPerWeek: String
    ) async throws -> GoalPolicyResponse {
        let payload = CreateGoalPolicyRequest(
            policyVersion: policyVersion,
            direction: direction,
            desiredRateKgPerWeek: desiredRateKgPerWeek
        )
        return try await requestM7(
            method: "POST",
            path: "v1/goal-policies",
            body: try JSONEncoder().encode(payload),
            successStatusCodes: [201]
        )
    }

    func createTargetReview(asOfDate: Date, timezone: String) async throws
        -> TargetReviewResponse
    {
        let payload = CreateTargetReviewRequest(
            asOfDate: WireDay.string(from: asOfDate),
            timezone: timezone
        )
        return try await requestM7(
            method: "POST",
            path: "v1/target-reviews",
            body: try JSONEncoder().encode(payload),
            successStatusCodes: [200, 201]
        )
    }

    func decideTargetReview(
        reviewId: UUID,
        decision: TargetReviewDecisionDTO,
        idempotencyKey: UUID
    ) async throws -> TargetReviewDecisionResponse {
        let payload = TargetReviewDecisionRequest(
            decision: decision,
            idempotencyKey: idempotencyKey
        )
        return try await requestM7(
            method: "POST",
            path: "v1/target-reviews/\(reviewId.uuidString.lowercased())/decision",
            body: try JSONEncoder().encode(payload),
            successStatusCodes: [200, 201]
        )
    }

    func fetchLatestProteinProposal() async throws -> ProteinTargetProposalResponse? {
        do {
            return try await requestM7(
                method: "GET",
                path: "v1/target-policies/protein-proposals/latest",
                body: nil,
                successStatusCodes: [200]
            )
        } catch BackendError.rejected(let status, _, _) where status == 404 {
            return nil
        }
    }

    func generateProteinProposal() async throws -> ProteinTargetProposalResponse {
        try await requestM7(
            method: "POST",
            path: "v1/target-policies/protein-proposals",
            body: nil,
            successStatusCodes: [200, 201]
        )
    }

    func decideProteinProposal(
        proposalId: UUID,
        decision: ProteinProposalDecisionDTO,
        clientEventId: UUID,
        rationale: String
    ) async throws -> ProteinProposalDecisionResponse {
        let payload = ProteinProposalDecisionRequest(
            decision: decision,
            clientEventId: clientEventId,
            rationale: rationale
        )
        return try await requestM7(
            method: "POST",
            path: "v1/target-policies/protein-proposals/"
                + "\(proposalId.uuidString.lowercased())/decision",
            body: try JSONEncoder().encode(payload),
            successStatusCodes: [200, 201]
        )
    }

    func fetchLatestNextMeal() async throws -> NextMealRecommendationResponse {
        try await requestM7(
            method: "GET",
            path: "v1/recommendations/next-meal/latest",
            body: nil,
            successStatusCodes: [200]
        )
    }

    func generateNextMeal(
        date: Date,
        timezone: String,
        clientRequestId: UUID
    ) async throws -> NextMealRecommendationResponse {
        let payload = GenerateNextMealRequest(
            localDate: WireDay.string(from: date),
            timezone: timezone,
            clientRequestId: clientRequestId
        )
        return try await requestM7(
            method: "POST",
            path: "v1/recommendations/next-meal",
            body: try JSONEncoder().encode(payload),
            successStatusCodes: [200, 201]
        )
    }

    func fetchNextMealConsumption(recommendationId: UUID) async throws
        -> NextMealConsumptionResponse
    {
        try await requestM7(
            method: "GET",
            path: "v1/recommendations/next-meal/"
                + "\(recommendationId.uuidString.lowercased())/consumption",
            body: nil,
            successStatusCodes: [200]
        )
    }

    func recordNextMealConsumption(
        recommendationId: UUID,
        clientEventId: UUID
    ) async throws -> NextMealConsumptionResponse {
        let payload = RecordNextMealConsumptionRequest(clientEventId: clientEventId)
        return try await requestM7(
            method: "POST",
            path: "v1/recommendations/next-meal/"
                + "\(recommendationId.uuidString.lowercased())/consumption",
            body: try JSONEncoder().encode(payload),
            successStatusCodes: [200, 201]
        )
    }

    // MARK: - Hevy sync and M14C Training Analytics

    func syncHevyTraining() async throws -> HevySyncResponse {
        try await requestM7(
            method: "POST",
            path: "v1/training/hevy/sync",
            body: nil,
            successStatusCodes: [200]
        )
    }

    func fetchRecentTrainingAnalytics(limit: Int) async throws
        -> TrainingAnalyticsRecentResponse
    {
        try await requestM7(
            method: "GET",
            path: "v1/training/analytics/recent",
            queryItems: [URLQueryItem(name: "limit", value: String(limit))],
            body: nil,
            successStatusCodes: [200]
        )
    }

    func fetchExerciseIndex(asOfDate: Date, timezone: String, limit: Int) async throws
        -> ExerciseIndexResponse
    {
        try await requestM7(
            method: "GET",
            path: "v1/training/exercises",
            queryItems: [
                URLQueryItem(name: "as_of_date", value: WireDay.string(from: asOfDate)),
                URLQueryItem(name: "timezone", value: timezone),
                URLQueryItem(name: "limit", value: String(limit)),
            ],
            body: nil,
            successStatusCodes: [200]
        )
    }

    func fetchExerciseHistory(
        sourceExerciseId: String,
        sourceSystem: String,
        asOfDate: Date,
        timezone: String,
        limit: Int
    ) async throws -> ExerciseTrainingHistoryResponse {
        try await requestM7(
            method: "GET",
            path: "v1/training/exercises/\(sourceExerciseId)/history",
            queryItems: [
                URLQueryItem(name: "source_system", value: sourceSystem),
                URLQueryItem(name: "as_of_date", value: WireDay.string(from: asOfDate)),
                URLQueryItem(name: "timezone", value: timezone),
                URLQueryItem(name: "limit", value: String(limit)),
            ],
            body: nil,
            successStatusCodes: [200]
        )
    }

    func fetchDetailedTrainingSession(revisionId: String) async throws
        -> DetailedTrainingSessionResponse
    {
        try await requestM7(
            method: "GET",
            path: "v1/training/sessions/\(revisionId)",
            body: nil,
            successStatusCodes: [200]
        )
    }

    private func post<Body: Encodable, Response: Decodable>(
        path: String, body: Body
    ) async throws -> Response {
        let token = try await auth.validAccessToken()
        var request = URLRequest(url: baseURL.appending(path: path))
        request.httpMethod = "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        do {
            let (data, response) = try await session.data(for: request)
            return try Self.decode(Response.self, data: data, response: response)
        } catch let error as BackendError {
            throw error
        } catch is AuthProviderError {
            throw BackendError.unauthorized
        } catch {
            throw BackendError.retryable(String(describing: error))
        }
    }

    private static func decode<Response: Decodable>(
        _ type: Response.Type, data: Data, response: URLResponse
    ) throws -> Response {
        guard let http = response as? HTTPURLResponse else {
            throw BackendError.retryable("non-HTTP response")
        }
        switch http.statusCode {
        case 200:
            do {
                return try JSONDecoder().decode(Response.self, from: data)
            } catch {
                throw BackendError.retryable("undecodable 200 payload")
            }
        case 400, 413:
            // Permanent batch problem; detail is a classification only.
            let bodyText = String(data: data.prefix(512), encoding: .utf8) ?? ""
            throw BackendError.rejectedPermanent(bodyText)
        case 401:
            throw BackendError.unauthorized
        default:
            throw BackendError.retryable("HTTP \(http.statusCode)")
        }
    }

    private func requestM7<Response: Decodable>(
        method: String,
        path: String,
        queryItems: [URLQueryItem] = [],
        body: Data?,
        successStatusCodes: Set<Int>
    ) async throws -> Response {
        do {
            var components = URLComponents(
                url: baseURL.appending(path: path), resolvingAgainstBaseURL: false)
            if !queryItems.isEmpty {
                components?.queryItems = queryItems
            }
            guard let url = components?.url else {
                throw BackendError.retryable("invalid backend URL")
            }

            let token = try await auth.validAccessToken()
            var request = URLRequest(url: url)
            request.httpMethod = method
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            if let body {
                request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                request.httpBody = body
            }
            let (data, response) = try await session.data(for: request)
            return try Self.decodeM7(
                Response.self,
                data: data,
                response: response,
                successStatusCodes: successStatusCodes
            )
        } catch let error as BackendError {
            throw error
        } catch is AuthProviderError {
            throw BackendError.unauthorized
        } catch {
            throw BackendError.retryable(String(describing: error))
        }
    }

    private static func decodeM7<Response: Decodable>(
        _ type: Response.Type,
        data: Data,
        response: URLResponse,
        successStatusCodes: Set<Int>
    ) throws -> Response {
        guard let http = response as? HTTPURLResponse else {
            throw BackendError.retryable("non-HTTP response")
        }
        if successStatusCodes.contains(http.statusCode) {
            do {
                return try makeM7Decoder().decode(Response.self, from: data)
            } catch {
                throw BackendError.retryable("undecodable HTTP \(http.statusCode) payload")
            }
        }

        let envelope = try? JSONDecoder().decode(BackendErrorEnvelope.self, from: data)
        let code = envelope?.error.code
        let detail = envelope?.error.detail
        switch http.statusCode {
        case 401:
            throw BackendError.unauthorized
        case 400, 404, 409, 422:
            throw BackendError.rejected(
                statusCode: http.statusCode, code: code, detail: detail)
        case 503:
            throw BackendError.retryableHTTP(
                statusCode: http.statusCode, code: code, detail: detail)
        default:
            throw BackendError.retryableHTTP(
                statusCode: http.statusCode, code: code, detail: detail)
        }
    }

    private static func makeM7Decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let value = try container.decode(String.self)
            guard let date = WireDate.date(fromISO8601: value) else {
                throw DecodingError.dataCorruptedError(
                    in: container, debugDescription: "invalid ISO-8601 timestamp")
            }
            return date
        }
        return decoder
    }
}

private struct GenerateDayPlanRequest: Encodable {
    let date: String
    let timezone: String
}

private struct CreateGoalPolicyRequest: Encodable {
    let policyVersion: String
    let direction: GoalDirectionDTO
    let desiredRateKgPerWeek: String

    enum CodingKeys: String, CodingKey {
        case policyVersion = "policy_version"
        case direction
        case desiredRateKgPerWeek = "desired_rate_kg_per_week"
    }
}

private struct ApproveInitialTargetPolicyRequest: Encodable {
    let policyVersion: String
    let rationale: String
    let goals: [TargetPolicyGoal]

    enum CodingKeys: String, CodingKey {
        case policyVersion = "policy_version"
        case rationale
        case goals
    }
}

private struct CreateTargetReviewRequest: Encodable {
    let asOfDate: String
    let timezone: String

    enum CodingKeys: String, CodingKey {
        case asOfDate = "as_of_date"
        case timezone
    }
}

private struct TargetReviewDecisionRequest: Encodable {
    let decision: TargetReviewDecisionDTO
    let idempotencyKey: UUID

    enum CodingKeys: String, CodingKey {
        case decision
        case idempotencyKey = "idempotency_key"
    }
}

private struct ProteinProposalDecisionRequest: Encodable {
    let decision: ProteinProposalDecisionDTO
    let clientEventId: UUID
    let rationale: String

    enum CodingKeys: String, CodingKey {
        case decision
        case clientEventId = "client_event_id"
        case rationale
    }
}

private struct GenerateNextMealRequest: Encodable {
    let localDate: String
    let timezone: String
    let clientRequestId: UUID

    enum CodingKeys: String, CodingKey {
        case localDate = "local_date"
        case timezone
        case clientRequestId = "client_request_id"
    }
}

private struct RecordNextMealConsumptionRequest: Encodable {
    let clientEventId: UUID

    enum CodingKeys: String, CodingKey {
        case clientEventId = "client_event_id"
    }
}

private struct BackendErrorEnvelope: Decodable {
    struct ErrorBody: Decodable {
        let code: String?
        let detail: String?
    }

    let error: ErrorBody
}
