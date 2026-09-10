import Foundation

struct BodyProfileRequestDTO: Encodable, Sendable {
    let heightCm: String
    let targetWeightKg: String?

    private enum CodingKeys: String, CodingKey {
        case heightCm = "height_cm"
        case targetWeightKg = "target_weight_kg"
    }
}

struct WaistMeasurementRequestDTO: Encodable, Sendable {
    let value: String
    let unit: String
    let measuredAt: String

    private enum CodingKeys: String, CodingKey {
        case value, unit
        case measuredAt = "measured_at"
    }
}

struct StartingCalorieEstimateRequestDTO: Encodable, Sendable {
    let asOfDate: String
    let timezone: String

    private enum CodingKeys: String, CodingKey {
        case asOfDate = "as_of_date"
        case timezone
    }
}

struct BodyGoalsResponse: Decodable, Equatable, Sendable {
    let profile: BodyProfileDTO?
    let currentWeight: CurrentWeightDTO
    let latestWaist: WaistMeasurementDTO?
    let waistHistory: [WaistMeasurementDTO]
    let goal: BodyGoalDTO?
    let target: ApprovedBodyTargetDTO?
    let progress: BodyProgressDTO?

    private enum CodingKeys: String, CodingKey {
        case profile
        case currentWeight = "current_weight"
        case latestWaist = "latest_waist"
        case waistHistory = "waist_history"
        case goal, target, progress
    }
}

struct BodyProfileDTO: Decodable, Equatable, Sendable {
    let heightCm: String
    let targetWeightKg: String?
    let updatedAt: String

    private enum CodingKeys: String, CodingKey {
        case heightCm = "height_cm"
        case targetWeightKg = "target_weight_kg"
        case updatedAt = "updated_at"
    }
}

struct CurrentWeightDTO: Decodable, Equatable, Sendable {
    let valueKg: String?
    let measuredAt: String?
    let freshnessDays: Int?
    let authority: String

    private enum CodingKeys: String, CodingKey {
        case valueKg = "value_kg"
        case measuredAt = "measured_at"
        case freshnessDays = "freshness_days"
        case authority
    }
}

struct WaistMeasurementDTO: Decodable, Equatable, Sendable {
    let waistCm: String
    let measuredAt: String
    let recordedAt: String
    let source: String

    private enum CodingKeys: String, CodingKey {
        case waistCm = "waist_cm"
        case measuredAt = "measured_at"
        case recordedAt = "recorded_at"
        case source
    }
}

struct BodyGoalDTO: Decodable, Equatable, Sendable {
    let direction: String
    let desiredRateKgPerWeek: String
    let policyVersion: String

    private enum CodingKeys: String, CodingKey {
        case direction
        case desiredRateKgPerWeek = "desired_rate_kg_per_week"
        case policyVersion = "policy_version"
    }
}

struct ApprovedBodyTargetDTO: Decodable, Equatable, Sendable {
    let policyVersion: String
    let caloriesKcal: String?
    let proteinG: String?
    let approvedAt: String

    private enum CodingKeys: String, CodingKey {
        case policyVersion = "policy_version"
        case caloriesKcal = "calories_kcal"
        case proteinG = "protein_g"
        case approvedAt = "approved_at"
    }
}

struct BodyProgressDTO: Decodable, Equatable, Sendable {
    let status: String
    let direction: String
    let reason: String
    let weightRateKgPerWeek: String?
    let waistRateCmPerWeek: String?

    private enum CodingKeys: String, CodingKey {
        case status, direction, reason
        case weightRateKgPerWeek = "weight_rate_kg_per_week"
        case waistRateCmPerWeek = "waist_rate_cm_per_week"
    }
}

struct StartingCalorieEstimateDTO: Decodable, Equatable, Sendable {
    let estimateKcal: Int
    let weightKg: String
    let policyVersion: String
    let confidence: String
    let rationale: String
    let approvalRequired: Bool

    private enum CodingKeys: String, CodingKey {
        case estimateKcal = "estimate_kcal"
        case weightKg = "weight_kg"
        case policyVersion = "policy_version"
        case confidence, rationale
        case approvalRequired = "approval_required"
    }
}
