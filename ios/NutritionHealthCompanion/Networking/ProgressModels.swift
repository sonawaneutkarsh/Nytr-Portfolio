import Foundation

enum ProgressNutrientState: String, Codable, Equatable, Sendable {
    case noRecords = "no_records"
    case quantified
    case partial
    case unavailable
}

enum ProgressGoalBandStatus: String, Codable, Equatable, Sendable {
    case unavailable
    case belowBand = "below_band"
    case withinBand = "within_band"
    case aboveBand = "above_band"
}

struct ProgressBodyPoint: Codable, Equatable, Sendable {
    let localDate: String
    let medianKg: String
    let observationCount: Int

    private enum CodingKeys: String, CodingKey {
        case localDate = "local_date"
        case medianKg = "median_kg"
        case observationCount = "observation_count"
    }
}

struct ProgressBodyResponse: Decodable, Equatable, Sendable {
    let windowDays: Int
    let startDate: String
    let endDate: String
    let dailyMedians: [ProgressBodyPoint]
    let trend28d: BodyMassTrendResponse

    private enum CodingKeys: String, CodingKey {
        case windowDays = "window_days"
        case startDate = "start_date"
        case endDate = "end_date"
        case dailyMedians = "daily_medians"
        case trend28d = "trend_28d"
    }
}

struct ProgressGoalResponse: Codable, Equatable, Sendable {
    let status: ProgressGoalBandStatus
    let mode: GoalDirectionDTO?
    let desiredRateKgPerWeek: String?
    let observedRateKgPerWeek: String?
    let acceptableRateLowerKgPerWeek: String?
    let acceptableRateUpperKgPerWeek: String?

    private enum CodingKeys: String, CodingKey {
        case status
        case mode
        case desiredRateKgPerWeek = "desired_rate_kg_per_week"
        case observedRateKgPerWeek = "observed_rate_kg_per_week"
        case acceptableRateLowerKgPerWeek = "acceptable_rate_lower_kg_per_week"
        case acceptableRateUpperKgPerWeek = "acceptable_rate_upper_kg_per_week"
    }
}

struct ProgressRecordedCalories: Codable, Equatable, Sendable {
    let state: ProgressNutrientState
    let valueKcal: String?

    private enum CodingKeys: String, CodingKey {
        case state
        case valueKcal = "value_kcal"
    }
}

struct ProgressRecordedProtein: Codable, Equatable, Sendable {
    let state: ProgressNutrientState
    let valueG: String?

    private enum CodingKeys: String, CodingKey {
        case state
        case valueG = "value_g"
    }
}

struct ProgressNutritionDay: Codable, Equatable, Sendable {
    let localDate: String
    let hasRecordedEvents: Bool
    let recordedEventCount: Int
    let recordedCalories: ProgressRecordedCalories
    let recordedProtein: ProgressRecordedProtein
    let recordedCalorieTargetComparison: CalorieAdherenceStatus
    let recordedProteinTargetComparison: ProteinAdherenceStatus
    let targetStatus: NutritionHistoryTargetStatus
    let target: DailyNutritionTarget?
    let authorityEventCounts: [String: Int]
    let sourceEventCounts: [String: Int]
    let includesEstimates: Bool

    private enum CodingKeys: String, CodingKey {
        case localDate = "local_date"
        case hasRecordedEvents = "has_recorded_events"
        case recordedEventCount = "recorded_event_count"
        case recordedCalories = "recorded_calories"
        case recordedProtein = "recorded_protein"
        case recordedCalorieTargetComparison = "recorded_calorie_target_comparison"
        case recordedProteinTargetComparison = "recorded_protein_target_comparison"
        case targetStatus = "target_status"
        case target
        case authorityEventCounts = "authority_event_counts"
        case sourceEventCounts = "source_event_counts"
        case includesEstimates = "includes_estimates"
    }
}

struct ProgressCoverage: Codable, Equatable, Sendable {
    let daysWithRecordedEvents: Int
    let daysWithoutRecordedEvents: Int

    private enum CodingKeys: String, CodingKey {
        case daysWithRecordedEvents = "days_with_recorded_events"
        case daysWithoutRecordedEvents = "days_without_recorded_events"
    }
}

struct ProgressRecordedCaloriesSummary: Codable, Equatable, Sendable {
    let quantifiedRecordedDays: Int
    let partialRecordedDays: Int
    let unavailableRecordedDays: Int
    let averageRecordedKcal: String?
    let averageDenominatorDays: Int

    private enum CodingKeys: String, CodingKey {
        case quantifiedRecordedDays = "quantified_recorded_days"
        case partialRecordedDays = "partial_recorded_days"
        case unavailableRecordedDays = "unavailable_recorded_days"
        case averageRecordedKcal = "average_recorded_kcal"
        case averageDenominatorDays = "average_denominator_days"
    }
}

