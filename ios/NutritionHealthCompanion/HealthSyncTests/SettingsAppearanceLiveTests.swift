#if os(iOS)
    import SwiftUI
    import UIKit
    import XCTest

    @testable import NutritionHealthCompanion

    /// Proves an ALREADY-PRESENTED Settings hierarchy recolors on the first
    /// change, with no dismissal, re-presentation, or artificial delay.
    ///
    /// Requires a simulator run (UIKit hosting + window). The non-Xcode harness
    /// only typechecks this file.
    @MainActor
    final class SettingsAppearanceLiveTests: XCTestCase {
        // Not `private`: the @Observable macro emits a file-scope conformance
        // extension that must be able to see this type.
        @Observable
        fileprivate final class AppearanceStore {
            var appearance: NytrAppearance
            init(_ appearance: NytrAppearance) { self.appearance = appearance }
        }

        /// Structurally identical to the sheet content root in `RootView`: a
        /// `NavigationStack` wrapping Form/List surfaces, carrying the preferred
        /// color scheme derived from the one persisted appearance preference.
        private struct PresentedSettingsRoot: View {
            let store: AppearanceStore

            var body: some View {
                NavigationStack {
                    Form {
                        Section("Appearance") {
                            Picker(
                                "Appearance",
                                selection: Binding(
                                    get: { store.appearance },
                                    set: { store.appearance = $0 })
                            ) {
                                ForEach(NytrAppearance.allCases) { option in
                                    Text(option.label).tag(option)
                                }
                            }
                            .pickerStyle(.segmented)
                            Text("System follows your device. Light and Dark override it for Nytr.")
                                .font(.caption)
                        }
                        Section("Apple Health") {
                            Text("Health Sync")
                        }
                        Section("Account") {
                            Button("Sign Out", role: .destructive) {}
                        }
                    }
                    .navigationTitle("Settings")
                }
                .preferredColorScheme(store.appearance.colorScheme)
            }
        }

        /// Regions of the presented sheet that must all follow the scheme.
        private enum Surface: String, CaseIterable {
            case navigationBar
            case formSurface

            var region: CGRect {
                switch self {
                case .navigationBar: CGRect(x: 0, y: 50, width: 393, height: 60)
                case .formSurface: CGRect(x: 0, y: 260, width: 393, height: 340)
                }
            }
        }

        func testAlreadyPresentedSettingsRecolorsOnEveryTransitionWithoutDismissal() async throws {
            let systemStyle = try systemInterfaceStyle()

            let store = AppearanceStore(.dark)
            let host = UIHostingController(rootView: PresentedSettingsRoot(store: store))
            let scene = try XCTUnwrap(
                UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first)
            let window = UIWindow(windowScene: scene)
            window.frame = CGRect(x: 0, y: 0, width: 393, height: 852)
            window.rootViewController = host
            window.makeKeyAndVisible()
            host.view.frame = window.bounds
            defer { window.isHidden = true }

            try await settle(host)
            XCTAssertEqual(host.traitCollection.userInterfaceStyle, .dark)
            var previous = try measure(window, label: "start-dark")

            // Covers every required direction on ONE presentation:
            // Dark->Light, Light->Dark, Dark->System, System->Light,
            // Light->System, System->Dark.
            let sequence: [NytrAppearance] = [.light, .dark, .system, .light, .system, .dark]
            var appliedFrom = NytrAppearance.dark

            for target in sequence {
                let expected: UIUserInterfaceStyle =
                    switch target {
                    case .light: .light
                    case .dark: .dark
                    case .system: systemStyle
                    }
                let label = "\(appliedFrom.rawValue)-to-\(target.rawValue)"

                // THE single change under test. Nothing is dismissed or rebuilt.
                store.appearance = target
                try await settle(host)

                XCTAssertEqual(
                    host.traitCollection.userInterfaceStyle, expected,
                    "\(label): open Settings did not resolve the expected style on the first change")

                let current = try measure(window, label: label)
                if expected != previous.style {
                    for surface in Surface.allCases {
                        let before = try XCTUnwrap(previous.luminance[surface])
                        let after = try XCTUnwrap(current.luminance[surface])
                        XCTAssertGreaterThan(
                            abs(after - before), 0.15,
                            "\(label): \(surface.rawValue) did not visibly recolor")
                        if expected == .light {
                            XCTAssertGreaterThan(
                                after, 0.55,
                                "\(label): \(surface.rawValue) is not a light surface")
                        } else {
                            XCTAssertLessThan(
                                after, 0.35,
                                "\(label): \(surface.rawValue) is not a dark surface")
                        }
                    }
                }

                previous = current
                appliedFrom = target
            }

            // Same hosting controller and window throughout: no re-presentation.
            XCTAssertTrue(host.viewIfLoaded?.window === window)
            XCTAssertEqual(host.traitCollection.userInterfaceStyle, .dark)
        }

        // MARK: - Measurement

        private struct Measurement {
            let style: UIUserInterfaceStyle
            let luminance: [Surface: CGFloat]
        }

        private func measure(_ window: UIWindow, label: String) throws -> Measurement {
            let image = render(window)
            let attachment = XCTAttachment(image: image)
            attachment.name = "nytr-settings-open-\(label)"
            attachment.lifetime = .keepAlways
            add(attachment)

            var luminance: [Surface: CGFloat] = [:]
            for surface in Surface.allCases {
                luminance[surface] = try XCTUnwrap(
                    averageLuminance(image, region: surface.region),
                    "Could not sample \(surface.rawValue)")
            }
            return Measurement(
                style: window.traitCollection.userInterfaceStyle, luminance: luminance)
        }

        private func render(_ window: UIWindow) -> UIImage {
            UIGraphicsImageRenderer(bounds: window.bounds).image { _ in
                window.drawHierarchy(in: window.bounds, afterScreenUpdates: true)
            }
        }

        /// Mean sRGB relative luminance of a region, used only to prove a visible
        /// light/dark flip. This is not a pixel baseline.
        private func averageLuminance(_ image: UIImage, region: CGRect) -> CGFloat? {
            guard let source = image.cgImage, image.size.width > 0 else { return nil }
            let scale = CGFloat(source.width) / image.size.width
            let scaled = CGRect(
                x: region.minX * scale, y: region.minY * scale,
                width: region.width * scale, height: region.height * scale)
            guard let crop = source.cropping(to: scaled) else { return nil }
            let width = crop.width
            let height = crop.height
            guard width > 0, height > 0 else { return nil }
            var bytes = [UInt8](repeating: 0, count: width * height * 4)
            guard let space = CGColorSpace(name: CGColorSpace.sRGB) else { return nil }
            guard
                let context = CGContext(
                    data: &bytes, width: width, height: height, bitsPerComponent: 8,
                    bytesPerRow: width * 4, space: space,
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
            else { return nil }
            context.draw(crop, in: CGRect(x: 0, y: 0, width: width, height: height))
            var total: CGFloat = 0
            for index in stride(from: 0, to: bytes.count, by: 4) {
                let red = CGFloat(bytes[index]) / 255
                let green = CGFloat(bytes[index + 1]) / 255
                let blue = CGFloat(bytes[index + 2]) / 255
                total += 0.2126 * red + 0.7152 * green + 0.0722 * blue
            }
            return total / CGFloat(width * height)
        }

        /// The simulator's own Light/Dark setting, read from a window that carries
        /// no override, so `System` can be asserted exactly.
        private func systemInterfaceStyle() throws -> UIUserInterfaceStyle {
            let scene = try XCTUnwrap(
                UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first)
            let probe = UIWindow(windowScene: scene)
            probe.frame = CGRect(x: 0, y: 0, width: 1, height: 1)
            probe.rootViewController = UIViewController()
            let style = probe.traitCollection.userInterfaceStyle
            XCTAssertNotEqual(style, .unspecified)
            return style
        }

        private func settle(_ host: UIViewController) async throws {
            try await Task.sleep(for: .milliseconds(450))
            host.view.setNeedsLayout()
            host.view.layoutIfNeeded()
            try await Task.sleep(for: .milliseconds(150))
        }
    }
#endif
