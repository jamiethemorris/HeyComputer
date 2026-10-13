# HeyComputer iPhone and Watch remote

The iPhone scans the Mac’s pairing QR code, saves its credential in Keychain, and
connects to the Mac over the local network. The bundled Watch companion sends
live status and recording requests through the iPhone using WatchConnectivity.
No Mac credential is transferred to the Watch. Neither app records audio yet;
Talk uses the microphone already selected in HeyComputer on the Mac.

## Build and install

1. Open `HeyComputerWatch.xcodeproj` and select **HeyComputerPhone** as the scheme.
2. In **Signing & Capabilities**, select your Apple development team for both
   targets, then select your connected iPhone as the destination. No personal
   signing team or certificate is included in the repository. The existing bundle
   IDs are `com.reajev.remote` (phone) and `com.reajev.remote.watch` (Watch); keep
   them when updating an existing installation to preserve pairings. For a new
   installation, use unique IDs under your own team if needed, and update the
   Watch's `WKCompanionAppBundleIdentifier` to match the phone ID. Both apps are
   displayed as **HeyComputer**.
3. Run to install the iPhone app and its embedded Watch app. If automatic Watch
   installation is disabled, open Apple’s Watch app on the phone and install HeyComputer
   from Available Apps. The **HeyComputerWatch** scheme also builds/runs the Watch target
   after the phone app is installed.
4. Open HeyComputer directly on each device when testing normal use. An Xcode
   debugger launch can take longer than a normal launch.

The app targets iOS 17+ and watchOS 10+. Source builds work with Xcode 16.2’s
installed iOS 18.2/watchOS 11.2 SDKs. Physical installation requires trusted,
paired devices, Developer Mode, and valid development signing. TestFlight is not
required for local development.

Unsigned build of both apps:

```sh
xcodebuild -project watch/HeyComputerWatch.xcodeproj -scheme HeyComputerPhone \
  -configuration Debug -destination 'generic/platform=iOS' \
  -derivedDataPath /tmp/heycomputer-phone-build CODE_SIGNING_ALLOWED=NO build
```

## Pair and use

1. Install the Python dependencies (`.venv/bin/python -m pip install -e .`), then
   close and reopen the HeyComputer panel in REAPER to load the updated helper and UI.
2. In **Settings → iPhone & Watch remote**, enable remote control and save.
   The default port is 8765. If another application uses this port, choose a free
   port such as 8766 and save. Allow incoming connections for the helper if asked.
3. Choose **Pair iPhone**. Scan the displayed QR using **Scan Mac QR code** in the
   phone app. Allow Camera and Local Network access when prompted. Confirm the
   displayed Mac address. Manual address/code entry remains available.
4. Open HeyComputer on the Watch. Pairing state transfers automatically; if it still says
   “Set up on iPhone,” open the phone app and keep both devices nearby.
5. Tap **Talk**, wait for the Watch’s start haptic, then speak. **Stop & send**
   submits through HeyComputer’s normal workflow. The **×** button beside it cancels the
   recording. The Watch controls fit on one screen; tap the info button for the
   Mac’s reply, connection details, or a previous command error.

The QR includes a version, private LAN address (or `.local` fallback), and a
six-digit code. It is generated locally and drawn in ReaImGui with a white quiet
zone. It expires in five minutes, allows one pairing, and closes after five
incorrect attempts. Generating a new QR invalidates the previous code. Pairing a
new phone revokes the old credential. **Forget devices** on the Mac revokes remote
access; **Forget Mac** on the phone deletes only the local credential and updates
the Watch’s setup state.

The iPhone must be reachable by the Watch and able to reach the Mac. Live Watch
messages can wake the phone app in the background; this is not an always-running
phone service, and the phone app does not need to stay on screen. Each phone relay
request holds a bounded background execution task until it replies; expiration
cancels the HTTP work and returns one error. If the phone is unavailable, open HeyComputer
on the phone once and try again.
Controls are never placed in a background transfer queue. The Watch polls only
while its app is active: every five seconds while idle, every second during
recording/processing, and every three seconds after a failure. A button press
fetches fresh status before sending its command. Lowering your wrist does not cancel a Mac recording.
HeyComputer’s existing 90-second recording limit remains in effect.

