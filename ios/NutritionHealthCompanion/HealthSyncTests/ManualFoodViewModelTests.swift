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
        var importedOwnerServing: OwnerServingRequestDTO?
        var product = Backend.barcodeProduct
        var fetchFoodCalls = 0
        var correctionPreview: (UUID, String, String)?
        var correctionRequest: (UUID, ManualCorrectionRequest)?
        var voidRequest: (UUID, UUID)?

        func submitBatch(added: [BodyMassSampleDTO], deletions: [DeletedSampleDTO]) async throws
            -> SyncResponse { fatalError() }
        func fetchSyncStatus() async throws -> StatusResponse { fatalError() }
        func fetchCustomFoods() async throws -> [CustomFoodVersionDTO] {
            fetchFoodCalls += 1
            return foods
        }
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
        func previewManualFoodCorrection(entryId: UUID, amount: String, unit: String)
            async throws -> FoodPreviewResponse
        {
            correctionPreview = (entryId, amount, unit)
            return .init(
                consumedAmount: amount,
                consumedUnit: unit == "servings" ? "serving" : unit,
                portionFactor: "1.5",
                nutrition: .init(
                    caloriesKcal: "600", proteinG: "30", carbohydrateG: nil,
                    totalFatG: nil, fiberG: nil, sodiumMg: nil
                )
            )
        }
        func correctManualFood(entryId: UUID, request: ManualCorrectionRequest)
            async throws -> ManualFoodAdjustmentResponse
        {
            correctionRequest = (entryId, request)
            return .init(
                adjustmentId: UUID(), clientEventId: request.clientEventId,
                supersededEntryId: entryId, replacementEntryId: UUID(),
                kind: "correction", recordedAt: Date(), replacement: nil, created: true
            )
        }
        func voidManualFood(entryId: UUID, clientEventId: UUID) async throws
            -> ManualFoodAdjustmentResponse
        {
            voidRequest = (entryId, clientEventId)
            return .init(
                adjustmentId: UUID(), clientEventId: clientEventId,
                supersededEntryId: entryId, replacementEntryId: nil, kind: "void",
                recordedAt: Date(), replacement: nil, created: true
            )
        }
        func lookupBarcodeFood(barcode: String) async throws -> BarcodeProductDTO {
            lookedUpBarcode = barcode
            return product
        }
        func importBarcodeFood(
            barcode: String, expectedPayloadSha256: String,
            ownerServing: OwnerServingRequestDTO?
        ) async throws -> ImportBarcodeFoodResponseDTO {
            importedBarcode = (barcode, expectedPayloadSha256)
            importedOwnerServing = ownerServing
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

        static func provenance(basis: String) -> CustomFoodProvenanceDTO {
            .init(
                authority: "open_food_facts", provider: "open_food_facts",
                scannedBarcode: "012345678905", providerCode: "012345678905",
                productUrl: "https://world.openfoodfacts.org/product/012345678905",
                fetchedAt: Date(timeIntervalSince1970: 0),
                payloadSha256: String(repeating: "a", count: 64),
                dataLicense: "ODbL-1.0/DbCL-1.0", nutritionBasis: basis,
                servingAuthority: nil)
        }

        static let barcodeProduct = BarcodeProductDTO(
            policyVersion: "barcode-food-import.v3", name: "Yogurt", brand: "Dairy",
            servingDescription: "170 g", servingAmount: "1", servingUnit: "serving",
            nutrition: .init(caloriesKcal: "100", proteinG: "17", carbohydrateG: nil,
                totalFatG: nil, fiberG: nil, sodiumMg: nil),
            provenance: provenance(basis: "per_serving"),
            limitations: ["Community data may be inaccurate."],
            basisReason: "source_serving_without_physical_quantity")

        /// Shaped like the real mass-only chocolate-milk record: exact per-100-g
        /// nutrition and no volume evidence anywhere in the source.
        static let massOnlyProduct = BarcodeProductDTO(
            policyVersion: "barcode-food-import.v3", name: "Chocolate Milk", brand: nil,
            servingDescription: "100 g", servingAmount: "100", servingUnit: "g",
            nutrition: .init(caloriesKcal: "87.5", proteinG: "3.3333333333333",
                carbohydrateG: "10.833333333333", totalFatG: "3.75", fiberG: nil,
                sodiumMg: "83.333333333332"),
            provenance: provenance(basis: "per_100g"),
            limitations: ["Community data may be inaccurate."],
            basisReason: "no_trustworthy_volume_evidence")
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

    func testSavedFoodsUseStaleWhileRevalidateAndForceRefreshExplicitly() async {
        let backend = Backend()
        backend.foods = [Backend.food]
        let model = ManualFoodViewModel(backend: backend)
        await model.load()
        await model.load()
        XCTAssertEqual(backend.fetchFoodCalls, 1)
        XCTAssertEqual(model.foods, [Backend.food])
        await model.load(force: true)
        XCTAssertEqual(backend.fetchFoodCalls, 2)
    }

    func testCorrectionUsesMatchingPreviewAndRemoveUsesAppendOnlyEndpoint() async {
        let backend = Backend()
        let eventId = UUID(uuidString: "00000000-0000-0000-0000-000000000123")!
        var refreshCount = 0
        let model = ManualFoodViewModel(
            backend: backend, eventId: { eventId }, onRecorded: { refreshCount += 1 }
        )
        let entry = DailyConsumedNutritionItem(
            entryId: UUID(), recordedAt: Date(), planRunId: nil, planVersionId: nil,
            planItemId: nil, mealContext: "lunch", candidateId: "manual",
            itemName: "Oats", configurationSummary: "one bowl",
            nutritionAuthority: .userEntered, confidence: "user_entered",
            caloriesKcal: "400", proteinG: "20", unknownNutrients: [],
            provenanceSummary: "frozen", sourceSystem: "manual_custom",
            customFoodId: Backend.food.foodId, customFoodVersionId: Backend.food.versionId,
            consumedAmount: "1", consumedUnit: "serving",
            servingDescription: "one serving", servingAmount: "1", servingUnit: "serving"
        )
        await model.updateCorrectionPreview(entry: entry, amount: "1.5", unit: "servings")
        XCTAssertEqual(backend.correctionPreview?.0, entry.entryId)
        XCTAssertEqual(backend.correctionPreview?.2, "servings")
        guard let preview = model.correctionPreview else {
            XCTFail("Expected a correction preview")
            return
        }
        let corrected = await model.correct(entry: entry, preview: preview)
        XCTAssertTrue(corrected)
        XCTAssertEqual(backend.correctionRequest?.1.amount, "1.5")
        XCTAssertEqual(backend.correctionRequest?.1.unit, "serving")
        XCTAssertEqual(backend.correctionRequest?.1.clientEventId, eventId)
        let removed = await model.remove(entry: entry)
        XCTAssertTrue(removed)
        XCTAssertEqual(backend.voidRequest?.0, entry.entryId)
        XCTAssertEqual(refreshCount, 2)
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

    func testOwnerEnteredVolumeIsAcceptedWhenSourceNutritionIsPerServing() async {
        let backend = Backend()
        let model = ManualFoodViewModel(backend: backend)
        _ = await model.lookupBarcode("012345678905")

        XCTAssertNil(
            ManualFoodViewModel.ownerServingRefusal(for: Backend.barcodeProduct, unit: "ml"))
        let imported = await model.importReviewedBarcode(
            "012345678905",
            ownerServing: .init(amount: "240", unit: "ml", label: "bottle"))

        XCTAssertEqual(imported, Backend.food)
        XCTAssertEqual(backend.importedOwnerServing?.amount, "240")
        XCTAssertEqual(backend.importedOwnerServing?.unit, "ml")
        XCTAssertEqual(backend.importedOwnerServing?.label, "bottle")
    }

    func testOwnerEnteredVolumeIsRefusedForMassBasedNutritionAndNeverReachesBackend() async {
        let backend = Backend()
        backend.product = Backend.massOnlyProduct
        let model = ManualFoodViewModel(backend: backend)
        _ = await model.lookupBarcode("012345678905")

        let refused = await model.importReviewedBarcode(
            "012345678905",
            ownerServing: .init(amount: "240", unit: "ml", label: "bottle"))

        XCTAssertNil(refused)
        XCTAssertNil(backend.importedBarcode)
        XCTAssertNil(backend.importedOwnerServing)
        XCTAssertEqual(model.message, ManualFoodViewModel.massBasisRefusal)
        XCTAssertEqual(
            model.message,
            "This product's nutrition is mass-based, so Nytr cannot convert it to mL without a verified serving nutrition basis."
        )
    }

    func testOwnerEnteredMassIsAcceptedForMassBasedNutrition() async {
        let backend = Backend()
        backend.product = Backend.massOnlyProduct
        let model = ManualFoodViewModel(backend: backend)
        _ = await model.lookupBarcode("012345678905")

        XCTAssertNil(
            ManualFoodViewModel.ownerServingRefusal(for: Backend.massOnlyProduct, unit: "g"))
        let imported = await model.importReviewedBarcode(
            "012345678905", ownerServing: .init(amount: "240", unit: "g", label: "carton"))

        XCTAssertEqual(imported, Backend.food)
        XCTAssertEqual(backend.importedOwnerServing?.unit, "g")
    }

    func testOwnerServingRejectsNonPositiveAmountBeforeAnyRequest() async {
        let backend = Backend()
        let model = ManualFoodViewModel(backend: backend)
        _ = await model.lookupBarcode("012345678905")

        let refused = await model.importReviewedBarcode(
            "012345678905", ownerServing: .init(amount: "0", unit: "ml", label: nil))

        XCTAssertNil(refused)
        XCTAssertNil(backend.importedOwnerServing)
    }

    func testBarcodeDiagnosticsRecordOnlyAllowListedClassifications() {
        XCTAssertEqual(
            NytrBarcodeDiagnostics.message(
                basis: "per_100g", unit: "g", reason: "no_trustworthy_volume_evidence"),
            "[NytrBarcode] basis=per_100g unit=g reason=no_trustworthy_volume_evidence")
        XCTAssertEqual(
            NytrBarcodeDiagnostics.message(
                basis: "per_serving", unit: "ml", reason: "owner_entered_serving"),
            "[NytrBarcode] basis=per_serving unit=ml reason=owner_entered_serving")

        // Anything outside the closed allow-list can never reach a log, so a
        // product name, barcode, or nutrition value degrades to `unknown`.
        let leaky = NytrBarcodeDiagnostics.message(
            basis: "Synthetic Creamery Chocolate Milk", unit: "01234567890123",
            reason: "87.5 kcal")
        XCTAssertEqual(leaky, "[NytrBarcode] basis=unknown unit=unknown reason=unknown")
        XCTAssertFalse(leaky.contains("Creamery"))
        XCTAssertFalse(leaky.contains("01234567890123"))
        XCTAssertFalse(leaky.contains("87.5"))
        XCTAssertEqual(
            NytrBarcodeDiagnostics.message(basis: nil, unit: nil, reason: nil),
            "[NytrBarcode] basis=unknown unit=unknown reason=unknown")
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
