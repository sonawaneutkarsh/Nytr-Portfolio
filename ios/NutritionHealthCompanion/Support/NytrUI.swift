import SwiftUI

/// Keeps long values readable on narrow screens and at accessibility text sizes.
struct NytrMetricRow: View {
    let title: String
    let value: String
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    init(_ title: String, value: String) {
        self.title = title
        self.value = value
    }

    var body: some View {
        Group {
            if dynamicTypeSize.isAccessibilitySize {
                stacked
            } else {
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .firstTextBaseline, spacing: 16) {
                        Text(title).fixedSize()
                        Spacer(minLength: 0)
                        Text(value).monospacedDigit().fixedSize()
                    }
                    stacked
                }
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(title)
        .accessibilityValue(value)
    }

    private var stacked: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).foregroundStyle(.secondary)
            Text(value).monospacedDigit()
        }
        .fixedSize(horizontal: false, vertical: true)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A quiet status treatment that communicates through words and a symbol.
struct NytrStatusLabel: View {
    let title: String
    let systemImage: String

    var body: some View {
        Label(title, systemImage: systemImage)
            .font(.subheadline.weight(.medium))
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityElement(children: .combine)
    }
}

/// A persistent field label, including after a value has been entered.
struct NytrNumberField: View {
    let title: String
    @Binding var text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.subheadline).foregroundStyle(.secondary)
            TextField(title, text: $text)
                #if os(iOS)
                    .keyboardType(.decimalPad)
                #endif
                .accessibilityLabel(title)
                .frame(minHeight: 44)
        }
    }
}

struct NytrWaistTrend: View {
    let trend: WaistTrendDTO

    var body: some View {
        NytrStatusLabel(
            title: statusTitle,
            systemImage: trend.status == "ready" ? "chart.xyaxis.line" : "info.circle"
        )
        if trend.status == "ready", let rate = trend.weeklyRateCm {
            NytrMetricRow("Waist trend", value: "\(rate) cm/week")
        }
        Text("\(trend.representedDayCount) measurement days across \(trend.coverageSpanDays) days")
            .font(.caption)
            .foregroundStyle(.secondary)
    }

    private var statusTitle: String {
        switch trend.status {
        case "ready": "Trend available"
        case "stale": "A recent waist measurement is needed"
        case "no_data": "No waist measurements yet"
        default: "More measurements needed for a trend"
        }
    }
}

struct NytrPhaseAssessmentSection: View {
    let assessment: PhaseAssessmentDTO

    var body: some View {
        Section("Phase assessment") {
            NytrStatusLabel(
                title: assessment.status.replacingOccurrences(of: "_", with: " ").capitalized,
                systemImage: assessment.status == "on_track" ? "checkmark.circle" : "info.circle"
            )
            ForEach(assessment.reasonCodes, id: \.self) { reason in
                Text(BodyEvidenceCopy.phaseReason(reason)).foregroundStyle(.secondary)
            }
            Text("Recommendation only. Your goal and targets stay unchanged until you approve a change.")
                .font(.caption).foregroundStyle(.secondary)
            DisclosureGroup("Assessment details") {
                NytrMetricRow("Policy", value: assessment.policyVersion)
                ForEach(assessment.reasonCodes, id: \.self) { reason in
                    Text(reason).font(.caption)
                }
            }
        }
    }
}
