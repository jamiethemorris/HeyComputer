import Foundation
import SwiftUI
#if os(watchOS)
import WatchKit
#endif

@MainActor
final class RemoteModel: ObservableObject {
    #if os(watchOS)
    @Published private(set) var pairing: Pairing? = Pairing(address: "iPhone", token: "")
    #else
    @Published private(set) var pairing = PairingStore.load()
    #endif
    @Published private(set) var state: RemoteState?
    @Published private(set) var working = false
    @Published var error: String?
    @Published private(set) var connectionError: String?
    @Published private(set) var readyForCommands = false
    private let client = RemoteClient()
    private var generation = 0
    private var active = false
    private var waitingForMicrophone = false

    var refreshInterval: Duration {
        if connectionError != nil { return .seconds(3) }
        return state?.recording == true || state?.busy == true ? .seconds(1) : .seconds(5)
    }

    func setActive(_ value: Bool) {
        active = value
        if !value {
            // Keep the last display snapshot during Always On. Controls stay
            // disabled until a fresh read arrives after the next wake.
            readyForCommands = false
            connectionError = nil
            generation += 1
        }
    }

    func pair(address: String, code: String) async {
        guard !working else { return }
        working = true
        defer { working = false }
        do {
            struct Response: Decodable { let token: String }
            let response = try await client.request(Response.self, address: address, path: "pair",
                body: ["code": code.trimmingCharacters(in: .whitespacesAndNewlines)])
            let saved = Pairing(address: try RemoteClient.baseURL(address).absoluteString, token: response.token)
            try PairingStore.save(saved)
            generation += 1
            pairing = saved
            state = nil
            readyForCommands = false
            error = nil
            connectionError = nil
            CompanionLink.shared.update()
        } catch { self.error = error.localizedDescription }
    }

    func forget() {
        guard !working else { return }
        do {
            try PairingStore.clear()
            CompanionLink.shared.update()
            generation += 1
            pairing = nil
            state = nil
            readyForCommands = false
            waitingForMicrophone = false
            error = nil
            connectionError = nil
        } catch { self.error = error.localizedDescription }
    }

    private func fetch(_ pairing: Pairing) async throws -> RemoteState {
        try await client.request(RemoteState.self, address: pairing.address, path: "status", token: pairing.token)
    }

    private func update(_ value: RemoteState) {
        guard active else { return }
        connectionError = nil
        state = value
        readyForCommands = true
        if waitingForMicrophone && value.recording && !value.busy {
            waitingForMicrophone = false
            #if os(watchOS)
            WKInterfaceDevice.current().play(.start)
            #endif
        } else if waitingForMicrophone && !value.busy {
            waitingForMicrophone = false
        }
    }

    func refresh() async {
        guard active, !working, let pairing else { return }
        let current = generation
        do {
            let value = try await fetch(pairing)
            guard current == generation, !working, !Task.isCancelled else { return }
            update(value)
        } catch {
            // Lowering your wrist cancels the poll; it is not a lost connection.
            guard current == generation, !working, active, !Task.isCancelled,
                  !(error is CancellationError) else { return }
            state = nil
            readyForCommands = false
            connectionError = error.localizedDescription
        }
    }

    func send(_ action: String) async {
        guard active, readyForCommands, !working, let pairing, state != nil else { return }
        working = true
        generation += 1 // Discard any status poll started before this command.
        error = nil
        let id = UUID().uuidString
        defer { working = false }
        do {
            // Wake the relay with a read and use current state before constructing
            // the short-lived command. Never retry or queue the action itself.
            let state = try await fetch(pairing)
            guard active else { return }
            update(state)
            let allowed = action == "start" ? state.can_start : action == "stop" ? state.can_stop : action == "cancel" && state.can_cancel
            guard allowed else { throw RemoteFailure.message("HeyComputer’s state changed. Check its status and try again.") }
            struct Queued: Decodable { let id: String; let queued: Bool }
            _ = try await client.request(Queued.self, address: pairing.address, path: "command", token: pairing.token,
                body: ["id": id, "action": action, "session": state.session, "revision": state.revision, "expires_at": Date().timeIntervalSince1970 + 5])
            // HTTP acceptance only queues the request. Wait for REAPER to accept
            // it, then let normal polling track microphone/provider completion.
            for _ in 0..<12 {
                try await Task.sleep(for: .milliseconds(250))
                let value = try await fetch(pairing)
                if let confirmation = value.last_command, confirmation.id == id {
                    guard confirmation.accepted else { throw RemoteFailure.message(confirmation.message) }
                    waitingForMicrophone = action == "start"
                    update(value)
                    return
                }
            }
            throw RemoteFailure.message("No confirmation. Check HeyComputer’s status before trying again.")
        } catch {
            self.state = nil
            self.readyForCommands = false
            self.error = error.localizedDescription
            // Never retry a recording action automatically after a lost response.
            #if os(watchOS)
            WKInterfaceDevice.current().play(.failure)
            #endif
        }
    }
}
