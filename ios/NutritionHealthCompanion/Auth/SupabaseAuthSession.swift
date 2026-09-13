import Foundation

enum SupabaseAuthError: Error, Equatable {
    case invalidEmail
    case requestRejected
    case invalidCallback
    case callbackRejected
}

protocol MagicLinkRequesting: Sendable {
    func requestMagicLink(email: String, redirectTo: URL) async throws
}

protocol AppAuthSession: AccessTokenProvider, MagicLinkRequesting {
    func completeMagicLinkCallback(_ url: URL) throws -> String
    func currentSubject() async throws -> String
    func signOut() throws
}

private actor AccessTokenRefreshCoordinator {
    private var inFlight: Task<String, Error>?

    func validAccessToken(
        tokens: any TokenStore,
        now: Date,
        refreshSkewSeconds: TimeInterval,
        refresh: @escaping @Sendable (AuthTokens) async throws -> String
    ) async throws -> String {
        guard let current = tokens.load() else { throw AuthProviderError.needsSignIn }
        if current.expiresAt.timeIntervalSince(now) > refreshSkewSeconds {
            return current.accessToken
        }
        if let inFlight {
            return try await inFlight.value
        }
        let task = Task { try await refresh(current) }
        inFlight = task
        do {
            let token = try await task.value
            inFlight = nil
            return token
        } catch {
            inFlight = nil
            throw error
        }
    }
}

/// The ONLY component that knows Supabase exists. Implements
/// AccessTokenProvider: expiry-checked refresh, token persistence via
/// TokenStore (Keychain), and mapping unrecoverable failures to needsSignIn.
struct SupabaseAuthSession: AppAuthSession {
    private let supabaseURL: URL
    private let anonKey: String
    private let tokens: TokenStore
    private let session: URLSession
    /// Injected clock for testability.
    private let now: @Sendable () -> Date
    /// Refresh when expiring within this window.
    private let refreshSkewSeconds: TimeInterval = 60
    private let refreshCoordinator: AccessTokenRefreshCoordinator

    init(
        supabaseURL: URL,
        anonKey: String,
        tokens: TokenStore,
        session: URLSession = .shared,
        now: @escaping @Sendable () -> Date = { Date() }
    ) {
        self.supabaseURL = supabaseURL
        self.anonKey = anonKey
        self.tokens = tokens
        self.session = session
        self.now = now
        refreshCoordinator = AccessTokenRefreshCoordinator()
    }

    func validAccessToken() async throws -> String {
        try await refreshCoordinator.validAccessToken(
            tokens: tokens,
            now: now(),
            refreshSkewSeconds: refreshSkewSeconds,
            refresh: { current in try await refresh(current: current) }
        )
    }

