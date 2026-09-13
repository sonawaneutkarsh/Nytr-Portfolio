import Foundation

#if canImport(FoundationModels)
    import FoundationModels
#endif

struct OnDeviceReviewSnapshot: Decodable, Equatable, Sendable {
    let snapshot: AIReviewSnapshotDTO
    let modelInput: ReviewModelInput
    enum CodingKeys: String, CodingKey {
        case snapshot
        case modelInput = "model_input"
    }
}

/// Only this deterministic allowlist is encoded for the local model. Exact
/// summary facts are permitted; names, dates, IDs, food rows, and authentication
/// information are excluded.
struct ReviewModelInput: Codable, Equatable, Sendable {
    let goal: String
    let weightEvidence: String
    let nutritionEvidence: String
    let calorieTargetAvailable: Bool
    let proteinTargetAvailable: Bool
    let nextMeal: String
    let includesEstimates: Bool
    let limitation: String
    var qualityFlags: [String] = []
    var goalBandStatus: String? = nil
    var calorieTargetKcal: String? = nil
    var calorieTargetKind: String? = nil
    var proteinTargetG: String? = nil
    var proteinTargetKind: String? = nil
    var recordedItemCount: Int? = nil
    var recordedCaloriesKcal: String? = nil
    var recordedProteinG: String? = nil
    var recordedNutritionReasonCodes: [String]? = nil
    var recordedNutritionAuthorities: [String]? = nil
    var weightTrailing7dAverageKg: String? = nil
    var weightWeeklyRateKg: String? = nil
    var latestWeightAgeDays: Int? = nil
    var representedWeightDays: Int? = nil
    var weightCoverageSpanDays: Int? = nil
    var daysWithRecords7d: Int? = nil
    var daysWithRecords28d: Int? = nil
    var averageRecordedCalories7d: String? = nil
    var averageRecordedProteinG7d: String? = nil
    var nextMealReasonCodes: [String]? = nil
    var limitationCodes: [String]? = nil
    enum CodingKeys: String, CodingKey {
        case goal, limitation
        case qualityFlags = "quality_flags"
        case weightEvidence = "weight_evidence"
        case nutritionEvidence = "nutrition_evidence"
        case calorieTargetAvailable = "calorie_target_available"
        case proteinTargetAvailable = "protein_target_available"
        case nextMeal = "next_meal"
        case includesEstimates = "includes_estimates"
        case goalBandStatus = "goal_band_status"
        case calorieTargetKcal = "calorie_target_kcal"
        case calorieTargetKind = "calorie_target_kind"
        case proteinTargetG = "protein_target_g"
        case proteinTargetKind = "protein_target_kind"
        case recordedItemCount = "recorded_item_count"
        case recordedCaloriesKcal = "recorded_calories_kcal"
        case recordedProteinG = "recorded_protein_g"
        case recordedNutritionReasonCodes = "recorded_nutrition_reason_codes"
        case recordedNutritionAuthorities = "recorded_nutrition_authorities"
        case weightTrailing7dAverageKg = "weight_trailing_7d_average_kg"
        case weightWeeklyRateKg = "weight_weekly_rate_kg"
        case latestWeightAgeDays = "latest_weight_age_days"
        case representedWeightDays = "represented_weight_days"
        case weightCoverageSpanDays = "weight_coverage_span_days"
        case daysWithRecords7d = "days_with_records_7d"
        case daysWithRecords28d = "days_with_records_28d"
        case averageRecordedCalories7d = "average_recorded_calories_7d"
        case averageRecordedProteinG7d = "average_recorded_protein_g_7d"
        case nextMealReasonCodes = "next_meal_reason_codes"
        case limitationCodes = "limitation_codes"
    }

    var allowedNumericClaims: Set<String> {
        let strings = [
            calorieTargetKcal, proteinTargetG, recordedCaloriesKcal, recordedProteinG,
            weightTrailing7dAverageKg, weightWeeklyRateKg, averageRecordedCalories7d,
            averageRecordedProteinG7d,
        ].compactMap { $0 }
        let integers = [
            recordedItemCount, representedWeightDays, weightCoverageSpanDays,
            daysWithRecords7d, daysWithRecords28d,
            latestWeightAgeDays,
        ].compactMap { $0 }
        var allowed = Set((strings + integers.map(String.init)).map(Self.normalizedNumber))
        if let encoded = try? JSONEncoder().encode(self) {
            allowed.formUnion(Self.numericClaims(in: [String(decoding: encoded, as: UTF8.self)]))
        }
        return allowed
    }

    private static func normalizedNumber(_ raw: String) -> String {
        let value = raw.replacingOccurrences(of: ",", with: "")
        guard let decimal = Decimal(string: value, locale: Locale(identifier: "en_US_POSIX")) else {
            return value
        }
        return NSDecimalNumber(decimal: decimal).stringValue
    }

    static func numericClaims(in texts: [String]) -> Set<String> {
        let pattern = #"(?<![A-Za-z0-9])[-+]?[0-9]+(?:[.,][0-9]+)*"#
        guard let expression = try? NSRegularExpression(pattern: pattern) else { return [] }
        return Set(texts.flatMap { text in
            let range = NSRange(text.startIndex..<text.endIndex, in: text)
            return expression.matches(in: text, range: range).compactMap { match -> String? in
                guard let swiftRange = Range(match.range, in: text) else { return nil }
                return normalizedNumber(
                    String(text[swiftRange]).replacingOccurrences(of: ",", with: "")
                )
            }
        })
    }
}

