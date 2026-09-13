import Foundation

/// Presentation only: the server remains authoritative for assessment and status.
enum BodyEvidenceCopy {
    static func phaseReason(_ code: String) -> String {
        switch code {
        case "insufficient_weight_or_waist_evidence":
            return "More current weight and waist measurements are needed."
        case "gain_rate_in_band": return "Weight gain is within your goal range."
        case "waist_slow_or_stable": return "Waist is stable or increasing slowly."
        case "gain_rate_above_band": return "Weight gain is above your goal range."
        case "waist_increasing_quickly": return "Waist is increasing quickly."
        case "loss_rate_in_band": return "Weight loss is within your goal range."
        case "waist_decreasing": return "Waist is decreasing."
        case "weight_and_waist_flat": return "Weight and waist show little change."
        case "weight_and_waist_stable": return "Weight and waist are stable."
        case "maintenance_drift": return "Weight or waist is moving beyond the maintenance range."
        case "mixed_gain_evidence", "mixed_loss_evidence", "mixed_maintenance_evidence":
            return "Weight and waist show mixed signals. Keep observing before making a change."
        default: return "This assessment needs further review."
        }
    }
}
