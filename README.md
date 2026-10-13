# HeyComputer

A small, dockable voice and text command panel for REAPER. Routine commands run
quietly. Clarifications, errors, and explicit questions get a conversational reply.

This is an independent implementation inspired by Michael Briggs’s
[ReaAssist](https://github.com/michaelbriggsaudio/mbriggs-reaper). No ReaAssist code,
prompts, assets, or helper binaries are included. ReaAssist remains Michael Briggs’s
work and has its own license. HeyComputer is not affiliated with or endorsed by him.

## What it can do

- Create and organize tracks, add plugins, set inputs, pan, and record-arm tracks.
- Control transport, jump by bars, loop or skip ranges, edit items and markers,
  manage sends, and save project versions.
- Edit exposed plugin parameters directly; bypass, offline, reorder, copy, or
  move plugins while preserving their settings.
- Write track and plugin automation points, ramps, fades, and repeating patterns.
- Launch installed REAPER actions and scripts by name.
- Optionally use REAPER-window vision for controls that need the interface, or
  control the Mac microphone from the experimental iPhone/Apple Watch remote.

The panel adapts to a side or bottom docker and follows REAPER's theme. It uses
push-to-talk or typed commands; it does not listen for a “Hey Computer” wake word.
This is a macOS-focused prototype, with the current limitations documented below.

## First setup (macOS)

Requirements: REAPER 7+, ReaImGui 0.10+ (via ReaPack), and Python 3.11+ available as
`python3`. Optional visual control requires macOS 14+. An API account is needed
for model-assisted commands and speech transcription; short local commands such
as “Play” and “Stop” also work without a key.

Clone this repository, or download and extract its source, then open a terminal
in the project folder:

```sh
git clone https://github.com/jamiethemorris/HeyComputer.git
cd HeyComputer
sh scripts/setup.sh
```

1. The setup command installs the helper dependencies into an isolated `.venv`;
   it does not change REAPER’s audio configuration.
2. In REAPER, open **Actions → Show action list → New action → Load ReaScript**.
   Select `reaper/HeyComputer.lua`, then run it.
3. In **Settings**, choose **Use OpenRouter for all**, enter your **OpenRouter key**
   once, and click **Save settings**. That key covers Jev, conversational fallback,
   and transcription. Keys are stored in macOS Keychain under the service `HeyComputer`.
   `OPENROUTER_API_KEY` overrides the saved key when inherited by REAPER. API use
   is billed separately from chat subscriptions.
4. Select a microphone device and its **one-based input channel**, then save.
   This device is independent of REAPER’s track recording input. macOS may ask
   for microphone access when recording starts.

Re-running the action after closing it loads source updates. REAPER actions refer
to this checkout, so keep it at its installed path (or retain a folder alias).

### Updating from ReaJev

The project is now **HeyComputer**. Load `reaper/HeyComputer.lua` for the new
name. The old `ReaJev` entry scripts remain compatibility launchers, so existing
keyboard shortcuts and toolbar buttons continue to work. Use the new
**HeyComputer** actions for new bindings. When renaming an existing checkout,
keep a `ReaJev` folder alias pointing to `HeyComputer` for previously registered
paths and the existing Python environment.

The first launch imports settings, microphone selection, UI memory, and remote
pairing from `Data/ReaJev` into `Data/HeyComputer` without replacing existing
HeyComputer data or replaying old requests. Saved API keys under the old Keychain
service remain readable; newly saved keys use `HeyComputer`. The dock position
is preserved. Phone/Watch display names, source folders and build targets use
HeyComputer; their existing bundle IDs and pairing Keychain identifier stay unchanged so updates preserve installed apps
and pairings. New QR codes use `heycomputer://`; the mobile app also accepts
older `jev://` pairing codes.

## API connections

Each service has an editable **provider**, **model ID**, and **endpoint URL** in
Settings. Enter the complete request URL, including its path (not just a base
URL). The bundled OpenRouter defaults are:

| Service | Model ID | Endpoint URL |
| --- | --- | --- |
| Jev | `~typesafe/jev-latest` | `https://openrouter.ai/api/v1/systemone` |
| Conversation / fallback | `openai/gpt-6.1-sol` | `https://openrouter.ai/api/v1/responses` |
| Transcription | `openai/gpt-transcribe` | `https://openrouter.ai/api/v1/audio/transcriptions` |

All three use the same saved OpenRouter key. Model presets include Sol, Luna,
Astra, GPT Transcribe, Mini Transcribe and Whisper; you may also enter a model ID.
The selected model must be available to your provider account and support that
service's API and, for the conversation model, strict JSON-schema output and low
reasoning effort. Presets are configurable defaults, not a guarantee of provider
availability.

Direct TypeSafe (Jev) and OpenAI (conversation/transcription) remain available.
Changing a service's provider restores its endpoint/model defaults and selects
that provider's saved key. Each provider has one shared key; mixed setups can use
OpenRouter for commands and direct OpenAI for voice, for example. Direct keys can
also come from `TYPESAFE_API_KEY` / `OPENAI_API_KEY`. Blank key fields keep saved
keys. Existing installations retain their direct-provider settings until you
choose **Use OpenRouter for all** and save.

Custom endpoints must implement the same request/response contract: System One
or Decisions for Jev, Responses for GPT, and audio/transcriptions for voice.
The selected provider determines the key used and the audio encoding (OpenRouter
uses base64 WAV JSON; direct OpenAI uses multipart WAV). The selected key is sent
to the endpoint you enter. HTTPS is required except for localhost; redirects are
rejected so credentials cannot be forwarded to a different destination.

Provider references: [Jev / System One](https://openrouter.ai/docs/guides/community/typesafe-sdk),
[Responses](https://openrouter.ai/docs/api/reference/responses/overview),
[transcription](https://openrouter.ai/blog/tutorials/transcription-on-openrouter/).

## Use

Example:

> Create 10 tracks, put them in a folder, alternate hard left and right panning,
> put Genome followed by Pro-Q on each track, set the input to Guitar 1, and
> arm the first track.

This creates ten child tracks and an additional folder parent. The parent gets
neither the plugin chain nor the alternating pan. Only the first new child is
armed; existing tracks’ arm states are preserved. Monitoring and transport
recording are not changed. If several Pro-Q versions or plugin formats are
installed, set a preference such as `Pro-Q = VST3: FabFilter Pro-Q 4` or answer
the clarification. Input aliases can also be written in preferences, e.g.
`Guitar 1 = Input 3` (use the actual REAPER input label).


- Type a command and click **Send** (or Command+Enter).
- Click **Talk**, speak, then **Stop & send**. **Cancel recording** discards audio.
  The first version uses click-to-record, not continuous listening. Recordings
  are limited to 90 seconds and audio stays in memory until transcription.
- Use **Dock** to choose a REAPER docker or return to a floating window. REAPER
  controls each docker’s position; its tab can be moved to a side or bottom dock.
  The panel remembers the docker and reflows to available width.
- Colors follow REAPER’s active theme and refresh while open. Backgrounds,
  text, input fields, buttons, borders, and accents use theme values with contrast
  fallbacks. Theme images and fonts are not reproduced.
- **Undo** only undoes the latest HeyComputer command if no intervening edits occurred.
  Otherwise use REAPER’s normal undo history.
- Conversation history is available under **Conversation** and kept in memory.
  Use **Clear conversation** for a fresh context.

### Keyboard shortcuts and toolbar actions

Opening HeyComputer automatically registers two scripts in REAPER's **Main** action
section. After updating, close and reopen the panel, open **Actions → Show action
list**, and search for **HeyComputer**:

- **HeyComputer - Talk or stop and send**: opens HeyComputer if closed, then starts the
  selected microphone once the helper and microphone settings are ready. The
  next press stops, transcribes, and sends the command.
- **HeyComputer - Send**: submits the text currently entered in the panel.

Assign shortcuts with **Add** in the action's shortcuts section, or add the
actions to a toolbar. For shortcuts that should also work while focus is inside
the panel, use REAPER's **Global** shortcut scope. The actions also work while
another docker tab is visible. Talk can open a closed panel; Send still requires
an open panel with entered text. Busy/disabled actions are ignored, not queued
for later. The one-time Talk startup handoff belongs only to the newly opened
panel, expires after 15 seconds and is discarded on startup failure or closing.
Repeated presses during startup do not open duplicate panels. Send is disabled
during recording; Talk stops and sends that recording instead.

If automatic registration fails, use **New action → Load ReaScript** and select
the two `HeyComputer - …` files alongside `reaper/HeyComputer.lua`. No shortcuts are assigned
or overwritten automatically.

### REAPER-only visual control (experimental, macOS 14+)

Run `sh scripts/setup.sh` again after updating, then close and reopen HeyComputer.
In Settings, enable **Allow REAPER-only visual control**, save, and click
**Check permissions**. Grant Screen Recording and Accessibility to the
helper/launching application identified by macOS in **System Settings → Privacy
& Security**; restart the helper by reopening HeyComputer after granting access.
The feature is off by default. It uses the existing GPT conversation model,
Responses endpoint, and key. The model must also accept image input.

Native commands remain the first choice. Plugin parameter requests first inspect
the exact instance through REAPER; exposed controls with usable value formatting
use direct parameter edits (see below). Remaining GUI-only tasks
can be routed to the visual fallback—for example, “In the open plugin window, set
Mix to 30%.” It can click, double/right-click, drag within one window, scroll, type,
use a bounded set of keyboard shortcuts, and press/set exposed accessibility
controls. Mixed plugin requests first execute a validated native setup plan, then
pass only the remaining plugin task to vision. `open_fx` uses
[REAPER's TrackFX_Show API](https://www.reaper.fm/sdk/reascript/reascripthelp.html#TrackFX_Show)
and the exact inserted FX index/GUID; it never clicks a track's FX or routing
button. An explicit add always inserts a new instance, even if the track's default
chain already includes the same plugin. Existing instances can be opened by GUID
without adding another. The handoff targets the verified plugin window, uses a
fresh project snapshot, and proceeds only after native execution succeeds.
A failed/cancelled visual phase does not roll back completed native setup.
It checks the next screenshot before reporting completion. Ordinary visual edits
accept small differences (frequency within 0.5%, gain within 0.1 dB); 300.2 Hz
and +2.94 dB satisfy 300 Hz and +3 dB. The visual model is instructed not to
spend extra actions chasing those decimals. Explicit “exactly” requests require
precise displayed-value verification or an explanation of the limitation.
Actual model accuracy and plugin behavior still require live verification.

Only windows belonging to the running `com.cockos.reaper` application are allowed
automatically. Separately hosted plugins must be hosted by a `reaper_host…`
executable inside that REAPER application's bundle and individually selected under
**Refresh windows** in HeyComputer Settings. These approvals last only for the current
helper session and are tied to process identity. Arbitrary external applications
cannot be added. If a required window is unavailable, the operation stops.

Capture uses ScreenCaptureKit's **desktop-independent single-window filter**,
not a full-screen screenshot followed by cropping. Occluding applications and
child windows are excluded. Images are compressed JPEGs, at most 1,600 pixels on
their longest edge and 2 MB per image, kept in memory only. One current image is
sent per model step; prior screenshots are not replayed. Images never go into IPC,
conversation logs, Desktop, or a screenshot directory. There is no debug-image
saving mode. Content inside the allowed window (including names and paths) goes
to the configured endpoint and its model provider.

Coordinates are converted from the actual returned image size to the captured
window's global point bounds, accounting for resizing, Retina scale and monitors
with negative coordinates. Window ownership and geometry are rechecked before
input. If geometry changes, the old action is discarded and a new image is taken.
The target is brought forward for its initial capture; focus changes stop input.
Before physical mouse input, AppKit's native mouse-down hit test must identify
that exact REAPER window. It accounts for transparent pixels and windows that
ignore mouse events, so a click-through character overlay can remain open.
An opaque part of the character that actually receives clicks still blocks input.
If verification fails, the error identifies the receiving application, window
ID and screen point. Direct accessibility actions validate the element's owning
window instead of applying a physical mouse overlap check. Owned dialogs can become the
next target. REAPER project switches, recording, or missing panel heartbeat stop
the controller. Leave the mouse and keyboard free while a task is running.

The panel shows **Visual control active** and the current window/step. **Escape**
or **Cancel request** stops further input, including when a model response is in
flight. The network call may still take up to its 60-second timeout to return, but
its actions are discarded after cancellation. Tasks are bounded to 20 observations
and a three-minute deadline checked between operations/network calls. Closing the
panel also cancels the task. Scrolls are capped at ten lines per observation;
oversized model requests use that bounded amount and then inspect a fresh image.
Visual actions are not one atomic REAPER transaction:
partial changes can remain, and plugin changes may use a separate undo stack.
Use REAPER/plugin Undo as appropriate; the panel's native Undo receipt is cleared.

### UI memory

**Use and learn UI memory** is enabled by default for visual control. HeyComputer
keeps a small, editable text file in JSON format at
`REAPER resource folder/Data/HeyComputer/ui-memory.json`. It learns reusable control
names, sections, prerequisites and verified interactions from successful visual
tasks. For example, a note can say that the filter-shape selector is on the left
of the selected-band panel. Learning is included in the existing visual response;
there is no extra model request, screenshot file or separate summarization job.

Plugin hints are separated by product/version/format as named by REAPER, shared
across instances and projects, and never keyed by screen position or track name.
Main-window notes are kept separately for each reported REAPER version. Unknown
dialogs are not memorized. Stable locations use fractions of the captured window,
plus its logical width and height. Moving a window or switching Retina scale
preserves those hints; resizing suppresses coordinates while keeping section
notes. Menus, moving band panels and scrollable controls should use descriptive
notes only. An `anchor` can identify a moving panel or landmark; the note describes
the control relative to that anchor. Anchored controls never supply fixed points.
Pro-Q 4 band controls specifically use the selected-band panel as their anchor;
legacy coordinates are suppressed even when the window size has not changed.
Changing frequency, dragging an EQ point or selecting another band requires
locating that panel again in the next screenshot. Plugin zoom, theme and mode
changes still require screenshot checks.

When a location or action result does not match expectations, the visual model
can request `reobserve`. This sends no input, drops suspect hints for this task,
and takes a fresh image for locating the panel and labels again. It must not repeat
the old click or type into an unverified target. Recovery is limited to two fresh
observations per window; failure stops with an explanation rather than continuing
to guess. This works for other plugins too: after verifying the same control in a new
location, the model can replace a fixed hint with a moving-panel anchor. A single
miss is insufficient evidence of movement; hidden controls, tabs, zoom and
layout changes must be considered. Corrected notes replace rejected hints only after successful completion;
rejected hints without a verified replacement are then removed. A failed or
cancelled task leaves the stored memory unchanged. Detection of a visual mismatch
still depends on the model interpreting the screenshot correctly.

Hints are untrusted observations, not recorded macros: the model must match them
to the current screenshot and all existing window/focus checks still run. Notes
are committed only after a task reports a verified completion. Errors,
cancellation and clarification replies do not save the task's proposed notes.
The file contains no screenshots, global screen coordinates or action history;
the model is instructed not to record project details, paths or user values.
Only matching UI notes are included with that window's normal model request.

The file is created when the first notes are saved. It can be edited or deleted
between tasks; changes are read on the next observation. Each entry has `scope`,
`control`, `note`, an optional `anchor`, optional coordinates in `position` (or `null`), and an `updated`
timestamp. Correcting the same control name replaces its old note. There are at
most 120 entries, with at most 12 relevant notes supplied per observation.
Malformed files are ignored and preserved rather than overwritten. Unchecking
**Use and learn UI memory**, then saving settings, disables both reading and
learning without deleting the file.

Initial limitations: no full desktop or external-app control, cross-window drags,
menu-bar/popover surfaces without identifiable accessibility windows, visual recording commands,
or automatic handoff back to a structured plan within the same task. A window
must be identifiable through macOS accessibility for safe focus verification.
Floating REAPER dialogs are allowed when a unique accessibility window matches,
and focus transitions get a bounded retry while macOS updates its window list,
even when its internal controls are drawn graphically. Some plugins won't expose
enough window information. ReaSplice search/download/import is not yet verified
and cross-window sample dragging is not implemented.

### iPhone and Apple Watch remote (prototype)

The **HeyComputerPhone** scheme in `watch/HeyComputerWatch.xcodeproj` builds the iPhone app
and its embedded Watch companion. In HeyComputer settings, enable remote control, save,
then choose **Pair iPhone**. Scan the QR code in the iPhone app and confirm the Mac
address. Pairing also enables the Watch controls; no address or code entry on the
Watch is needed. The phone keeps the credential in Keychain and relays the
Watch’s live requests to the Mac using WatchConnectivity.

Both apps provide **Talk**, **Stop & send**, **Cancel**, and HeyComputer’s status/replies
using the Mac’s selected microphone. Keep the iPhone nearby on the Mac’s Wi-Fi,
and REAPER and HeyComputer running on an awake Mac. Watch microphone recording is not
implemented yet. The connection to the Mac uses unencrypted local HTTP on trusted
Wi-Fi. Pairing is single-use and expires after five minutes; microphone commands
expire and are never queued for offline delivery or automatically retried.
See [iPhone and Watch setup](watch/README.md) for build, installation, and protocol
details.


## Models and execution

- **Jev** handles a bounded recipe for creation, optional folder, shared pan,
  up to four ordered default plugin inserts, a mono input, and record arming.
  It selects values from real project data in one API call. Low-confidence,
  ambiguous, unsupported, and conversational requests go to the fallback.
- **GPT-6.1 Sol** is the default fallback; **GPT-6 Luna** and **GPT-6 Astra** are
  selectable. Reasoning is set to low. The fallback can discuss the project,
  ask questions, or produce a validated action plan. No routine narration.
- Normal plans also support mute, solo, absolute volume, track naming/coloring,
  transport, musical navigation, ranges, markers/regions, item properties and
  stereo track sends (see below). Targets are captured by GUID/selection at
  request time. Complex commands use the conversation model to choose a plan;
  native REAPER code performs the operations without generated Lua.
- **Reviewed custom Lua** is optional and off by default. When enabled, scripts
  appear for manual review before Run. They receive a limited REAPER API, no
  file/network access, and a Lua instruction budget. This is a capability limit,
  not a complete isolation boundary for hostile code or native plugin crashes.
  Native REAPER/FX calls cannot be interrupted by the Lua instruction hook.
  Custom scripts currently edit existing tracks/FX; track deletion/creation,
  deferred scripts and external processes are unavailable in that mode.

The assistant reads effective tempo and meter at the project start, cursor,
selection and loop endpoints, together with up to 512 tempo/signature markers
(including linear tempo ramps). Empty complete maps still include the default
tempo/meter. Musical positions honor REAPER's displayed bar offset, and quarter
notes respect the meter denominator. It should use this context instead of asking
for tempo or time signature. Incomplete maps are explicitly marked.

The snapshot also includes track numbers, levels, mute/solo, folder relationships,
channel counts and monitoring/record modes; item mute/lock, fades and audio/MIDI
type; musical item/marker positions; project playrate, length and save state; and
grid/swing and available native option toggle states. Existing selection, input,
plugin, routing and envelope context remains available. Missing user intent or
ambiguous targets can still require a question. Context does not itself add new
editing operations.

The executor preflights the entire plan, checks the active project and revision,
groups edits into one undo block, and restores captured track state if an
operation fails. A response arriving after another edit, selection, cursor move,
transport change or project switch is rejected; resend the command to use the
latest project. Stop remains usable when recording changes the project. Failures
during restoration are reported. Transport and saving run last, outside the edit
transaction. If that final step fails, completed edits remain and the result says
so; saving a file and recording audio cannot be rolled back as ordinary edits.

## Common commands

| Task | Examples / behavior |
| --- | --- |
| Transport | “Play”, “Stop”, “Pause”, “Record”. Recording requires an armed track; it never arms unrelated tracks automatically. |
| Musical navigation | “Rewind 4 bars”, “Forward 8 bars”, “Go to bar 17”, “Go to the Chorus region”. |
| Cycle / time selection | “Loop bars 9 through 12” selects bar 9 up to the start of bar 13 and enables repeat. “Turn looping off” disables repeat. |
| Skip playback | “Skip bars 9 through 12 during playback” sets a time selection, disables repeat, and invokes REAPER's native **Transport: Play (skip time selection)**. It starts playback without deleting audio. “Skip the cycle area” uses the existing loop bounds. Stop ends playback; ordinary Play resumes normal playback. |
| Markers and regions | “Add a red marker called Verse at bar 9”, “Make bars 17 through 24 a blue region called Chorus”, “Rename that region Outro and make it orange”. Ambiguous names get a question. |
| Items | “Name the selected items Kick”, “Color those items green”, “Reduce the selected items by 6 dB”. Names apply to active takes (or notes on empty items), and gain applies to items, not track faders. Coloring clears take-color overrides so the item color can show according to REAPER's theme preferences. |
| Track colors | “Make the guitar tracks blue”. Existing naming, pan, volume, mute, solo, input, FX and arming remain available. |
| Sends / buses | “Send the selected tracks to Reverb at −12 dB”, “Make that send pre-fader”, “Mute the send from Guitar to Reverb”, “Remove that send”, “Create a Reverb bus and send the selected tracks to it”. |
| Save | “Save”, “Save a new version”. Versions use the next unused `song_v001.rpp`, `song_v002.rpp`, etc. in the project folder, and switch to the new file. Media files are shared; this is project versioning, not a media backup. An unsaved project opens native Save As for you to finish. |
| Native actions | Common built-in Main-section item, track, selection and view actions: split, duplicate, trim, normalize, remove, lock/group, zoom, snapping, grid, metronome, ripple editing and crossfades where available in the installed action list. |

Bar movement uses REAPER's tempo/time-signature map and lands at the **start of
the destination bar** by default. From bar 12 beat 3, “forward 4 bars” lands at
bar 16 beat 1; “rewind 4 bars” lands at bar 8 beat 1. Explicitly ask to preserve
the beat to keep it, or specify seconds for a time offset. During playback, a
relative jump uses the current play position; while stopped, it uses the edit
cursor. Absolute bar numbers honor the project's displayed numbering offset.

Exact short commands such as “Play”, “Stop”, “Record”, “Save” and numeric bar jumps
use a local parser and need no model request or API key when typed. Voice still
needs transcription. Other commands use the configured model; simple track setup
retains the Jev recipe. Busy requests are not interrupted by another assistant
command; REAPER's normal transport controls remain available.

Native-action candidates come from the **installed action list**, filtered for
supported local operations and searched by the request. Models cannot invent
command IDs. These commands run one action per request, using the current
captured selection. The result says which action was invoked; use REAPER's normal
Undo for its edits. Compound native-action macros aren't supported yet. Structured
operations can be combined, e.g. create tracks, arm the first, then record.

## Plugin chain commands

Normal track FX can be deleted, bypassed/re-enabled, taken offline/brought online,
reordered, and copied/moved between tracks through native REAPER APIs. These
operations do not need screen capture or mouse control. Examples:

- “Bypass Pro-Q on the guitar track.”
- “Enable that Pro-Q again.”
- “Take Genome offline,” or “Bring Genome back online.”
- “Move Pro-Q before Genome,” or “Move the compressor to the end of the chain.”
- “Delete the second Pro-Q instance on that track.”
- “Copy Pro-Q from Guitar to Bass, keeping its settings.”
- “Move Genome from Guitar to the start of the Guitar Bus chain.”
- “Create a track named Parallel EQ and copy this EQ to it.”

The snapshot includes each instance's GUID, slot, processing state and offline state.
Commands identify an exact existing instance; duplicate names need a clear slot/order
or clarification. Multiple instances/tracks can be handled in one bounded plan.
Bypass and offline are separate states: bringing a bypassed plugin online keeps it
bypassed. Delete removes an instance from the project, not the installed plugin files.
State changes and order are checked after execution. These commands use the normal
undo/rollback protection, and later steps resolve plugin GUIDs again after moves or
deletions. Invalid references to deleted instances are rejected before any edits.

Copy/move transfers the native instance state, including its settings and bypass/offline
flags. A copy is independent of the original; a move removes it from the source.
Existing native FX automation travels with the instance. The destination can be
an existing track or the first track created in the command,
positioned first/last or before/after another destination plugin. Both tracks are backed
up for rollback. Further parameter work on a transferred instance needs fresh state.

Currently this covers existing top-level normal track FX. Master/input/take FX and
nested containers are not included. Build new chains using plugin insertion order. Parameter edits
cannot be combined with unloading/reloading or deleting the same instance; an explicit
online-plus-parameter request can bring it online before a fresh parameter inspection.
Plugin chain commands go directly to the configured conversation model, bypassing Jev,
and do not need a parameter-inspection or visual-model round trip.

## Automation

Native envelope commands cover normal-track volume, pan, mute, and inspected plugin
parameters that expose usable automation/value formatting. New tracks can receive
volume, pan and mute automation in the same command that creates them; the complete
operation shares one undo step and rolls back on failure. Plugin parameters still need
inspection of the actual inserted plugin. Examples:

- “Create a track called guitar, ramp its volume from −20 dB at bar 1 to 0 dB at bar 2, and alternate hard left/right pan every quarter note to bar 4.”
- “Fade Guitar from 0 dB at bar 5 to −6 dB at bar 9.”
- “Pan Bass from hard left at bar 2 to hard right at bar 4.”
- “Automate this filter’s cutoff from 300 Hz at bar 5 to 2 kHz at bar 9.”
- “Clear Guitar’s volume automation from bars 5 through 9.”
- “Show and arm Guitar’s volume envelope.”
- “Set Guitar to touch mode,” or “Set Guitar back to trim/read.”

Points support linear, square, slow, fast-start and fast-end curves; mute uses square
points. Positions use the project’s bar/beat and tempo map, seconds or timeline anchors.
A ramp replaces points in its inclusive span; additive edits replace only points at the
specified times. Other points remain, although adjacent interpolation naturally changes.
Native interpolation follows the envelope’s scale, which may not be linear in Hz or dB.

Envelope edits require stopped transport. They activate/show the target envelope and
preserve its armed state; new envelopes are unarmed. Track modes (trim/read, read,
touch, write, latch) change only when explicitly requested. A global automation override
must be cleared before changing a track mode. Mode changes follow REAPER’s native
envelope auto-arming/showing preferences. Mode/arming commands prepare manual
recording but do not automatically move controls, start playback or record audio.

Plugin automation uses the same one-instance inspection/value-conversion path as direct
parameters. Unsupported scales stop with an explanation. Automation items, pooled items,
master/take/send envelopes, and freehand/audio-following automation are not supported.
Chain edits on affected tracks require a separate request from envelope edits. Changes
use REAPER Undo and track-state rollback; snapshots include bounded envelope context.

Live integration checks on REAPER 7.78 cover native copy/move independence and settings,
FX-envelope transfer, volume/pan/mute points, range clearing, envelope state, modes,
ReaEQ automation, and VST3 Pro-Q 4 frequency automation (verified `2000.0 Hz`).
`scripts/test_automation_live.lua` creates/removes only its own temporary tracks and
refuses to run outside the stopped `Jev Testing.RPP` project.

## Direct plugin parameters

Plugin parameter work uses the existing conversation model; Jev routing is unchanged.
HeyComputer first requests `inspect_fx` on one exact normal track FX instance. A new-track
request can create the track and insert the plugin before inspection. Inspection
alone is read-only, requires no screen capture, does not open the plugin, and does
not create an undo entry. The continuation contains the actual parameter indices,
names, current displayed values and sample formatting for up to 2,048 parameters;
larger catalogs are marked truncated. Offline plugins are reported without automatically bringing them online; an
explicit online request can run before inspection. The model does not receive every plugin's catalog on every
ordinary request.

`set_fx_param` targets an inspected track/FX GUID and an observed parameter index
AND name. Local code uses the plugin's read-only value formatter to find a matching
normalized setting, including nonlinear frequency scales; it never sweeps live knobs
to discover the scale. Numeric values support Hz/kHz, dB, ms/s, percent and unitless
values, plus observed discrete choice labels. Unsupported formats, nonmonotonic
scales, unknown labels and out-of-range values stop before the batch writes anything.
Displayed-value readback is verified after each write and again after the whole batch.
Failure rolls the parameter batch back using the existing track-state transaction.
“Exact” means the plugin reports the requested displayed value; it is not a guarantee
about arbitrary internal floating-point precision. Approximate requests tolerate
small readback differences.

The planner prefers direct controls and falls back to vision when inspection shows
missing controls or unavailable formatting. A value-conversion failure discovered
during execution stops with an explanation; it does not automatically retry visually.
There is one bounded inspection continuation per request, currently for one plugin
instance. Setup and parameter editing have separate undo steps. Failure or cancellation
after setup leaves the created track/plugin in place and reports that fact.

Live verified on REAPER 7.78 with VST3 Pro-Q 4: a new instance exposed 740 parameters;
its band Used/Enabled, Shape, Frequency, Gain and Q were available. A native test
created a low shelf with host readback `300.00 Hz` / `+3.00 dB`, with no GUI parameter
editing. Other loaded plugins exposed parameters too, but that is not a compatibility
certification for every value or plugin. `scripts/inspect_fx.lua` is a read-only local
diagnostic that writes loaded-plugin catalogs to `/tmp/heycomputer-fx-inspection.json`.

## Current limits

These are limits of this build, not necessarily limits of REAPER or future work:

- **ReaSplice / sample libraries:** no dedicated search/download/import bridge.
  Visual browsing inside allowed windows is experimental; cross-window dragging
  and a complete sample-import workflow are not implemented or verified.
- **Listening and musical judgment:** the assistant reads project metadata; it
  does not hear the mix, identify sounds, choose the “best” take, detect chords,
  judge tone or automatically mix/master audio.
- **Plugin interfaces:** direct parameter control covers one inspected normal track
  FX instance per request. Master/input/take FX, nested containers and arbitrary
  preset/sample browser operations are not covered by this path.
  Plugins must expose the control and usable value formatting; not every interface
  control does. The optional visual fallback can attempt remaining visible controls,
  subject to the window/focus limits above.
- **Detailed MIDI / automation / comping:** no note drawing/editing, automation-item work,
  master/take/send-envelope editing, take-lane comping or complex folder restructuring
  as structured operations yet. Those APIs are also absent from reviewed Lua.
- **Advanced routing:** sends currently use stereo channels 1/2, with MIDI disabled
  on new sends. No hardware-output changes, sidechain/channel maps, MIDI sends,
  master/parent-send switching or feedback routes. Multiple sends to the same
  destination must be made unambiguous before they can be changed.
- **Actions and scripts:** common editing goals use the supported built-in Main
  actions. Explicitly named installed Main actions, scripts, custom actions and
  extension actions can also be launched as separate commands. MIDI-editor
  sections, mouse-position setup and unattended dialog completion need dedicated
  support. English action names currently drive discovery/filtering.
- **Media-file operations:** rendering/exporting, gluing, freezing, destructive
  source edits, cleaning files, project opening/closing and unattended dialog
  completion aren't inferred from general editing goals. Project Save/Save new
  version are explicitly implemented exceptions; running a specifically named
  installed action still follows that action's own behavior.
- **Live recording:** stop and marker insertion are supported while REAPER records;
  the new editing/routing operations require recording to stop first. This is a
  command assistant, not sample-accurate live performance control.
- **Scale and rollback:** plans have up to 64 operations. Item discovery includes
  up to 2,000 items plus selected items; “all items” is rejected if the inventory
  is incomplete. Native actions follow REAPER's own undo behavior. A native plugin
  crash or an external disk/audio effect cannot be undone by this helper.

## Voice and data

Settings → **Pronunciation / spelling corrections** accepts one literal correction
per line, for example:

```text
ReSplice = ReaSplice
ree splice = ReaSplice
pro cue = Pro-Q 4
```

Corrections match whole phrases, ignore case, prefer longer phrases and apply once
without cascading into other corrections. They run locally after transcription,
before the corrected transcript appears or the command is interpreted, on either
transcription provider. They also apply to typed commands. On the direct OpenAI
transcription route, canonical terms are additionally included as vocabulary hints.
**Term definitions & preferences** is the existing preferences field: use it for
meaning and choices, such as “ReaSplice is my sample-browser script” or preferred
plugin formats. These preferences are passed to the command model.

Installed REAPER **Main** actions, registered scripts, custom actions and extension
actions can be launched explicitly by name: “open ReaSplice” or “run ReaSplice Lua
script.” Exact unambiguous tool names use a local shortcut without an API call;
other wording uses the conversation model and a filtered action catalog. No manual
command ID entry or reviewed-custom-Lua setting is needed. Each launch runs alone,
rechecks the installed action's identity and uses its own undo/side-effect behavior;
it is not enclosed in HeyComputer's edit rollback or generated-Lua restrictions. This
does not load arbitrary script files, search ReaSplice's sample catalog, or fill
dialogs. Scripts must already be registered in REAPER's Main Actions list. Multiple
matching names require clarification. A receipt confirms action dispatch, not
completion of a deferred script or verification of its window.

Transcription presets: `gpt-transcribe` (default), `gpt-4o-mini-transcribe`, and
`whisper-1`, with an `openai/` prefix on OpenRouter. Audio is sent to the configured
transcription endpoint only after **Stop & send**. Silence detection
avoids submitting empty recordings. Transcripts, relevant track metadata,
installed plugin names, tempo/meter/grid and timeline/item/marker/send metadata,
track/option states, project filename,
relevant action/script names and preferences are sent to the selected command service.
OpenRouter routes requests to the serving model providers. One OpenRouter key
supports all three services. A direct OpenAI key alone works with Jev disabled;
a direct TypeSafe key alone supports the routine recipe but cannot provide
speech transcription or conversational fallback. Vocabulary hints are currently
sent only on the direct OpenAI transcription route.

A local Python helper handles audio and HTTPS outside REAPER’s UI thread. Private
IPC/config files live in `Data/HeyComputer` under REAPER’s resource directory, not the
source checkout. The helper exits when the panel closes or stops heartbeating.
API keys are never included in model requests as project data, displayed after
saving, or written to settings/logs. The short-lived key-save IPC request is
removed before processing and resides in a directory accessible only to the user.

## Development and verification

Run from the repository root after setup. The Lua suites require a standalone
Lua 5.3+ interpreter and compiler (`lua` and `luac`); REAPER's embedded Lua does
not provide those terminal commands.

```sh
.venv/bin/python -m unittest discover -s tests -v
for test_file in tests/test_*.lua; do
  lua "$test_file" || exit 1
done
find reaper scripts tests -name '*.lua' -exec luac -p {} \;
```

The Python suite includes local loopback-server tests, so allow local networking
when running it in a sandbox. These automated suites use fixtures and mocks;
they do not call paid model APIs or record microphone audio. See
[the remote README](watch/README.md#verification) for Swift checks and the unsigned
iPhone/Watch build. Live REAPER scripts under `scripts/` are separate, opt-in
checks; use a disposable test project and read each script's prerequisites first.

Source lives in `heycomputer/` (Python helper), `reaper/` (panel and native
executor), and `watch/` (iPhone/Watch apps). `tests/` contains the automated suites.
Local environments, runtime files, logs, credentials, REAPER projects, and Xcode
user/signing files are excluded by `.gitignore`. Keep personal configuration in
REAPER's resource directory or macOS Keychain, not in source files.

The tests use mocked providers and a REAPER API fixture. They cover the ten-track
workflow, plugin ordering, missing inputs/plugins, rollback, stale project
responses, conversational replies, custom Lua limits and theme color conversion.
Provider tests cover shared-key routing, separate endpoints, model IDs, both
audio payload formats, settings migration, Keychain separation and HTTP failures.
Extended tests cover musical navigation across tempo/signature changes, displayed
bar offsets, loop/skip commands, item and marker changes, send feedback prevention,
rollback, stale selection checks, transport, version naming and action filtering.
Actual model accuracy, latency, audio permissions and plugin behavior need live
verification with the user’s API keys and hardware.
Visual tests use synthetic images, mocked providers and OS adapters. They cover
window/process filtering, memory-only upload payloads, coordinate conversion,
cancelled responses, stale geometry, step limits, partial-change reporting and
project guards. macOS framework availability is checked locally; this does not
establish real capture/input behavior before macOS permissions are granted.

### Local test commands

After reopening the updated panel, developers can submit a command without
focusing the text box:

```sh
.venv/bin/python scripts/test_command.py "What does hard panning mean? Do not change anything."
```

A command can also be piped to the script on standard input. This is a local
file-based interface in REAPER's `Data/HeyComputer` session directory, with no new
network endpoint. It uses the same snapshot, conversation, model, execution and
script-review path as **Send**, and returns the panel's outcome as JSON. Commands
can change the project exactly as typed commands can. Model requests use the
configured service and incur its normal charges.

Busy panels, microphone recording, unsent drafts and script-review states reject
commands rather than queuing them. Requests expire after five seconds if not
accepted. Use `--session ID` if more than one panel is open, or `--runtime PATH`
for a nonstandard REAPER resource directory. A waiting timeout does not cancel an
accepted command; inspect the panel or use **Cancel request**. The command and
result use temporary session files, and accepted commands appear in conversation
history just like typed commands. No API keys or screenshots are included.

## Third-party notices

`reaper/lib/json.lua` is rxi’s MIT-licensed JSON library; its copyright/license
notice is in that source and `reaper/lib/JSON-LICENSE.txt`. Python dependencies
retain their respective licenses. ReaAssist is referenced for inspiration only.
