import Foundation

enum GoalDirectionDTO: String, Codable, CaseIterable, Equatable, Sendable {
    case maintain
    case gain
    case lose
}

struct GoalPolicyResponse: Decodable, Equatable, Sendable {
    let versionId: UUID
    let policyVersion: String
    let direction: GoalDirectionDTO
    /// Exact backend Decimal text; never converted to Double.
    let desiredRateKgPerWeek: String
    let payloadSha256: String
    let createdAt: Date

    private enum CodingKeys: String, CodingKey {
        case versionId = "version_id"
        case policyVersion = "policy_version"
        case direction
        case desiredRateKgPerWeek = "desired_rate_kg_per_week"
        case payloadSha256 = "payload_sha256"
        case createdAt = "created_at"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        versionId = try values.decode(UUID.self, forKey: .versionId)
        policyVersion = try values.decode(String.self, forKey: .policyVersion)
        direction = try values.decode(GoalDirectionDTO.self, forKey: .direction)
        desiredRateKgPerWeek = try values.decode(String.self, forKey: .desiredRateKgPerWeek)
        payloadSha256 = try values.decode(String.self, forKey: .payloadSha256)
        createdAt = try values.decode(Date.self, forKey: .createdAt)
        guard !policyVersion.isEmpty,
              M10Wire.isDecimal(desiredRateKgPerWeek),
              M10Wire.isDigest(payloadSha256)
        else {
            throw DecodingError.dataCorruptedError(
                forKey: .desiredRateKgPerWeek,
                in: values,
                debugDescription: "invalid goal-policy evidence"
            )
        }
    }
}

struct TargetPolicyApprovalResponse: Decodable, Equatable, Sendable {
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

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        versionId = try values.decode(UUID.self, forKey: .versionId)
        policyVersion = try values.decode(String.self, forKey: .policyVersion)
        payloadSha256 = try values.decode(String.self, forKey: .payloadSha256)
        approvedAt = try values.decodeIfPresent(Date.self, forKey: .approvedAt)
        guard !policyVersion.isEmpty, M10Wire.isDigest(payloadSha256) else {
            throw DecodingError.dataCorruptedError(
                forKey: .payloadSha256,
                in: values,
                debugDescription: "invalid target-policy approval evidence"
            )
        }
    }
}

enum TargetReviewStatusDTO: String, Decodable, Equatable, Sendable {
    case evidenceUnavailable = "evidence_unavailable"
    case cooldownHold = "cooldown_hold"
    case withinBand = "within_band"
    case boundHold = "bound_hold"
    case recommendationReady = "recommendation_ready"
}

struct TargetReviewTrendDTO: Decodable, Equatable, Sendable {
    let status: BodyMassTrendStatus
    let algorithmVersion: String
    let inputDigest: String
    let weeklyRateKg: String?

    private enum CodingKeys: String, CodingKey {
        case status
        case algorithmVersion = "algorithm_version"
        case inputDigest = "input_digest"
        case weeklyRateKg = "weekly_rate_kg"
    }
}

struct TargetReviewGoalPolicyDTO: Decodable, Equatable, Sendable {
    let versionId: UUID
    let policyVersion: String
    let direction: GoalDirectionDTO
    let desiredRateKgPerWeek: String
    let payloadSha256: String
    let createdAt: Date
    let acceptableRateLowerKgPerWeek: String
    let acceptableRateUpperKgPerWeek: String

    private enum CodingKeys: String, CodingKey {
        case versionId = "version_id"
        case policyVersion = "policy_version"
        case direction
        case desiredRateKgPerWeek = "desired_rate_kg_per_week"
        case payloadSha256 = "payload_sha256"
        case createdAt = "created_at"
        case acceptableRateLowerKgPerWeek = "acceptable_rate_lower_kg_per_week"
        case acceptableRateUpperKgPerWeek = "acceptable_rate_upper_kg_per_week"
    }
}

struct TargetReviewTargetPolicyDTO: Decodable, Equatable, Sendable {
    let versionId: UUID
    let policyVersion: String
    let payloadSha256: String
    let approvedAt: Date
    let currentCaloriesKcal: String
    let proposedCaloriesKcal: String?
    let calorieDelta: String?

