import SwiftUI

@main
struct HeyComputerWatchApp: App {
    @StateObject private var remote = RemoteModel()

    var body: some Scene {
        WindowGroup {
            ContentView(remote: remote)
        }
        .backgroundTask(.watchConnectivity) {
            await CompanionLink.shared.update()
        }
    }
}
