import Foundation

/// The three retrieval/generation states returned by the daily-plan API.
/// The associated records model the wire response without recalculating any
/// nutrition or planner output on-device.
enum DayPlanResponse: Codable, Equatable, Sendable {
    case completed(CompletedDayPlan)
    case noPlan(NoPlanDay)
    case notGenerated(NotGeneratedDay)

    private enum CodingKeys: String, CodingKey {
        case state
    }

    private enum State: String, Decodable {
        case completed
        case noPlan = "no_plan"
        case notGenerated = "not_generated"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        switch try container.decode(State.self, forKey: .state) {
        case .completed:
            self = .completed(try CompletedDayPlan(from: decoder))
        case .noPlan:
            self = .noPlan(try NoPlanDay(from: decoder))
        case .notGenerated:
            self = .notGenerated(try NotGeneratedDay(from: decoder))
        }
    }

    func encode(to encoder: Encoder) throws {
        switch self {
        case .completed(let value):
            try value.encode(to: encoder)
        case .noPlan(let value):
            try value.encode(to: encoder)
        case .notGenerated(let value):
            try value.encode(to: encoder)
        }
    }
}

struct CompletedDayPlan: Codable, Equatable, Sendable {
    let requestedDate: String
    let planDate: String
    let planSha256: String
    let inputsFingerprint: String
    let runId: UUID
    let versionId: UUID
    let plan: DailyPlanArtifact
    /// Present only on canonical completed GET responses. Generation keeps its
    /// frozen shape and is followed by GET before display or consumption.
    let planItems: [PlanItemReference]?
    /// Present on enriched GET responses; generation responses intentionally
    /// retain their frozen Step 6 shape and therefore omit this key.
    let targetPolicy: TargetPolicyUsed?
    /// Present on enriched GET responses and sourced from the persisted run.
    /// The frozen generation response does not include it.
    let generatedAt: Date?

    private enum CodingKeys: String, CodingKey {
        case state
        case requestedDate = "requested_date"
        case planDate = "plan_date"
        case planSha256 = "plan_sha256"
        case inputsFingerprint = "inputs_fingerprint"
        case runId = "run_id"
        case versionId = "version_id"
        case plan
        case planItems = "plan_items"
        case targetPolicy = "target_policy"
        case generatedAt = "generated_at"
    }

    init(
        requestedDate: String,
        planDate: String,
        planSha256: String,
        inputsFingerprint: String,
        runId: UUID,
        versionId: UUID,
        plan: DailyPlanArtifact,
        planItems: [PlanItemReference]?,
        targetPolicy: TargetPolicyUsed?,
        generatedAt: Date?
    ) {
        self.requestedDate = requestedDate
        self.planDate = planDate
        self.planSha256 = planSha256
        self.inputsFingerprint = inputsFingerprint
        self.runId = runId
        self.versionId = versionId
        self.plan = plan
        self.planItems = planItems
        self.targetPolicy = targetPolicy
        self.generatedAt = generatedAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        guard try container.decode(String.self, forKey: .state) == "completed" else {
            throw DecodingError.dataCorruptedError(
                forKey: .state, in: container, debugDescription: "expected completed state")
        }
        requestedDate = try container.decode(String.self, forKey: .requestedDate)
        planDate = try container.decode(String.self, forKey: .planDate)
        planSha256 = try container.decode(String.self, forKey: .planSha256)
        inputsFingerprint = try container.decode(String.self, forKey: .inputsFingerprint)
        runId = try container.decode(UUID.self, forKey: .runId)
        versionId = try container.decode(UUID.self, forKey: .versionId)
        plan = try container.decode(DailyPlanArtifact.self, forKey: .plan)
        planItems = try container.decodeIfPresent([PlanItemReference].self, forKey: .planItems)
        targetPolicy = try container.decodeIfPresent(TargetPolicyUsed.self, forKey: .targetPolicy)
        generatedAt = try container.decodeIfPresent(Date.self, forKey: .generatedAt)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("completed", forKey: .state)
        try container.encode(requestedDate, forKey: .requestedDate)
        try container.encode(planDate, forKey: .planDate)
        try container.encode(planSha256, forKey: .planSha256)
        try container.encode(inputsFingerprint, forKey: .inputsFingerprint)
        try container.encode(runId, forKey: .runId)
        try container.encode(versionId, forKey: .versionId)
        try container.encode(plan, forKey: .plan)
        try container.encodeIfPresent(planItems, forKey: .planItems)
        try container.encodeIfPresent(targetPolicy, forKey: .targetPolicy)
        try container.encodeIfPresent(generatedAt, forKey: .generatedAt)
    }
}

