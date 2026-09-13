import SwiftUI

struct ManualFoodView: View {
    @State private var viewModel: ManualFoodViewModel
    @State private var selected: CustomFoodVersionDTO?
    @State private var amount = ""
    @State private var mealPeriod = ManualMealPeriodDTO.breakfast
    @State private var showingCreate = false
    @State private var showingBarcode = false
    @State private var showingSavedFood = false
    @State private var editingEntry: DailyConsumedNutritionItem?
    @State private var viewingEntry: DailyConsumedNutritionItem?
    @State private var removingEntry: DailyConsumedNutritionItem?
    private let todayViewModel: TodayViewModel?
    private let nutritionHistoryViewModel: NutritionHistoryViewModel?
    private let subject: String?
    private let focusedMeal: MealGuidanceKind?
    private let onShowSettings: () -> Void

    init(
        viewModel: ManualFoodViewModel,
        todayViewModel: TodayViewModel? = nil,
        nutritionHistoryViewModel: NutritionHistoryViewModel? = nil,
        subject: String? = nil,
        focusedMeal: MealGuidanceKind? = nil,
        onShowSettings: @escaping () -> Void = {}
    ) {
        _viewModel = State(initialValue: viewModel)
        self.todayViewModel = todayViewModel
        self.nutritionHistoryViewModel = nutritionHistoryViewModel
        self.subject = subject
        self.focusedMeal = focusedMeal
        self.onShowSettings = onShowSettings
    }

    @State private var quantityUnit = "g"

    private var currentPreview: FoodPreviewResponse? {
        guard let selected,
            viewModel.previewRequest
                == FoodPreviewRequest(
                    foodId: selected.foodId, foodVersionId: selected.versionId, amount: amount, unit: quantityUnit
                )
        else { return nil }
        return viewModel.preview
    }

    private var todayEntries: [DailyConsumedNutritionItem] {
        guard let todayViewModel, case .result(let ledger) = todayViewModel.ledgerPhase else {
            return []
        }
        return ledger.consumedItems
    }

    private func choose(_ food: CustomFoodVersionDTO?) {
        viewModel.clearPreview()
        let manufacturerServing = food?.servingDescription.hasPrefix("1 serving (") == true
        quantityUnit = manufacturerServing ? "servings" : (food?.servingUnit ?? "g")
        amount = manufacturerServing ? "1" : (food?.servingAmount ?? "")
    }

