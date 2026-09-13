import Foundation

enum NextMealStatusDTO: String, Codable, Equatable, Sendable {
    case recommended
    case noApprovedTargetPolicy = "no_approved_target_policy"
    case noApprovedCalorieTarget = "no_approved_calorie_target"
    case noApprovedProteinTarget = "no_approved_protein_target"
    case unsupportedTargetSemantics = "unsupported_target_semantics"
    case incompleteLedgerNutrition = "incomplete_ledger_nutrition"
    case noRemainingMealOpportunity = "no_remaining_meal_opportunity"
    case dailyCalorieTargetMet = "daily_calorie_target_met"
    case menuDataUnavailable = "menu_data_unavailable"
    case staleMenuData = "stale_menu_data"
    case noEligibleCandidate = "no_eligible_candidate"
}

struct NextMealOpportunityDTO: Codable, Equatable, Sendable {
    let context: String
    let menuPeriod: String
    let window: [String]

    enum CodingKeys: String, CodingKey {
        case context
        case menuPeriod = "menu_period"
        case window
    }
}

struct NextMealLedgerDTO: Codable, Equatable, Sendable {
    let consumedEntryIds: [UUID]
    let knownCaloriesConsumed: String?
    let knownProteinGConsumed: String?
    let remainingCalories: String?
    let remainingProteinG: String?
    var unknownNutrients: [String]? = nil

    enum CodingKeys: String, CodingKey {
        case consumedEntryIds = "consumed_entry_ids"
        case knownCaloriesConsumed = "known_calories_consumed"
        case knownProteinGConsumed = "known_protein_g_consumed"
        case remainingCalories = "remaining_calories"
        case remainingProteinG = "remaining_protein_g"
        case unknownNutrients = "unknown_nutrients"
    }
}

struct NextMealAllocatedTargetsDTO: Codable, Equatable, Sendable {
    let caloriesKcal: String
    let caloriesGoalKind: String
    let proteinG: String
    let proteinGoalKind: String
    let proteinScoringActive: Bool
    let opportunityCount: Int
    let rule: String

    enum CodingKeys: String, CodingKey {
        case caloriesKcal = "calories_kcal"
        case caloriesGoalKind = "calories_goal_kind"
        case proteinG = "protein_g"
        case proteinGoalKind = "protein_goal_kind"
        case proteinScoringActive = "protein_scoring_active"
        case opportunityCount = "opportunity_count"
        case rule
    }
}

struct NextMealArtifactDTO: Codable, Equatable, Sendable {
    let status: NextMealStatusDTO
    let reasonCodes: [String]
    let decisionAt: Date
    let nextMealPolicyVersion: String
    let scheduleVersion: String?
    let targetPolicyVersion: String?
    let menuSnapshotSha256: String?
    let nutritionAuthorities: [String]?
    let ledger: NextMealLedgerDTO?
    let remainingOpportunities: [NextMealOpportunityDTO]?
    let selectedOpportunity: NextMealOpportunityDTO?
    let allocatedTargets: NextMealAllocatedTargetsDTO?
    let selected: PlanCandidate?
    let alternatives: [PlanCandidate]?

    enum CodingKeys: String, CodingKey {
        case status
        case reasonCodes = "reason_codes"
        case decisionAt = "decision_at"
        case nextMealPolicyVersion = "next_meal_policy_version"
        case scheduleVersion = "schedule_version"
        case targetPolicyVersion = "target_policy_version"
        case menuSnapshotSha256 = "menu_snapshot_sha256"
        case nutritionAuthorities = "nutrition_authorities"
        case ledger
        case remainingOpportunities = "remaining_opportunities"
        case selectedOpportunity = "selected_opportunity"
        case allocatedTargets = "allocated_targets"
        case selected
        case alternatives
    }
}

struct NextMealRecommendationResponse: Codable, Equatable, Sendable {
    var nutritionQuality: NutritionQualityDTO? = nil
    let recommendationId: UUID
    let clientRequestId: UUID
    let localDate: String
    let timezone: String
    let decisionAt: Date
    let status: NextMealStatusDTO
    let reasonCodes: [String]
    let inputsDigest: String
    let artifactSha256: String
    let artifact: NextMealArtifactDTO
    let created: Bool?

    enum CodingKeys: String, CodingKey {
        case recommendationId = "recommendation_id"
        case clientRequestId = "client_request_id"
        case localDate = "local_date"
        case timezone
        case decisionAt = "decision_at"
        case status
        case reasonCodes = "reason_codes"
        case inputsDigest = "inputs_digest"
        case nutritionQuality = "nutrition_quality"
        case artifactSha256 = "artifact_sha256"
        case artifact
        case created
    }
}

struct NextMealConsumptionResponse: Codable, Equatable, Sendable {
    let entryId: UUID
    let recommendationId: UUID
    let clientEventId: UUID
    let state: String
    let localDate: String
    let timezone: String
    let recordedAt: Date
    let recommendationArtifactSha256: String
    let nextMealPolicyVersion: String
    let mealContext: String
    let menuPeriod: String
    let candidateId: String
    let itemName: String
    let servingDescription: String
    let configurationSummary: String?
    let nutritionAuthority: String
    let nutritionConfidence: String?
    let caloriesKcal: String?
    let proteinG: String?
    let unknownNutrients: [String]
    let selectedCandidateSha256: String
    let created: Bool?

    enum CodingKeys: String, CodingKey {
        case entryId = "entry_id"
        case recommendationId = "recommendation_id"
        case clientEventId = "client_event_id"
        case state
        case localDate = "local_date"
        case timezone
        case recordedAt = "recorded_at"
        case recommendationArtifactSha256 = "recommendation_artifact_sha256"
        case nextMealPolicyVersion = "next_meal_policy_version"
        case mealContext = "meal_context"
        case menuPeriod = "menu_period"
        case candidateId = "candidate_id"
        case itemName = "item_name"
        case servingDescription = "serving_description"
        case configurationSummary = "configuration_summary"
        case nutritionAuthority = "nutrition_authority"
        case nutritionConfidence = "nutrition_confidence"
        case caloriesKcal = "calories_kcal"
        case proteinG = "protein_g"
        case unknownNutrients = "unknown_nutrients"
        case selectedCandidateSha256 = "selected_candidate_sha256"
        case created
    }
}

struct NutritionQualityDTO: Codable, Equatable, Sendable {
    let policyVersion: String
    let confidence: String
    let scope: String
    let findings: [NutrientQualityFinding]
    let missingNutrients: [String]
    let notice: String
    enum CodingKeys: String, CodingKey {
        case policyVersion = "policy_version"
        case missingNutrients = "missing_nutrients"
        case confidence, scope, findings, notice
    }
}
struct NutrientQualityFinding: Codable, Equatable, Sendable, Identifiable {
    let nutrient: String
    let label: String
    let amount: String
    let unit: String
    let dailyValuePercent: String
    let band: String
    let message: String
    var id: String { nutrient }
    enum CodingKeys: String, CodingKey {
        case nutrient, label, amount, unit, band, message
        case dailyValuePercent = "daily_value_percent"
    }
}
