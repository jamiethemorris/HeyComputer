# HeyComputer

A small, dockable voice and text command panel for REAPER. Routine commands run
quietly. Clarifications, errors, and explicit questions get a conversational reply.

This is an independent implementation inspired by Michael Briggs’s
[ReaAssist](https://github.com/michaelbriggsaudio/mbriggs-reaper). No ReaAssist code,
prompts, assets, or helper binaries are included. ReaAssist remains Michael Briggs’s
work and has its own license. HeyComputer is not affiliated with or endorsed by him.

## What it can do

- Create and organize tracks, add plugins, set inputs, pan, and record-arm tracks.
- Hide/show tracks in the track panel (TCP), mixer (MCP), or both.
- Control transport, jump by bars, loop or skip ranges, edit items and markers,
  manage sends, and save project versions.
- Edit exposed plugin parameters directly; bypass, offline, reorder, copy, or
  move plugins while preserving their settings.
- Write track, send-volume and plugin automation points, ramps, fades, and repeating patterns.
- Create MIDI items and patterns; edit, transpose, quantize, or delete MIDI notes.
- Launch installed REAPER actions and scripts by name.
- Optionally use REAPER-window vision for controls that need the interface.

The panel adapts to a side or bottom docker and follows REAPER's theme. It supports
typed commands, push-to-talk, and optional local “Hey computer” wake-word detection.
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

1. The setup command installs everything HeyComputer runs locally into an isolated
   `.venv`: the helper, the wake-word engine and its small model, and on Apple
   Silicon Macs local Whisper transcription. That last part is a large download.
   It does not change REAPER’s audio configuration. Rerun it after updating.
2. In REAPER, open **Actions → Show action list → New action → Load ReaScript**.
   Select `reaper/HeyComputer.lua`, then run it.
3. In **Settings → Account and model**, click **Sign in** to use your ChatGPT plan for
   conversation (the default for new installs; Jev is off). On Apple Silicon, Talk is
   transcribed locally by MLX Whisper and needs no key. On other machines
   transcription defaults to OpenRouter: under **Account and model → Other providers**
   enter an **OpenRouter key** (stored in macOS Keychain under the service
   `HeyComputer`; `OPENROUTER_API_KEY` overrides it when inherited by REAPER). Click
   **Save all settings**. API use is billed separately from chat subscriptions.
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

The first launch imports settings, microphone selection and UI memory
from `Data/ReaJev` into `Data/HeyComputer` without replacing existing
HeyComputer data or replaying old requests. Saved API keys under the old Keychain
service remain readable; newly saved keys use `HeyComputer`. The dock position
is preserved.

## API connections

**To use your ChatGPT plan:** If you’re updating an existing installation, rerun `sh scripts/setup.sh` first to install the sign-in dependency, then reopen the panel. New installs already select ChatGPT for conversation; existing installs can choose it under **Other providers** (or **Use my ChatGPT account**). Click **Sign in**, approve plan usage in your browser, wait for the model list (or click **Refresh models**), choose a model, and **Save all settings**. This also selects the provider for optional REAPER-only visual control. Jev and speech transcription keep their own provider settings and may still incur charges. Wake and stop words are detected locally, as before.

The ChatGPT endpoint is fixed. The helper stores its credentials in an owner-only file under REAPER’s `Data/HeyComputer`, separate from settings and session history. Use **Sign out** to clear the local tokens; if revocation cannot be confirmed, disconnect the app in ChatGPT Settings as well.

**Talk transcription is local on Apple Silicon:** a ChatGPT sign-in cannot transcribe audio, so
new installs on Apple Silicon Macs use **MLX Whisper (local)** and `sh scripts/setup.sh` installs it.
No API key is needed. Existing installs keep their saved provider; to switch, rerun
`sh scripts/setup.sh`, reopen the panel, choose **MLX Whisper (local)** under
**Settings → Voice input**, and click **Save all settings**. On other machines the default stays
OpenRouter, which needs a key. The default English model is
`mlx-community/whisper-base.en-mlx`. You can change the Model ID to another MLX
Whisper checkpoint. The model may download on the first submitted recording;
the recording stays in memory on your Mac. There is no transcription API key,
per-minute fee, or automatic cloud fallback. If you also want to avoid API keys
for planning, disable Jev and select **ChatGPT** for conversation/fallback.
Local transcription does not make the conversation provider local.

Settings are grouped into collapsible sections: **Account and model** (open by default),
**Voice input**, **Spoken replies**, **REAPER control** and **Vocabulary**. For API-key
connections, each service has an editable **provider**, **model ID**, and **endpoint URL**;
the provider choice, keys and endpoints live under **Account and model → Other providers**. Enter the complete request URL, including its path (not just a base
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
keys. New installs use ChatGPT for conversation with Jev off. Existing installations
retain their saved provider settings until you change them (for example with
**Use OpenRouter for all**) and save.

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
  With local wake words enabled, say the wake phrase to start and the stop phrase
  to submit. Recordings are limited to 90 seconds and audio stays in memory until
  transcription.
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

### Spoken replies (optional, macOS)

In **Settings → Spoken replies**, enable **Speak questions, replies and problems**
to hear clarification questions, conversation replies and errors. Successful
command receipts stay silent. Speech uses installed macOS voices locally: no
speech API key, endpoint, model download or per-use fee is needed. Your existing
command model and transcription service still use their normal connections.

Choose a voice, audio output device, and mono channel or stereo output pair. The
separate **Voice volume** starts at **20%** and ranges from mute to 100%; it does
not change REAPER's mix or macOS volume. **Test voice** previews unsaved output
choices, and **Stop voice** interrupts playback. **Save all settings**, at the
bottom of Settings (always visible), applies all sections; refreshing audio devices preserves
unsaved choices. Speech starts disabled on existing and new installations.

Wake detection pauses during synthesis/playback and briefly afterward to avoid
hearing the assistant itself. Starting Talk or another command stops speech.
While REAPER is recording, replies remain text-only and are not queued for later.
Unavailable outputs produce a visible error rather than falling back to a
different device. Only speech playback opens the output stream; no background
speech model runs while idle. Synthesis uses private temporary audio files that
are removed on completion, cancellation or error. Long replies are shortened for
speech; the full response remains in the conversation.

### Local wake and stop words (optional)

`sh scripts/setup.sh` already installs what this needs: the `sherpa-onnx` and
`sentencepiece` dependencies and a checksum-verified English keyword model from the
upstream project. The feature stays off until you enable it.
Only about 5.5 MB of model files are retained under REAPER's
`Data/HeyComputer/models/kws-en`; model files are not part of this repository.
For a portable/nonstandard resource folder, pass `--runtime /path/to/Data/HeyComputer`.
Reopen the panel after installation.

In **Settings → Voice input → Wake & stop words**, enable **Listen for a wake word** and
save. The defaults are **hey computer** to start Talk and **execute** to stop and
send. Both phrases are editable (English, up to six words); use a stop phrase you
will not normally say inside a command, such as “send it.” Wait for **Listening…**
after waking, then speak your command. The stop phrase also works for recordings
started with Talk or a shortcut. A detected stop phrase is removed
from the end of the transcript before command interpretation. The buttons still
work normally.

Keyword detection runs locally on one CPU inference thread, using the microphone
device and channel selected above. The same input stream is reused for Talk.
Idle audio is held only in a bounded processing queue, then discarded; it is
neither saved, transcribed, nor uploaded. The listener does not make model-service
requests or require another account. Initial dependency/model installation needs
internet access; subsequent wake/stop detection does not. Submitted command audio
still goes to your configured transcription provider, just like **Stop & send**.

Listening continues when the panel's dock tab is hidden, and ends when the panel
closes. **Pause wake word** releases the idle microphone; **Resume wake word**
enables it again. Settings, unsent drafts, pending requests and script review block
wake activation. Stop detections are tied to the current Talk recording, never a
future one. A failed capture or 90-second recording timeout discards the audio
without submitting it. No automatic silence-based submission is enabled.

**Keep a local activation log** records detected phrases, recording starts,
submissions, command transcripts, cancellations, errors and ignored/expired
activations. It writes only on events, outside the audio callback. Use
**View / refresh activation log** and expand **Recent activations**, or inspect
`Data/HeyComputer/wake-activations.jsonl`. One previous file is retained after
rotation at about 1 MB (roughly 2 MB total). Files are private to your user account;
**Clear log** deletes both. The log is excluded from Git and model requests.
It contains no audio. A cancelled or timed-out activation has no transcript:
the detected phrase records what the keyword detector matched, not an independent
transcription of the background sound. Logging does not transcribe ambient speech.

Music and background speech can cause false activations. Lower sensitivity if
necessary, or pause listening. A false wake alone cannot send a command; submission
requires a stop phrase or **Stop & send**, which can itself be falsely detected.
Recognition with your voice, mic and room needs testing; the automated fixtures
do not establish accuracy in a studio.

The detector uses [sherpa-onnx keyword spotting](https://k2-fsa.github.io/sherpa/onnx/kws/index.html)
and its [English GigaSpeech keyword model](https://k2-fsa.github.io/sherpa/onnx/kws/pretrained_models/index.html).

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

The Talk toolbar button follows the confirmed microphone recording state,
including recordings started by the wake phrase or panel. Existing
ReaJev Talk buttons are discovered when the panel opens, so they also light up
without being clicked first. Stopping, cancelling or closing the panel clears
the indicator.

If automatic registration fails, use **New action → Load ReaScript** and select
the two `HeyComputer - …` files alongside `reaper/HeyComputer.lua`. No shortcuts are assigned
or overwritten automatically.

### Combined track workflows

Track duplication and folder grouping are direct native operations. For example:

> Duplicate guitar track 12 twice, pan the copies hard left and right, put the
> original and both copies into a Guitars folder, and add Pro-Q to that folder.

The whole plan runs as one undoable command. Duplication uses REAPER's native
track duplication, retaining media items, FX settings and bypass/offline state,
envelopes and routing. A folder source includes its complete child branch once.
New folder parents start without default FX; only the requested inserts are added.
Existing nonadjacent sibling tracks can be gathered into a folder at the first
member's position, preserving their project order and any nested folders. Tracks
at different folder levels need a more specific restructuring operation first.

Plans can refer to tracks produced by earlier steps, including individual copies,
new folders and separate batches. This also supports commands such as creating two
tracks and a bus, sending the tracks to the bus, copying an existing plugin to it,
then coloring or automating the new tracks. Built-in volume/pan/mute automation
can be combined with plugin chain edits; FX parameter automation still requires
inspection and a stable FX chain.

Internally, `result:N` refers to every track produced by earlier action N, and
`result:N:K` selects its Kth track. Track numbers are resolved from the original
snapshot; later steps use these references rather than guessing new numbers or
GUIDs. Existing `created` / `first_created` selectors refer to the most recent
creation batch, while `selected` / `all` remain tied to the original snapshot.
References are supported in native track edits, sends, built-in automation,
plugin-transfer destinations and plugin opening/inspection after insertion.

The executor validates the complete plan, folder topology and routing before
editing. A plan may add at most 64 tracks total, including copied descendants and
new folder parents, and contain at most 64 operations. Failures roll back created
tracks, original track order, state and selection. Raw action/script launches
still run separately: their arbitrary effects do not provide typed results for
later steps. This improves composition of supported operations; it does not give
the model unrestricted access to every REAPER feature.

`scripts/test_track_layout_live.lua` verifies duplication, nested/nonadjacent
folders, Pro-Q state, media, envelopes, routing, result references and undo on
temporary tracks in the dedicated `Jev Testing` project, then removes those tracks.
It makes no model requests and does not save the project.

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

### Optional multi-step execution with ReaperMCP

In a disposable project, load and run the matching ReaperMCP 0.8.3 `reaper_mcp_server.lua` in REAPER's Actions list. In HeyComputer Settings, select **Enable REAPER MCP tools** and **Save all settings**. The switch defaults off. The helper starts a local stdio process for each MCP request using `uvx --python 3.13 xdarkzx-reaper-mcp==0.8.3` (the first run may download the package). Make `uvx` available to the helper, or set `HEYCOMPUTER_REAPER_MCP_COMMAND` in its local environment to use an absolute executable path. Neither the panel nor a model request can set this command. On macOS the helper aligns the child process’s temporary directory with `getconf DARWIN_USER_TEMP_DIR` so the server can find the Lua bridge.

Try: “Create a return named Short Room, copy the reverb from Vocals to it, set the copy’s decay to 800 ms, send Vocals to it at −6 dB, and automate the send to −12 dB at bar 5.” Use a project with one unambiguous reverb on Vocals. The selected conversation provider receives the request, bounded project context and tool results, not idle microphone audio. Standalone quick commands still bypass the model. For nonlinear parameters the panel uses the plug-in’s displayed units rather than guessing a normalized setting.

Requests stop after 28 model turns, 8 edit calls, or a 120-second budget. Keep the same project active and avoid simultaneous edits. Recording or unknown recording state blocks edits. Cancel stops further steps; it does **not** roll back edits already sent. Check REAPER before retrying a partial result. A compound request can leave multiple undo entries: 0.8.3 groups certain built-in operations, but `project_undo_group` **undoes** a group rather than opening one. HeyComputer does not call it automatically. Disable the switch and save to return to the existing planner.

## Common commands

| Task | Examples / behavior |
| --- | --- |
| Transport | “Play”, “Stop”, “Pause”, “Record”. Recording requires an armed track; it never arms unrelated tracks automatically. |
| Musical navigation | “Rewind 4 bars”, “Forward 8 bars”, “Go to bar 17”, “Go to the Chorus region”. |
| Cycle / time selection | “Loop bars 9 through 12” selects bar 9 up to the start of bar 13 and enables repeat. “Turn looping off” disables repeat. |
| Skip playback | “Skip bars 9 through 12 during playback” sets a time selection, disables repeat, and invokes REAPER's native **Transport: Play (skip time selection)**. It starts playback without deleting audio. “Skip the cycle area” uses the existing loop bounds. Stop ends playback; ordinary Play resumes normal playback. |
| Markers and regions | “Add a red marker called Verse at bar 9”, “Make bars 17 through 24 a blue region called Chorus”, “Rename that region Outro and make it orange”. Ambiguous names get a question. |
| Items | “Name the selected items Kick”, “Color those items green”, “Reduce the selected items by 6 dB”. Names apply to active takes (or notes on empty items), and gain applies to items, not track faders. Coloring clears take-color overrides so the item color can show according to REAPER's theme preferences. |
| Item targeting and selection | “Lower all items on Kick by 6 dB”, “Color the vocal items in the chorus blue”, “Select all items on Kick”. Native track/range targeting requires no manual selection and preserves selection unless asked to change it. |
| Repeat audio samples | “Keep the kicks going every half note to the end of the vocal track below.” Copies an existing audio item natively using the tempo map; no timeline zoom, menus or visual control. |
| Track colors | “Make the guitar tracks blue”. Existing naming, pan, volume, mute, solo, input, FX and arming remain available. |
| Track visibility | “Hide selected tracks”, “Show all tracks”, “Hide the guitar tracks in the mixer”, “Show the bass track in TCP”. Without a panel, both TCP and MCP change. Naming one panel preserves the other. Hidden tracks remain in context and can be addressed by name. No muting, selection changes or SWS dependency. |
| MIDI items and notes | Create MIDI items on existing or newly created tracks, insert notes/chords/repeating patterns, transpose, set pitch/velocity/length/channel/mute, shift or quantize note starts, and delete matching notes. All use native edits, including combined create-and-fill commands. |
| Sends / buses | “Send the selected tracks to Reverb at −12 dB”, “Make that send pre-fader”, “Mute the send from Guitar to Reverb”, “Remove that send”, “Create a Reverb bus and send the selected tracks to it”. |
| Save | “Save”, “Save a new version”. Versions use the next unused `song_v001.rpp`, `song_v002.rpp`, etc. in the project folder, and switch to the new file. Media files are shared; this is project versioning, not a media backup. An unsaved project opens native Save As for you to finish. |
| Native actions | Common built-in Main-section item, track, selection and view actions: split, duplicate, trim, normalize, remove, lock/group, zoom, snapping, grid, metronome, ripple editing and crossfades where available in the installed action list. |

Bar movement uses REAPER's tempo/time-signature map and lands at the **start of
the destination bar** by default. From bar 12 beat 3, “forward 4 bars” lands at
bar 16 beat 1; “rewind 4 bars” lands at bar 8 beat 1. Explicitly ask to preserve
the beat to keep it, or specify seconds for a time offset. During playback, a
relative jump uses the current play position; while stopped, it uses the edit
cursor. Absolute bar numbers honor the project's displayed numbering offset.

Exact short commands such as “Play”, “Stop”, “Record”, “Save”, numeric bar jumps,
and “Hide selected tracks” / “Show all tracks” (including TCP/MCP variants)
use a local parser and need no model request or API key when typed. Voice still
needs transcription. Other commands use the configured model; simple track setup
retains the Jev recipe. Busy requests are not interrupted by another assistant
command; REAPER's normal transport controls remain available.

“Hide this track” also runs locally when exactly one track is selected; “hide
these tracks” uses the selected tracks. Normal requests do not require preference
entries or SWS action names. Other wording uses the model and current project
context. If a singular target is unclear, it uses an unambiguous conversational
reference or asks which track; it never assumes a hovered track.

Native-action candidates come from the **installed action list**, filtered for
supported local operations and searched by the request. Models cannot invent
command IDs. These commands run one action per request, using the current
captured selection. The result says which action was invoked; use REAPER's normal
Undo for its edits. Compound native-action macros aren't supported yet. Structured
operations can be combined, e.g. create tracks, arm the first, then record.

The named-action launcher can access every installed action in the **Main**
section, including SWS, registered scripts and custom actions. For example,
“Run SWS: Hide selected track(s)” and “Run SWS: Show all tracks” still invoke those
exact installed actions. Actions in separate MIDI Editor/Media Explorer sections
are not part of this launcher. Main actions are searched locally: model requests
receive up to 64 relevant candidates from each catalog, and exact named launches
can bypass the model. A large installed action list therefore does not all become
prompt text. Arbitrary actions remain standalone: their selection/focus needs,
dialogs, external effects and script completion are not generally verifiable.

Ordinary hide/show requests use the native `track_visibility` operation and can
be combined with naming, coloring, track creation, etc. Its value contains `tcp`
and/or `mcp` booleans, with omitted panels preserved. `selected`, `all`, exact
track GUIDs and earlier track-result references work. Visibility settings are
reported in the snapshot and verified after writes; failure rolls back the batch.
Folder collapse, global mixer filters and master-track display are separate and
are not changed. `scripts/test_visibility_live.lua` checks the flags, Undo and
rollback on temporary tracks in **Jev Testing**, then removes those tracks.

## Item targeting and selection

Item gain, naming and color edits can address all items on one or more named
tracks directly, including hidden tracks and items omitted from the model's
bounded preview. Optional filters restrict the set to audio or MIDI and a time
range. No visual control, clipboard or selection preparation is needed.

- “Lower all the Kick samples by 6 dB” adjusts item gain relatively, preserving
  track faders, selection and other tracks' items.
- “Set the Kick items to −6 dB” sets their absolute item gain.
- “Color the vocal items in the chorus blue” uses items overlapping the known
  region; “starting in the chorus” instead matches item starts.
- “Select all items on Kick”, “Add the snare items to the selection”, “Deselect
  the vocal items”, and “Clear item selection” explicitly change item selection.

Range filters affect whole matching items, including the whole item's gain;
they do not split items at range boundaries. Endpoints are start-inclusive and
end-exclusive. Folder children must be included explicitly; a folder name does
not silently include every descendant. Ambiguous track names or ranges can still
need a question. Track selection remains unchanged.

Track/range selectors are evaluated at each native step. Repeating the kicks and
then reducing all Kick items' gain therefore includes the new copies in the same
transaction and Undo. Explicit selection is the final step when combined with
edits. Existing `selected`/`all` item selectors still refer to the captured set;
new MIDI items can use `item_result:N`. Filtered selectors target existing track
GUIDs and currently apply to item properties and selection, not MIDI note edits
or arbitrary native actions. Limits are 64 tracks and 10,000 matched items per
filtered operation, with at most 10,000 selection changes. Malformed targets are
rejected before editing; execution failures restore saved track state.

Live checks in REAPER 7.78 verify direct grouped gain with unchanged faders and
selection, explicit selection and its Undo, range boundaries, and repeat-then-gain
including newly created copies in one Undo. The local
`scripts/test_item_repeat_live.lua` test restores prior track/item selections and
removes its temporary tracks and generated audio.

## Repeating audio samples

The conversation model identifies the template item, interval and endpoint;
REAPER performs the copies directly in one undoable operation. “Extend the kicks
every half note to the end of the vocal track” continues after the last existing
item onset on the kick track. Existing items remain intact. A specific start/end
range can fill missing hits instead, skipping existing onsets. The template's
original onset anchors the grid, including any intentional offset. Half notes
are two quarter notes, with each placement resolved against REAPER's tempo map.

Endpoints can be an exact item, the last item on an exact track, or a musical/time
position. Each track's item count and bounds cover all its items, even when the
model's individual item preview is truncated. An empty folder's children are not
implicitly included. If several possible templates or vocal tracks make the
request ambiguous, HeyComputer should ask which one.

Copies preserve the template's item/take settings and effects, receive fresh
identities, and are unselected and ungrouped. Starts are before the endpoint;
sample tails retain their original length rather than being cut off. This first
version requires stopped transport and audio-only takes on ordinary tracks
(no fixed lanes or free item positioning). Limits are 512 new items per operation,
2,048 per command, and 64 MiB of copied item state. It does not infer or recreate
complex alternating samples/accents from one template. Failed edits use the
existing track-state rollback; success is one REAPER Undo.

Live verified in REAPER 7.78: native half-note repetition to another track's
endpoint, unique item/take/FX identities, preserved audio source and item/take/FX
settings, one-step Undo, duplicate avoidance, musical item context and rollback
after a simulated mid-copy failure. `scripts/test_item_repeat_live.lua` runs only
in the stopped **Jev Testing** project, uses a generated silent WAV and temporary
tracks, and removes its test tracks and WAV afterward. It makes no model calls.

## MIDI items and notes

MIDI commands use the conversation model and native REAPER operations. They do
not require visual control or reviewed custom Lua. Examples:

- “Create a MIDI item on the drum track from bar 1 to bar 5. Add MIDI note 36
  every quarter note at velocity 100, with sixteenth-note lengths.”
- “Create a new track called Bass, add a MIDI item from bar 5 to bar 9, and
  fill it with eighth notes at MIDI pitch 40.”
- “Transpose the selected notes up an octave and set their velocity to 90.”
- “Quantize the selected notes to sixteenths at 50% strength.”
- “Shorten all notes in this item to eighth notes.”
- “Delete the notes at MIDI pitch 36 between bars 3 and 5.”

New tracks and MIDI items can be reused later in the same command. Notes/chords
and repeating patterns can be added, shifted, transposed, muted, deleted, or
given a new pitch, velocity, length or channel. Quantizing adjusts note starts
on a straight musical grid; duration stays unchanged unless requested. Native
musical-time conversion follows the project's tempo map. “Through bar 4” ends
at the start of bar 5. Adding notes preserves existing notes and other MIDI
events; it does not implicitly replace the item contents or load an instrument.

The snapshot includes active-take identity, note and selection counts, MIDI grid,
custom note names, and a bounded preview of notes. Selected notes are prioritized.
Whole-item or pitch/time/selection-filter edits still work when the preview is
truncated; exact note indices must have appeared in the preview and may only be
used on the first MIDI edit to that item in a command. Item selection and note
selection are separate. `selected`/`all` MIDI targets ignore audio items.

Plans validate before editing and share one undo block, with rollback on failure.
Edits require stopped transport and preserve other MIDI events. Locked items,
external MIDI files, shared/pooled sources, and items repeating a looped source
are refused without changing their source/lock settings. Only the active take is
edited. Pitch and velocity overflow or notes extending beyond the item cause a
clear error, rather than silent clipping. This version does not edit CC, pitch
bend, MPE, notation or sysex, or add swing/humanization.

Limits: 32 seed notes per insertion operation, up to 512 repetitions, 4,096
inserted notes per command, 8,192 notes per item, and 16,384 existing notes across
edited items per command. The model sees at most 128 notes per item and 512 total.
Internally, `midi_create` produces `item_result:N` for later note and item-property
edits; `result:N` remains a track reference. Durations/repetition intervals use
quarter-note units (`1` quarter, `0.5` eighth, `0.25` sixteenth), independent of the
time signature's denominator. MIDI channels are 1–16 in plans.

`scripts/test_midi_live.lua` runs native integration checks on temporary tracks
in the dedicated **Jev Testing** project, then removes those tracks. It checks
creation/patterns, coloring, one-step Undo, note selection, editing/deletion,
preservation of CC/text, and rollback. It never saves the project or calls a model.

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

Native envelope commands cover normal-track volume, pan, mute, send volume, and inspected plugin
parameters that expose usable automation/value formatting. New tracks can receive
volume, pan and mute automation in the same command that creates them; the complete
operation shares one undo step and rolls back on failure. Plugin parameters still need
inspection of the actual inserted plugin. Examples:

- “Create a track called guitar, ramp its volume from −20 dB at bar 1 to 0 dB at bar 2, and alternate hard left/right pan every quarter note to bar 4.”
- “Fade Guitar from 0 dB at bar 5 to −6 dB at bar 9.”
- “Automate Clap’s send to clapverb from −6 dB at bar 5 to −12 dB at bar 9.”
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

Send-volume automation addresses one existing source-to-destination track route. It can
draw points, clear a range, or change envelope visibility/arming using the same native
operations as track automation. A fresh envelope holds the current send level until the
first requested point; it does not change the dry-track fader, return fader, static send
trim, or other sends. To lower only particular hits, specify the reduction and include
return-to-baseline boundaries; ambiguous hit timing or an unspecified reduction requires
clarification. The assistant does not detect transients inside recorded audio. Duplicate
sends to the same destination are ambiguous; hardware-send automation is unsupported.
Routing changes and track creation/duplication/folder changes must be separate requests
from send automation so the route can be checked against fresh state.

Plugin automation uses the same one-instance inspection/value-conversion path as direct
parameters. Unsupported scales stop with an explanation. Automation items, pooled items,
master/take envelopes, send pan/mute envelopes, and freehand/audio-following automation are not supported.
Chain edits on affected tracks require a separate request from envelope edits. Changes
use REAPER Undo and track-state rollback; snapshots include bounded envelope context.

Live integration checks on REAPER 7.78 cover native copy/move independence and settings,
FX-envelope transfer, volume/pan/mute points, range clearing, envelope state, modes,
ReaEQ automation, and VST3 Pro-Q 4 frequency automation (verified `2000.0 Hz`).
`scripts/test_automation_live.lua` creates/removes only its own temporary tracks and
refuses to run outside the stopped `Jev Testing.RPP` project.

`scripts/test_send_automation_live.lua` instead creates a fresh muted test tab, never
starts playback, saves only its owned fixture under `.runtime/`, closes it and restores
the original project. REAPER 7.78 verification covers alternating-hit send dips and
restoration, pre-first-point baseline preservation, unchanged faders/other sends,
snapshot selectors, range clear/state edits, duplicate-route refusal and native rollback
after an injected partial-write failure. Original project change counts are checked.

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
- **Advanced MIDI / automation / comping:** no CC/pitch-bend/MPE editing, shared/looped
  MIDI source editing, swing/humanization, automation-item work,
  master/take/send-envelope editing, take-lane comping or arbitrary moves across folder levels
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
before the corrected transcript appears or the command is interpreted, on every
transcription route. They also apply to typed commands. Direct OpenAI and local
MLX Whisper additionally receive canonical terms as vocabulary hints.
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
`whisper-1`, with an `openai/` prefix on OpenRouter. Only Talk audio submitted with
**Stop & send** or a detected stop phrase reaches the selected transcription
provider. Wake and stop-word detection always uses local `sherpa-onnx`; no idle
audio goes to a transcription provider. Silence detection avoids submitting empty
recordings. **Cancel recording** discards audio before submission. If you cancel a
submitted request during MLX transcription, the helper discards the result without
planning a command when the in-progress inference or model download returns.
Transcripts, relevant track metadata,
installed plugin names, tempo/meter/grid and timeline/item/marker/send metadata,
bounded MIDI note previews (pitch, timing, velocity, channel and selection),
track/option states, project filename,
relevant action/script names and preferences are sent to the selected command service.
OpenRouter routes requests to the serving model providers. One OpenRouter key
supports all three services. A direct OpenAI key alone works with Jev disabled;
a direct TypeSafe key alone supports the routine recipe but cannot provide
speech transcription or conversational fallback.

A local Python helper handles audio and HTTPS outside REAPER’s UI thread. Private
IPC/config files live in `Data/HeyComputer` under REAPER’s resource directory, not the
source checkout. The helper exits when the panel closes or stops heartbeating.
REAPER can pause panel updates while a native action or dialog runs. If this lasts
over 20 seconds, the helper stops and discards queued requests and audio. When the
panel resumes, it shows **Helper disconnected** instead of failing on a missing
session folder. Close and reopen HeyComputer to reconnect. Completed edits and
their receipts remain; interrupted commands are never retried automatically.
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
they do not call paid model APIs or record microphone audio. Live REAPER scripts under `scripts/` are separate, opt-in
checks; use a disposable test project and read each script's prerequisites first.

Source lives in `heycomputer/` (Python helper), `reaper/` (panel and native
executor). `tests/` contains the automated suites.
Local environments, runtime files, logs, credentials, and REAPER projects
are excluded by `.gitignore`. Keep personal configuration in
REAPER's resource directory or macOS Keychain, not in source files.

The tests use mocked providers and a REAPER API fixture. They cover the ten-track
workflow, plugin ordering, missing inputs/plugins, rollback, stale project
responses, conversational replies, custom Lua limits and theme color conversion.
Provider tests cover shared-key routing, separate endpoints, model IDs, both
audio payload formats, settings migration, Keychain separation and HTTP failures.
Extended tests cover musical navigation across tempo/signature changes, displayed
bar offsets, loop/skip commands, item and marker changes, send feedback prevention,
rollback, stale selection checks, transport, version naming and action filtering.
MIDI tests cover combined track/item/note creation, musical patterns, selected
notes, quantization, filters, source-sharing guards, bounds and rollback.
Actual model accuracy, latency, audio permissions and plugin behavior need live
verification with the user’s API keys and hardware.
Visual tests use synthetic images, mocked providers and OS adapters. They cover
window/process filtering, memory-only upload payloads, coordinate conversion,
cancelled responses, stale geometry, step limits, partial-change reporting and
project guards. macOS framework availability is checked locally; this does not
establish real capture/input behavior before macOS permissions are granted.
Wake-word tests cover shared microphone lifetime, bounded idle queues, private
log rotation, stale/replayed detections, stop-word transcript cleanup and timeout
discard without uploading. The normal unit suite uses a fake detector; optional
model checks require the installed wake-word model and a local speech fixture.

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