struct ProgressRecordedProteinSummary: Codable, Equatable, Sendable {
    let quantifiedRecordedDays: Int
    let partialRecordedDays: Int
    let unavailableRecordedDays: Int
    let averageRecordedG: String?
    let averageDenominatorDays: Int

    private enum CodingKeys: String, CodingKey {
        case quantifiedRecordedDays = "quantified_recorded_days"
        case partialRecordedDays = "partial_recorded_days"
        case unavailableRecordedDays = "unavailable_recorded_days"
        case averageRecordedG = "average_recorded_g"
        case averageDenominatorDays = "average_denominator_days"
    }
}

struct ProgressCalorieComparisonCounts: Codable, Equatable, Sendable {
    let eligibleDayCount: Int
    let below: Int
    let at: Int
    let above: Int
    let unavailable: Int

    private enum CodingKeys: String, CodingKey {
        case eligibleDayCount = "eligible_day_count"
        case below
        case at
        case above
        case unavailable
    }
}

struct ProgressProteinComparisonCounts: Codable, Equatable, Sendable {
    let eligibleDayCount: Int
    let below: Int
    let atOrAbove: Int
    let unavailable: Int

    private enum CodingKeys: String, CodingKey {
        case eligibleDayCount = "eligible_day_count"
        case below
        case atOrAbove = "at_or_above"
        case unavailable
    }
}

struct ProgressNutritionSummary: Codable, Equatable, Sendable {
    let windowDays: Int
    let startDate: String
    let endDate: String
    let coverage: ProgressCoverage
    let recordedCalories: ProgressRecordedCaloriesSummary
    let recordedProtein: ProgressRecordedProteinSummary
    let recordedCalorieTargetComparisons: ProgressCalorieComparisonCounts
    let recordedProteinTargetComparisons: ProgressProteinComparisonCounts
    let authorityEventCounts: [String: Int]
    let sourceEventCounts: [String: Int]
    let includesEstimates: Bool

    private enum CodingKeys: String, CodingKey {
        case windowDays = "window_days"
        case startDate = "start_date"
        case endDate = "end_date"
        case coverage
        case recordedCalories = "recorded_calories"
        case recordedProtein = "recorded_protein"
        case recordedCalorieTargetComparisons = "recorded_calorie_target_comparisons"
        case recordedProteinTargetComparisons = "recorded_protein_target_comparisons"
        case authorityEventCounts = "authority_event_counts"
        case sourceEventCounts = "source_event_counts"
        case includesEstimates = "includes_estimates"
    }
}

struct ProgressTargetChange: Codable, Equatable, Sendable {
    let effectiveAt: String
    let effectiveLocalDate: String
    let targetPolicyVersionId: UUID
    let targetPolicyVersion: String
    let caloriesKcal: String?
    let caloriesGoalKind: String?
    let proteinG: String?
    let proteinGoalKind: String?

    private enum CodingKeys: String, CodingKey {
        case effectiveAt = "effective_at"
        case effectiveLocalDate = "effective_local_date"
        case targetPolicyVersionId = "target_policy_version_id"
        case targetPolicyVersion = "target_policy_version"
        case caloriesKcal = "calories_kcal"
        case caloriesGoalKind = "calories_goal_kind"
        case proteinG = "protein_g"
        case proteinGoalKind = "protein_goal_kind"
    }
}

struct ProgressNutritionResponse: Codable, Equatable, Sendable {
    let windowDays: Int
    let startDate: String
    let endDate: String
    let days: [ProgressNutritionDay]
    let summary7d: ProgressNutritionSummary
    let summary28d: ProgressNutritionSummary
    let targetChanges: [ProgressTargetChange]

    private enum CodingKeys: String, CodingKey {
        case windowDays = "window_days"
        case startDate = "start_date"
        case endDate = "end_date"
        case days
        case summary7d = "summary_7d"
        case summary28d = "summary_28d"
        case targetChanges = "target_changes"
    }
}

struct ProgressLimitations: Codable, Equatable, Sendable {
    let codes: [String]
    let loggingCoverage: String
    let recordTimeAttribution: String
    let causality: String

    private enum CodingKeys: String, CodingKey {
        case codes
        case loggingCoverage = "logging_coverage"
        case recordTimeAttribution = "record_time_attribution"
        case causality
    }
}

struct OwnerProgressResponse: Decodable, Equatable, Sendable {
    let policyVersion: String
    let asOfDate: String
    let timezone: String
    let body: ProgressBodyResponse
    let goal: ProgressGoalResponse
    let nutrition: ProgressNutritionResponse
    let limitations: ProgressLimitations

    private enum CodingKeys: String, CodingKey {
        case policyVersion = "policy_version"
        case asOfDate = "as_of_date"
        case timezone
        case body
        case goal
        case nutrition
        case limitations
    }
}