On wake, the Watch waits up to two seconds for both session activation and live
reachability before sending. A status read that races with a reachability change
may retry once; microphone commands never retry. While inactive, the display
retains its last snapshot with “Raise wrist to refresh.” Controls remain disabled
until fresh status arrives after wake.

## Troubleshooting

- A timeout indicates network reachability, not necessarily an incorrect code.
  Keep the Mac awake, HeyComputer’s panel open, and the phone on the Mac’s local Wi-Fi.
  Check Local Network access in iPhone Settings and avoid guest-network isolation.
- QR pairing prefers a numeric private IPv4 address to avoid `.local` lookup
  problems. If the Mac changes networks or its IP changes, generate a new QR.
  Macs with multiple network adapters may need the reachable address entered
  manually. The helper tries physical interfaces en0, en1, then en2.
- Open `http://MAC-ADDRESS:PORT/v1/status` in iPhone Safari as a reachability test.
  An immediate “Pair your iPhone with HeyComputer again” response is expected without
  credentials. The app itself performs the authenticated pairing.
- If a request loses its reply, check the Mac’s recording state before retrying.
  A lost acknowledgment does not guarantee that the command was not executed.
- Current connection warnings clear when communication recovers. A **Last action**
  error describes a previous command and remains until dismissed; it does not
  mean the phone is still disconnected. Canceling a status poll when the app
  becomes inactive is not reported as a connection failure.
- Error **7007** comes from WatchConnectivity before the Mac request is sent.
  The phone’s **Apple Watch → Connection details** and the Watch’s info button
  show activation, companion installation, and live reachability. Each app saves
  its last 60 connection events to `Documents/heycomputer-watch-link.json` in its own
  container. This local diagnostic log contains flags, times, and error codes;
  it excludes credentials, request bodies, responses, and conversation content.

## Transport and protocol

This remains a **trusted-LAN HTTP prototype**. A QR code simplifies setup; it
does not encrypt the connection. Codes, credentials, and responses can be observed
on that network. Do not expose the port publicly. The phone permits only `.local`
names and RFC1918/link-local IPv4 addresses on ports 1024–65535, rejects redirects,
and includes local-network ATS exceptions scoped to private IPv4 ranges.

- `POST /v1/pair`, body `{"code":"123456"}`, returns a 64-character token.
- `GET /v1/status` requires a bearer token and returns the current session,
  revision, recording/busy state, enabled actions, reply, and last-command receipt.
- `POST /v1/command` requires the token plus `id`, `action` (start/stop/cancel),
  `session`, and `revision`. New clients also send `expires_at` (Unix seconds).
  HTTP 202 means queued; the UI waits for HeyComputer’s acceptance and recording state.
- Commands expire after five seconds in the mobile clients. The phone rejects
  delayed Watch messages and the Mac rejects expired commands before queuing.
  Read-only status requests allow up to 15 seconds for phone wake-up; the Watch
  still stops waiting for replies after 12 seconds.
  REAPER’s own queue still expires after two seconds. Duplicate IDs never replay
  previously accepted actions within the server’s bounded recent-request cache.
- The phone relay accepts only status and recording commands, limits concurrent
  requests, and has bounded response waits. No pairing token, provider API key,
  arbitrary URL, or arbitrary worker job crosses the Watch message bridge.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -v
lua tests/test_remote.lua
luac -p reaper/HeyComputer.lua
xcrun swiftc -module-cache-path /tmp/heycomputer-swift-cache \
  watch/HeyComputerWatch/RemoteClient.swift watch/Shared/PairingCode.swift \
  watch/Shared/RelayRequest.swift tests/swift/main.swift -o /tmp/heycomputer-remote-tests
/tmp/heycomputer-remote-tests
xcrun swiftc -parse-as-library -module-cache-path /tmp/heycomputer-swift-cache \
  watch/HeyComputerWatch/RemoteClient.swift watch/Shared/RelayRequest.swift \
  tests/swift/relay_lifecycle.swift -o /tmp/heycomputer-relay-lifecycle-tests
/tmp/heycomputer-relay-lifecycle-tests
```

Hardware acceptance: scan and pair, receive the Watch’s setup state, Talk and
wait for the actual recording haptic, Stop & send, Cancel, phone locked/background
relay, leaving/rejoining Wi-Fi, and revoked/expired pairing. Automated tests use
fake recording state and do not start a microphone or call a model.
