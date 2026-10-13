import Foundation

struct PairingCode {
    let address: String
    let code: String

    init(_ text: String) throws {
        guard text.utf8.count < 2048,
              let parts = URLComponents(string: text),
              ["heycomputer", "jev"].contains(parts.scheme ?? ""), parts.host == "pair",
              parts.user == nil, parts.password == nil, parts.port == nil,
              parts.path.isEmpty, parts.fragment == nil,
              let items = parts.queryItems, items.count == 3,
              Set(items.map(\.name)) == Set(["v", "address", "code"]),
              items.first(where: { $0.name == "v" })?.value == "1",
              let address = items.first(where: { $0.name == "address" })?.value,
              let code = items.first(where: { $0.name == "code" })?.value,
              code.count == 6, code.utf8.allSatisfy({ (48...57).contains($0) }) else {
            throw RemoteFailure.message("This isn’t a HeyComputer pairing code. Generate one in HeyComputer Settings on your Mac.")
        }
        self.address = try RemoteClient.baseURL(address).absoluteString
        self.code = code
    }
}