    /// Requests Supabase's implicit passwordless-email flow. Omitting PKCE
    /// parameters is deliberate: the matching app callback contains the
    /// access/refresh session in its URL fragment.
    func requestMagicLink(email: String, redirectTo: URL) async throws {
        let normalizedEmail = email.trimmingCharacters(in: .whitespacesAndNewlines)
        guard Self.looksLikeEmail(normalizedEmail) else {
            throw SupabaseAuthError.invalidEmail
        }

        var components = URLComponents(
            url: supabaseURL.appending(path: "auth/v1/otp"),
            resolvingAgainstBaseURL: false
        )
        components?.queryItems = [URLQueryItem(name: "redirect_to", value: redirectTo.absoluteString)]
        guard let url = components?.url else { throw SupabaseAuthError.requestRejected }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue(anonKey, forHTTPHeaderField: "apikey")
        request.setValue("Bearer \(anonKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "email": normalizedEmail,
            "create_user": true,
        ])

        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse,
              (200 ..< 300).contains(http.statusCode)
        else {
            throw SupabaseAuthError.requestRejected
        }
    }

    /// Completes only the proven implicit-flow callback contract. PKCE/code
    /// callbacks are intentionally rejected because this app does not own a
    /// verifier store or code-exchange flow.
    func completeMagicLinkCallback(_ url: URL) throws -> String {
        guard url.scheme?.lowercased() == "nutritionhealthcompanion",
              url.host?.lowercased() == "auth-callback",
              url.path.isEmpty,
              url.query == nil,
              let fragment = url.fragment,
              let fragmentComponents = URLComponents(string: "?\(fragment)")
        else {
            throw SupabaseAuthError.invalidCallback
        }

        var values: [String: String] = [:]
        for item in fragmentComponents.queryItems ?? [] {
            guard let value = item.value, values[item.name] == nil else {
                throw SupabaseAuthError.invalidCallback
            }
            values[item.name] = value
        }
        if values["error"] != nil || values["error_description"] != nil {
            throw SupabaseAuthError.callbackRejected
        }
        guard let accessToken = values["access_token"], !accessToken.isEmpty,
              let refreshToken = values["refresh_token"], !refreshToken.isEmpty
        else {
            throw SupabaseAuthError.invalidCallback
        }
        if let tokenType = values["token_type"], tokenType.lowercased() != "bearer" {
            throw SupabaseAuthError.invalidCallback
        }

        let expiresAt: Date
        if let rawExpiresAt = values["expires_at"],
           let seconds = TimeInterval(rawExpiresAt), seconds > 0
        {
            expiresAt = Date(timeIntervalSince1970: seconds)
        } else if let rawExpiresIn = values["expires_in"],
                  let seconds = TimeInterval(rawExpiresIn), seconds > 0
        {
            expiresAt = now().addingTimeInterval(seconds)
        } else {
            throw SupabaseAuthError.invalidCallback
        }

        // Decode `sub` for local cache/UI partitioning only. The backend still
        // authenticates and authorizes the bearer token.
        let subject = try Self.subject(fromJWT: accessToken)
        try storeSession(
            accessToken: accessToken,
            refreshToken: refreshToken,
            expiresAt: expiresAt
        )
        return subject
    }

    func currentSubject() async throws -> String {
        try Self.subject(fromJWT: await validAccessToken())
    }

    /// Persist tokens obtained from the interactive magic-link callback.
    func storeSession(accessToken: String, refreshToken: String, expiresAt: Date) throws {
        try tokens.save(AuthTokens(
            accessToken: accessToken, refreshToken: refreshToken, expiresAt: expiresAt
        ))
    }

    func signOut() throws {
        try tokens.clear()
    }

    private func refresh(current: AuthTokens) async throws -> String {
        var components = URLComponents(
            url: supabaseURL.appending(path: "auth/v1/token"),
            resolvingAgainstBaseURL: false
        )
        components?.queryItems = [URLQueryItem(name: "grant_type", value: "refresh_token")]
        guard let url = components?.url else { throw AuthProviderError.needsSignIn }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue(anonKey, forHTTPHeaderField: "apikey")
        request.setValue("Bearer \(anonKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body = ["refresh_token": current.refreshToken]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw AuthProviderError.needsSignIn
        }
        guard http.statusCode == 200 else {
            // Invalid/expired refresh token: the user must sign in again.
            try? tokens.clear()
            throw AuthProviderError.needsSignIn
        }
        struct RefreshResponse: Decodable {
            let access_token: String
            let refresh_token: String
            let expires_at: Int?
        }
        let decoded: RefreshResponse
        do {
            decoded = try JSONDecoder().decode(RefreshResponse.self, from: data)
        } catch {
            try? tokens.clear()
            throw AuthProviderError.needsSignIn
        }
        let expiresAt = decoded.expires_at.map { Date(timeIntervalSince1970: TimeInterval($0)) }
            ?? now().addingTimeInterval(3600)
        let updated = AuthTokens(
            accessToken: decoded.access_token,
            refreshToken: decoded.refresh_token,
            expiresAt: expiresAt
        )
        try? tokens.save(updated)
        return updated.accessToken
    }

    private static func looksLikeEmail(_ value: String) -> Bool {
        let parts = value.split(separator: "@", omittingEmptySubsequences: false)
        return parts.count == 2 && parts[0].isEmpty == false && parts[1].contains(".")
    }

    private static func subject(fromJWT token: String) throws -> String {
        let segments = token.split(separator: ".", omittingEmptySubsequences: false)
        guard segments.count == 3 else { throw AuthProviderError.needsSignIn }
        var payload = String(segments[1])
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        let remainder = payload.count % 4
        if remainder != 0 {
            payload.append(String(repeating: "=", count: 4 - remainder))
        }
        guard let data = Data(base64Encoded: payload),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let subject = json["sub"] as? String,
              subject.isEmpty == false
        else {
            throw AuthProviderError.needsSignIn
        }
        return subject
    }
}