    var body: some View {
        Form {
            if let focusedMeal {
                Section {
                    Label(
                        "Opened \(focusedMeal.rawValue.capitalized) guidance",
                        systemImage: "bell.badge"
                    )
                    .foregroundStyle(NytrDesign.accent)
                }
            }
            Section {
                NytrScreenHeader(
                    eyebrow: "FOOD LOG", title: "Make it count",
                    subtitle: "Scan or choose a food. Review your portion, then log what you ate.", symbol: "fork.knife"
                )
            }.listRowBackground(Color.clear)
            Section("Quick actions") {
                Button {
                    showingSavedFood = true
                } label: {
                    Label("Choose Saved Food", systemImage: "clock.arrow.circlepath")
                }
                Button {
                    showingBarcode = true
                } label: {
                    Label("Scan Barcode", systemImage: "barcode.viewfinder")
                }
                Button {
                    showingCreate = true
                } label: {
                    Label("Add Manually", systemImage: "square.and.pencil")
                }
            }
            if todayViewModel != nil {
                Section("What I ate today") {
                    if todayEntries.isEmpty {
                        Text("No food is recorded yet today.")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(todayEntries) { entry in
                            loggedFoodRow(entry)
                                .contentShape(Rectangle())
                                .onTapGesture {
                                    if entry.customFoodId != nil {
                                        editingEntry = entry
                                    } else {
                                        viewingEntry = entry
                                    }
                                }
                                .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                                    if entry.customFoodId != nil {
                                        Button("Remove", role: .destructive) {
                                            removingEntry = entry
                                        }
                                        Button("Edit") { editingEntry = entry }
                                            .tint(NytrDesign.buttonFill)
                                    }
                                }
                        }
                    }
                }
            }
            if let todayViewModel {
                MealDiscoverySections(viewModel: todayViewModel)
            }
            if let message = viewModel.message { Section { Text(message).foregroundStyle(.secondary) } }
            if let nutritionHistoryViewModel, let subject {
                Section("Nutrition History") {
                    NavigationLink("View Last 7 Days") {
                        NutritionHistoryView(viewModel: nutritionHistoryViewModel, subject: subject)
                    }
                }
            }
        }
        .nytrList()
        .navigationTitle("Food")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                NytrSettingsToolbarButton(action: onShowSettings)
            }
        }
        .task { await viewModel.load() }
        .refreshable {
            await viewModel.load(force: true)
            if let todayViewModel, subject != nil {
                await todayViewModel.refreshNutrition()
            }
        }
        .task(id: subject) {
            guard let todayViewModel, let subject else { return }
            await todayViewModel.activate(subject: subject)
        }
        .task(id: "\(selected?.versionId.uuidString ?? ""):\(amount):\(quantityUnit)") {
            guard let selected else { return }
            await viewModel.updatePreview(food: selected, amount: amount, unit: quantityUnit)
        }
        .sheet(isPresented: $showingCreate) {
            CreateCustomFoodView(viewModel: viewModel) { food in
                selected = food
                choose(food)
                showingCreate = false
            }
        }
        .sheet(isPresented: $showingSavedFood) {
            NavigationStack {
                Form {
                    foodLoggingSections
                    if let message = viewModel.message {
                        Section("Status") { Text(message).foregroundStyle(.secondary) }
                    }
                }
                .navigationTitle("Log saved food")
                .toolbar {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Done") { showingSavedFood = false }
                    }
                }
            }
        }
        .sheet(isPresented: $showingBarcode) {
            BarcodeFoodImportView(viewModel: viewModel) { food in
                selected = food
                choose(food)
                showingBarcode = false
            }
        }
        .sheet(item: $editingEntry) { entry in
            EditFoodConsumptionView(viewModel: viewModel, entry: entry) {
                editingEntry = nil
            }
        }
        .sheet(item: $viewingEntry) { entry in
            LoggedFoodDetailView(entry: entry)
        }
        .confirmationDialog(
            "Remove this food from today’s totals?",
            isPresented: Binding(
                get: { removingEntry != nil },
                set: { if !$0 { removingEntry = nil } }
            ),
            presenting: removingEntry
        ) { entry in
            Button("Remove food", role: .destructive) {
                Task {
                    if await viewModel.remove(entry: entry) { removingEntry = nil }
                }
            }
            Button("Cancel", role: .cancel) { removingEntry = nil }
        } message: { _ in
            Text("Nytr keeps the original event in its audit trail and excludes it from active totals.")
        }
    }

    @ViewBuilder
    private func loggedFoodRow(_ entry: DailyConsumedNutritionItem) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(entry.itemName).font(.headline)
                Spacer()
                Text(entry.mealContext.capitalized).font(.caption).foregroundStyle(.secondary)
            }
            Text(loggedQuantity(entry))
            .font(.subheadline)
            .foregroundStyle(.secondary)
            HStack {
                Text(
                    "\(NytrNumberFormat.whole(entry.caloriesKcal) ?? "—") kcal · "
                        + "\(NytrNumberFormat.detail(entry.proteinG) ?? "—") g protein"
                )
                .font(.subheadline)
                Spacer()
                Text(sourceLabel(entry))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .contain)
    }

    private func loggedQuantity(_ entry: DailyConsumedNutritionItem) -> String {
        let physical = [
            NytrNumberFormat.detail(entry.consumedAmount),
            entry.consumedUnit.map(NytrNumberFormat.unitLabel)
        ].compactMap { $0 }.joined(separator: " ")
        guard let consumed = decimal(entry.consumedAmount),
            let serving = decimal(entry.servingAmount), serving > 0,
            entry.servingUnit == entry.consumedUnit
        else {
            return physical.isEmpty ? (entry.configurationSummary ?? "Recorded quantity") : physical
        }
        let count = consumed / serving
        let rawCount = NSDecimalNumber(decimal: count).stringValue
        let formatted = NytrNumberFormat.detail(rawCount) ?? rawCount
        let label = count == 1 ? "serving" : "servings"
        return physical.isEmpty ? "\(formatted) \(label)" : "\(formatted) \(label) · \(physical)"
    }

    private func sourceLabel(_ entry: DailyConsumedNutritionItem) -> String {
        switch entry.sourceSystem {
        case "manual_custom": return "Manual"
        case "barcode_open_food_facts": return "Scanned"
        case "next_meal": return "Next Meal"
        default: return "Plan"
        }
    }

    private func decimal(_ raw: String?) -> Decimal? {
        guard let raw else { return nil }
        return Decimal(string: raw, locale: Locale(identifier: "en_US_POSIX"))
    }

    @ViewBuilder
    private var foodLoggingSections: some View {
        Section("Saved food") {
            if viewModel.isLoading { ProgressView("Loading saved foods…") }
            Picker("Saved food", selection: $selected) {
                Text("Choose food").tag(CustomFoodVersionDTO?.none)
                ForEach(viewModel.foods) { food in Text(food.name).tag(Optional(food)) }
            }
            .onChange(of: selected) { _, food in choose(food) }
            if !viewModel.isLoading, viewModel.foods.isEmpty {
                Text("Foods you save will appear here.").foregroundStyle(.secondary)
            }
        }
        if let selected {
            Section("Your portion") {
                Text(selected.name).font(.title2.weight(.semibold))
                Text(selected.servingDescription).foregroundStyle(.secondary)
                Picker("Quantity unit", selection: $quantityUnit) {
                    Text(NytrNumberFormat.unitLabel(selected.servingUnit)).tag(selected.servingUnit)
                    if selected.servingUnit != "serving" {
                        Text("Servings").tag("servings")
                    }
                }
                .pickerStyle(.segmented)
                NytrNumberField(
                    title: "Amount (\(NytrNumberFormat.unitLabel(quantityUnit)))", text: $amount
                )
                if let preview = currentPreview {
                    NytrMetricRow(
                        "Physical quantity",
                        value: "\(preview.consumedAmount) \(NytrNumberFormat.unitLabel(preview.consumedUnit))"
                    )
                    ForEach(preview.nutrition.displayRows) { row in
                        NytrMetricRow(row.label, value: row.displayedValue)
                    }
                } else if viewModel.isPreviewing {
                    ProgressView("Updating nutrition…")
                } else {
                    Text("Enter a quantity to preview its nutrition.")
                        .foregroundStyle(.secondary)
                }
                DisclosureGroup("Source nutrition & limitations") {
                    ForEach(selected.nutrition.displayRows) { row in
                        NytrMetricRow(row.label, value: row.displayedValue)
                    }
                    Text(
                        "Values above apply to \(selected.servingDescription). Unknown values remain unknown."
                    )
                    Text(
                        selected.provenance?.authority == "open_food_facts"
                            ? "Open Food Facts · community data. Verify against your package."
                            : "Your entered nutrition label."
                    )
                }
                .font(.subheadline)
            }
            Section("Log food") {
                Picker("Meal", selection: $mealPeriod) {
                    ForEach(ManualMealPeriodDTO.allCases, id: \.self) {
                        Text($0.rawValue.capitalized).tag($0)
                    }
                }
                Toggle(
                    "I ate this amount",
                    isOn: Binding(
                        get: { viewModel.isConsumptionConfirmed },
                        set: { viewModel.setConsumptionConfirmed($0) }
                    )
                )
                .disabled(currentPreview == nil || viewModel.isPreviewing)
                Button("Log food") {
                    guard let preview = currentPreview else { return }
                    Task {
                        if await viewModel.record(
                            food: selected, amount: preview.consumedAmount,
                            mealPeriod: mealPeriod
                        ) {
                            showingSavedFood = false
                        }
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(NytrDesign.buttonFill)
                .controlSize(.large)
                .disabled(
                    currentPreview == nil || viewModel.isPreviewing || viewModel.isSaving
                        || !viewModel.isConsumptionConfirmed
                )
            }
        }
    }
}

private struct EditFoodConsumptionView: View {
    let viewModel: ManualFoodViewModel
    let entry: DailyConsumedNutritionItem
    let onSaved: () -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var amount: String
    @State private var quantityUnit: String
    @State private var confirmed = false

    init(
        viewModel: ManualFoodViewModel, entry: DailyConsumedNutritionItem,
        onSaved: @escaping () -> Void
    ) {
        self.viewModel = viewModel
        self.entry = entry
        self.onSaved = onSaved
        let consumed = entry.consumedAmount.flatMap {
            Decimal(string: $0, locale: Locale(identifier: "en_US_POSIX"))
        }
        let serving = entry.servingAmount.flatMap {
            Decimal(string: $0, locale: Locale(identifier: "en_US_POSIX"))
        }
        if let consumed, let serving, serving > 0, entry.servingUnit == entry.consumedUnit {
            _amount = State(initialValue: NSDecimalNumber(decimal: consumed / serving).stringValue)
            _quantityUnit = State(initialValue: "servings")
        } else {
            _amount = State(initialValue: entry.consumedAmount ?? "")
            _quantityUnit = State(initialValue: entry.consumedUnit ?? "")
        }
    }

    private var matchingPreview: FoodPreviewResponse? {
        guard viewModel.correctionPreviewRequest == .init(amount: amount, unit: quantityUnit)
        else { return nil }
        return viewModel.correctionPreview
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Logged food") {
                    LabeledContent("Food", value: entry.itemName)
                    LabeledContent("Meal", value: entry.mealContext.capitalized)
                }
                Section("Correct quantity") {
                    if let physicalUnit = entry.servingUnit, entry.servingAmount != nil {
                        Picker("Quantity unit", selection: $quantityUnit) {
                            Text("Servings").tag("servings")
                            Text(NytrNumberFormat.unitLabel(physicalUnit)).tag(physicalUnit)
                        }
                        .pickerStyle(.segmented)
                    }
                    NytrNumberField(
                        title: "Amount (\(NytrNumberFormat.unitLabel(quantityUnit)))", text: $amount
                    )
                    if let preview = matchingPreview {
                        NytrMetricRow(
                            "Physical quantity",
                            value: "\(preview.consumedAmount) \(NytrNumberFormat.unitLabel(preview.consumedUnit))"
                        )
                        ForEach(preview.nutrition.displayRows) { row in
                            NytrMetricRow(row.label, value: row.displayedValue)
                        }
                        Toggle("I checked this corrected amount", isOn: $confirmed)
                    } else if viewModel.isPreviewingCorrection {
                        ProgressView("Updating nutrition…")
                    } else {
                        Text("Enter a positive quantity to preview the corrected totals.")
                            .foregroundStyle(.secondary)
                    }
                }
                Section {
                    Button("Save correction") {
                        guard let preview = matchingPreview else { return }
                        Task {
                            if await viewModel.correct(entry: entry, preview: preview) {
                                onSaved()
                                dismiss()
                            }
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(NytrDesign.buttonFill)
                    .controlSize(.large)
                    .disabled(matchingPreview == nil || !confirmed || viewModel.isSaving)
                    Text("Saving appends a replacement and supersedes this active entry; history is not rewritten.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Edit quantity")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .task(id: "\(amount):\(quantityUnit)") {
                confirmed = false
                await viewModel.updateCorrectionPreview(
                    entry: entry, amount: amount, unit: quantityUnit
                )
            }
            .onDisappear { viewModel.clearCorrectionPreview() }
        }
    }
}

private struct LoggedFoodDetailView: View {
    let entry: DailyConsumedNutritionItem
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("Recorded food") {
                    LabeledContent("Food", value: entry.itemName)
                    LabeledContent("Meal", value: entry.mealContext.capitalized)
                    if let summary = entry.configurationSummary {
                        LabeledContent("Serving", value: summary)
                    }
                }
                Section("Recorded nutrition") {
                    NytrMetricRow(
                        "Calories", value: "\(NytrNumberFormat.whole(entry.caloriesKcal) ?? "—") kcal"
                    )
                    NytrMetricRow(
                        "Protein", value: "\(NytrNumberFormat.detail(entry.proteinG) ?? "—") g"
                    )
                    if !entry.unknownNutrients.isEmpty {
                        Text("Some nutrient values were unavailable in this recorded evidence.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Section {
                    Text(entry.provenanceSummary)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Food details")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}

private struct BarcodeFoodImportView: View {
    let viewModel: ManualFoodViewModel
    let onImported: (CustomFoodVersionDTO) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var barcode = ""
    @State private var showingScanner = false
    @State private var settingServingFromLabel = false
    @State private var labelServingAmount = ""
    @State private var labelServingUnit = "ml"
    @State private var labelServingName = "serving"

    var body: some View {
        NavigationStack {
            Form {
                Section("Product barcode") {
                    TextField("8, 12, 13, or 14 digits", text: $barcode)
                        .accessibilityLabel("Product barcode")
                        .accessibilityHint("Enter 8, 12, 13, or 14 digits")
                        #if os(iOS)
                            .keyboardType(.numberPad)
                        #endif
                    if BarcodeScannerView.isAvailable {
                        Button("Open Camera") { showingScanner = true }
                    }
                    Button("Look up barcode") {
                        Task { _ = await viewModel.lookupBarcode(barcode) }
                    }
                    .disabled(viewModel.isResolvingBarcode)
                    Text("Lookup is exact—Nytr does not search for similar products or guess nutrition.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if viewModel.isResolvingBarcode {
                    Section { ProgressView("Checking product facts…") }
                }
                if let product = viewModel.barcodeProduct {
                    Section("Review nutrition") {
                        LabeledContent("Product", value: product.name)
                        if let brand = product.brand { LabeledContent("Brand", value: brand) }
                        LabeledContent("Basis", value: product.servingDescription)
                        ForEach(product.nutrition.displayRows) { row in
                            LabeledContent(row.label, value: row.displayedValue)
                        }
                    }
                    Section("Serving from label") {
                        Toggle("Set serving from label", isOn: $settingServingFromLabel)
                        if settingServingFromLabel {
                            NytrNumberField(title: "Serving amount", text: $labelServingAmount)
                            Picker("Physical unit", selection: $labelServingUnit) {
                                Text("mL").tag("ml")
                                Text("g").tag("g")
                            }
                            .pickerStyle(.segmented)
                            Picker("Serving label", selection: $labelServingName) {
                                Text("Serving").tag("serving")
                                Text("Bottle").tag("bottle")
                                Text("Carton").tag("carton")
                            }
                            if let refusal = ManualFoodViewModel.ownerServingRefusal(
                                for: product, unit: labelServingUnit)
                            {
                                Text(refusal).font(.caption).foregroundStyle(.orange)
                                Text(
                                    "You can still save this product on its source basis above, or enter the label's own nutrition as a separate manual food."
                                )
                                .font(.caption).foregroundStyle(.secondary)
                            } else {
                                Text(
                                    "This is your reading of the package label, not provider data. Nytr records it as an owner-entered serving and keeps the provider nutrition unchanged."
                                )
                                .font(.caption).foregroundStyle(.secondary)
                                Button("Save with label serving") {
                                    Task {
                                        if let food = await viewModel.importReviewedBarcode(
                                            barcode,
                                            ownerServing: .init(
                                                amount: labelServingAmount,
                                                unit: labelServingUnit,
                                                label: labelServingName
                                            )
                                        ) {
                                            onImported(food)
                                        }
                                    }
                                }
                                .buttonStyle(.borderedProminent).tint(NytrDesign.buttonFill)
                                .controlSize(.large)
                                .disabled(
                                    viewModel.isResolvingBarcode || labelServingAmount.isEmpty)
                            }
                        }
                    }
                    Section("Provenance") {
                        LabeledContent("Provider", value: "Open Food Facts")
                        LabeledContent(
                            "Barcode", value: product.provenance.providerCode ?? barcode)
                        LabeledContent(
                            "Data license", value: product.provenance.dataLicense ?? "Unknown")
                        if let rawURL = product.provenance.productUrl,
                            let url = URL(string: rawURL)
                        {
                            Link("View provider record", destination: url)
                        }
                        ForEach(product.limitations, id: \.self) { limitation in
                            Text(limitation).font(.caption).foregroundStyle(.secondary)
                        }
                        Button("Save reviewed food") {
                            Task {
                                if let food = await viewModel.importReviewedBarcode(barcode) {
                                    onImported(food)
                                }
                            }
                        }
                        .buttonStyle(.borderedProminent).tint(NytrDesign.buttonFill)
                        .controlSize(.large)
                        .disabled(viewModel.isResolvingBarcode)
                    }
                }
                if let message = viewModel.message {
                    Section("Status") { Text(message).foregroundStyle(.secondary) }
                }
            }
            .navigationTitle("Barcode Food")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        viewModel.clearBarcodeReview()
                        dismiss()
                    }
                }
            }
            .sheet(isPresented: $showingScanner) {
                ZStack(alignment: .topTrailing) {
                    BarcodeScannerView { value in
                        barcode = value
                        showingScanner = false
                        Task { _ = await viewModel.lookupBarcode(value) }
                    }
                    .ignoresSafeArea()
                    Button("Cancel") { showingScanner = false }
                        .buttonStyle(.borderedProminent).tint(NytrDesign.buttonFill)
                        .controlSize(.large)
                        .padding()
                }
            }
        }
    }
}

private struct CreateCustomFoodView: View {
    let viewModel: ManualFoodViewModel
    let onCreated: (CustomFoodVersionDTO) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var brand = ""
    @State private var servingDescription = "1 serving"
    @State private var servingAmount = "1"
    @State private var servingUnit = "serving"
    @State private var calories = ""
    @State private var protein = ""
    @State private var carbs = ""
    @State private var fat = ""
    @State private var fiber = ""
    @State private var sodium = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Food details") {
                    TextField("Name", text: $name)
                    TextField("Brand (optional)", text: $brand)
                }
                Section("Serving") {
                    TextField("Description", text: $servingDescription)
                    NytrNumberField(title: "Amount", text: $servingAmount)
                    TextField("Unit", text: $servingUnit)
                }
                Section("Nutrition per serving") {
                    nutrient("Calories (kcal)", $calories)
                    nutrient("Protein (g)", $protein)
                    nutrient("Carbohydrate (g), optional", $carbs)
                    nutrient("Fat (g), optional", $fat)
                    nutrient("Fiber (g), optional", $fiber)
                    nutrient("Sodium (mg), optional", $sodium)
                    Text("Blank optional nutrients remain unknown, never zero.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let message = viewModel.message {
                    Section("Status") {
                        Text(message)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("New Custom Food")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { Task { await save() } }.disabled(viewModel.isSaving)
                }
            }
        }
    }

    private func nutrient(_ label: String, _ value: Binding<String>) -> some View {
        NytrNumberField(title: label, text: value)
    }

    private func blankToNil(_ value: String) -> String? {
        value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : value
    }

    private func save() async {
        let request = CreateCustomFoodRequestDTO(
            name: name, brand: blankToNil(brand), servingDescription: servingDescription,
            servingAmount: servingAmount, servingUnit: servingUnit,
            nutrition: .init(
                caloriesKcal: blankToNil(calories), proteinG: blankToNil(protein),
                carbohydrateG: blankToNil(carbs), totalFatG: blankToNil(fat),
                fiberG: blankToNil(fiber), sodiumMg: blankToNil(sodium)))
        if let food = await viewModel.create(request) { onCreated(food) }
    }
}
