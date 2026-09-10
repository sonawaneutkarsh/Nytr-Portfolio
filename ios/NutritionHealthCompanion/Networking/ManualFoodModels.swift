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
        amount.map { "\($0) \(unit)" } ?? "Unknown"
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

    private enum CodingKeys: String, CodingKey {
        case authority, provider
        case scannedBarcode = "scanned_barcode"
        case providerCode = "provider_code"
        case productUrl = "product_url"
        case fetchedAt = "fetched_at"
        case payloadSha256 = "payload_sha256"
        case dataLicense = "data_license"
        case nutritionBasis = "nutrition_basis"
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

    private enum CodingKeys: String, CodingKey {
        case name, brand, nutrition, provenance, limitations
        case policyVersion = "policy_version"
        case servingDescription = "serving_description"
        case servingAmount = "serving_amount"
        case servingUnit = "serving_unit"
    }
}

struct ImportBarcodeFoodRequestDTO: Codable, Equatable, Sendable {
    let expectedPayloadSha256: String

    private enum CodingKeys: String, CodingKey {
        case expectedPayloadSha256 = "expected_payload_sha256"
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
