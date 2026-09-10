import XCTest
@testable import NutritionHealthCompanion

@MainActor
final class ManualFoodViewModelTests: XCTestCase {
    final class Backend: BackendClient, @unchecked Sendable {
        var foods: [CustomFoodVersionDTO] = []
        var created: CreateCustomFoodRequestDTO?
        var recorded: ManualFoodConsumptionRequestDTO?
        var lookedUpBarcode: String?
        var importedBarcode: (String, String)?

        func submitBatch(added: [BodyMassSampleDTO], deletions: [DeletedSampleDTO]) async throws
            -> SyncResponse { fatalError() }
        func fetchSyncStatus() async throws -> StatusResponse { fatalError() }
        func fetchCustomFoods() async throws -> [CustomFoodVersionDTO] { foods }
        func createCustomFood(_ request: CreateCustomFoodRequestDTO) async throws
            -> CustomFoodVersionDTO {
            created = request
            return Self.food
        }
        func recordManualFood(_ request: ManualFoodConsumptionRequestDTO) async throws
            -> ManualFoodConsumptionResponse {
            recorded = request
            return .init(
                entryId: UUID(), foodId: request.foodId,
                foodVersionId: request.foodVersionId, clientEventId: request.clientEventId,
                mealPeriod: request.mealPeriod, consumedAmount: request.consumedAmount,
                consumedUnit: request.consumedUnit, portionFactor: "0.5",
                foodName: "Oats", recordedAt: Date(),
                nutrition: .init(caloriesKcal: "200", proteinG: nil,
                    carbohydrateG: nil, totalFatG: nil, fiberG: nil, sodiumMg: nil))
        }
        func lookupBarcodeFood(barcode: String) async throws -> BarcodeProductDTO {
            lookedUpBarcode = barcode
            return Self.barcodeProduct
        }
        func importBarcodeFood(barcode: String, expectedPayloadSha256: String) async throws
            -> ImportBarcodeFoodResponseDTO
        {
            importedBarcode = (barcode, expectedPayloadSha256)
            return .init(created: true, food: Self.food)
        }

        static let food = CustomFoodVersionDTO(
            foodId: UUID(uuidString: "00000000-0000-0000-0000-000000000001")!,
            versionId: UUID(uuidString: "00000000-0000-0000-0000-000000000002")!,
            name: "Oats", brand: nil, servingDescription: "one bowl",
            servingAmount: "1", servingUnit: "serving",
            nutrition: .init(caloriesKcal: "400", proteinG: nil,
                carbohydrateG: nil, totalFatG: nil, fiberG: nil, sodiumMg: nil),
            createdAt: Date(timeIntervalSince1970: 0), provenance: nil)

        static let barcodeProduct = BarcodeProductDTO(
            policyVersion: "barcode-food-import.v1", name: "Yogurt", brand: "Dairy",
            servingDescription: "170 g", servingAmount: "1", servingUnit: "serving",
            nutrition: .init(caloriesKcal: "100", proteinG: "17", carbohydrateG: nil,
                totalFatG: nil, fiberG: nil, sodiumMg: nil),
            provenance: .init(
                authority: "open_food_facts", provider: "open_food_facts",
                scannedBarcode: "012345678905", providerCode: "012345678905",
                productUrl: "https://world.openfoodfacts.org/product/012345678905",
                fetchedAt: Date(timeIntervalSince1970: 0),
                payloadSha256: String(repeating: "a", count: 64),
                dataLicense: "ODbL-1.0/DbCL-1.0", nutritionBasis: "per_serving"),
            limitations: ["Community data may be inaccurate."])
    }

