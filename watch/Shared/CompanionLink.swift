import Foundation
import WatchConnectivity
#if os(iOS)
import UIKit
#else
import WatchKit
#endif

// Only live messages are used for controls. Never queue a microphone action for
// later delivery, and never transfer the Mac credential to the Watch.
@MainActor
final class CompanionLink: NSObject, ObservableObject, WCSessionDelegate {
    static let shared = CompanionLink()
    @Published private(set) var paired = false
    @Published private(set) var reachable = false
    @Published private(set) var watchInstalled = false
    @Published private(set) var linkSummary = "Starting Watch connection…"
    private var connectionEvents: [[String: Any]] = []
    private var pending: [UUID: CheckedContinuation<Data, Error>] = [:]
    private var expiryTasks: [UUID: Task<Void, Never>] = [:]
    private var activeRequests = 0
    private let client = RemoteClient()

    private override init() {
        super.init()
        guard WCSession.isSupported() else { return }
        let session = WCSession.default
        session.delegate = self
        session.activate()
        record("activation-requested")
    }

    func update() {
        guard WCSession.isSupported() else { return }
        let session = WCSession.default
        reachable = session.isReachable
        #if os(iOS)
        paired = PairingStore.load() != nil
        watchInstalled = session.isWatchAppInstalled
        if session.activationState == .activated,
           session.applicationContext["paired"] as? Bool != paired {
            try? session.updateApplicationContext(["paired": paired])
        }
        #else
        paired = session.receivedApplicationContext["paired"] as? Bool ?? false
        #endif
        record("session-updated")
    }

