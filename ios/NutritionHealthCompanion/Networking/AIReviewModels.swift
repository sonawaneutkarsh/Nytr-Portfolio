import Foundation

struct GenerateAIReviewRequestDTO: Encodable, Equatable, Sendable {
    let asOfDate: String
    let timezone: String

    private enum CodingKeys: String, CodingKey {
        case asOfDate = "as_of_date"
        case timezone
    }
}

struct AIReviewResponse: Decodable, Equatable, Sendable {
    let status: String
    let promptVersion: String
    let snapshot: AIReviewSnapshotDTO
    let review: AIReviewContentDTO?
    let failureCode: String?
    let authorityNotice: String

    private enum CodingKeys: String, CodingKey {
        case status
        case promptVersion = "prompt_version"
        case snapshot
        case review
        case failureCode = "failure_code"
        case authorityNotice = "authority_notice"
    }
}

struct AIReviewContentDTO: Decodable, Equatable, Sendable {
    let summary: String
    let attentionItems: [String]
    let evidenceNotes: [String]
    let limitations: [String]

    private enum CodingKeys: String, CodingKey {
        case summary
        case attentionItems = "attention_items"
        case evidenceNotes = "evidence_notes"
        case limitations
    }
}

struct AIReviewSnapshotDTO: Decodable, Equatable, Sendable {
    let snapshotVersion: String
    let asOfDate: String
    let timezone: String
    let goal: AIReviewGoalDTO
    let targets: AIReviewTargetsDTO
    let todayRecorded: AIReviewTodayRecordedDTO
    let bodyTrend: AIReviewBodyTrendDTO
    let recordedNutritionProgress: AIReviewNutritionProgressDTO
    let nextMeal: AIReviewNextMealDTO
    let limitations: [String]

    private enum CodingKeys: String, CodingKey {
        case snapshotVersion = "snapshot_version"
        case asOfDate = "as_of_date"
        case timezone
        case goal
        case targets
        case todayRecorded = "today_recorded"
        case bodyTrend = "body_trend"
        case recordedNutritionProgress = "recorded_nutrition_progress"
        case nextMeal = "next_meal"
        case limitations
    }
}

struct AIReviewGoalDTO: Decodable, Equatable, Sendable {
    let mode: String?
    let bandStatus: String

    private enum CodingKeys: String, CodingKey {
        case mode
        case bandStatus = "band_status"
    }
}

struct AIReviewTargetsDTO: Decodable, Equatable, Sendable {
    let caloriesKcal: String?
    let caloriesKind: String?
    let proteinG: String?
    let proteinKind: String?

    private enum CodingKeys: String, CodingKey {
        case caloriesKcal = "calories_kcal"
        case caloriesKind = "calories_kind"
        case proteinG = "protein_g"
        case proteinKind = "protein_kind"
    }
}

struct AIReviewTodayRecordedDTO: Decodable, Equatable, Sendable {
    let itemCount: Int
    let completeness: String
    let caloriesKcal: String?
    let proteinG: String?
    let reasonCodes: [String]
    let authorities: [String]

    private enum CodingKeys: String, CodingKey {
        case itemCount = "item_count"
        case completeness
        case caloriesKcal = "calories_kcal"
        case proteinG = "protein_g"
        case reasonCodes = "reason_codes"
        case authorities
    }
}

struct AIReviewBodyTrendDTO: Decodable, Equatable, Sendable {
    let status: String
    let latestMeasurementAgeDays: Int?
    let representedDayCount: Int
    let coverageSpanDays: Int

    private enum CodingKeys: String, CodingKey {
        case status
        case latestMeasurementAgeDays = "latest_measurement_age_days"
        case representedDayCount = "represented_day_count"
        case coverageSpanDays = "coverage_span_days"
    }
}

struct AIReviewNutritionProgressDTO: Decodable, Equatable, Sendable {
    let daysWithRecords7d: Int
    let daysWithRecords28d: Int
    let calorieQuantifiedDays7d: Int
    let proteinQuantifiedDays7d: Int
    let includesEstimates7d: Bool

    private enum CodingKeys: String, CodingKey {
        case daysWithRecords7d = "days_with_records_7d"
        case daysWithRecords28d = "days_with_records_28d"
        case calorieQuantifiedDays7d = "calorie_quantified_days_7d"
        case proteinQuantifiedDays7d = "protein_quantified_days_7d"
        case includesEstimates7d = "includes_estimates_7d"
    }
}

struct AIReviewNextMealDTO: Decodable, Equatable, Sendable {
    let status: String
    let reasonCodes: [String]

    private enum CodingKeys: String, CodingKey {
        case status
        case reasonCodes = "reason_codes"
    }
}