struct PlanItemReference: Codable, Equatable, Sendable {
    let itemId: UUID
    let slotIndex: Int
    let rank: Int
    let candidateId: String

    private enum CodingKeys: String, CodingKey {
        case itemId = "item_id"
        case slotIndex = "slot_index"
        case rank
        case candidateId = "candidate_id"
    }
}

/// A presentation-only reference to one candidate at its original artifact rank.
/// No nutrition, scoring, or selection logic is recomputed on-device.
struct PlanCandidatePresentation: Equatable, Sendable {
    let rank: Int
    let candidate: PlanCandidate
    let itemReference: PlanItemReference?

    var mealName: String {
        if let estimate = candidate.configurableEstimate {
            return estimate.definition.displayName
        }
        return candidate.lines.map(\.nameNormalized).joined(separator: " + ")
    }
}

/// The bounded Today presentation for one canonical plan slot.
struct PlanSlotPresentation: Equatable, Sendable {
    let primary: PlanCandidatePresentation?
    let alternatives: [PlanCandidatePresentation]
}

extension CompletedDayPlan {
    static let maximumVisibleAlternatives = 3

    func itemReference(
        slotIndex: Int,
        rank: Int,
        candidateId: String
    ) -> PlanItemReference? {
        planItems?.first {
            $0.slotIndex == slotIndex && $0.rank == rank && $0.candidateId == candidateId
        }
    }

    func presentation(forSlotAt slotIndex: Int) -> PlanSlotPresentation? {
        guard plan.slots.indices.contains(slotIndex) else { return nil }
        let candidates = plan.slots[slotIndex].candidates
        let visible = candidates.prefix(1 + Self.maximumVisibleAlternatives)
        let presented = visible.enumerated().map { index, candidate in
            let rank = index + 1
            return PlanCandidatePresentation(
                rank: rank,
                candidate: candidate,
                itemReference: itemReference(
                    slotIndex: slotIndex,
                    rank: rank,
                    candidateId: candidate.candidateId
                )
            )
        }
        return PlanSlotPresentation(
            primary: presented.first,
            alternatives: Array(presented.dropFirst())
        )
    }
}

/// Generation supplies planDate/runId; retrieval's historical no-plan shape
/// supplies only requestedDate, reasons, and the fingerprint.
struct NoPlanDay: Codable, Equatable, Sendable {
    let requestedDate: String
    let planDate: String?
    let reasonCodes: [String]
    let inputsFingerprint: String
    let runId: UUID?

    private enum CodingKeys: String, CodingKey {
        case state
        case requestedDate = "requested_date"
        case planDate = "plan_date"
        case reasonCodes = "reason_codes"
        case inputsFingerprint = "inputs_fingerprint"
        case runId = "run_id"
    }

    init(
        requestedDate: String,
        planDate: String?,
        reasonCodes: [String],
        inputsFingerprint: String,
        runId: UUID?
    ) {
        self.requestedDate = requestedDate
        self.planDate = planDate
        self.reasonCodes = reasonCodes
        self.inputsFingerprint = inputsFingerprint
        self.runId = runId
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        guard try container.decode(String.self, forKey: .state) == "no_plan" else {
            throw DecodingError.dataCorruptedError(
                forKey: .state, in: container, debugDescription: "expected no_plan state")
        }
        requestedDate = try container.decode(String.self, forKey: .requestedDate)
        planDate = try container.decodeIfPresent(String.self, forKey: .planDate)
        reasonCodes = try container.decode([String].self, forKey: .reasonCodes)
        inputsFingerprint = try container.decode(String.self, forKey: .inputsFingerprint)
        runId = try container.decodeIfPresent(UUID.self, forKey: .runId)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("no_plan", forKey: .state)
        try container.encode(requestedDate, forKey: .requestedDate)
        try container.encodeIfPresent(planDate, forKey: .planDate)
        try container.encode(reasonCodes, forKey: .reasonCodes)
        try container.encode(inputsFingerprint, forKey: .inputsFingerprint)
        try container.encodeIfPresent(runId, forKey: .runId)
    }
}

struct NotGeneratedDay: Codable, Equatable, Sendable {
    let requestedDate: String