    private enum CodingKeys: String, CodingKey {
        case versionId = "version_id"
        case policyVersion = "policy_version"
        case payloadSha256 = "payload_sha256"
        case approvedAt = "approved_at"
        case currentCaloriesKcal = "current_calories_kcal"
        case proposedCaloriesKcal = "proposed_calories_kcal"
        case calorieDelta = "calorie_delta"
    }
}

struct TargetReviewPolicyDTO: Decodable, Equatable, Sendable {
    let policyVersion: String
    let deadbandKgPerWeek: String
    let adjustmentStepKcal: String
    let cooldownDays: Int
    let lowerCalorieBound: String?
    let upperCalorieBound: String?

    private enum CodingKeys: String, CodingKey {
        case policyVersion = "policy_version"
        case deadbandKgPerWeek = "deadband_kg_per_week"
        case adjustmentStepKcal = "adjustment_step_kcal"
        case cooldownDays = "cooldown_days"
        case lowerCalorieBound = "lower_calorie_bound"
        case upperCalorieBound = "upper_calorie_bound"
    }
}

struct TargetReviewDetail: Decodable, Equatable, Sendable {
    let created: Bool
    let reviewId: UUID
    let createdAt: Date
    let status: TargetReviewStatusDTO
    let reasonCodes: [String]
    let asOfDate: String
    let timezone: String
    let trend: TargetReviewTrendDTO
    let goalPolicy: TargetReviewGoalPolicyDTO
    let targetPolicy: TargetReviewTargetPolicyDTO
    let reviewPolicy: TargetReviewPolicyDTO
    let recommendationDigest: String

    private enum CodingKeys: String, CodingKey {
        case created
        case reviewId = "review_id"
        case createdAt = "created_at"
        case status
        case reasonCodes = "reason_codes"
        case asOfDate = "as_of_date"
        case timezone
        case trend
        case goalPolicy = "goal_policy"
        case targetPolicy = "target_policy"
        case reviewPolicy = "review_policy"
        case recommendationDigest = "recommendation_digest"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        created = try values.decode(Bool.self, forKey: .created)
        reviewId = try values.decode(UUID.self, forKey: .reviewId)
        createdAt = try values.decode(Date.self, forKey: .createdAt)
        status = try values.decode(TargetReviewStatusDTO.self, forKey: .status)
        reasonCodes = try values.decode([String].self, forKey: .reasonCodes)
        asOfDate = try values.decode(String.self, forKey: .asOfDate)
        timezone = try values.decode(String.self, forKey: .timezone)
        trend = try values.decode(TargetReviewTrendDTO.self, forKey: .trend)
        goalPolicy = try values.decode(TargetReviewGoalPolicyDTO.self, forKey: .goalPolicy)
        targetPolicy = try values.decode(TargetReviewTargetPolicyDTO.self, forKey: .targetPolicy)
        reviewPolicy = try values.decode(TargetReviewPolicyDTO.self, forKey: .reviewPolicy)
        recommendationDigest = try values.decode(String.self, forKey: .recommendationDigest)

        let decimals = [
            trend.weeklyRateKg,
            goalPolicy.desiredRateKgPerWeek,
            goalPolicy.acceptableRateLowerKgPerWeek,
            goalPolicy.acceptableRateUpperKgPerWeek,
            targetPolicy.currentCaloriesKcal,
            targetPolicy.proposedCaloriesKcal,
            targetPolicy.calorieDelta,
            reviewPolicy.deadbandKgPerWeek,
            reviewPolicy.adjustmentStepKcal,
            reviewPolicy.lowerCalorieBound,
            reviewPolicy.upperCalorieBound,
        ]
        guard M10Wire.isLocalDay(asOfDate),
              !timezone.isEmpty,
              decimals.compactMap({ $0 }).allSatisfy(M10Wire.isDecimal),
              M10Wire.isDigest(trend.inputDigest),
              M10Wire.isDigest(goalPolicy.payloadSha256),
              M10Wire.isDigest(targetPolicy.payloadSha256),
              M10Wire.isDigest(recommendationDigest),
              reviewPolicy.cooldownDays >= 0
        else {
            throw DecodingError.dataCorruptedError(
                forKey: .recommendationDigest,
                in: values,
                debugDescription: "invalid target-review evidence"
            )
        }
        let hasProposal = targetPolicy.proposedCaloriesKcal != nil && targetPolicy.calorieDelta != nil
        guard (status == .recommendationReady) == hasProposal else {
            throw DecodingError.dataCorruptedError(
                forKey: .targetPolicy,
                in: values,
                debugDescription: "review status and proposal payload disagree"
            )
        }
    }
}

