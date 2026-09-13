import XCTest

@testable import NutritionHealthCompanion

@MainActor
final class FoodPreviewTests: XCTestCase {
    private actor Backend: BackendClient {
        var pending: [String: CheckedContinuation<FoodPreviewResponse, Error>] = [:]
        func submitBatch(added: [BodyMassSampleDTO], deletions: [DeletedSampleDTO]) async throws -> SyncResponse {
            throw BackendError.retryable("unused")
        }
        func fetchSyncStatus() async throws -> StatusResponse { throw BackendError.retryable("unused") }
        func previewFood(_ request: FoodPreviewRequest) async throws -> FoodPreviewResponse {
            try await withCheckedThrowingContinuation { pending[request.amount] = $0 }
        }
        func count() -> Int { pending.count }
        func resolve(_ amount: String) {
            pending.removeValue(forKey: amount)?.resume(
                returning: FoodPreviewResponse(
                    consumedAmount: amount,
                    consumedUnit: "g", portionFactor: "1",
                    nutrition: .init(
                        caloriesKcal: "120", proteinG: "25", carbohydrateG: nil, totalFatG: nil, fiberG: nil,
                        sodiumMg: nil)))
        }
    }
    func testQuantityChangesClearOldMacrosAndIgnoreLateResponses() async {
        let backend = Backend()
        let vm = ManualFoodViewModel(backend: backend)
        let food = ManualFoodViewModelTests.Backend.food
        let first = Task { await vm.updatePreview(food: food, amount: "30", unit: "g") }
        for _ in 0..<100 {
            if await backend.count() == 1 { break }
            await Task.yield()
        }
        let second = Task { await vm.updatePreview(food: food, amount: "80", unit: "g") }
        for _ in 0..<100 {
            if await backend.count() == 2 { break }
            await Task.yield()
        }
        XCTAssertNil(vm.preview)
        XCTAssertFalse(vm.isConsumptionConfirmed)
        await backend.resolve("80")
        await second.value
        await backend.resolve("30")
        await first.value
        XCTAssertEqual(vm.preview?.consumedAmount, "80")
        XCTAssertEqual(vm.preview?.nutrition.proteinG, "25")
        XCTAssertNil(vm.preview?.nutrition.fiberG)
        vm.resetForSignOut()
        XCTAssertNil(vm.preview)
    }
}
