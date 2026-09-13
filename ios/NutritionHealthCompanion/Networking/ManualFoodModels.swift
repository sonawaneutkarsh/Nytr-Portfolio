import Foundation

enum ManualMealPeriodDTO: String, Codable, CaseIterable, Sendable {
    case breakfast
    case lunch
    case dinner
}

struct ManualNutritionFactsDTO: Codable, Hashable, Sendable {
    let caloriesKcal: String?
    let proteinG: String?
    let carbohydrateG: String?
    let totalFatG: String?
    let fiberG: String?
    let sodiumMg: String?

    private enum CodingKeys: String, CodingKey {
        case caloriesKcal = "calories_kcal"
        case proteinG = "protein_g"
        case carbohydrateG = "carbohydrate_g"
        case totalFatG = "total_fat_g"
        case fiberG = "fiber_g"
        case sodiumMg = "sodium_mg"
    }

    var displayRows: [ManualNutritionDisplayRow] {
        [
            .init(id: "calories_kcal", label: "Calories", amount: caloriesKcal, unit: "kcal"),
            .init(id: "protein_g", label: "Protein", amount: proteinG, unit: "g"),
            .init(
                id: "carbohydrate_g", label: "Carbohydrate",
                amount: carbohydrateG, unit: "g"),
            .init(id: "total_fat_g", label: "Total fat", amount: totalFatG, unit: "g"),
            .init(id: "fiber_g", label: "Fiber", amount: fiberG, unit: "g"),
            .init(id: "sodium_mg", label: "Sodium", amount: sodiumMg, unit: "mg"),
        ]
    }
}

struct ManualNutritionDisplayRow: Identifiable, Equatable, Sendable {
    let id: String
    let label: String
    let amount: String?
    let unit: String

    var displayedValue: String {
        guard let amount else { return "Unknown" }
        let formatted =
            id == "calories_kcal" || id == "sodium_mg"
            ? NytrNumberFormat.whole(amount)
            : NytrNumberFormat.detail(amount)
        return "\(formatted ?? amount) \(unit)"
    }
}

struct CustomFoodVersionDTO: Codable, Identifiable, Hashable, Sendable {
    let foodId: UUID
    let versionId: UUID
    let name: String
    let brand: String?
    let servingDescription: String
    let servingAmount: String
    let servingUnit: String
    let nutrition: ManualNutritionFactsDTO
    let createdAt: Date
    let provenance: CustomFoodProvenanceDTO?

    var id: UUID { versionId }

    private enum CodingKeys: String, CodingKey {
        case foodId = "food_id"
        case versionId = "version_id"
        case name, brand
        case servingDescription = "serving_description"
        case servingAmount = "serving_amount"
        case servingUnit = "serving_unit"
        case nutrition
        case createdAt = "created_at"
        case provenance
    }
}

struct CustomFoodProvenanceDTO: Codable, Hashable, Sendable {
    let authority: String
    let provider: String?
    let scannedBarcode: String?
    let providerCode: String?
    let productUrl: String?
    let fetchedAt: Date?
    let payloadSha256: String?
    let dataLicense: String?
    let nutritionBasis: String?
    /// "owner_entered" when the physical serving came from the owner's label
    /// reading rather than the provider. Nutrition authority stays `authority`.
    let servingAuthority: String?

    private enum CodingKeys: String, CodingKey {
        case authority, provider
        case scannedBarcode = "scanned_barcode"
        case providerCode = "provider_code"
        case productUrl = "product_url"
        case fetchedAt = "fetched_at"
        case payloadSha256 = "payload_sha256"
        case dataLicense = "data_license"
        case nutritionBasis = "nutrition_basis"
        case servingAuthority = "serving_authority"
    }
}

struct BarcodeProductDTO: Codable, Equatable, Sendable {
    let policyVersion: String
    let name: String
    let brand: String?
    let servingDescription: String
    let servingAmount: String
    let servingUnit: String
    let nutrition: ManualNutritionFactsDTO
    let provenance: CustomFoodProvenanceDTO
    let limitations: [String]
    /// Privacy-safe classification of how the serving basis was chosen. Carries
    /// no product identity, source values, or payload detail.
    let basisReason: String?

    private enum CodingKeys: String, CodingKey {
        case name, brand, nutrition, provenance, limitations
        case policyVersion = "policy_version"
        case servingDescription = "serving_description"
        case servingAmount = "serving_amount"
        case servingUnit = "serving_unit"
        case basisReason = "basis_reason"
    }

    /// True when the source nutrition is attached to a serving, so an
    /// owner-supplied physical size names that serving without any conversion.
    var acceptsOwnerServingInAnyUnit: Bool {
        provenance.nutritionBasis == "per_serving"
    }

    /// The single physical unit an owner serving may use, or nil when any unit
    /// is acceptable. Mass-based nutrition can only be restated in grams.
    var requiredOwnerServingUnit: String? {
        switch provenance.nutritionBasis {
        case "per_100g": return "g"
        case "per_100ml": return "ml"
        default: return nil
        }
    }
}