    private enum CodingKeys: String, CodingKey {
        case state
        case requestedDate = "requested_date"
    }

    init(requestedDate: String) {
        self.requestedDate = requestedDate
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        guard try container.decode(String.self, forKey: .state) == "not_generated" else {
            throw DecodingError.dataCorruptedError(
                forKey: .state, in: container, debugDescription: "expected not_generated state")
        }
        requestedDate = try container.decode(String.self, forKey: .requestedDate)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode("not_generated", forKey: .state)
        try container.encode(requestedDate, forKey: .requestedDate)
    }
}

struct TargetPolicyUsed: Codable, Equatable, Sendable {
    let versionId: UUID
    let policyVersion: String
    let payloadSha256: String
    let approvedAt: Date?

    private enum CodingKeys: String, CodingKey {
        case versionId = "version_id"
        case policyVersion = "policy_version"
        case payloadSha256 = "payload_sha256"
        case approvedAt = "approved_at"
    }
}

struct TargetPolicyLatest: Codable, Equatable, Sendable {
    let versionId: UUID
    let policyVersion: String
    let goals: [TargetPolicyGoal]
    let payloadSha256: String
    let approvedAt: Date?

    private enum CodingKeys: String, CodingKey {
        case versionId = "version_id"
        case policyVersion = "policy_version"
        case goals
        case payloadSha256 = "payload_sha256"
        case approvedAt = "approved_at"
    }
}

struct TargetPolicyGoal: Codable, Equatable, Sendable {
    let nutrient: String
    let kind: String
    /// Exact backend decimal strings. They are never converted to Double.
    let value: String
    let weight: String
}

struct DailyPlanArtifact: Codable, Equatable, Sendable {
    let artifactKind: String
    let planDate: String
    let policyVersions: PlanPolicyVersions
    let menuSnapshotSha256: String
    let slots: [PlanSlot]
    let status: String

    private enum CodingKeys: String, CodingKey {
        case artifactKind = "artifact_kind"
        case planDate = "plan_date"
        case policyVersions = "policy_versions"
        case menuSnapshotSha256 = "menu_snapshot_sha256"
        case slots
        case status
    }
}

struct PlanPolicyVersions: Codable, Equatable, Sendable {
    let engine: String
    let planner: String
    let schedule: String
    let target: String
}

struct PlanSlot: Codable, Equatable, Sendable {
    let context: String
    let menuPeriod: String
    let status: String
    let window: [String]
    let failureReasons: [String]
    let rejectionCounts: [String: Int]
    let rejectionDetails: [String]
    let candidates: [PlanCandidate]

    private enum CodingKeys: String, CodingKey {
        case context
        case menuPeriod = "menu_period"
        case status
        case window
        case failureReasons = "failure_reasons"
        case rejectionCounts = "rejection_counts"
        case rejectionDetails = "rejection_details"
        case candidates
    }
}

struct PlanCandidate: Codable, Equatable, Sendable {
    let candidateId: String
    let candidateKind: String?
    let caloriesKcal: String?
    let categoryNames: [String]
    let configurableEstimate: PlanConfigurableEstimate?
    let dietaryTags: [String]
    let lines: [PlanLine]
    let provenance: PlanProvenance
    let score: PlanScore
    let totals: PlanNutritionFacts

    private enum CodingKeys: String, CodingKey {
        case candidateId = "candidate_id"
        case candidateKind = "candidate_kind"
        case caloriesKcal = "calories_kcal"
        case categoryNames = "category_names"
        case configurableEstimate = "configurable_estimate"
        case dietaryTags = "dietary_tags"
        case lines
        case provenance
        case score
        case totals
    }
}

struct PlanConfigurableEstimate: Codable, Equatable, Sendable {
    let availability: PlanEstimateAvailability
    let definition: PlanConfigurableDefinition
    let evidenceDigest: String
    let nutritionalScore: PlanScore
    let scoreAdjustments: [String: String]

    private enum CodingKeys: String, CodingKey {
        case availability
        case definition
        case evidenceDigest = "evidence_digest"
        case nutritionalScore = "nutritional_score"
        case scoreAdjustments = "score_adjustments"
    }
}

