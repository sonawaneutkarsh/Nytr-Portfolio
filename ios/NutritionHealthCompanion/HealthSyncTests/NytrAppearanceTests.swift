import SwiftUI
import XCTest

@testable import NutritionHealthCompanion

final class NytrAppearanceTests: XCTestCase {
    func testAppearancePreferenceMapsSystemLightAndDarkAtRoot() {
        XCTAssertNil(NytrAppearance.system.colorScheme)
        XCTAssertEqual(NytrAppearance.light.colorScheme, ColorScheme.light)
        XCTAssertEqual(NytrAppearance.dark.colorScheme, ColorScheme.dark)
        XCTAssertEqual(NytrAppearance.preferenceKey, "nytr.appearance")
        XCTAssertEqual(NytrAppearance.allCases.map(\.rawValue), ["system", "light", "dark"])
    }

    func testSettingsBindingWritesCanonicalPreferenceImmediatelyInEveryDirection() {
        final class Storage {
            var rawValue = NytrAppearance.dark.rawValue
        }
        let storage = Storage()
        let raw = Binding(
            get: { storage.rawValue },
            set: { storage.rawValue = $0 }
        )
        let selection = NytrAppearance.binding(raw)

        XCTAssertEqual(selection.wrappedValue, .dark)
        selection.wrappedValue = .light
        XCTAssertEqual(storage.rawValue, "light")
        XCTAssertEqual(selection.wrappedValue.colorScheme, .light)
        selection.wrappedValue = .dark
        XCTAssertEqual(storage.rawValue, "dark")
        XCTAssertEqual(selection.wrappedValue.colorScheme, .dark)
        selection.wrappedValue = .system
        XCTAssertEqual(storage.rawValue, "system")
        XCTAssertNil(selection.wrappedValue.colorScheme)
    }

    /// The defect: the app root and the already-presented Settings sheet derived
    /// their color scheme from different places, so only the root followed a
    /// change. Both now derive from the SAME binding, so every transition must
    /// agree within one render cycle with no dismissal in between.
    func testOpenSettingsSheetAndUnderlyingRootAgreeOnEveryTransition() {
        final class Storage {
            var rawValue = NytrAppearance.dark.rawValue
        }
        let storage = Storage()
        let selection = NytrAppearance.binding(
            Binding(get: { storage.rawValue }, set: { storage.rawValue = $0 })
        )

        // Exactly what CompanionApp applies at the app root and what RootView
        // now applies to the presented Settings hierarchy.
        func rootScheme() -> ColorScheme? {
            (NytrAppearance(rawValue: storage.rawValue) ?? .system).colorScheme
        }
        func presentedSettingsScheme() -> ColorScheme? {
            selection.wrappedValue.colorScheme
        }

        let transitions: [(NytrAppearance, ColorScheme?)] = [
            (.light, .light),  // dark -> light, the reported first tap
            (.dark, .dark),  // light -> dark
            (.system, nil),  // dark -> system
            (.light, .light),  // system -> explicit light
            (.system, nil),  // light -> system
            (.dark, .dark),  // system -> explicit dark
        ]
        for (target, expected) in transitions {
            selection.wrappedValue = target
            XCTAssertEqual(presentedSettingsScheme(), expected)
            XCTAssertEqual(rootScheme(), expected)
            XCTAssertEqual(
                presentedSettingsScheme(), rootScheme(),
                "Open Settings sheet and underlying root disagreed for \(target.rawValue)")
            XCTAssertEqual(storage.rawValue, target.rawValue)
        }
    }

    /// There must remain exactly ONE persisted preference, and it must survive a
    /// relaunch. A fresh reader for the same suite models the next cold launch.
    func testAppearancePersistsUnderOneCanonicalKeyAcrossRelaunch() {
        let suite = "nytr.appearance.tests.\(UUID().uuidString)"
        guard let defaults = UserDefaults(suiteName: suite) else {
            XCTFail("Could not open an isolated defaults suite")
            return
        }
        defer { defaults.removePersistentDomain(forName: suite) }

        XCTAssertNil(defaults.string(forKey: NytrAppearance.preferenceKey))
        defaults.set(NytrAppearance.light.rawValue, forKey: NytrAppearance.preferenceKey)

        guard let relaunched = UserDefaults(suiteName: suite) else {
            XCTFail("Could not reopen the defaults suite as a relaunch would")
            return
        }
        let restored = NytrAppearance(
            rawValue: relaunched.string(forKey: NytrAppearance.preferenceKey) ?? "")
        XCTAssertEqual(restored, .light)
        XCTAssertEqual(restored?.colorScheme, ColorScheme.light)

        // Only the canonical key is written; no second owner exists.
        let nytrKeys = relaunched.dictionaryRepresentation().keys.filter {
            $0.hasPrefix("nytr.appearance")
        }
        XCTAssertEqual(nytrKeys, [NytrAppearance.preferenceKey])

        relaunched.set(NytrAppearance.system.rawValue, forKey: NytrAppearance.preferenceKey)
        guard let reread = UserDefaults(suiteName: suite) else {
            XCTFail("Could not reopen the defaults suite a second time")
            return
        }
        XCTAssertEqual(
            NytrAppearance(rawValue: reread.string(forKey: NytrAppearance.preferenceKey) ?? ""),
            .system)
    }

    func testUnknownPersistedValueFallsBackToSystemInsteadOfCrashing() {
        XCTAssertNil(NytrAppearance(rawValue: "sepia"))
        let resolved = NytrAppearance(rawValue: "sepia") ?? .system
        XCTAssertEqual(resolved, .system)
        XCTAssertNil(resolved.colorScheme)
    }
}
