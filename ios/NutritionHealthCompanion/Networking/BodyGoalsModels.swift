import Foundation

struct BodyGoalsResponse: Decodable, Equatable, Sendable {
    let weight: BodyGoalsWeightDTO?
    let profile: BodyGoalProfileDTO?
    let waist: BodyGoalsWaistDTO
    let goal: BodyGoalsGoalDTO?
    let approvedTargets: BodyGoalsTargetsDTO
    let startingCalorieProposal: StartingCalorieProposalDTO?
    let phaseAssessment: PhaseAssessmentDTO

    enum CodingKeys: String, CodingKey {
        case weight, profile, waist, goal
        case approvedTargets = "approved_targets"
        case startingCalorieProposal = "starting_calorie_proposal"
        case phaseAssessment = "phase_assessment"
    }
}

struct BodyGoalsWeightDTO: Decodable, Equatable, Sendable {
    let valueKg: String
    let measuredAt: Date
    let ageDays: Int
    let authority: String
    enum CodingKeys: String, CodingKey {
        case valueKg = "value_kg"
        case measuredAt = "measured_at"
        case ageDays = "age_days"
        case authority
    }
}

struct BodyGoalProfileDTO: Decodable, Equatable, Sendable {
    let profileId: UUID
    let heightCm: String
    let dateOfBirth: String
    let formulaSex: String
    let activityLevel: String
    let targetWeightKg: String?
    let provenance: String
    enum CodingKeys: String, CodingKey {
        case profileId = "profile_id"
        case heightCm = "height_cm"
        case dateOfBirth = "date_of_birth"
        case formulaSex = "formula_sex"
        case activityLevel = "activity_level"
        case targetWeightKg = "target_weight_kg"
        case provenance
    }
}

struct BodyGoalsWaistDTO: Decodable, Equatable, Sendable {
    let latest: WaistMeasurementDTO?
    let history: [WaistMeasurementDTO]
    let trend: WaistTrendDTO
}

struct WaistMeasurementDTO: Decodable, Equatable, Sendable {
    let measurementId: UUID
    let measuredAt: Date
    let valueCm: String
    let enteredValue: String
    let enteredUnit: String
    let correctsMeasurementId: UUID?
    enum CodingKeys: String, CodingKey {
        case measurementId = "measurement_id"
        case measuredAt = "measured_at"
        case valueCm = "value_cm"
        case enteredValue = "entered_value"
        case enteredUnit = "entered_unit"
        case correctsMeasurementId = "corrects_measurement_id"
    }
}

struct WaistTrendDTO: Decodable, Equatable, Sendable {
    let policyVersion: String
    let status: String
    let latestMeasurementAgeDays: Int?
    let representedDayCount: Int
    let coverageSpanDays: Int
    let weeklyRateCm: String?
    enum CodingKeys: String, CodingKey {
        case policyVersion = "policy_version"
        case status
        case latestMeasurementAgeDays = "latest_measurement_age_days"
        case representedDayCount = "represented_day_count"
        case coverageSpanDays = "coverage_span_days"
        case weeklyRateCm = "weekly_rate_cm"
    }
}

struct BodyGoalsGoalDTO: Decodable, Equatable, Sendable {
    let direction: String
    let desiredRateKgPerWeek: String
    enum CodingKeys: String, CodingKey {
        case direction
        case desiredRateKgPerWeek = "desired_rate_kg_per_week"
    }
}

struct BodyGoalsTargetsDTO: Decodable, Equatable, Sendable {
    let caloriesKcal: String?
    let proteinG: String?
    enum CodingKeys: String, CodingKey {
        case caloriesKcal = "calories_kcal"
        case proteinG = "protein_g"
    }
}

struct StartingCalorieProposalDTO: Decodable, Equatable, Sendable {
    let proposalId: UUID
    let policyVersion: String
    let maintenanceKcal: String
    let goalAdjustmentKcal: String
    let proposedCalorieKcal: String
    let isEstimate: Bool
    let requiresExplicitApproval: Bool
    let decisionStatus: String
    enum CodingKeys: String, CodingKey {
        case proposalId = "proposal_id"
        case policyVersion = "policy_version"
        case maintenanceKcal = "maintenance_kcal"
        case goalAdjustmentKcal = "goal_adjustment_kcal"
        case proposedCalorieKcal = "proposed_calorie_kcal"
        case isEstimate = "is_estimate"
        case requiresExplicitApproval = "requires_explicit_approval"
        case decisionStatus = "decision_status"
    }
}

struct PhaseAssessmentDTO: Decodable, Equatable, Sendable {
    let policyVersion: String
    let status: String
    let reasonCodes: [String]
    let recommendationOnly: Bool
    enum CodingKeys: String, CodingKey {
        case policyVersion = "policy_version"
        case status
        case reasonCodes = "reason_codes"
        case recommendationOnly = "recommendation_only"
    }
}

struct SaveBodyGoalProfileRequest: Encodable, Sendable {
    let heightCm: String
    let dateOfBirth: String
    let formulaSex: String
    let activityLevel: String
    let targetWeightKg: String?
    enum CodingKeys: String, CodingKey {
        case heightCm = "height_cm"
        case dateOfBirth = "date_of_birth"
        case formulaSex = "formula_sex"
        case activityLevel = "activity_level"
        case targetWeightKg = "target_weight_kg"
    }
}

struct AddWaistRequest: Encodable, Sendable {
    let value: String
    let unit: String
    let measuredAt: String
    let correctsMeasurementId: UUID?
    enum CodingKeys: String, CodingKey {
        case value, unit
        case measuredAt = "measured_at"
        case correctsMeasurementId = "corrects_measurement_id"
    }
}

struct StartingTargetDecisionRequest: Encodable, Sendable {
    let decision: String
    let clientEventId: UUID
    enum CodingKeys: String, CodingKey {
        case decision
        case clientEventId = "client_event_id"
    }
}

struct StartingTargetDecisionResponse: Decodable, Equatable, Sendable {
    let decision: String
    let resultingTargetPolicyVersionId: UUID?
    enum CodingKeys: String, CodingKey {
        case decision
        case resultingTargetPolicyVersionId = "resulting_target_policy_version_id"
    }
}
