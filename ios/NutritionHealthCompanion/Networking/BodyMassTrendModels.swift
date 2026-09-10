import Foundation

enum BodyMassTrendStatus: String, Decodable, Equatable, Sendable {
    case noData = "no_data"
    case insufficient
    case stale
    case ready
}

/// Typed representation of the backend-authoritative M9 trend summary.
/// Decimal values stay as lossless strings; Swift only parses them for display.
struct BodyMassTrendResponse: Decodable, Equatable, Sendable {
    let status: BodyMassTrendStatus
    let asOfDate: String
    let timezone: String
    let algorithmVersion: String
    let inputDigest: String
    let representedDayCount: Int
    let coverageSpanDays: Int
    let firstMeasurementDate: String?
    let lastMeasurementDate: String?
    let latestMeasurementDate: String?
    let latestMeasurementAgeDays: Int?
    let trailing7dAverageKg: String?
    let weeklyRateKg: String?

    enum CodingKeys: String, CodingKey {
        case status
        case asOfDate = "as_of_date"
        case timezone
        case algorithmVersion = "algorithm_version"
        case inputDigest = "input_digest"
        case representedDayCount = "represented_day_count"
        case coverageSpanDays = "coverage_span_days"
        case firstMeasurementDate = "first_measurement_date"
        case lastMeasurementDate = "last_measurement_date"
        case latestMeasurementDate = "latest_measurement_date"
        case latestMeasurementAgeDays = "latest_measurement_age_days"
        case trailing7dAverageKg = "trailing_7d_average_kg"
        case weeklyRateKg = "weekly_rate_kg"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        status = try values.decode(BodyMassTrendStatus.self, forKey: .status)
        asOfDate = try values.decode(String.self, forKey: .asOfDate)
        timezone = try values.decode(String.self, forKey: .timezone)
        algorithmVersion = try values.decode(String.self, forKey: .algorithmVersion)
        inputDigest = try values.decode(String.self, forKey: .inputDigest)
        representedDayCount = try values.decode(Int.self, forKey: .representedDayCount)
        coverageSpanDays = try values.decode(Int.self, forKey: .coverageSpanDays)
        firstMeasurementDate = try values.decodeIfPresent(
            String.self, forKey: .firstMeasurementDate)
        lastMeasurementDate = try values.decodeIfPresent(
            String.self, forKey: .lastMeasurementDate)
        latestMeasurementDate = try values.decodeIfPresent(
            String.self, forKey: .latestMeasurementDate)
        latestMeasurementAgeDays = try values.decodeIfPresent(
            Int.self, forKey: .latestMeasurementAgeDays)
        trailing7dAverageKg = try values.decodeIfPresent(
            String.self, forKey: .trailing7dAverageKg)
        weeklyRateKg = try values.decodeIfPresent(String.self, forKey: .weeklyRateKg)

        guard Self.isLocalDay(asOfDate) else {
            throw DecodingError.dataCorruptedError(
                forKey: .asOfDate, in: values, debugDescription: "invalid local date")
        }
        for (key, day) in [
            (CodingKeys.firstMeasurementDate, firstMeasurementDate),
            (CodingKeys.lastMeasurementDate, lastMeasurementDate),
            (CodingKeys.latestMeasurementDate, latestMeasurementDate),
        ] where day != nil {
            guard Self.isLocalDay(day!) else {
                throw DecodingError.dataCorruptedError(
                    forKey: key, in: values, debugDescription: "invalid local date")
            }
        }
        guard !timezone.isEmpty, !algorithmVersion.isEmpty else {
            throw DecodingError.dataCorruptedError(
                forKey: .timezone, in: values,
                debugDescription: "timezone and algorithm version must not be empty")
        }
        guard inputDigest.count == 64,
              inputDigest.allSatisfy({ $0.isHexDigit })
        else {
            throw DecodingError.dataCorruptedError(
                forKey: .inputDigest, in: values, debugDescription: "invalid input digest")
        }
        guard representedDayCount >= 0, coverageSpanDays >= 0,
              latestMeasurementAgeDays.map({ $0 >= 0 }) ?? true
        else {
            throw DecodingError.dataCorruptedError(
                forKey: .representedDayCount, in: values,
                debugDescription: "trend counts must not be negative")
        }
        try Self.validateDecimalString(trailing7dAverageKg, key: .trailing7dAverageKg, in: values)
        try Self.validateDecimalString(weeklyRateKg, key: .weeklyRateKg, in: values)
    }

    var formattedTrailingAverageKg: String? {
        Self.formattedDecimal(trailing7dAverageKg, signed: false)
    }

    var formattedWeeklyRateKg: String? {
        Self.formattedDecimal(weeklyRateKg, signed: true)
    }

    private static func validateDecimalString(
        _ raw: String?,
        key: CodingKeys,
        in values: KeyedDecodingContainer<CodingKeys>
    ) throws {
        guard let raw else { return }
        guard decimal(from: raw) != nil else {
            throw DecodingError.dataCorruptedError(
                forKey: key, in: values, debugDescription: "invalid decimal string")
        }
    }

    private static func formattedDecimal(_ raw: String?, signed: Bool) -> String? {
        guard let raw, let value = decimal(from: raw) else { return nil }
        let formatter = NumberFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.numberStyle = .decimal
        formatter.minimumFractionDigits = 2
        formatter.maximumFractionDigits = 2
        formatter.usesGroupingSeparator = false
        guard let rendered = formatter.string(from: NSDecimalNumber(decimal: value)) else {
            return nil
        }
        if signed, value > 0 { return "+\(rendered)" }
        return rendered
    }

    private static func decimal(from raw: String) -> Decimal? {
        let pattern = #"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"#
        guard raw.range(of: pattern, options: .regularExpression) != nil,
              let value = Decimal(string: raw, locale: Locale(identifier: "en_US_POSIX")),
              !value.isNaN
        else { return nil }
        return value
    }

    private static func isLocalDay(_ value: String) -> Bool {
        let pieces = value.split(separator: "-", omittingEmptySubsequences: false)
        guard value.count == 10,
              pieces.count == 3,
              pieces[0].count == 4,
              pieces[1].count == 2,
              pieces[2].count == 2,
              let year = Int(pieces[0]),
              let month = Int(pieces[1]),
              let day = Int(pieces[2]),
              year >= 1
        else { return false }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let components = DateComponents(year: year, month: month, day: day)
        guard let date = calendar.date(from: components) else { return false }
        let roundTrip = calendar.dateComponents([.year, .month, .day], from: date)
        return roundTrip.year == year && roundTrip.month == month && roundTrip.day == day
    }
}
