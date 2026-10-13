import SwiftUI
import UIKit

final class PhoneDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        // Activate even when WatchConnectivity launches us in the background.
        _ = CompanionLink.shared
        return true
    }
}

@main
struct HeyComputerPhoneApp: App {
    @UIApplicationDelegateAdaptor(PhoneDelegate.self) private var delegate
    @StateObject private var remote = RemoteModel()
    var body: some Scene {
        WindowGroup { PhoneView(remote: remote) }
    }
}
