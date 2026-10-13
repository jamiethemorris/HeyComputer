import Foundation

@MainActor
enum WatchLinkReadiness {
    // Session activation and foreground reachability arrive separately on wake.
    // This waits only in memory while the app is active; cancellation must stop
    // it before any network operation can be dispatched.
    static func wait(attempts: Int = 40, ready: () -> Bool,
                     pause: () async throws -> Void = { try await Task.sleep(for: .milliseconds(50)) }) async throws {
        for _ in 0..<attempts {
            try Task.checkCancellation()
            if ready() { return }
            try await pause()
        }
        try Task.checkCancellation()
        guard ready() else {
            throw RemoteFailure.message("Waiting for the iPhone link. Keep the Watch awake for a moment; HeyComputer will try again.")
        }
    }
}

struct RelayRequest {
    let path: String
    let body: [String: Any]?

    init(_ message: [String: Any], now: TimeInterval = Date().timeIntervalSince1970) throws {
        guard let sent = message["sent"] as? Double, sent.isFinite,
              let path = message["path"] as? String, path == "status" || path == "command",
              sent - now < 5, now - sent < (path == "status" ? 15 : 5) else {
            throw RemoteFailure.message("Expired or unavailable request. Refresh and try again.")
        }
        self.path = path
        if path == "command" {
            guard var body = message["body"] as? [String: Any],
                  Set(body.keys) == Set(["id", "action", "session", "revision", "expires_at"]),
                  let action = body["action"] as? String, ["start", "stop", "cancel"].contains(action),
                  let id = body["id"] as? String, UUID(uuidString: id) != nil,
                  let session = body["session"] as? String, !session.isEmpty, session.count <= 100,
                  body["revision"] is Int,
                  let expires = body["expires_at"] as? Double, expires.isFinite,
                  expires > now, expires <= sent + 6 else {
                throw RemoteFailure.message("Expired or invalid Watch command. Refresh and try again.")
            }
            body["expires_at"] = min(expires, sent + 5)
            self.body = body
        } else {
            guard message["body"] == nil else { throw RemoteFailure.message("Invalid status request.") }
            self.body = nil
        }
    }
}

// Own the asynchronous HTTP operation until its reply is delivered or iOS
// expires its background time. Injecting the lease keeps this lifecycle testable.
@MainActor
final class RelayWork {
    private var task: Task<Void, Never>?
    private var endBackground: (() -> Void)?
    private var reply: (([String: Any]) -> Void)?

    func start(beginBackground: (@escaping @MainActor @Sendable () -> Void) -> () -> Void,
               operation: @escaping () async throws -> Data,
               reply: @escaping ([String: Any]) -> Void) {
        precondition(self.reply == nil && task == nil)
        self.reply = reply
        endBackground = beginBackground { [weak self] in self?.expire() }
        // The OS may refuse background execution immediately.
        guard self.reply != nil else {
            endBackground?()
            endBackground = nil
            return
        }
        task = Task {
            defer { task = nil }
            do {
                try Task.checkCancellation()
                let data = try await operation()
                finish(["data": data])
            } catch {
                finish(["error": error.localizedDescription])
            }
        }
    }

    private func expire() {
        task?.cancel()
        finish(["error": "iPhone background time expired. Try again from your Watch; if it persists, open HeyComputer on your phone once."])
    }

    private func finish(_ response: [String: Any]) {
        guard let reply else { return }
        self.reply = nil
        reply(response)
        endBackground?()
        endBackground = nil
    }
}
