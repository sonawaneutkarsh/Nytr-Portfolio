import Foundation

enum TrainingWeightUnit: String, CaseIterable {
    case lb, kg
    static let preferenceKey = "nytr.training.weightUnit"
    static func preferred(in defaults: UserDefaults = .standard) -> Self {
        Self(rawValue: defaults.string(forKey: preferenceKey) ?? "lb") ?? .lb
    }
    func display(_ value: String, sourceUnit: String = "kg", volume: Bool = false) -> String {
        guard let number = Decimal(string: value, locale: Locale(identifier: "en_US_POSIX")),
            sourceUnit == "kg" || sourceUnit == "lb"
        else { return "\(value) \(sourceUnit)" }
        let kgPerPound = Decimal(string: "0.45359237")!
        let canonical = sourceUnit == "lb" ? number * kgPerPound : number
        var converted = self == .lb ? canonical / kgPerPound : canonical
        var rounded = Decimal()
        NSDecimalRound(&rounded, &converted, 1, .plain)
        return "\(NSDecimalNumber(decimal: rounded).stringValue) \(rawValue)\(volume ? "·reps" : "")"
    }
}
