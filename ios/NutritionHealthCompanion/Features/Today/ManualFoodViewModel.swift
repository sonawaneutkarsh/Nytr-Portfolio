import Foundation
import Observation

@MainActor
@Observable
final class ManualFoodViewModel {
    private struct PendingAdjustment: Equatable {
        let entryId: UUID
        let kind: String
        let amount: String?
        let unit: String?
        let eventId: UUID
    }
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
    private(set) var preview: FoodPreviewResponse?
    private(set) var previewRequest: FoodPreviewRequest?
    private(set) var isPreviewing = false
    private var previewToken = UUID()
    private var hasLoadedFoods = false
    private var correctionPreviewToken = UUID()
    private var pendingAdjustment: PendingAdjustment?
    private(set) var correctionPreview: FoodPreviewResponse?
    private(set) var correctionPreviewRequest: ManualCorrectionPreviewRequest?
    private(set) var isPreviewingCorrection = false

    func updatePreview(food: CustomFoodVersionDTO, amount: String, unit: String) async {
        let token = UUID()
        previewToken = token
        preview = nil
        isConsumptionConfirmed = false
        let request = FoodPreviewRequest(foodId: food.foodId, foodVersionId: food.versionId, amount: amount, unit: unit)
        previewRequest = request
        guard Self.isPositiveDecimal(amount) else {
            isPreviewing = false
            return
        }
        isPreviewing = true
        defer { if previewToken == token { isPreviewing = false } }
        do {
            let value = try await backend.previewFood(request)
            guard previewToken == token, !Task.isCancelled else { return }
            preview = value
            message = nil
        } catch {
            guard previewToken == token, !Task.isCancelled else { return }
            message = "Nutrition preview could not be loaded. Check your connection and retry."
        }
    }

    func clearPreview() {
        previewToken = UUID()
        preview = nil
        previewRequest = nil
        isPreviewing = false
        isConsumptionConfirmed = false
    }

    func updateCorrectionPreview(
        entry: DailyConsumedNutritionItem, amount: String, unit: String? = nil
    ) async {
        let token = UUID()
        correctionPreviewToken = token
        correctionPreview = nil
        let requestedUnit = unit ?? entry.consumedUnit ?? ""
        correctionPreviewRequest = .init(amount: amount, unit: requestedUnit)
        guard Self.isPositiveDecimal(amount), !requestedUnit.isEmpty else {
            isPreviewingCorrection = false
            return
        }
        isPreviewingCorrection = true
        defer { if correctionPreviewToken == token { isPreviewingCorrection = false } }
        do {
            let value = try await backend.previewManualFoodCorrection(
                entryId: entry.entryId, amount: amount, unit: requestedUnit
            )
            guard correctionPreviewToken == token, !Task.isCancelled else { return }
            correctionPreview = value
            message = nil
        } catch {
            guard correctionPreviewToken == token, !Task.isCancelled else { return }
            message = "The corrected nutrition preview could not be loaded."
        }
    }

    func clearCorrectionPreview() {
        correctionPreviewToken = UUID()
        correctionPreview = nil
        correctionPreviewRequest = nil
        isPreviewingCorrection = false
    }

    init(
        backend: any BackendClient,
        eventId: @escaping () -> UUID = UUID.init,
        onRecorded: @escaping @MainActor () async -> Void = {}
    ) {
        self.backend = backend
        self.eventId = eventId
        self.onRecorded = onRecorded
    }