enum TargetReviewResponse: Decodable, Equatable, Sendable {
    case noGoalPolicy
    case noTargetPolicy
    case review(TargetReviewDetail)

    private enum CodingKeys: String, CodingKey { case state }
    private enum State: String, Decodable {
        case noGoalPolicy = "no_goal_policy"
        case noTargetPolicy = "no_target_policy"
        case review
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        switch try values.decode(State.self, forKey: .state) {
        case .noGoalPolicy: self = .noGoalPolicy
        case .noTargetPolicy: self = .noTargetPolicy
        case .review: self = .review(try TargetReviewDetail(from: decoder))
        }
    }
}

enum TargetReviewDecisionDTO: String, Codable, Equatable, Sendable {
    case approved
    case rejected
}

struct TargetReviewDecisionResponse: Decodable, Equatable, Sendable {
    let created: Bool
    let targetReviewId: UUID
    let targetReviewDecisionId: UUID
    let decision: TargetReviewDecisionDTO
    let clientEventId: UUID
    let resultingTargetPolicyId: UUID?
    let decidedAt: Date

    private enum CodingKeys: String, CodingKey {
        case created
        case targetReviewId = "target_review_id"
        case targetReviewDecisionId = "target_review_decision_id"
        case decision
        case clientEventId = "client_event_id"
        case resultingTargetPolicyId = "resulting_target_policy_id"
        case decidedAt = "decided_at"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        created = try values.decode(Bool.self, forKey: .created)
        targetReviewId = try values.decode(UUID.self, forKey: .targetReviewId)
        targetReviewDecisionId = try values.decode(UUID.self, forKey: .targetReviewDecisionId)
        decision = try values.decode(TargetReviewDecisionDTO.self, forKey: .decision)
        clientEventId = try values.decode(UUID.self, forKey: .clientEventId)
        resultingTargetPolicyId = try values.decodeIfPresent(
            UUID.self, forKey: .resultingTargetPolicyId)
        decidedAt = try values.decode(Date.self, forKey: .decidedAt)
        guard (decision == .approved) == (resultingTargetPolicyId != nil) else {
            throw DecodingError.dataCorruptedError(
                forKey: .resultingTargetPolicyId,
                in: values,
                debugDescription: "decision and resulting target disagree"
            )
        }
    }
}

private enum M10Wire {
    static func isDecimal(_ raw: String) -> Bool {
        let pattern = #"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"#
        return raw.range(of: pattern, options: .regularExpression) != nil
            && Decimal(string: raw, locale: Locale(identifier: "en_US_POSIX")) != nil
    }

    static func isDigest(_ raw: String) -> Bool {
        raw.count == 64 && raw.allSatisfy(\.isHexDigit)
    }

    static func isLocalDay(_ raw: String) -> Bool {
        let format = DateFormatter()
        format.calendar = Calendar(identifier: .gregorian)
        format.locale = Locale(identifier: "en_US_POSIX")
        format.timeZone = TimeZone(secondsFromGMT: 0)
        format.dateFormat = "yyyy-MM-dd"
        format.isLenient = false
        guard let date = format.date(from: raw) else { return false }
        return format.string(from: date) == raw
    }
}

enum ProteinProposalDecisionDTO: String, Codable, Equatable, Sendable {
    case approved
    case rejected
}

enum ProteinTargetKindDTO: String, Decodable, Equatable, Sendable {
    case floor
}