struct PlanEstimateAvailability: Codable, Equatable, Sendable {
    let campusId: Int
    let categoryName: String
    let foodId: UUID
    let menuPeriod: String
    let menuSnapshotSha256: String
    let nutritionSnapshotSha256: String?
    let nutritionSourceState: String?
    let occurrenceOrdinal: Int
    let offeringId: UUID
    let serviceDate: String
    let sourcePageSnapshotSha256: String
    let sourceMid: String

    private enum CodingKeys: String, CodingKey {
        case campusId = "campus_id"
        case categoryName = "category_name"
        case foodId = "food_id"
        case menuPeriod = "menu_period"
        case menuSnapshotSha256 = "menu_snapshot_sha256"
        case nutritionSnapshotSha256 = "nutrition_snapshot_sha256"
        case nutritionSourceState = "nutrition_source_state"
        case occurrenceOrdinal = "occurrence_ordinal"
        case offeringId = "offering_id"
        case serviceDate = "service_date"
        case sourcePageSnapshotSha256 = "source_page_snapshot_sha256"
        case sourceMid = "source_mid"
    }
}

struct PlanConfigurableDefinition: Codable, Equatable, Sendable {
    let campusId: Int
    let configurationSummary: String
    let configurationVersion: String
    let definitionId: String
    let definitionVersion: String
    let displayName: String
    let estimate: PlanConfigurableNutritionEstimate
    let selectedComponents: [PlanSelectedComponent]
    let sourceNameNormalized: String
    let templateVersion: String

    private enum CodingKeys: String, CodingKey {
        case campusId = "campus_id"
        case configurationSummary = "configuration_summary"
        case configurationVersion = "configuration_version"
        case definitionId = "definition_id"
        case definitionVersion = "definition_version"
        case displayName = "display_name"
        case estimate
        case selectedComponents = "selected_components"
        case sourceNameNormalized = "source_name_normalized"
        case templateVersion = "template_version"
    }
}

struct PlanConfigurableNutritionEstimate: Codable, Equatable, Sendable {
    let caveats: [String]
    let confidence: String?
    let resolvedComponents: [PlanResolvedComponent]
    let state: String
    let totals: PlanNutritionFacts
    let unknownNutrients: [String]
    let unresolvedComponents: [PlanUnresolvedComponent]

    private enum CodingKeys: String, CodingKey {
        case caveats
        case confidence
        case resolvedComponents = "resolved_components"
        case state
        case totals
        case unknownNutrients = "unknown_nutrients"
        case unresolvedComponents = "unresolved_components"
    }
}

struct PlanSelectedComponent: Codable, Equatable, Sendable {
    let componentId: String
    let portion: PlanComponentPortion?

    private enum CodingKeys: String, CodingKey {
        case componentId = "component_id"
        case portion
    }
}

struct PlanResolvedComponent: Codable, Equatable, Sendable {
    let componentId: String
    let multiplier: String
    let nutritionReference: PlanNutritionReference
    let portion: PlanComponentPortion

    private enum CodingKeys: String, CodingKey {
        case componentId = "component_id"
        case multiplier
        case nutritionReference = "nutrition_reference"
        case portion
    }
}

struct PlanUnresolvedComponent: Codable, Equatable, Sendable {
    let componentId: String
    let reason: String

    private enum CodingKeys: String, CodingKey {
        case componentId = "component_id"
        case reason
    }
}

struct PlanComponentPortion: Codable, Equatable, Sendable {
    let amount: String
    let evidence: PlanEvidenceReference
    let unit: String
}

struct PlanNutritionReference: Codable, Equatable, Sendable {
    let basisAmount: String
    let basisUnit: String
    let caveats: [String]
    let evidence: PlanEvidenceReference
    let facts: PlanNutritionFacts

    private enum CodingKeys: String, CodingKey {
        case basisAmount = "basis_amount"
        case basisUnit = "basis_unit"
        case caveats
        case evidence
        case facts
    }
}

struct PlanEvidenceReference: Codable, Equatable, Sendable {
    let citationUrls: [String]
    let description: String
    let referenceId: String
    let sourceClass: String
    let version: String

    private enum CodingKeys: String, CodingKey {
        case citationUrls = "citation_urls"
        case description
        case referenceId = "reference_id"
        case sourceClass = "source_class"
        case version
    }
}

struct PlanLine: Codable, Equatable, Sendable {
    let categoryName: String
    let foodId: UUID
    let nameNormalized: String
    let occurrenceOrdinal: Int
    let offeringId: UUID
    let parserVersion: String
    let profileContentSha256: String
    let servings: String
    let sourceMid: String

