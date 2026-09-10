import XCTest
@testable import NutritionHealthCompanion

final class SignInViewModelTests: XCTestCase {
    private final class StubProtocol: URLProtocol {
        nonisolated(unsafe) static var handler: ((URLRequest) -> (Int, Data))?

        override class func canInit(with request: URLRequest) -> Bool { true }
        override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
        override func startLoading() {
            guard let handler = Self.handler else { return }
            let (status, data) = handler(request)
            let response = HTTPURLResponse(
                url: request.url!, statusCode: status,
                httpVersion: "HTTP/1.1", headerFields: nil
            )!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        }
        override func stopLoading() {}
    }

    private final class RecordingTokenStore: TokenStore, @unchecked Sendable {
        private let lock = NSLock()
        private var stored: AuthTokens?
        private var saves = 0
        private var clears = 0

        init(_ initial: AuthTokens? = nil) { stored = initial }

        func save(_ tokens: AuthTokens) throws {
            lock.lock(); defer { lock.unlock() }
            stored = tokens
            saves += 1
        }

        func load() -> AuthTokens? {
            lock.lock(); defer { lock.unlock() }
            return stored
        }

        func clear() throws {
            lock.lock(); defer { lock.unlock() }
            stored = nil
            clears += 1
        }

        var saveCount: Int {
            lock.lock(); defer { lock.unlock() }
            return saves
        }

        var clearCount: Int {
            lock.lock(); defer { lock.unlock() }
            return clears
        }
    }

    private actor BlockingRequester: MagicLinkRequesting {
        private var calls = 0
        private var continuation: CheckedContinuation<Void, Never>?

        func requestMagicLink(email _: String, redirectTo _: URL) async throws {
            calls += 1
            await withCheckedContinuation { continuation = $0 }
        }

        func callCount() -> Int { calls }
        func finish() {
            continuation?.resume()
            continuation = nil
        }
    }

    override func tearDown() {
        StubProtocol.handler = nil
        super.tearDown()
    }