    func testLoadCreateAndRecordBreakfast() async {
        let backend = Backend()
        backend.foods = [Backend.food]
        var refreshed = false
        let event = UUID(uuidString: "00000000-0000-0000-0000-000000000003")!
        let model = ManualFoodViewModel(
            backend: backend, eventId: { event }, onRecorded: { refreshed = true })

        await model.load()
        XCTAssertEqual(model.foods, [Backend.food])
        let request = CreateCustomFoodRequestDTO(
            name: "Eggs", brand: nil, servingDescription: "two eggs",
            servingAmount: "2", servingUnit: "egg",
            nutrition: .init(caloriesKcal: "140", proteinG: "12",
                carbohydrateG: nil, totalFatG: nil, fiberG: nil, sodiumMg: nil))
        let created = await model.create(request)
        XCTAssertNotNil(created)
        XCTAssertEqual(backend.created, request)
        XCTAssertNil(backend.recorded)
        XCTAssertFalse(model.isConsumptionConfirmed)

        let unconfirmed = await model.record(
            food: Backend.food, amount: "0.5", mealPeriod: .breakfast)
        XCTAssertFalse(unconfirmed)
        XCTAssertNil(backend.recorded)

        model.setConsumptionConfirmed(true)
        let recorded = await model.record(
            food: Backend.food, amount: "0.5", mealPeriod: .breakfast)
        XCTAssertTrue(recorded)
        XCTAssertFalse(model.isConsumptionConfirmed)
        XCTAssertEqual(backend.recorded?.clientEventId, event)
        XCTAssertEqual(backend.recorded?.mealPeriod, .breakfast)
        XCTAssertEqual(backend.recorded?.consumedAmount, "0.5")
        XCTAssertTrue(refreshed)
    }

    func testInvalidQuantityDoesNotCallBackend() async {
        let backend = Backend()
        let model = ManualFoodViewModel(backend: backend)
        model.setConsumptionConfirmed(true)
        let recorded = await model.record(
            food: Backend.food, amount: "0", mealPeriod: .breakfast)
        XCTAssertFalse(recorded)
        XCTAssertNil(backend.recorded)
    }

    func testSelectionChangeRequiresFreshConsumptionConfirmation() async {
        let backend = Backend()
        backend.foods = [Backend.food]
        let model = ManualFoodViewModel(backend: backend)

        await model.load()
        XCTAssertEqual(model.foods, [Backend.food])
        model.setConsumptionConfirmed(true)
        model.resetConsumptionConfirmation()

        XCTAssertFalse(model.isConsumptionConfirmed)
        XCTAssertNil(backend.recorded)
    }

    func testNutritionDisplayPreservesStoredValuesAndUnknowns() {
        let rows = Backend.food.nutrition.displayRows

        XCTAssertEqual(rows.map(\.label), [
            "Calories", "Protein", "Carbohydrate", "Total fat", "Fiber", "Sodium",
        ])
        XCTAssertEqual(rows.map(\.displayedValue), [
            "400 kcal", "Unknown", "Unknown", "Unknown", "Unknown", "Unknown",
        ])
        XCTAssertFalse(rows.map(\.displayedValue).contains("0 g"))
    }

    func testBarcodeRequiresExactReviewBeforeImportAndDoesNotRecordConsumption() async {
        let backend = Backend()
        let model = ManualFoodViewModel(backend: backend)

        let invalid = await model.lookupBarcode("123")
        XCTAssertNil(invalid)
        XCTAssertNil(backend.lookedUpBarcode)
        let preview = await model.lookupBarcode("012345678905")
        XCTAssertEqual(preview, Backend.barcodeProduct)
        XCTAssertEqual(backend.lookedUpBarcode, "012345678905")
        XCTAssertNil(backend.recorded)

        let imported = await model.importReviewedBarcode("012345678905")
        XCTAssertEqual(imported, Backend.food)
        XCTAssertEqual(backend.importedBarcode?.0, "012345678905")
        XCTAssertEqual(backend.importedBarcode?.1, String(repeating: "a", count: 64))
        XCTAssertNil(backend.recorded)
    }

    func testBarcodeReviewCannotBeImportedForDifferentCodeAndClearsAtSignOut() async {
        let backend = Backend()
        let model = ManualFoodViewModel(backend: backend)
        _ = await model.lookupBarcode("012345678905")

        let mismatched = await model.importReviewedBarcode("12345678")
        XCTAssertNil(mismatched)
        XCTAssertNil(backend.importedBarcode)
        model.resetForSignOut()
        XCTAssertNil(model.barcodeProduct)
    }
}
