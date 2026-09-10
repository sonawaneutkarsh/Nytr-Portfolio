import Foundation

// The only auth abstraction other components depend on. Coordinator and
// BackendClient contain zero Supabase/token-refresh knowledge.

enum AuthProviderError: Error {
    /// No valid session can be established; user must sign in.
    case needsSignIn
}

protocol AccessTokenProvider: Sendable {
    /// Returns a currently-valid access token, refreshing first when needed.
    func validAccessToken() async throws -> String
}

struct AuthTokens: Codable, Equatable, Sendable {
    let accessToken: String
    let refreshToken: String
    let expiresAt: Date

    init(accessToken: String, refreshToken: String, expiresAt: Date) {
        self.accessToken = accessToken
        self.refreshToken = refreshToken
        self.expiresAt = expiresAt
    }
}

protocol TokenStore: Sendable {
    func save(_ tokens: AuthTokens) throws
    func load() -> AuthTokens?
    func clear() throws
}

/// Keychain-backed token storage. Tokens are the ONLY secrets in this app;
/// everything else lives in UserDefaults/xcconfig.
struct KeychainTokenStore: TokenStore {
    private let service: String

    init(service: String = "com.nutritionagent.healthcompanion.tokens") {
        self.service = service
    }

    private struct Payload: Codable {
        let tokens: AuthTokens
    }

    func save(_ tokens: AuthTokens) throws {
        let data = try JSONEncoder().encode(Payload(tokens: tokens))
        // Replace-then-insert keeps this idempotent.
        let deleteQuery: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
        ]
        SecItemDelete(deleteQuery as CFDictionary)
        var addQuery = deleteQuery
        addQuery[kSecValueData as String] = data
        addQuery[kSecAttrAccessible as String] =
            kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(addQuery as CFDictionary, nil)
        guard status == errSecSuccess else { throw KeychainError.unhandled(status) }
    }

    func load() -> AuthTokens? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data else { return nil }
        return try? JSONDecoder().decode(Payload.self, from: data).tokens
    }

    func clear() throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
        ]
        SecItemDelete(query as CFDictionary)
    }
}

enum KeychainError: Error {
    case unhandled(OSStatus)
}