struct ProteinProposalBodyMassEvidenceDTO: Decodable, Equatable, Sendable {
    let measuredAt: Date
    let sampleUUID: UUID
    let valueKg: String

    private enum CodingKeys: String, CodingKey {
        case measuredAt = "measured_at"
        case sampleUUID = "sample_uuid"
        case valueKg = "value_kg"
    }
}

struct ProteinProposalCalculationDTO: Decodable, Equatable, Sendable {
    let bodyMass: ProteinProposalBodyMassEvidenceDTO
    let formula: String
    let gramsPerPound: String
    let kilogramsPerPound: String
    let maxEvidenceAgeDays: Int
    let proposedProteinG: String
    let rounding: String
    let roundingQuantumG: String
    let targetKind: ProteinTargetKindDTO
    let targetPolicy: String

    private enum CodingKeys: String, CodingKey {
        case bodyMass = "body_mass"
        case formula
        case gramsPerPound = "grams_per_pound"
        case kilogramsPerPound = "kilograms_per_pound"
        case maxEvidenceAgeDays = "max_evidence_age_days"
        case proposedProteinG = "proposed_protein_g"
        case rounding
        case roundingQuantumG = "rounding_quantum_g"
        case targetKind = "target_kind"
        case targetPolicy = "target_policy"
    }
}

struct ProteinProposalDecisionDetailDTO: Decodable, Equatable, Sendable {
    let clientEventId: UUID
    let decidedAt: Date
    let decisionId: UUID
    let rationale: String
    let resultingTargetPolicyVersionId: UUID?

    private enum CodingKeys: String, CodingKey {
        case clientEventId = "client_event_id"
        case decidedAt = "decided_at"
        case decisionId = "decision_id"
        case rationale
        case resultingTargetPolicyVersionId = "resulting_target_policy_version_id"
    }
}

struct ProteinTargetProposalResponse: Decodable, Equatable, Sendable {
    let proposalId: UUID
    let priorTargetPolicyVersionId: UUID
    let bodyMassSampleUUID: UUID
    let policyVersion: String
    let targetKind: ProteinTargetKindDTO
    let bodyMassKg: String
    let gramsPerPound: String
    let proposedProteinG: String
    let evidenceDigest: String
    let calculation: ProteinProposalCalculationDTO
    let rationale: String
    let provenance: String
    let generatedAt: Date
    let decisionStatus: ProteinProposalDecisionDTO?
    let requiresExplicitApproval: Bool
    let decision: ProteinProposalDecisionDetailDTO?
    let created: Bool?

    var isPending: Bool { decisionStatus == nil }

    private enum CodingKeys: String, CodingKey {
        case proposalId = "proposal_id"
        case priorTargetPolicyVersionId = "prior_target_policy_version_id"
        case bodyMassSampleUUID = "body_mass_sample_uuid"
        case policyVersion = "policy_version"
        case targetKind = "target_kind"
        case bodyMassKg = "body_mass_kg"
        case gramsPerPound = "grams_per_pound"
        case proposedProteinG = "proposed_protein_g"
        case evidenceDigest = "evidence_digest"
        case calculation
        case rationale
        case provenance
        case generatedAt = "generated_at"
        case decisionStatus = "decision_status"
        case requiresExplicitApproval = "requires_explicit_approval"
        case decision
        case created
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        proposalId = try values.decode(UUID.self, forKey: .proposalId)
        priorTargetPolicyVersionId = try values.decode(
            UUID.self, forKey: .priorTargetPolicyVersionId)
        bodyMassSampleUUID = try values.decode(UUID.self, forKey: .bodyMassSampleUUID)
        policyVersion = try values.decode(String.self, forKey: .policyVersion)
        targetKind = try values.decode(ProteinTargetKindDTO.self, forKey: .targetKind)
        bodyMassKg = try values.decode(String.self, forKey: .bodyMassKg)
        gramsPerPound = try values.decode(String.self, forKey: .gramsPerPound)
        proposedProteinG = try values.decode(String.self, forKey: .proposedProteinG)
        evidenceDigest = try values.decode(String.self, forKey: .evidenceDigest)
        calculation = try values.decode(ProteinProposalCalculationDTO.self, forKey: .calculation)
        rationale = try values.decode(String.self, forKey: .rationale)
        provenance = try values.decode(String.self, forKey: .provenance)
        generatedAt = try values.decode(Date.self, forKey: .generatedAt)
        let rawStatus = try values.decode(String.self, forKey: .decisionStatus)
        if rawStatus == "pending" {
            decisionStatus = nil
        } else if let parsedStatus = ProteinProposalDecisionDTO(rawValue: rawStatus) {
            decisionStatus = parsedStatus
        } else {
            throw DecodingError.dataCorruptedError(
                forKey: .decisionStatus,
                in: values,
                debugDescription: "unsupported protein-proposal decision state"
            )
        }
        requiresExplicitApproval = try values.decode(Bool.self, forKey: .requiresExplicitApproval)
        decision = try values.decodeIfPresent(ProteinProposalDecisionDetailDTO.self, forKey: .decision)
        created = try values.decodeIfPresent(Bool.self, forKey: .created)