enum LocalReviewFailureCategory: String, Equatable, Sendable {
    case modelUnavailable = "model_unavailable"
    case modelNotReady = "model_not_ready"
    case unsupportedLocale = "unsupported_locale"
    case generationRefused = "generation_refused"
    case generationTimeout = "generation_timeout"
    case structuredGenerationFailed = "structured_generation_failed"
    case outputValidationFailed = "output_validation_failed"
    case cancelled
    case unknownLocalModelFailure = "unknown_local_model_failure"
}

enum LocalReviewError: Error {
    case unavailable
    case invalidResponse
    case classified(LocalReviewFailureCategory)

    var category: LocalReviewFailureCategory {
        switch self {
        case .unavailable: .modelUnavailable
        case .invalidResponse: .outputValidationFailed
        case .classified(let category): category
        }
    }
}

@MainActor
protocol OnDeviceReviewing {
    var isAvailable: Bool { get }
    var unavailableCategory: LocalReviewFailureCategory? { get }
    func explain(_ input: ReviewModelInput) async throws -> AIReviewContentDTO
}

extension OnDeviceReviewing {
    var unavailableCategory: LocalReviewFailureCategory? {
        isAvailable ? nil : .modelUnavailable
    }
}

@MainActor
struct AppleOnDeviceReview: OnDeviceReviewing {
    var isAvailable: Bool {
        unavailableCategory == nil
    }

    var unavailableCategory: LocalReviewFailureCategory? {
        #if canImport(FoundationModels)
            if #available(iOS 26.0, macOS 26.0, *) {
                switch SystemLanguageModel.default.availability {
                case .available:
                    return nil
                case .unavailable(let reason):
                    switch reason {
                    case .modelNotReady:
                        return .modelNotReady
                    case .deviceNotEligible, .appleIntelligenceNotEnabled:
                        return .modelUnavailable
                    @unknown default:
                        return .modelUnavailable
                    }
                }
            }
        #endif
        return .modelUnavailable
    }

    func explain(_ input: ReviewModelInput) async throws -> AIReviewContentDTO {
        guard isAvailable else {
            throw LocalReviewError.classified(unavailableCategory ?? .modelUnavailable)
        }
        #if canImport(FoundationModels)
            if #available(iOS 26.0, macOS 26.0, *) {
                do {
                    let session = LanguageModelSession(
                        model: .default,
                        instructions: """
                            Explain only the exact, bounded Nytr evidence supplied as JSON. This is an optional on-device
                            explanation, not medical advice. Preserve supplied numbers exactly and never calculate or invent
                            a number, nutrient, meal, cause, or missing fact. Never recommend changing targets, goals,
                            treatment, or training. "recorded_partial" is genuine recorded consumption with incomplete
                            evidence, never a complete day. Treat missing evidence as unknown. Return the requested four
                            fields with concise, transparent language.
                            """)
                    let prompt = String(decoding: try JSONEncoder().encode(input), as: UTF8.self)
                    let result = try await session.respond(
                        to: prompt, generating: LocalReviewText.self,
                        options: GenerationOptions(sampling: .greedy, maximumResponseTokens: 500))
                    return try Self.validate(
                        summary: result.content.summary,
                        keyFindings: result.content.keyFindings,
                        considerations: result.content.considerations,
                        limitations: result.content.limitations,
                        input: input)
                } catch is CancellationError {
                    throw LocalReviewError.classified(.cancelled)
                } catch let error as LocalReviewError {
                    throw error
                } catch let error as LanguageModelSession.GenerationError {
                    throw LocalReviewError.classified(Self.failureCategory(for: error))
                } catch {
                    throw LocalReviewError.classified(.unknownLocalModelFailure)
                }
            }
        #endif
        throw LocalReviewError.unavailable
    }

    #if canImport(FoundationModels)
        @available(iOS 26.0, macOS 26.0, *)
        static func failureCategory(
            for error: LanguageModelSession.GenerationError
        ) -> LocalReviewFailureCategory {
            switch error {
            case .assetsUnavailable:
                return .modelNotReady
            case .unsupportedLanguageOrLocale:
                return .unsupportedLocale
            case .guardrailViolation, .refusal:
                return .generationRefused
            case .exceededContextWindowSize, .unsupportedGuide, .decodingFailure:
                return .structuredGenerationFailed
            case .rateLimited, .concurrentRequests:
                return .unknownLocalModelFailure
            @unknown default:
                return .unknownLocalModelFailure
            }
        }
    #endif

    static func validate(
        summary: String, keyFindings: String, considerations: String, limitations: String,
        input: ReviewModelInput
    ) throws -> AIReviewContentDTO {
        let texts = [summary, keyFindings, considerations, limitations]
        guard
            texts.allSatisfy({
                !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && $0.count <= 520
                    && !$0.localizedCaseInsensitiveContains("http")
                    && !$0.contains("://")
            }), texts.reduce(0, { $0 + $1.count }) <= 2_080,
            ReviewModelInput.numericClaims(in: texts).isSubset(of: input.allowedNumericClaims)
        else {
            throw LocalReviewError.invalidResponse
        }
        return AIReviewContentDTO(
            summary: summary, attentionItems: [keyFindings],
            evidenceNotes: [considerations], limitations: [limitations])
    }

}

#if canImport(FoundationModels)
    @available(iOS 26.0, macOS 26.0, *)
    @Generable
    private struct LocalReviewText {
        @Guide(description: "Brief summary grounded only in supplied facts")
        var summary: String
        @Guide(description: "Most important exact findings; preserve any supplied numbers verbatim")
        var keyFindings: String
        @Guide(description: "Non-prescriptive considerations grounded only in supplied facts")
        var considerations: String
        @Guide(description: "Missing, partial, estimated, or non-causal evidence limitations")
        var limitations: String
    }
#endif
