import Foundation
import Observation

@MainActor
@Observable
final class ManualFoodViewModel {
    private let backend: any BackendClient
    private let eventId: () -> UUID
    private let onRecorded: @MainActor () async -> Void

    private(set) var foods: [CustomFoodVersionDTO] = []
    private(set) var isLoading = false
    private(set) var isSaving = false
    private(set) var isConsumptionConfirmed = false
    private(set) var barcodeProduct: BarcodeProductDTO?
    private(set) var isResolvingBarcode = false
    private(set) var message: String?

    init(
        backend: any BackendClient,
        eventId: @escaping () -> UUID = UUID.init,
        onRecorded: @escaping @MainActor () async -> Void = {}
    ) {
        self.backend = backend
        self.eventId = eventId
        self.onRecorded = onRecorded
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            foods = try await backend.fetchCustomFoods()
            isConsumptionConfirmed = false
            message = nil
        } catch {
            message = "Custom foods could not be loaded. Try again when online."
        }
    }

    func resetForSignOut() {
        foods = []
        message = nil
        isLoading = false
        isSaving = false
        isConsumptionConfirmed = false
        barcodeProduct = nil
        isResolvingBarcode = false
    }

    func setConsumptionConfirmed(_ confirmed: Bool) {
        isConsumptionConfirmed = confirmed
    }

    func resetConsumptionConfirmation() {
        isConsumptionConfirmed = false
    }

    func clearBarcodeReview() {
        barcodeProduct = nil
    }

    func lookupBarcode(_ rawBarcode: String) async -> BarcodeProductDTO? {
        guard !isResolvingBarcode else { return nil }
        let barcode = rawBarcode.trimmingCharacters(in: .whitespacesAndNewlines)
        guard Self.isSupportedBarcode(barcode) else {
            message = "Use an 8, 12, 13, or 14 digit product barcode."
            return nil
        }
        isResolvingBarcode = true
        defer { isResolvingBarcode = false }
        do {
            let product = try await backend.lookupBarcodeFood(barcode: barcode)
            barcodeProduct = product
            message = nil
            return product
        } catch {
            barcodeProduct = nil
            message = "No usable exact product facts were returned. You can enter the label manually."
            return nil
        }
    }

    func importReviewedBarcode(_ rawBarcode: String) async -> CustomFoodVersionDTO? {
        guard !isResolvingBarcode, let product = barcodeProduct else { return nil }
        let barcode = rawBarcode.trimmingCharacters(in: .whitespacesAndNewlines)
        guard product.provenance.scannedBarcode == barcode,
            let digest = product.provenance.payloadSha256
        else {
            message = "Scan and review this barcode again before importing it."
            return nil
        }
        isResolvingBarcode = true
        defer { isResolvingBarcode = false }
        do {
            let response = try await backend.importBarcodeFood(
                barcode: barcode, expectedPayloadSha256: digest)
            if let index = foods.firstIndex(where: { $0.foodId == response.food.foodId }) {
                foods[index] = response.food
            } else {
                foods.append(response.food)
            }
            foods.sort { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
            barcodeProduct = nil
            isConsumptionConfirmed = false
            message = response.created ? "Barcode food imported." : "Barcode food is already current."
            return response.food
        } catch {
            message = "The reviewed product was not imported. Review it again and retry."
            return nil
        }
    }

    func create(_ request: CreateCustomFoodRequestDTO) async -> CustomFoodVersionDTO? {
        guard !isSaving else { return nil }
        isSaving = true
        defer { isSaving = false }
        do {
            let food = try await backend.createCustomFood(request)
            foods.append(food)
            foods.sort { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
            isConsumptionConfirmed = false
            message = "Custom food saved."
            return food
        } catch {
            message = "Custom food was not saved. Check the factual values and try again."
            return nil
        }
    }

    func record(
        food: CustomFoodVersionDTO,
        amount: String,
        mealPeriod: ManualMealPeriodDTO
    ) async -> Bool {
        guard !isSaving else { return false }
        guard isConsumptionConfirmed else {
            message = "Confirm that you consumed this food before recording it."
            return false
        }
        guard Self.isPositiveDecimal(amount) else {
            message = "Enter a positive quantity using decimal notation."
            return false
        }
        isConsumptionConfirmed = false
        isSaving = true
        defer { isSaving = false }
        do {
            _ = try await backend.recordManualFood(.init(
                foodId: food.foodId,
                foodVersionId: food.versionId,
                consumedAmount: amount,
                consumedUnit: food.servingUnit,
                mealPeriod: mealPeriod,
                clientEventId: eventId()
            ))
            message = "Food recorded."
            await onRecorded()
            return true
        } catch {
            message = "Food was not recorded. Try again when online."
            return false
        }
    }

    private static func isPositiveDecimal(_ value: String) -> Bool {
        guard let decimal = Decimal(string: value, locale: Locale(identifier: "en_US_POSIX"))
        else { return false }
        return decimal > 0
    }

    private static func isSupportedBarcode(_ value: String) -> Bool {
        [8, 12, 13, 14].contains(value.count) && value.allSatisfy(\.isNumber)
            && value.unicodeScalars.allSatisfy { $0.value >= 48 && $0.value <= 57 }
    }
}