        let decimals = [
            bodyMassKg,
            gramsPerPound,
            proposedProteinG,
            calculation.bodyMass.valueKg,
            calculation.gramsPerPound,
            calculation.kilogramsPerPound,
            calculation.proposedProteinG,
            calculation.roundingQuantumG,
        ]
        let decisionShapeIsValid: Bool = switch decisionStatus {
        case nil:
            requiresExplicitApproval && decision == nil
        case .approved:
            !requiresExplicitApproval && decision?.resultingTargetPolicyVersionId != nil
        case .rejected:
            !requiresExplicitApproval && decision != nil
                && decision?.resultingTargetPolicyVersionId == nil
        }
        guard !policyVersion.isEmpty,
              !rationale.isEmpty,
              !provenance.isEmpty,
              M10Wire.isDigest(evidenceDigest),
              decimals.allSatisfy(M10Wire.isDecimal),
              bodyMassSampleUUID == calculation.bodyMass.sampleUUID,
              bodyMassKg == calculation.bodyMass.valueKg,
              gramsPerPound == calculation.gramsPerPound,
              proposedProteinG == calculation.proposedProteinG,
              targetKind == calculation.targetKind,
              policyVersion == calculation.targetPolicy,
              calculation.maxEvidenceAgeDays >= 0,
              calculation.rounding == "ROUND_HALF_EVEN",
              decisionShapeIsValid
        else {
            throw DecodingError.dataCorruptedError(
                forKey: .calculation,
                in: values,
                debugDescription: "invalid protein-proposal evidence"
            )
        }
    }
}

struct ProteinProposalDecisionResponse: Decodable, Equatable, Sendable {
    let created: Bool
    let decisionId: UUID
    let proposalId: UUID
    let decision: ProteinProposalDecisionDTO
    let clientEventId: UUID
    let resultingTargetPolicyVersionId: UUID?
    let decidedAt: Date

    private enum CodingKeys: String, CodingKey {
        case created
        case decisionId = "decision_id"
        case proposalId = "proposal_id"
        case decision
        case clientEventId = "client_event_id"
        case resultingTargetPolicyVersionId = "resulting_target_policy_version_id"
        case decidedAt = "decided_at"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        created = try values.decode(Bool.self, forKey: .created)
        decisionId = try values.decode(UUID.self, forKey: .decisionId)
        proposalId = try values.decode(UUID.self, forKey: .proposalId)
        decision = try values.decode(ProteinProposalDecisionDTO.self, forKey: .decision)
        clientEventId = try values.decode(UUID.self, forKey: .clientEventId)
        resultingTargetPolicyVersionId = try values.decodeIfPresent(
            UUID.self, forKey: .resultingTargetPolicyVersionId)
        decidedAt = try values.decode(Date.self, forKey: .decidedAt)
        guard (decision == .approved) == (resultingTargetPolicyVersionId != nil) else {
            throw DecodingError.dataCorruptedError(
                forKey: .resultingTargetPolicyVersionId,
                in: values,
                debugDescription: "protein decision and resulting target disagree"
            )
        }
    }
}
