import SwiftUI

enum NytrAppearance: String, CaseIterable, Identifiable {
    case system, light, dark

    static let preferenceKey = "nytr.appearance"
    var id: String { rawValue }
    var label: String { rawValue.capitalized }
    var colorScheme: ColorScheme? {
        switch self {
        case .system: nil
        case .light: .light
        case .dark: .dark
        }
    }

    static func binding(_ rawValue: Binding<String>) -> Binding<NytrAppearance> {
        Binding(
            get: { NytrAppearance(rawValue: rawValue.wrappedValue) ?? .system },
            set: { rawValue.wrappedValue = $0.rawValue }
        )
    }
}

/// A small native presentation vocabulary; evidence and actions stay in feature views.
enum NytrDesign {
    // White native prominent-button labels need a darker fill in both appearances.
    static let buttonFill = Color(red: 0.04, green: 0.40, blue: 0.38)
    #if os(iOS)
        static let accent = Color(
            uiColor: UIColor { traits in
                traits.userInterfaceStyle == .dark
                    ? UIColor(red: 0.37, green: 0.84, blue: 0.77, alpha: 1)
                    : UIColor(red: 0.04, green: 0.40, blue: 0.38, alpha: 1)
            })
    #else
        static let accent = Color.teal
    #endif
    static let radius: CGFloat = 24
    static let spacing: CGFloat = 16
}

struct NytrBrandMark: View {
    let size: CGFloat

    var body: some View {
        Image("NytrMark")
            .resizable()
            .scaledToFit()
            .frame(width: size, height: size)
            .clipShape(RoundedRectangle(cornerRadius: size * 0.22, style: .continuous))
            .accessibilityHidden(true)
    }
}

struct NytrScreenHeader: View {
    let eyebrow: String
    let title: String
    let subtitle: String
    let symbol: String
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(eyebrow, systemImage: symbol)
                .font(.caption.weight(.bold)).tracking(1.4).foregroundStyle(NytrDesign.accent)
            Text(title).font(.system(.largeTitle, design: .rounded, weight: .bold))
                .fixedSize(horizontal: false, vertical: true)
            Text(subtitle).font(.subheadline).foregroundStyle(.secondary)
        }.padding(.vertical, 12).accessibilityElement(children: .combine)
    }
}

struct NytrStateView: View {
    let title: String
    let message: String
    let symbol: String
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Image(systemName: symbol).font(.title2).foregroundStyle(NytrDesign.accent)
                .accessibilityHidden(true)
            Text(title).font(.title3.weight(.semibold))
            Text(message).font(.subheadline).foregroundStyle(.secondary)
        }.padding(.vertical, 12).fixedSize(horizontal: false, vertical: true)
    }
}

struct NytrDailySummary: View {
    let ledger: DailyNutritionLedgerResponse
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Label("DAILY NUTRITION", systemImage: "leaf")
                .font(.caption.weight(.bold)).tracking(1).foregroundStyle(NytrDesign.accent)
            VStack(alignment: .leading, spacing: 6) {
                Text(NytrNumberFormat.whole(ledger.knownCaloriesConsumed) ?? "—")
                    .font(.system(.largeTitle, design: .rounded, weight: .bold)).monospacedDigit()
                Text(
                    ledger.target?.caloriesKcal.map {
                        "/ \(NytrNumberFormat.whole($0) ?? $0) kcal"
                    }
                        ?? "kcal logged · target not set"
                )
                .font(.subheadline).foregroundStyle(.secondary)
                if let progress = NytrNumberFormat.fraction(
                    ledger.knownCaloriesConsumed,
                    of: ledger.target?.caloriesKcal
                ) {
                    ProgressView(value: progress)
                        .tint(NytrDesign.accent)
                        .accessibilityLabel("Recorded calorie progress")
                        .accessibilityValue("\(Int((progress * 100).rounded())) percent")
                }
            }.accessibilityElement(children: .combine)
            Divider()
            NytrMetricRow(
                "Protein",
                value: ledger.knownProteinGConsumed.map {
                    "\(NytrNumberFormat.whole($0) ?? $0) g"
                } ?? "Unknown"
            )
                .font(.title2.weight(.semibold))
            if let target = ledger.target?.proteinG {
                Text(
                    "\(NytrNumberFormat.whole(target) ?? target) g daily protein "
                        + "\(ledger.target?.proteinGoalKind == "floor" ? "minimum" : "target")"
                )
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            if let remaining = ledger.remainingKnownCalories {
                let over = remaining.hasPrefix("-")
                let magnitude = over ? String(remaining.dropFirst()) : remaining
                NytrMetricRow(
                    over ? "Over target" : "Remaining",
                    value: "\(NytrNumberFormat.whole(magnitude) ?? magnitude) kcal"
                )
            }
            if ledger.consumedItemCount == 0 {
                Label("Nothing logged yet", systemImage: "plus.circle").font(.subheadline)
            } else if ledger.nutritionCompleteness != .complete {
                Label("Partial evidence · some values are unknown", systemImage: "info.circle")
                    .font(.subheadline)
            } else if ledger.nutritionAuthorities.contains(.estimated) || ledger.nutritionAuthorities.contains(.partial)
            {
                Label("Includes estimates", systemImage: "info.circle").font(.subheadline)
            }
            Text("Only recorded food counts toward these totals.").font(.caption).foregroundStyle(.secondary)
        }
        .fixedSize(horizontal: false, vertical: true)
        .padding(24)
        .background(NytrDesign.accent.opacity(0.08), in: RoundedRectangle(cornerRadius: NytrDesign.radius))
        .overlay(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: 3).fill(NytrDesign.accent).frame(width: 36, height: 4)
                .padding(.leading, 24)
        }
    }
}

struct TrainingUnitPicker: View {
    @AppStorage(TrainingWeightUnit.preferenceKey) private var unit = TrainingWeightUnit.lb.rawValue
    var body: some View {
        Picker("Training weight unit", selection: $unit) {
            Text("lb").tag("lb")
            Text("kg").tag("kg")
        }.pickerStyle(.segmented).frame(minWidth: 100, maxWidth: 150)
            .accessibilityLabel("Training weight unit")
    }
}

extension View {
    func nytrList() -> some View {
        self
            #if os(iOS)
                .listStyle(.insetGrouped)
                .listSectionSpacing(20)
                .scrollDismissesKeyboard(.interactively)
            #endif
            .tint(NytrDesign.accent)
    }
}
