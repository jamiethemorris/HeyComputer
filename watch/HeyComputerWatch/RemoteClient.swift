import Foundation
import Security

struct RemoteState: Codable {
    struct Confirmation: Codable {
        let id: String
        let accepted: Bool
        let message: String
    }
    let session: String
    let revision: Int
    let recording: Bool
    let busy: Bool
    let status: String
    let message: String
    let can_start: Bool
    let can_stop: Bool
    let can_cancel: Bool
    let last_command: Confirmation?
}

struct Pairing: Codable {
    let address: String
    let token: String
}

enum RemoteFailure: LocalizedError {
    case message(String)
    var errorDescription: String? {
        switch self { case .message(let text): return text }
    }
}

enum PairingStore {
    // Preserve existing pairings when the renamed app updates in place.
    private static let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "ReaJevRemote",
        kSecAttrAccount as String: "paired-mac"
    ]

    static func load() -> Pairing? {
        var request = query
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(request as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return try? JSONDecoder().decode(Pairing.self, from: data)
    }

    static func save(_ pairing: Pairing) throws {
        let data = try JSONEncoder().encode(pairing)
        let attributes: [String: Any] = [kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        var result = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if result == errSecItemNotFound {
            result = SecItemAdd(query.merging(attributes) { _, new in new } as CFDictionary, nil)
        }
        guard result == errSecSuccess else {
            throw RemoteFailure.message("Could not save pairing on this device.")
        }
    }

    static func clear() throws {
        let result = SecItemDelete(query as CFDictionary)
        guard result == errSecSuccess || result == errSecItemNotFound else {
            throw RemoteFailure.message("Could not remove pairing on this device.")
        }
    }
}

// A local HTTP prototype. Redirects are rejected so a Mac cannot redirect the
// pairing code or bearer token to another host. No cloud/provider keys live here.
final class RemoteClient: NSObject, URLSessionTaskDelegate {
    private struct ErrorBody: Decodable { let message: String }
    private lazy var session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 8
        config.timeoutIntervalForResource = 10
        config.urlCache = nil
        config.httpCookieStorage = nil
        config.waitsForConnectivity = true
        return URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }()

    static func isLocalHost(_ host: String) -> Bool {
        if host.hasSuffix(".local"), host.count > 6 {
            return host.split(separator: ".", omittingEmptySubsequences: false).allSatisfy {
                !$0.isEmpty && $0.utf8.allSatisfy { (97...122).contains($0) || (48...57).contains($0) || $0 == 45 }
            }
        }
        let parts = host.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else { return false }
        let numbers = parts.compactMap { UInt8($0) }
        guard numbers.count == 4,
              zip(parts, numbers).allSatisfy({ String($0.1) == $0.0 }) else { return false }
        return numbers[0] == 10 || (numbers[0] == 172 && (16...31).contains(numbers[1])) ||
            (numbers[0] == 192 && numbers[1] == 168) || (numbers[0] == 169 && numbers[1] == 254)
    }

    static func baseURL(_ address: String) throws -> URL {
        let text = address.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let parts = URLComponents(string: text), parts.scheme == "http",
              let host = parts.host?.lowercased(), isLocalHost(host), parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/",
              let port = parts.port, (1024...65535).contains(port),
              let url = parts.url else {
            throw RemoteFailure.message("Enter the Mac address from HeyComputer Settings, such as http://Studio-Mac.local:8765.")
        }
        return url
    }

    func request<T: Decodable>(_ type: T.Type, address: String, path: String,
                               token: String? = nil, body: [String: Any]? = nil) async throws -> T {
        #if os(watchOS)
        return try await CompanionLink.shared.request(type, path: path, body: body)
        #else
        let data = try await requestData(address: address, path: path, token: token, body: body)
        return try JSONDecoder().decode(type, from: data)
        #endif
    }

    @MainActor
    func requestData(address: String, path: String, token: String? = nil,
                     body: [String: Any]? = nil) async throws -> Data {
        guard ["pair", "status", "command"].contains(path) else {
            throw RemoteFailure.message("Unsupported remote request.")
        }
        let base = try Self.baseURL(address)
        var request = URLRequest(url: base.appendingPathComponent("v1/" + path))
        request.cachePolicy = .reloadIgnoringLocalCacheData
        if let token { request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization") }
        if let body {
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let data: Data
        let response: URLResponse
        do { (data, response) = try await session.data(for: request) }
        catch let error as URLError {
            if error.code == .timedOut || error.code == .cannotConnectToHost || error.code == .cannotFindHost || error.code == .notConnectedToInternet {
                throw RemoteFailure.message("Cannot reach HeyComputer on your Mac. Check that HeyComputer is open, both devices use the same Wi-Fi, and Local Network access is enabled for HeyComputer in iPhone Settings.")
            }
            throw error
        }
        guard let response = response as? HTTPURLResponse else {
            throw RemoteFailure.message("Unexpected response from the Mac.")
        }
        guard (200...299).contains(response.statusCode) else {
            let error = try? JSONDecoder().decode(ErrorBody.self, from: data)
            throw RemoteFailure.message(error?.message ?? "The Mac rejected the request.")
        }
        return data
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
