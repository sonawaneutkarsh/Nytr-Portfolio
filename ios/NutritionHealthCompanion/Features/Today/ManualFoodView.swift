import SwiftUI

struct ManualFoodView: View {
    @State private var viewModel: ManualFoodViewModel
    @State private var selected: CustomFoodVersionDTO?
    @State private var amount = ""
    @State private var mealPeriod = ManualMealPeriodDTO.breakfast
    @State private var showingCreate = false
    @State private var showingBarcode = false

    init(viewModel: ManualFoodViewModel) {
        _viewModel = State(initialValue: viewModel)
    }

    var body: some View {
        Form {
            Section("Choose food") {
                if viewModel.isLoading {
                    ProgressView("Loading custom foods…")
                } else if viewModel.foods.isEmpty {
                    Text("No custom foods yet. Create one from its factual serving label.")
                        .foregroundStyle(.secondary)
                }
                Picker("Custom food", selection: $selected) {
                    Text("Select…").tag(CustomFoodVersionDTO?.none)
                    ForEach(viewModel.foods) { food in
                        Text(food.name).tag(Optional(food))
                    }
                }
                .onChange(of: selected) { _, food in
                    amount = food?.servingAmount ?? ""
                    viewModel.resetConsumptionConfirmation()
                }
                Button("Create custom food") { showingCreate = true }
                Button("Scan product barcode") { showingBarcode = true }
            }
            if let selected {
                Section("Stored nutrition") {
                    LabeledContent(
                        "Serving basis",
                        value: "\(selected.servingAmount) \(selected.servingUnit)"
                    )
                    ForEach(selected.nutrition.displayRows) { row in
                        LabeledContent(row.label, value: row.displayedValue)
                    }
                    Text(selected.provenance?.authority == "open_food_facts"
                        ? "Open Food Facts values for \(selected.servingDescription); community data may be incomplete or inaccurate. Unknown values are not zero."
                        : "Owner-entered values for \(selected.servingDescription); these are not Stacks menu nutrition. Unknown values are not zero.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
                Section("Record consumption") {
                    LabeledContent("Serving", value: selected.servingDescription)
                    LabeledContent("Quantity (\(selected.servingUnit))") {
                        TextField("Amount", text: $amount)
                            #if os(iOS)
                            .keyboardType(.decimalPad)
                            #endif
                            .multilineTextAlignment(.trailing)
                    }
                    Picker("Meal", selection: $mealPeriod) {
                        ForEach(ManualMealPeriodDTO.allCases, id: \.self) {
                            Text($0.rawValue.capitalized).tag($0)
                        }
                    }
                    Toggle(
                        "I confirm I consumed this amount",
                        isOn: Binding(
                            get: { viewModel.isConsumptionConfirmed },
                            set: { viewModel.setConsumptionConfirmed($0) }
                        )
                    )
                    Button("Record food") {
                        Task { _ = await viewModel.record(
                            food: selected, amount: amount, mealPeriod: mealPeriod) }
                    }
                    .disabled(viewModel.isSaving || !viewModel.isConsumptionConfirmed)
                }
            }
            if let message = viewModel.message {
                Section { Text(message).foregroundStyle(.secondary) }
            }
        }
        .navigationTitle("Add Food")
        .task { await viewModel.load() }
        .sheet(isPresented: $showingCreate) {
            CreateCustomFoodView(viewModel: viewModel) { food in
                amount = food.servingAmount
                viewModel.resetConsumptionConfirmation()
                selected = food
                showingCreate = false
            }
        }
        .sheet(isPresented: $showingBarcode) {
            BarcodeFoodImportView(viewModel: viewModel) { food in
                amount = food.servingAmount
                viewModel.resetConsumptionConfirmation()
                selected = food
                showingBarcode = false
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

    var body: some View {
        NavigationStack {
            Form {
                Section("Product barcode") {
                    TextField("8, 12, 13, or 14 digits", text: $barcode)
                        #if os(iOS)
                        .keyboardType(.numberPad)
                        #endif
                    if BarcodeScannerView.isAvailable {
                        Button("Open Camera") { showingScanner = true }
                    }
                    Button("Look up exact barcode") {
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
                    Section("Review source facts") {
                        LabeledContent("Product", value: product.name)
                        if let brand = product.brand { LabeledContent("Brand", value: brand) }
                        LabeledContent("Basis", value: product.servingDescription)
                        ForEach(product.nutrition.displayRows) { row in
                            LabeledContent(row.label, value: row.displayedValue)
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
                        Button("Import reviewed facts") {
                            Task {
                                if let food = await viewModel.importReviewedBarcode(barcode) {
                                    onImported(food)
                                }
                            }
                        }
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
                        .buttonStyle(.borderedProminent)
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
                Section("Identity") {
                    TextField("Name", text: $name)
                    TextField("Brand (optional)", text: $brand)
                }
                Section("Serving") {
                    TextField("Description", text: $servingDescription)
                    TextField("Amount", text: $servingAmount)
                        #if os(iOS)
                        .keyboardType(.decimalPad)
                        #endif
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
        TextField(label, text: value)
            #if os(iOS)
            .keyboardType(.decimalPad)
            #endif
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
