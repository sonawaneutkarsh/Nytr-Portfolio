import SwiftUI

#if os(iOS) && canImport(VisionKit)
import VisionKit

struct BarcodeScannerView: UIViewControllerRepresentable {
    static var isAvailable: Bool {
        DataScannerViewController.isSupported && DataScannerViewController.isAvailable
    }

    let onScanned: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onScanned: onScanned) }

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let controller = DataScannerViewController(
            recognizedDataTypes: [
                .barcode(symbologies: [.ean8, .ean13, .upce])
            ],
            qualityLevel: .balanced,
            recognizesMultipleItems: false,
            isHighFrameRateTrackingEnabled: false,
            isPinchToZoomEnabled: true,
            isGuidanceEnabled: true,
            isHighlightingEnabled: true
        )
        controller.delegate = context.coordinator
        return controller
    }

    func updateUIViewController(_ controller: DataScannerViewController, context: Context) {
        if !controller.isScanning { try? controller.startScanning() }
    }

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        private let onScanned: (String) -> Void
        private var delivered = false

        init(onScanned: @escaping (String) -> Void) { self.onScanned = onScanned }

        func dataScanner(
            _ dataScanner: DataScannerViewController,
            didAdd addedItems: [RecognizedItem],
            allItems: [RecognizedItem]
        ) {
            guard !delivered else { return }
            for item in addedItems {
                guard case .barcode(let barcode) = item,
                    let value = barcode.payloadStringValue
                else { continue }
                delivered = true
                dataScanner.stopScanning()
                onScanned(value)
                return
            }
        }
    }
}
#else
struct BarcodeScannerView: View {
    static let isAvailable = false
    let onScanned: (String) -> Void

    var body: some View {
        Text("Camera barcode scanning is unavailable on this device.")
            .padding()
    }
}
#endif
