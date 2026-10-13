import SwiftUI

struct ContentView: View {
    @ObservedObject var remote: RemoteModel
    @ObservedObject private var companion = CompanionLink.shared
    @Environment(\.scenePhase) private var scenePhase
    @State private var showingDetails = false

    private var recording: Bool { remote.state?.recording == true }
    private var details: String {
        var lines = ["Talk uses your Mac’s microphone. Your iPhone relays the Watch controls to HeyComputer on your Mac."]
        lines.append(companion.linkSummary)
        if let message = remote.state?.message, !message.isEmpty { lines.append(message) }
        if let error = remote.connectionError { lines.append(error) }
        if let error = remote.error { lines.append("Last action: " + error) }
        return lines.joined(separator: "\n\n")
    }

    var body: some View {
        GeometryReader { geometry in
            VStack(spacing: 6) {
                if !companion.paired {
                    Image(systemName: "iphone").font(.title2)
                    Text("Set up on iPhone").font(.headline)
                    Text("Open HeyComputer on your iPhone and scan the Mac’s QR code.")
                        .font(.footnote).multilineTextAlignment(.center)
                } else {
                    HStack(spacing: 6) {
                        Image(systemName: recording ? "waveform" : "mic.fill")
                            .foregroundStyle(recording ? .red : .cyan)
                        Text(remote.state?.status ?? (remote.connectionError == nil ? "Connecting…" : "Reconnecting…"))
                            .font(.headline).lineLimit(2).minimumScaleFactor(0.8)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Button { showingDetails = true } label: {
                            Image(systemName: remote.error == nil && remote.connectionError == nil ? "info.circle" : "exclamationmark.circle")
                                .foregroundStyle(remote.error == nil && remote.connectionError == nil ? Color.secondary : Color.orange)
                                .frame(width: 32, height: 36)
                        }
                        .buttonStyle(.plain).accessibilityLabel("Connection and last action details")
                    }
                    Spacer(minLength: 0)
                    HStack(spacing: 6) {
                        Button {
                            Task { await remote.send(recording ? "stop" : "start") }
                        } label: {
                            HStack(spacing: 6) {
                                if remote.working { ProgressView().controlSize(.mini) }
                                Text(recording ? "Stop & send" : "Talk")
                                    .font(.headline).lineLimit(1).minimumScaleFactor(0.75)
                            }
                            .frame(maxWidth: .infinity)
                            .frame(height: min(64, max(44, geometry.size.height * 0.36)))
                            .background((recording ? Color.red : Color.cyan).opacity(0.25), in: Capsule())
                        }
                        .buttonStyle(.plain)
                        // The transport checks live reachability when sending,
                        // rather than relying on a potentially stale UI snapshot.
                        .disabled(scenePhase != .active || !remote.readyForCommands || remote.working || !(recording ? remote.state?.can_stop == true : remote.state?.can_start == true))
                        if recording {
                            Button { Task { await remote.send("cancel") } } label: {
                                Image(systemName: "xmark").frame(width: 44, height: 44)
                                    .background(Color.secondary.opacity(0.2), in: Circle())
                            }
                            .buttonStyle(.plain).accessibilityLabel("Cancel recording")
                            .disabled(scenePhase != .active || !remote.readyForCommands || remote.working || remote.state?.can_cancel != true)
                        }
                    }
                    Spacer(minLength: 0)
                    Text(scenePhase != .active ? "Raise wrist to refresh" : !remote.readyForCommands ? "Updating…" : remote.error == nil ? "Mac microphone" : "Check last action ⓘ")
                        .font(.caption2).foregroundStyle(remote.error == nil ? Color.secondary : Color.orange)
                        .lineLimit(1).minimumScaleFactor(0.8)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .padding(.horizontal, 2)
        }
        .alert("HeyComputer", isPresented: $showingDetails) {
            Button("Done") { remote.error = nil }
        } message: { Text(details) }
        .task(id: scenePhase) {
            remote.setActive(scenePhase == .active)
            companion.update()
            guard scenePhase == .active else { return }
            while !Task.isCancelled {
                await remote.refresh()
                do { try await Task.sleep(for: remote.refreshInterval) } catch { return }
            }
        }
    }
}