    private enum CodingKeys: String, CodingKey {
        case categoryName = "category_name"
        case foodId = "food_id"
        case nameNormalized = "name_normalized"
        case occurrenceOrdinal = "occurrence_ordinal"
        case offeringId = "offering_id"
        case parserVersion = "parser_version"
        case profileContentSha256 = "profile_content_sha256"
        case servings
        case sourceMid = "source_mid"
    }
}

struct PlanProvenance: Codable, Equatable, Sendable {
    let offeringIds: [UUID]
    let foodIds: [UUID]
    let profileContentSha256s: [String]

    private enum CodingKeys: String, CodingKey {
        case offeringIds = "offering_ids"
        case foodIds = "food_ids"
        case profileContentSha256s = "profile_content_sha256s"
    }
}

struct PlanScore: Codable, Equatable, Sendable {
    let breakdown: [String: String]
    let total: String
}

struct PlanNutritionFacts: Codable, Equatable, Sendable {
    let confidence: String
    let declaredUnavailable: [String]
    let presences: [String: String]
    let publishedZero: [String]
    /// Exact backend decimal strings. They are never converted to Double.
    let quantities: [String: String]

    private enum CodingKeys: String, CodingKey {
        case confidence
        case declaredUnavailable = "declared_unavailable"
        case presences
        case publishedZero = "published_zero"
        case quantities
    }
}

/// Stable, locale-independent calendar-date encoding for M7 query/body dates.
enum WireDay {
    static func string(from date: Date) -> String {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: date)
    }
}

enum DailyNutritionCompleteness: String, Codable, Equatable, Sendable {
    case complete
    case partial
    case unavailable
}

enum DailyNutritionAuthority: String, Codable, Equatable, Sendable {
    case userEntered = "user_entered"
    case official
    case estimated
    case partial
    case unknown
}

struct DailyNutritionTarget: Codable, Equatable, Sendable {
    let policyVersionId: UUID
    let policyVersion: String
    let caloriesKcal: String?
    let caloriesGoalKind: String?
    let proteinG: String?
    let proteinGoalKind: String?

    private enum CodingKeys: String, CodingKey {
        case policyVersionId = "policy_version_id"
        case policyVersion = "policy_version"
        case caloriesKcal = "calories_kcal"
        case caloriesGoalKind = "calories_goal_kind"
        case proteinG = "protein_g"
        case proteinGoalKind = "protein_goal_kind"
    }
}

struct DailyConsumedNutritionItem: Codable, Equatable, Sendable {
    let entryId: UUID
    let recordedAt: Date
    let planRunId: UUID?
    let planVersionId: UUID?
    let planItemId: UUID?
    let mealContext: String
    let candidateId: String
    let itemName: String
    let configurationSummary: String?
    let nutritionAuthority: DailyNutritionAuthority
    let confidence: String?
    let caloriesKcal: String?
    let proteinG: String?
    let unknownNutrients: [String]
    let provenanceSummary: String
    var sourceSystem: String? = nil
    var customFoodId: UUID? = nil
    var customFoodVersionId: UUID? = nil
    var consumedAmount: String? = nil
    var consumedUnit: String? = nil

    private enum CodingKeys: String, CodingKey {
        case entryId = "entry_id"
        case recordedAt = "recorded_at"
        case planRunId = "plan_run_id"
        case planVersionId = "plan_version_id"
        case planItemId = "plan_item_id"
        case mealContext = "meal_context"
        case candidateId = "candidate_id"
        case itemName = "item_name"
        case configurationSummary = "configuration_summary"
        case nutritionAuthority = "nutrition_authority"
        case confidence
        case caloriesKcal = "calories_kcal"
        case proteinG = "protein_g"
        case unknownNutrients = "unknown_nutrients"
        case provenanceSummary = "provenance_summary"
        case sourceSystem = "source_system"
        case customFoodId = "custom_food_id"
        case customFoodVersionId = "custom_food_version_id"
        case consumedAmount = "consumed_amount"
        case consumedUnit = "consumed_unit"
    }
}

