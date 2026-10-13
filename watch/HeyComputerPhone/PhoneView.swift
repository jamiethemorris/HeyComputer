import SwiftUI

struct PhoneView: View {
    @ObservedObject var remote: RemoteModel
    @ObservedObject private var companion = CompanionLink.shared
    @Environment(\.scenePhase) private var scenePhase
    @State private var scanning = false
    @State private var scanned: PairingCode?
    @State private var confirmPairing = false
    @State private var scanResult: Result<String, Error>?
    @State private var address = "http://"
    @State private var code = ""

    var body: some View {
        NavigationStack {
            Form {
                if remote.pairing == nil {
                    Section {
                        Label("Connect to your Mac", systemImage: "desktopcomputer")
                            .font(.title2.bold())
                        Text("In HeyComputer’s settings on your Mac, enable remote control, save, then choose Pair iPhone.")
                        Button { scanning = true } label: {
                            Label("Scan Mac QR code", systemImage: "qrcode.viewfinder")
                        }.disabled(remote.working)
                    }
                    Section("Or enter the details") {
                        TextField("Mac address", text: $address)
                            .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                        TextField("6-digit pairing code", text: $code).keyboardType(.numberPad)
                        Button("Connect") { Task { await remote.pair(address: address, code: code) } }
                            .disabled(remote.working || code.count != 6)
                    }
                } else {
                    Section {
                        Label(remote.state?.status ?? "Connecting to Mac…",
                              systemImage: remote.state?.recording == true ? "waveform" : "mic.fill")
                            .font(.title2.bold())
                        if let state = remote.state {
                            Button(state.recording ? "Stop & send" : "Talk") {
                                Task { await remote.send(state.recording ? "stop" : "start") }
                            }
                            .disabled(!remote.readyForCommands || remote.working || !(state.recording ? state.can_stop : state.can_start))
                            if state.recording {
                                Button("Cancel recording", role: .cancel) { Task { await remote.send("cancel") } }
                                    .disabled(!remote.readyForCommands || remote.working || !state.can_cancel)
                            }
                            if !state.message.isEmpty { Text(state.message) }
                        }
                        Text("Using the Mac’s microphone").foregroundStyle(.secondary)
                    }
                    Section("Apple Watch") {
                        Text(companion.watchInstalled ? "Open HeyComputer on your Watch to use Talk and Stop." :
                                "Install HeyComputer from the Watch app on your iPhone, then open it on your Watch.")
                        Text(companion.reachable ? "Watch connected" : "Watch will connect when you open HeyComputer.")
                            .foregroundStyle(.secondary)
                        DisclosureGroup("Connection details") {
                            Text(companion.linkSummary).font(.footnote)
                        }
                    }
                    Section("Connection") {
                        Text(remote.pairing?.address ?? "").textSelection(.enabled)
                        Button("Forget Mac", role: .destructive) { remote.forget() }.disabled(remote.working)
                    }
                }
                if remote.working { Section { ProgressView("Connecting…") } }
                if let error = remote.connectionError { Section { Text(error).foregroundStyle(.orange) } }
                if let error = remote.error {
                    Section("Last action") { Text(error).foregroundStyle(.orange); Button("Dismiss") { remote.error = nil } }
                }
                Section { Text("Keep your iPhone and Mac on trusted Wi-Fi. This prototype uses an unencrypted local connection.")
                    .font(.footnote).foregroundStyle(.secondary) }
            }
            .navigationTitle("HeyComputer")
            .sheet(isPresented: $scanning, onDismiss: {
                guard let result = scanResult else { return }
                scanResult = nil
                switch result {
                case .success(let text):
                    do { scanned = try PairingCode(text); confirmPairing = true }
                    catch { remote.error = error.localizedDescription }
                case .failure(let error): remote.error = error.localizedDescription
                }
            }) {
                QRScanner { result in scanResult = result; scanning = false }
            }
            .alert("Connect to this Mac?", isPresented: $confirmPairing) {
                Button("Connect") {
                    if let value = scanned {
                        Task { await remote.pair(address: value.address, code: value.code) }
                    }
                    scanned = nil
                }
                Button("Cancel", role: .cancel) { scanned = nil }
            } message: { Text(scanned?.address ?? "") }
        }
        .task(id: scenePhase) {
            remote.setActive(scenePhase == .active)
            companion.update()
            guard scenePhase == .active else { return }
            while !Task.isCancelled {
                await remote.refresh()
                do { try await Task.sleep(for: .seconds(1)) } catch { return }
            }
        }
    }
}
