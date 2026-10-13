import Foundation

var checks = 0
func rejects(_ operation: () throws -> Void) {
    do { try operation(); fatalError("Expected rejection") } catch { checks += 1 }
}
for address in ["http://192.168.1.4:8765", "http://10.0.0.2:8766", "http://studio-mac.local:8766"] {
    _ = try RemoteClient.baseURL(address); checks += 1
}
for address in ["http://example.com:8765", "http://127.0.0.1:8765", "http://192.168.1.4:80",
                "http://user:pass@studio.local:8765", "http://192.168.1.4:8765/other",
                "http://192.168.1.4:8765?redirect=evil", "http://2130706433:8765",
                "file:///tmp/a", "http://192.168.001.4:8765", "http://studio.local.evil.com:8765"] {
    rejects { _ = try RemoteClient.baseURL(address) }
}
let good = "heycomputer://pair?v=1&address=http%3A%2F%2F192.168.1.4%3A8765&code=012345"
let pair = try PairingCode(good)
precondition(pair.code == "012345" && pair.address == "http://192.168.1.4:8765"); checks += 1
let legacyPair = try PairingCode(good.replacingOccurrences(of: "heycomputer://", with: "jev://"))
precondition(legacyPair.code == pair.code && legacyPair.address == pair.address); checks += 1
rejects { _ = try PairingCode(good.replacingOccurrences(of: "heycomputer://", with: "other://")) }
for bad in [good + "&code=234567", good.replacingOccurrences(of: "v=1", with: "v=2"),
            good.replacingOccurrences(of: "012345", with: "abcdef"), good + "#fragment",
            good.replacingOccurrences(of: "192.168.1.4", with: "example.com"), "https://example.com/"] {
    rejects { _ = try PairingCode(bad) }
}
let body: [String: Any] = ["id": UUID().uuidString, "action": "start", "session": "test", "revision": 1, "expires_at": 104.0]
let message: [String: Any] = ["sent": 100.0, "path": "command", "body": body]
_ = try RelayRequest(message, now: 101); checks += 1
rejects { _ = try RelayRequest(message, now: 106) }
rejects { _ = try RelayRequest(message, now: 90) }
for action in ["pair", "save_settings", "execute"] {
    var modified = body; modified["action"] = action
    rejects { _ = try RelayRequest(["sent": 100.0, "path": "command", "body": modified], now: 101) }
}
rejects { _ = try RelayRequest(["sent": 100.0, "path": "pair"], now: 101) }
rejects { _ = try RelayRequest(["sent": 100.0, "path": "status", "body": body], now: 101) }
_ = try RelayRequest(["sent": 100.0, "path": "status"], now: 101); checks += 1
// Reads tolerate a background wake; commands retain the five-second deadline.
_ = try RelayRequest(["sent": 100.0, "path": "status"], now: 110); checks += 1
rejects { _ = try RelayRequest(["sent": 100.0, "path": "status"], now: 115) }
rejects { _ = try RelayRequest(["sent": 100.0, "path": "status"], now: 90) }
print("Passed \(checks) pairing URL and relay validation checks")