struct DailyNutritionLedgerResponse: Codable, Equatable, Sendable {
    let localDate: String
    let timezone: String
    let target: DailyNutritionTarget?
    let consumedItemCount: Int
    let knownCaloriesConsumed: String?
    let knownProteinGConsumed: String?
    let remainingKnownCalories: String?
    let remainingKnownProteinG: String?
    let nutritionCompleteness: DailyNutritionCompleteness
    let nutritionAuthorities: [DailyNutritionAuthority]
    let unknownNutrients: [String]
    let consumedItems: [DailyConsumedNutritionItem]
    let reasonCodes: [String]

    private enum CodingKeys: String, CodingKey {
        case localDate = "local_date"
        case timezone
        case target
        case consumedItemCount = "consumed_item_count"
        case knownCaloriesConsumed = "known_calories_consumed"
        case knownProteinGConsumed = "known_protein_g_consumed"
        case remainingKnownCalories = "remaining_known_calories"
        case remainingKnownProteinG = "remaining_known_protein_g"
        case nutritionCompleteness = "nutrition_completeness"
        case nutritionAuthorities = "nutrition_authorities"
        case unknownNutrients = "unknown_nutrients"
        case consumedItems = "consumed_items"
        case reasonCodes = "reason_codes"
    }
}

enum NutritionHistoryTargetStatus: String, Codable, Equatable, Sendable {
    case available
    case unavailable
    case targetChangedDuringDay = "target_changed_during_day"
}

enum CalorieAdherenceStatus: String, Codable, Equatable, Sendable {
    case belowTarget = "below_target"
    case atTarget = "at_target"
    case aboveTarget = "above_target"
    case unavailable
}

enum ProteinAdherenceStatus: String, Codable, Equatable, Sendable {
    case belowTarget = "below_target"
    case atOrAboveTarget = "at_or_above_target"
    case unavailable
}

struct NutritionHistoryDayResponse: Codable, Equatable, Sendable {
    let localDate: String
    let timezone: String
    let target: DailyNutritionTarget?
    let consumedEventCount: Int
    let knownCaloriesConsumed: String?
    let knownProteinGConsumed: String?
    let remainingKnownCalories: String?
    let remainingKnownProteinG: String?
    let nutritionCompleteness: DailyNutritionCompleteness
    let nutritionAuthorities: [DailyNutritionAuthority]
    let unknownNutrients: [String]
    let consumedItems: [DailyConsumedNutritionItem]
    let reasonCodes: [String]
    let targetStatus: NutritionHistoryTargetStatus
    let calorieAdherence: CalorieAdherenceStatus
    let proteinAdherence: ProteinAdherenceStatus

    private enum CodingKeys: String, CodingKey {
        case localDate = "local_date"
        case timezone
        case target
        case consumedEventCount = "consumed_event_count"
        case knownCaloriesConsumed = "known_calories_consumed"
        case knownProteinGConsumed = "known_protein_g_consumed"
        case remainingKnownCalories = "remaining_known_calories"
        case remainingKnownProteinG = "remaining_known_protein_g"
        case nutritionCompleteness = "nutrition_completeness"
        case nutritionAuthorities = "nutrition_authorities"
        case unknownNutrients = "unknown_nutrients"
        case consumedItems = "consumed_items"
        case reasonCodes = "reason_codes"
        case targetStatus = "target_status"
        case calorieAdherence = "calorie_adherence"
        case proteinAdherence = "protein_adherence"
    }
}

struct NutritionHistorySummaryResponse: Codable, Equatable, Sendable {
    let daysWithConsumption: Int
    let daysComplete: Int
    let daysPartial: Int
    let daysUnavailable: Int
    let daysTargetAvailable: Int
    let daysTargetChanged: Int
    let knownCaloriesTotal: String?
    let knownProteinGTotal: String?

    private enum CodingKeys: String, CodingKey {
        case daysWithConsumption = "days_with_consumption"
        case daysComplete = "days_complete"
        case daysPartial = "days_partial"
        case daysUnavailable = "days_unavailable"
        case daysTargetAvailable = "days_target_available"
        case daysTargetChanged = "days_target_changed"
        case knownCaloriesTotal = "known_calories_total"
        case knownProteinGTotal = "known_protein_g_total"
    }
}

struct NutritionHistory7DayResponse: Codable, Equatable, Sendable {
    let startDate: String
    let endDate: String
    let timezone: String
    let days: [NutritionHistoryDayResponse]
    let summary: NutritionHistorySummaryResponse

    private enum CodingKeys: String, CodingKey {
        case startDate = "start_date"
        case endDate = "end_date"
        case timezone
        case days
        case summary
    }
}