struct OwnerServingRequestDTO: Codable, Equatable, Sendable {
    let amount: String
    let unit: String
    let label: String?
}

struct ImportBarcodeFoodRequestDTO: Codable, Equatable, Sendable {
    let expectedPayloadSha256: String
    let ownerServing: OwnerServingRequestDTO?

    init(expectedPayloadSha256: String, ownerServing: OwnerServingRequestDTO? = nil) {
        self.expectedPayloadSha256 = expectedPayloadSha256
        self.ownerServing = ownerServing
    }

    private enum CodingKeys: String, CodingKey {
        case expectedPayloadSha256 = "expected_payload_sha256"
        case ownerServing = "owner_serving"
    }
}

struct ImportBarcodeFoodResponseDTO: Codable, Equatable, Sendable {
    let created: Bool
    let food: CustomFoodVersionDTO
}

struct CustomFoodsResponse: Codable, Equatable, Sendable {
    let foods: [CustomFoodVersionDTO]
}

struct CreateCustomFoodRequestDTO: Codable, Equatable, Sendable {
    let name: String
    let brand: String?
    let servingDescription: String
    let servingAmount: String
    let servingUnit: String
    let nutrition: ManualNutritionFactsDTO

    private enum CodingKeys: String, CodingKey {
        case name, brand
        case servingDescription = "serving_description"
        case servingAmount = "serving_amount"
        case servingUnit = "serving_unit"
        case nutrition
    }
}

struct ManualFoodConsumptionRequestDTO: Codable, Equatable, Sendable {
    let foodId: UUID
    let foodVersionId: UUID
    let consumedAmount: String
    let consumedUnit: String
    let mealPeriod: ManualMealPeriodDTO
    let clientEventId: UUID

    private enum CodingKeys: String, CodingKey {
        case foodId = "food_id"
        case foodVersionId = "food_version_id"
        case consumedAmount = "consumed_amount"
        case consumedUnit = "consumed_unit"
        case mealPeriod = "meal_period"
        case clientEventId = "client_event_id"
    }
}

struct ManualFoodConsumptionResponse: Codable, Equatable, Sendable {
    let entryId: UUID
    let foodId: UUID
    let foodVersionId: UUID
    let clientEventId: UUID
    let mealPeriod: ManualMealPeriodDTO
    let consumedAmount: String
    let consumedUnit: String
    let portionFactor: String
    let foodName: String
    let recordedAt: Date
    let nutrition: ManualNutritionFactsDTO

    private enum CodingKeys: String, CodingKey {
        case entryId = "entry_id"
        case foodId = "food_id"
        case foodVersionId = "food_version_id"
        case clientEventId = "client_event_id"
        case mealPeriod = "meal_period"
        case consumedAmount = "consumed_amount"
        case consumedUnit = "consumed_unit"
        case portionFactor = "portion_factor"
        case foodName = "food_name"
        case recordedAt = "recorded_at"
        case nutrition
    }
}

struct FoodPreviewRequest: Codable, Equatable, Sendable {
    let foodId: UUID
    let foodVersionId: UUID
    let amount: String
    let unit: String
    enum CodingKeys: String, CodingKey {
        case foodId = "food_id"
        case foodVersionId = "food_version_id"
        case amount, unit
    }
}

struct FoodPreviewResponse: Codable, Equatable, Sendable {
    let consumedAmount: String
    let consumedUnit: String
    let portionFactor: String
    let nutrition: ManualNutritionFactsDTO
    enum CodingKeys: String, CodingKey {
        case consumedAmount = "consumed_amount"
        case consumedUnit = "consumed_unit"
        case portionFactor = "portion_factor"
        case nutrition
    }
}

struct ManualCorrectionPreviewRequest: Codable, Equatable, Sendable {
    let amount: String
    let unit: String
}

struct ManualCorrectionRequest: Codable, Equatable, Sendable {
    let amount: String
    let unit: String
    let clientEventId: UUID
    enum CodingKeys: String, CodingKey {
        case amount, unit
        case clientEventId = "client_event_id"
    }
}

struct ManualVoidRequest: Codable, Equatable, Sendable {
    let clientEventId: UUID
    enum CodingKeys: String, CodingKey { case clientEventId = "client_event_id" }
}

struct ManualFoodAdjustmentResponse: Codable, Equatable, Sendable {
    let adjustmentId: UUID
    let clientEventId: UUID
    let supersededEntryId: UUID
    let replacementEntryId: UUID?
    let kind: String
    let recordedAt: Date
    let replacement: ManualFoodConsumptionResponse?
    let created: Bool
    enum CodingKeys: String, CodingKey {
        case adjustmentId = "adjustment_id"
        case clientEventId = "client_event_id"
        case supersededEntryId = "superseded_entry_id"
        case replacementEntryId = "replacement_entry_id"
        case kind
        case recordedAt = "recorded_at"
        case replacement, created
    }
}