    // Local, bounded diagnostics: flags and error codes only, never credentials,
    // request bodies, Mac responses, or conversation content.
    private func record(_ event: String, error: Error? = nil) {
        let session = WCSession.default
        let activation: String
        switch session.activationState {
        case .activated: activation = "active"
        case .inactive: activation = "inactive"
        default: activation = "not activated"
        }
        var entry: [String: Any] = ["time": Date().timeIntervalSince1970, "event": event,
            "activation": activation, "reachable": session.isReachable,
            "delegateAttached": session.delegate === self]
        #if os(iOS)
        entry["watchPaired"] = session.isPaired
        entry["watchAppInstalled"] = session.isWatchAppInstalled
        entry["phoneAppState"] = UIApplication.shared.applicationState.rawValue
        linkSummary = "Session: \(activation)\nWatch paired: \(session.isPaired ? "yes" : "no")\nWatch app installed: \(session.isWatchAppInstalled ? "yes" : "no")\nLive link: \(session.isReachable ? "available" : "unavailable")"
        #else
        entry["phoneAppInstalled"] = session.isCompanionAppInstalled
        entry["watchAppState"] = WKApplication.shared().applicationState.rawValue
        linkSummary = "Session: \(activation)\nPhone app installed: \(session.isCompanionAppInstalled ? "yes" : "no")\nLive link: \(session.isReachable ? "available" : "unavailable")"
        #endif
        if let error {
            entry["errorDomain"] = (error as NSError).domain
            entry["errorCode"] = (error as NSError).code
        }
        connectionEvents.append(entry)
        connectionEvents = Array(connectionEvents.suffix(60))
        if let directory = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first,
           let data = try? JSONSerialization.data(withJSONObject: connectionEvents, options: [.prettyPrinted, .sortedKeys]) {
            try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try? data.write(to: directory.appendingPathComponent("heycomputer-watch-link.json"), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        }
    }

    func request<T: Decodable>(_ type: T.Type, path: String, body: [String: Any]?) async throws -> T {
        guard WCSession.isSupported() else { throw RemoteFailure.message("Watch Connectivity is unavailable.") }
        for attempt in 0..<2 {
            try await WatchLinkReadiness.wait(ready: {
                WCSession.default.activationState == .activated && WCSession.default.isReachable
            })
            updateReachability()
            do {
                let data = try await send(path: path, body: body)
                return try JSONDecoder().decode(type, from: data)
            } catch let error as NSError where error.domain == WCErrorDomain {
                // A link can still change between the check and send. Only the
                // harmless status read may retry; recording actions never do.
                if path == "status", error.code == WCError.Code.notReachable.rawValue, attempt == 0 { continue }
                throw RemoteFailure.message("Watch–iPhone link: \(error.localizedDescription) (\(error.code)). Keep the phone nearby. If this persists, open HeyComputer on the phone once.")
            }
        }
        throw RemoteFailure.message("The iPhone link is not ready yet.")
    }

    private func send(path: String, body: [String: Any]?) async throws -> Data {
        try Task.checkCancellation()
        record("sending-" + path)
        let id = UUID()
        let data: Data = try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                pending[id] = continuation
                expiryTasks[id] = Task { [weak self] in
                    do { try await Task.sleep(for: .seconds(12)) } catch { return }
                    self?.finish(id, .failure(RemoteFailure.message("The iPhone didn’t respond. Check HeyComputer on your phone before trying again.")))
                }
                var message: [String: Any] = ["path": path, "sent": Date().timeIntervalSince1970]
                if let body { message["body"] = body }
                WCSession.default.sendMessage(message, replyHandler: { reply in
                    Task { @MainActor in
                        self.updateReachability()
                        self.record("reply-" + path)
                        if let error = reply["error"] as? String {
                            self.finish(id, .failure(RemoteFailure.message(error)))
                        } else if let data = reply["data"] as? Data {
                            self.finish(id, .success(data))
                        } else {
                            self.finish(id, .failure(RemoteFailure.message("Unexpected response from iPhone.")))
                        }
                    }
                }, errorHandler: { error in
                    Task { @MainActor in
                        self.updateReachability()
                        self.record("send-failed-" + path, error: error)
                        self.finish(id, .failure(error))
                    }
                })
            }
        } onCancel: {
            Task { @MainActor in self.finish(id, .failure(CancellationError())) }
        }
        return data
    }

    private func updateReachability() {
        reachable = WCSession.isSupported() && WCSession.default.isReachable
    }

    private func finish(_ id: UUID, _ result: Result<Data, Error>) {
        expiryTasks.removeValue(forKey: id)?.cancel()
        pending.removeValue(forKey: id)?.resume(with: result)
    }

    nonisolated func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        Task { @MainActor in self.update(); self.record("activation-completed", error: error) }
    }
    nonisolated func sessionReachabilityDidChange(_ session: WCSession) {
        Task { @MainActor in self.update(); self.record("reachability-changed") }
    }
    nonisolated func session(_ session: WCSession, didReceiveApplicationContext applicationContext: [String: Any]) {
        Task { @MainActor in self.update() }
    }

    #if os(iOS)
    nonisolated func sessionDidBecomeInactive(_ session: WCSession) {
        Task { @MainActor in self.update(); self.record("session-became-inactive") }
    }
    nonisolated func sessionDidDeactivate(_ session: WCSession) { session.activate() }
    nonisolated func sessionWatchStateDidChange(_ session: WCSession) {
        Task { @MainActor in self.update() }
    }
    nonisolated func session(_ session: WCSession, didReceiveMessage message: [String: Any],
                             replyHandler: @escaping ([String: Any]) -> Void) {
        Task { @MainActor in
            self.record("message-received")
            do {
                let request = try RelayRequest(message)
                guard self.activeRequests < 4 else {
                    throw RemoteFailure.message("iPhone is busy. Refresh and try again.")
                }
                guard let pairing = PairingStore.load() else {
                    throw RemoteFailure.message("Scan the Mac’s QR code in HeyComputer on your iPhone first.")
                }
                self.activeRequests += 1
                let work = RelayWork()
                work.start(beginBackground: { expire in
                    let id = UIApplication.shared.beginBackgroundTask(withName: "HeyComputer Watch relay") {
                        Task { @MainActor in expire() }
                    }
                    if id == .invalid { expire() }
                    return { if id != .invalid { UIApplication.shared.endBackgroundTask(id) } }
                }, operation: {
                    try await self.client.requestData(address: pairing.address, path: request.path,
                                                      token: pairing.token, body: request.body)
                }, reply: { response in
                    self.activeRequests -= 1
                    self.record(response["error"] == nil ? "relay-replied" : "relay-failed")
                    replyHandler(response)
                })
            } catch { replyHandler(["error": error.localizedDescription]) }
        }
    }
    #endif
}