    func load(force: Bool = false) async {
        guard force || !hasLoadedFoods else { return }
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            foods = try await backend.fetchCustomFoods()
            hasLoadedFoods = true
            isConsumptionConfirmed = false
            message = nil
        } catch {
            message = "Custom foods could not be loaded. Try again when online."
        }
    }

    func resetForSignOut() {
        clearPreview()
        foods = []
        hasLoadedFoods = false
        message = nil
        isLoading = false
        isSaving = false
        isConsumptionConfirmed = false
        barcodeProduct = nil
        isResolvingBarcode = false
        clearCorrectionPreview()
        pendingAdjustment = nil
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
            NytrBarcodeDiagnostics.record(
                basis: product.provenance.nutritionBasis,
                unit: product.servingUnit,
                reason: product.basisReason
            )
            message = nil
            return product
        } catch {
            barcodeProduct = nil
            message = "No usable exact product facts were returned. You can enter the label manually."
            return nil
        }
    }

    /// Owner-entered serving refusal message, or nil when the entry is safe.
    ///
    /// A volume the owner typed does not establish a density, so mass-based
    /// nutrition can never be restated in mL and volume-based nutrition can never
    /// be restated in g. This mirrors the authoritative backend rule so the owner
    /// is told before a request is sent; the backend still fails closed on its own.
    static func ownerServingRefusal(for product: BarcodeProductDTO, unit: String) -> String? {
        if product.acceptsOwnerServingInAnyUnit { return nil }
        guard let required = product.requiredOwnerServingUnit else {
            return "This product has no recorded nutrition basis, so Nytr cannot apply a serving to it."
        }
        if required == unit { return nil }
        return required == "g" ? Self.massBasisRefusal : Self.volumeBasisRefusal
    }

    static let massBasisRefusal =
        "This product's nutrition is mass-based, so Nytr cannot convert it to mL without a verified serving nutrition basis."
    static let volumeBasisRefusal =
        "This product's nutrition is volume-based, so Nytr cannot convert it to g without a verified serving nutrition basis."

    func importReviewedBarcode(
        _ rawBarcode: String, ownerServing: OwnerServingRequestDTO? = nil
    ) async -> CustomFoodVersionDTO? {
        guard !isResolvingBarcode, let product = barcodeProduct else { return nil }
        let barcode = rawBarcode.trimmingCharacters(in: .whitespacesAndNewlines)
        guard product.provenance.scannedBarcode == barcode,
            let digest = product.provenance.payloadSha256
        else {
            message = "Scan and review this barcode again before importing it."
            return nil
        }
        if let ownerServing {
            guard Self.isPositiveDecimal(ownerServing.amount) else {
                message = "Enter the serving amount printed on the label using decimal notation."
                return nil
            }
            if let refusal = Self.ownerServingRefusal(for: product, unit: ownerServing.unit) {
                message = refusal
                return nil
            }
        }
        isResolvingBarcode = true
        defer { isResolvingBarcode = false }
        do {
            let response = try await backend.importBarcodeFood(
                barcode: barcode, expectedPayloadSha256: digest, ownerServing: ownerServing)
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
            _ = try await backend.recordManualFood(
                .init(
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

    func correct(entry: DailyConsumedNutritionItem, preview: FoodPreviewResponse) async -> Bool {
        guard !isSaving else { return false }
        let fingerprint = PendingAdjustment(
            entryId: entry.entryId, kind: "correction", amount: preview.consumedAmount,
            unit: preview.consumedUnit, eventId: pendingAdjustment?.eventId ?? eventId()
        )
        let event = pendingAdjustmentMatches(fingerprint)
            ? pendingAdjustment!.eventId : eventId()
        pendingAdjustment = PendingAdjustment(
            entryId: entry.entryId, kind: "correction", amount: preview.consumedAmount,
            unit: preview.consumedUnit, eventId: event
        )
        isSaving = true
        defer { isSaving = false }
        do {
            _ = try await backend.correctManualFood(
                entryId: entry.entryId,
                request: .init(
                    amount: preview.consumedAmount, unit: preview.consumedUnit,
                    clientEventId: event
                )
            )
            pendingAdjustment = nil
            clearCorrectionPreview()
            message = "Food quantity corrected. Earlier evidence remains in the audit trail."
            await onRecorded()
            return true
        } catch {
            message = "The quantity correction was not recorded. Retry to safely reuse the same event."
            return false
        }
    }

    func remove(entry: DailyConsumedNutritionItem) async -> Bool {
        guard !isSaving else { return false }
        let candidate = PendingAdjustment(
            entryId: entry.entryId, kind: "void", amount: nil, unit: nil,
            eventId: pendingAdjustment?.eventId ?? eventId()
        )
        let event = pendingAdjustmentMatches(candidate)
            ? pendingAdjustment!.eventId : eventId()
        pendingAdjustment = PendingAdjustment(
            entryId: entry.entryId, kind: "void", amount: nil, unit: nil,
            eventId: event
        )
        isSaving = true
        defer { isSaving = false }
        do {
            _ = try await backend.voidManualFood(
                entryId: entry.entryId, clientEventId: event
            )
            pendingAdjustment = nil
            message = "Food removed from active totals. The original record remains auditable."
            await onRecorded()
            return true
        } catch {
            message = "The food was not removed. Retry to safely reuse the same event."
            return false
        }
    }

    private func pendingAdjustmentMatches(_ candidate: PendingAdjustment) -> Bool {
        guard let pendingAdjustment else { return false }
        return pendingAdjustment.entryId == candidate.entryId
            && pendingAdjustment.kind == candidate.kind
            && pendingAdjustment.amount == candidate.amount
            && pendingAdjustment.unit == candidate.unit
    }

    private static func isPositiveDecimal(_ value: String) -> Bool {
        guard value.range(of: "^[0-9]+(?:[.][0-9]+)?$", options: .regularExpression) != nil,
            let decimal = Decimal(string: value, locale: Locale(identifier: "en_US_POSIX"))
        else { return false }
        return decimal > 0
    }

    private static func isSupportedBarcode(_ value: String) -> Bool {
        [8, 12, 13, 14].contains(value.count) && value.allSatisfy(\.isNumber)
            && value.unicodeScalars.allSatisfy { $0.value >= 48 && $0.value <= 57 }
    }
}
