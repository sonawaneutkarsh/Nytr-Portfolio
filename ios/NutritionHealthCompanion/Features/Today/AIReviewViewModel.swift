import Foundation
import Observation

@MainActor
@Observable
final class AIReviewViewModel {
    enum Phase: Equatable {
        case idle, loading
        case result(AIReviewResponse)
        case unavailable(AIReviewResponse)
        case error(String)
        case signedOut
    }
    private(set) var phase: Phase = .idle
    private(set) var isRequesting = false
    private(set) var isGenerating = false
    private(set) var evidence: OnDeviceReviewSnapshot?
    private(set) var localMessage: String?
    private(set) var localFailureCategory: LocalReviewFailureCategory?
    private let backend: any BackendClient
    private let reviewer: any OnDeviceReviewing
    private let requestDate: () -> Date
    private let timezoneIdentifier: () -> String
    private let onUnauthorized: @MainActor () -> Void
    private var subject: String?
    private var requestToken: UUID?
    private var generationTask: Task<AIReviewContentDTO, Error>?
    private let generationTimeout: Duration
    var modelAvailable: Bool { reviewer.isAvailable }
    var modelUnavailableMessage: String {
        reviewer.unavailableCategory == .modelNotReady
            ? "Apple Intelligence is still getting its model ready. Nytr analysis works without it."
            : "Use a supported device with Apple Intelligence enabled and its model ready. Nytr analysis works without it."
    }

    init(
        backend: any BackendClient, reviewer: (any OnDeviceReviewing)? = nil,
        requestDate: @escaping () -> Date = { WireDay.localRequestDate() },
        timezoneIdentifier: @escaping () -> String = { TimeZone.current.identifier },
        generationTimeout: Duration = .seconds(30),
        onUnauthorized: @escaping @MainActor () -> Void = {}
    ) {
        self.backend = backend
        self.reviewer = reviewer ?? AppleOnDeviceReview()
        self.requestDate = requestDate
        self.timezoneIdentifier = timezoneIdentifier
        self.generationTimeout = generationTimeout
        self.onUnauthorized = onUnauthorized
    }

    func activate(subject: String) {
        if self.subject != subject {
            generationTask?.cancel()
            generationTask = nil
            isGenerating = false
            self.subject = subject
            phase = .idle
            requestToken = nil
            evidence = nil
            localMessage = nil
            localFailureCategory = nil
            isRequesting = false
            if let category = reviewer.unavailableCategory {
                localFailureCategory = category
                debugLog(category)
            }
        }
    }

    /// Explicit read only. This endpoint cannot invoke a remote provider.
    func generate() async {
        guard let expectedSubject = subject, !isRequesting else { return }
        let token = UUID()
        requestToken = token
        isRequesting = true
        phase = .loading
        defer { if requestToken == token { isRequesting = false } }
        do {
            let value = try await backend.fetchReviewSnapshot(asOfDate: requestDate(), timezone: timezoneIdentifier())
            guard subject == expectedSubject, requestToken == token else { return }
            evidence = value
            localMessage = nil
            localFailureCategory = nil
            phase = .idle
        } catch BackendError.unauthorized {
            guard subject == expectedSubject, requestToken == token else { return }
            resetForSignOut()
            onUnauthorized()
        } catch {
            guard subject == expectedSubject, requestToken == token else { return }
            phase = .error("Current evidence could not be refreshed. Try again.")
        }
    }

    func explainOnDevice() async {
        guard let evidence, let expectedSubject = subject, !isRequesting else { return }
        guard reviewer.isAvailable else {
            reportLocalFailure(reviewer.unavailableCategory ?? .modelUnavailable)
            return
        }
        let token = UUID()
        requestToken = token
        isRequesting = true
        localMessage = nil
        localFailureCategory = nil
        isGenerating = true
        let task = Task { try await reviewer.explain(evidence.modelInput) }
        generationTask = task
        let deadline = Task { [weak self, generationTimeout] in
            do { try await Task.sleep(for: generationTimeout) } catch { return }
            guard let self, self.requestToken == token else { return }
            self.stopLocalReview(
                category: .generationTimeout,
                message: "AI Review took too long. Your Nytr analysis remains available."
            )
        }
        defer {
            deadline.cancel()
            if requestToken == token {
                isRequesting = false
                isGenerating = false
                generationTask = nil
            }
        }
        do {
            let review = try await task.value
            guard subject == expectedSubject, requestToken == token else { return }
            localFailureCategory = nil
            phase = .result(
                AIReviewResponse(
                    status: "available", promptVersion: "nytr.on-device.v2",
                    snapshot: evidence.snapshot, review: review, failureCode: nil,
                    authorityNotice: "On-device AI explanation. Nytr’s calculations remain authoritative."))
        } catch {
            guard subject == expectedSubject, requestToken == token else { return }
            let category =
                (error as? LocalReviewError)?.category
                ?? (error is CancellationError ? .cancelled : .unknownLocalModelFailure)
            reportLocalFailure(category)
            phase = .idle
        }
    }

    func cancelLocalReview() {
        stopLocalReview(
            category: .cancelled,
            message: "AI Review stopped. Your Nytr analysis remains available."
        )
    }

    private func stopLocalReview(
        category: LocalReviewFailureCategory, message: String
    ) {
        guard isGenerating else { return }
        generationTask?.cancel()
        generationTask = nil
        requestToken = nil
        isGenerating = false
        isRequesting = false
        phase = .idle
        localFailureCategory = category
        localMessage = message
        debugLog(category)
    }

    func invalidateEvidenceAfterConsumption() {
        generationTask?.cancel()
        generationTask = nil
        requestToken = nil
        isGenerating = false
        isRequesting = false
        evidence = nil
        localFailureCategory = nil
        localMessage = "Food was recorded. Review current evidence again before asking Apple Intelligence."
        if subject != nil { phase = .idle }
    }

    func resetForSignOut() {
        generationTask?.cancel()
        generationTask = nil
        isGenerating = false
        subject = nil
        requestToken = nil
        evidence = nil
        localMessage = nil
        localFailureCategory = nil
        phase = .signedOut
        isRequesting = false
    }

    private func reportLocalFailure(_ category: LocalReviewFailureCategory) {
        localFailureCategory = category
        switch category {
        case .modelUnavailable:
            localMessage = "AI Review requires Apple Intelligence"
        case .modelNotReady:
            localMessage = "Apple Intelligence is still getting ready. Your Nytr analysis remains available."
        case .unsupportedLocale:
            localMessage = "AI Review isn’t available for the current language. Your Nytr analysis remains available."
        case .generationTimeout:
            localMessage = "AI Review took too long. Your Nytr analysis remains available."
        case .cancelled:
            localMessage = "AI Review stopped. Your Nytr analysis remains available."
        case .generationRefused, .structuredGenerationFailed, .outputValidationFailed,
            .unknownLocalModelFailure:
            localMessage = "AI Review couldn’t finish on this device. Your Nytr analysis remains available."
        }
        debugLog(category)
    }

    private func debugLog(_ category: LocalReviewFailureCategory) {
        #if DEBUG
            print("[NytrLocalReview] failure_category=\(category.rawValue)")
        #endif
    }
}