    private func makeSession(
        store: RecordingTokenStore,
        now: @escaping @Sendable () -> Date = { Date(timeIntervalSince1970: 2_000) }
    ) -> SupabaseAuthSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubProtocol.self]
        return SupabaseAuthSession(
            supabaseURL: URL(string: "https://project.supabase.co")!,
            anonKey: "public-anon-key",
            tokens: store,
            session: URLSession(configuration: configuration),
            now: now
        )
    }

    private func jwt(subject: String) -> String {
        func encode(_ value: [String: String]) -> String {
            let data = try! JSONSerialization.data(withJSONObject: value)
            return data.base64EncodedString()
                .replacingOccurrences(of: "+", with: "-")
                .replacingOccurrences(of: "/", with: "_")
                .replacingOccurrences(of: "=", with: "")
        }
        return "\(encode(["alg": "none"]))\(String("."))\(encode(["sub": subject])).signature"
    }

    private func callback(
        token: String,
        refreshToken: String = "refresh-one",
        expiresIn: Int = 3_600
    ) -> URL {
        URL(string: "nutritionhealthcompanion://auth-callback#access_token=\(token)&refresh_token=\(refreshToken)&expires_in=\(expiresIn)&token_type=bearer")!
    }

    @MainActor
    func test_validEmail_sendsImplicitOTPRequestAndShowsCheckEmail() async {
        let store = RecordingTokenStore()
        let session = makeSession(store: store)
        let viewModel = SignInViewModel(requester: session)
        viewModel.email = " demo@example.invalid "

        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/auth/v1/otp")
            let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)
            XCTAssertEqual(
                components?.queryItems?.first(where: { $0.name == "redirect_to" })?.value,
                "nutritionhealthcompanion://auth-callback"
            )
            XCTAssertEqual(request.value(forHTTPHeaderField: "apikey"), "public-anon-key")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"),
                "Bearer public-anon-key"
            )
            let body = try! JSONSerialization.jsonObject(with: StubRequestBody.data(from: request)) as! [String: Any]
        XCTAssertEqual(body["email"] as? String, "demo@example.invalid")
            XCTAssertEqual(body["create_user"] as? Bool, true)
            XCTAssertNil(body["code_challenge"])
            return (200, Data())
        }

        await viewModel.sendMagicLink()
        XCTAssertEqual(viewModel.phase, .checkEmail)
        XCTAssertNil(store.load())
    }

    @MainActor
    func test_invalidEmailAndServerFailureAreSafeAndDoNotCreateSession() async {
        let store = RecordingTokenStore()
        let session = makeSession(store: store)
        let viewModel = SignInViewModel(requester: session)
        viewModel.email = "not-an-email"
        await viewModel.sendMagicLink()
        XCTAssertEqual(viewModel.phase, .failed(message: "Enter a valid email address."))

        StubProtocol.handler = { _ in (429, Data("provider details must stay hidden".utf8)) }
        viewModel.email = "demo@example.invalid"
        await viewModel.sendMagicLink()
        XCTAssertEqual(
            viewModel.phase,
            .failed(message: "We couldn't send the sign-in link. Please try again.")
        )
        XCTAssertNil(store.load())
    }

    @MainActor
    func test_duplicateTapWhileSendingMakesOneRequest() async {
        let requester = BlockingRequester()
        let viewModel = SignInViewModel(requester: requester)
        viewModel.email = "demo@example.invalid"

        let first = Task { await viewModel.sendMagicLink() }
        await Task.yield()
        let second = Task { await viewModel.sendMagicLink() }
        await Task.yield()
        let count = await requester.callCount()
        XCTAssertEqual(count, 1)
        await requester.finish()
        await first.value
        await second.value
        XCTAssertEqual(viewModel.phase, .checkEmail)
    }

    func test_callbackStoresSessionExactlyOnceAndReturnsSubject() throws {
        let now = Date(timeIntervalSince1970: 10_000)
        let store = RecordingTokenStore()
        let session = makeSession(store: store, now: { now })
        let accessToken = jwt(subject: "owner-subject")

        let subject = try session.completeMagicLinkCallback(callback(token: accessToken))

        XCTAssertEqual(subject, "owner-subject")
        XCTAssertEqual(store.saveCount, 1)
        XCTAssertEqual(store.load()?.accessToken, accessToken)
        XCTAssertEqual(store.load()?.refreshToken, "refresh-one")
        XCTAssertEqual(store.load()?.expiresAt, now.addingTimeInterval(3_600))
    }

    func test_callbackRejectsWrongRoutePKCEAndMalformedPayloadWithoutStorage() {
        let store = RecordingTokenStore()
        let session = makeSession(store: store)
        let token = jwt(subject: "owner-subject")
        let invalidURLs = [
            URL(string: "other://auth-callback#access_token=\(token)&refresh_token=r&expires_in=10")!,
            URL(string: "nutritionhealthcompanion://wrong#access_token=\(token)&refresh_token=r&expires_in=10")!,
            URL(string: "nutritionhealthcompanion://auth-callback?code=pkce-code")!,
            URL(string: "nutritionhealthcompanion://auth-callback#access_token=\(token)&expires_in=10")!,
            URL(string: "nutritionhealthcompanion://auth-callback#error=access_denied")!,
        ]

        for url in invalidURLs {
            do {
                _ = try session.completeMagicLinkCallback(url)
                XCTFail("expected callback rejection")
            } catch {
                XCTAssertNil(store.load())
            }
        }
        XCTAssertEqual(store.saveCount, 0)
    }

    func test_subjectAccessorReadsOnlySubFromStoredJWT() async throws {
        let token = jwt(subject: "cache-partition-subject")
        let store = RecordingTokenStore(AuthTokens(
            accessToken: token,
            refreshToken: "refresh",
            expiresAt: Date(timeIntervalSince1970: 20_000)
        ))
        let session = makeSession(store: store)
        let subject = try await session.currentSubject()
        XCTAssertEqual(subject, "cache-partition-subject")
    }

    func test_refreshRemainsOwnedByAuthSessionAndUsesSupabaseContract() async throws {
        let store = RecordingTokenStore(AuthTokens(
            accessToken: "expired",
            refreshToken: "refresh-old",
            expiresAt: Date(timeIntervalSince1970: 1_000)
        ))
        let session = makeSession(store: store)
        let refreshedJWT = jwt(subject: "owner-subject")
        StubProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/auth/v1/token")
            let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)
            XCTAssertEqual(
                components?.queryItems?.first(where: { $0.name == "grant_type" })?.value,
                "refresh_token"
            )
            XCTAssertEqual(request.value(forHTTPHeaderField: "apikey"), "public-anon-key")
            return (200, Data("""
            {"access_token":"\(refreshedJWT)","refresh_token":"refresh-new","expires_at":9000}
            """.utf8))
        }

        let token = try await session.validAccessToken()
        XCTAssertEqual(token, refreshedJWT)
        XCTAssertEqual(store.load()?.refreshToken, "refresh-new")
    }

    @MainActor
    func test_missingSessionAndRefreshRejectionTransitionToSignedOut() async {
        let emptyStore = RecordingTokenStore()
        let emptySession = makeSession(store: emptyStore)
        let emptyViewModel = SessionViewModel(auth: emptySession)
        await emptyViewModel.checkSession()
        XCTAssertEqual(emptyViewModel.phase, .signedOut)

        let expiredStore = RecordingTokenStore(AuthTokens(
            accessToken: "expired",
            refreshToken: "invalid-refresh",
            expiresAt: Date(timeIntervalSince1970: 1_000)
        ))
        let expiredSession = makeSession(store: expiredStore)
        StubProtocol.handler = { _ in (401, Data()) }
        let expiredViewModel = SessionViewModel(auth: expiredSession)
        await expiredViewModel.checkSession()
        XCTAssertEqual(expiredViewModel.phase, .signedOut)
        XCTAssertNil(expiredStore.load())
        XCTAssertEqual(expiredStore.clearCount, 1)
    }

    @MainActor
    func test_restoredSessionIsEligibleForInitialForegroundSync() async {
        let store = RecordingTokenStore(AuthTokens(
            accessToken: jwt(subject: "owner-subject"),
            refreshToken: "refresh",
            expiresAt: Date(timeIntervalSince1970: 20_000)
        ))
        let viewModel = SessionViewModel(auth: makeSession(store: store))

        await viewModel.checkSession()

        XCTAssertEqual(viewModel.phase, .signedIn(subject: "owner-subject"))
        XCTAssertTrue(viewModel.shouldStartForegroundSync())
    }

    @MainActor
    func test_validCallbackTransitionsSessionToSignedIn() {
        let store = RecordingTokenStore()
        let session = makeSession(store: store)
        let viewModel = SessionViewModel(auth: session)
        let didHandle = viewModel.handleOpenURL(callback(token: jwt(subject: "owner-subject")))
        XCTAssertTrue(didHandle)
        XCTAssertEqual(viewModel.phase, .signedIn(subject: "owner-subject"))
    }

    @MainActor
    func test_validCallbackSuppressesOnlyItsHealthKitForegroundTrigger() {
        let store = RecordingTokenStore()
        let session = makeSession(store: store)
        let viewModel = SessionViewModel(auth: session)

        XCTAssertTrue(
            viewModel.handleOpenURL(callback(token: jwt(subject: "owner-subject")))
        )

        XCTAssertFalse(viewModel.shouldStartForegroundSync())
        XCTAssertTrue(viewModel.shouldStartForegroundSync())
        XCTAssertEqual(viewModel.phase, .signedIn(subject: "owner-subject"))
        XCTAssertEqual(store.saveCount, 1)
    }

    @MainActor
    func test_callbackAfterActiveEventPreservesNextRealForegroundSync() {
        let store = RecordingTokenStore()
        let session = makeSession(store: store)
        let viewModel = SessionViewModel(auth: session)

        // SwiftUI may publish scene activation before delivering onOpenURL.
        XCTAssertFalse(viewModel.shouldStartForegroundSync())
        XCTAssertTrue(
            viewModel.handleOpenURL(callback(token: jwt(subject: "owner-subject")))
        )

        viewModel.sceneDidLeaveActiveState()

        XCTAssertTrue(viewModel.shouldStartForegroundSync())
        XCTAssertEqual(viewModel.phase, .signedIn(subject: "owner-subject"))
    }

    @MainActor
    func test_signOutClearsSessionPlanCacheAndPresentationButPreservesAnchorAndDefaults() throws {
        let tokenStore = RecordingTokenStore(AuthTokens(
            accessToken: jwt(subject: "owner-subject"),
            refreshToken: "refresh",
            expiresAt: Date(timeIntervalSince1970: 20_000)
        ))
        let auth = makeSession(store: tokenStore)
        let session = SessionViewModel(auth: auth)
        let suite = "SignOutTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)
        defaults.set("keep", forKey: "unrelated.preference")
        defer { defaults.removePersistentDomain(forName: suite) }
        let cache = DayPlanCache(defaults: defaults)
        let plan = CompletedDayPlan(
            requestedDate: "2026-08-21",
            planDate: "2026-08-21",
            planSha256: "sha",
            inputsFingerprint: "fp",
            runId: UUID(uuidString: "10000000-0000-0000-0000-000000000001")!,
            versionId: UUID(uuidString: "20000000-0000-0000-0000-000000000001")!,
            plan: DailyPlanArtifact(
                artifactKind: "daily_plan",
                planDate: "2026-08-21",
                policyVersions: PlanPolicyVersions(
                    engine: "e", planner: "p", schedule: "s", target: "t"),
                menuSnapshotSha256: "menu",
                slots: [],
                status: "ok"
            ),
            planItems: [],
            targetPolicy: nil,
            generatedAt: nil
        )
        XCTAssertTrue(cache.save(.completed(plan), forSubject: "owner-subject"))
        let today = TodayViewModel(backend: FakeBackend(), cache: cache)
        let syncStore = InMemoryStateStore()
        let workoutSyncStore = InMemoryStateStore()
        let syncState = DurableSyncState(
            durableAnchorData: Data("anchor-must-survive".utf8),
            lastSuccessfulSync: Date(timeIntervalSince1970: 100),
            lastAttempt: Date(timeIntervalSince1970: 100),
            lastErrorDescription: nil
        )
        try syncStore.persist(syncState)
        let workoutSyncState = DurableSyncState(
            durableAnchorData: Data("workout-anchor-must-survive".utf8),
            lastSuccessfulSync: Date(timeIntervalSince1970: 101),
            lastAttempt: Date(timeIntervalSince1970: 101),
            lastErrorDescription: nil
        )
        try workoutSyncStore.persist(workoutSyncState)

        session.signOut { today.clearForSignOut() }

        XCTAssertNil(tokenStore.load())
        XCTAssertEqual(tokenStore.clearCount, 1)
        XCTAssertNil(cache.load(forSubject: "owner-subject"))
        XCTAssertEqual(defaults.string(forKey: "unrelated.preference"), "keep")
        XCTAssertEqual(today.phase, .signedOut)
        XCTAssertEqual(session.phase, .signedOut)
        XCTAssertEqual(try syncStore.load(), syncState)
        XCTAssertEqual(try workoutSyncStore.load(), workoutSyncState)
    }
}
