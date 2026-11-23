# HeyComputer

A dockable voice and text command panel for REAPER. Routine commands run quietly; clarifications, errors, and questions get a conversational reply.

HeyComputer is an independent project inspired by Michael Briggs's [ReaAssist](https://github.com/michaelbriggsaudio/mbriggs-reaper). It shares no code, prompts, assets, or binaries with ReaAssist and is not affiliated with it.

## What it does

- Creates and organizes tracks, folders, routing, sends, inputs, pan, and record-arming.
- Shows or hides tracks in the track panel (TCP), mixer (MCP), or both.
- Controls transport, bar jumps, loops, skip playback, items, markers, regions, and versioned saves.
- Edits exposed plugin parameters directly, and bypasses, offlines, reorders, copies, or moves plugins with their settings intact.
- Writes track, send-volume, and plugin automation (points, ramps, fades, repeating patterns).
- Creates and edits MIDI items and notes (patterns, transpose, quantize, velocity, length, delete).
- Launches installed REAPER Main actions and scripts by name.
- Optionally uses **ReaperMCP** for complex multi-step workflows that go beyond the built-in planner.
- Optionally uses macOS window vision for plugin controls that REAPER doesn't expose directly.

The panel docks on the side or bottom, follows REAPER's active theme, and supports typed commands, push-to-talk, and local "hey computer" wake-word detection. It is built for macOS first.

---

## Setup (macOS)

Requires **REAPER 7+**, **ReaImGui 0.10+** (via ReaPack), and **Python 3.11+**. Optional visual control requires **macOS 14+**.

### Option 1: Install via ReaPack

1. In **Extensions → ReaPack → Import repositories**, paste:
   ```text
   https://raw.githubusercontent.com/jamiethemorris/HeyComputer/main/index.xml
   ```
   Then open **Browse packages**, select **HeyComputer**, click **Install**, and apply.
2. Open **Actions → Show action list** and run **HeyComputer.lua**.
3. Click **Run setup** inside the panel. This downloads the Python helper and voice packages into a private `.venv` (over 1 GB on Apple Silicon because of local Whisper). Reopen the panel when prompted.

ReaPack installs files under `Scripts/HeyComputer/Assistant/` in REAPER's resource folder and leaves your local `.venv` intact during updates. If an update changes Python dependencies, open **Settings → Run setup** (or run `sh scripts/setup.sh` from that folder) and reopen the panel. If a reboot interrupts setup and it stays stuck on "running", close the panel, delete `setup.lock` and `setup.status` inside `Data/HeyComputer/` in REAPER's resource folder, and retry.

### Option 2: Install from source

Make sure `python3` is 3.11 or newer:

```sh
git clone https://github.com/jamiethemorris/HeyComputer.git
cd HeyComputer
sh scripts/setup.sh
```

Then in REAPER, open **Actions → Show action list → New action → Load ReaScript**, pick `reaper/HeyComputer.lua`, and run it. Rerun `sh scripts/setup.sh` after pulling updates.

### Connect your account and mic

1. Open **Settings → Account and model** and click **Sign in** to use your ChatGPT plan (the default on new installs).
   - On Apple Silicon Macs, speech transcription runs locally via **MLX Whisper** with no API key.
   - On Intel Macs or other setups, enter an **OpenRouter key** under **Account and model → Other providers** (stored in macOS Keychain under `HeyComputer`, or overridden by `OPENROUTER_API_KEY`).
   - Click **Save all settings**.
2. Pick your microphone and its **1-based input channel**, then save. This is separate from REAPER's track input.
3. If macOS prompts that Python wants to access `HeyComputer` in Keychain, click **Always Allow** so it doesn't ask on every launch.

On Apple Silicon, wake-word listening starts automatically on new installs. Turn it off under **Settings → Voice input** if you prefer push-to-talk.

### Recommended for multi-step workflows: enable ReaperMCP (optional)

HeyComputer's built-in mode is fast and undo-safe for everyday single-step commands, but complex multi-step requests work much better with **ReaperMCP** enabled:

1. In REAPER's Actions list, load and run the matching ReaperMCP 0.8.3 `reaper_mcp_server.lua`.
2. Make sure `uvx` is installed (or point `HEYCOMPUTER_REAPER_MCP_COMMAND` to an executable). The helper runs `uvx --python 3.13 xdarkzx-reaper-mcp==0.8.3` locally over stdio.
3. In HeyComputer **Settings**, enable **Enable REAPER MCP tools** and click **Save all settings**.

Example multi-step request:
> Create a return named Short Room, copy the reverb from Vocals to it, set the copy's decay to 800 ms, send Vocals to it at −6 dB, and automate the send to −12 dB at bar 5.

**Heads-up:** ReaperMCP runs up to 28 model turns / 8 edit calls / 120 seconds, and cancelling stops further steps without rolling back edits already sent. Try it in a test project first.

---

## Everyday use

Type a command and press **Cmd+Enter** (or click **Send**), or use **Talk** / **Stop & send** (recordings cap at 90 seconds).

Example:
> Create 10 tracks, put them in a folder, alternate hard left and right panning, put ReaComp followed by ReaEQ on each track, set the input to Guitar 1, and arm the first track.

- **Dock** moves the panel between REAPER dockers or a floating window.
- **Undo** reverts the last HeyComputer command if you haven't made other edits since; otherwise use REAPER's normal Undo.
- **Clear conversation** resets chat context.

### Common commands

| Category | Examples |
| --- | --- |
| Transport | "Play", "Stop", "Pause", "Record" (only records if a track is already armed) |
| Navigation | "Rewind 4 bars", "Forward 8 bars", "Go to bar 17", "Go to the Chorus region" |
| Loop & skip | "Loop bars 9 through 12", "Turn looping off", "Skip bars 9 through 12 during playback" |
| Markers & regions | "Add a red marker called Verse at bar 9", "Make bars 17 through 24 a blue region called Chorus" |
| Items | "Lower all items on Kick by 6 dB", "Color the vocal items in the chorus blue", "Select all items on Kick" |
| Repeat samples | "Keep the kicks going every half note to the end of the vocal track" |
| Track visibility | "Hide selected tracks", "Show all tracks", "Hide the guitar tracks in the mixer" |
| Track duplication | "Duplicate guitar track 12 twice, pan the copies hard left and right, put them in a Guitars folder, and add ReaEQ to the folder" |
| Plugin chains | "Bypass ReaEQ on Guitar", "Take ReaComp offline", "Move ReaEQ before ReaComp", "Copy ReaEQ from Guitar to Bass" |
| Plugin parameters | "Add a low shelf at 300 Hz, +3 dB on ReaEQ" |
| Automation | "Fade Guitar from 0 dB at bar 5 to −6 dB at bar 9", "Automate Clap's send to Reverb from −6 dB to −12 dB at bar 5–9" |
| MIDI | "Create a MIDI item on Drums from bar 1 to 5 and put note 36 on every quarter note", "Transpose selected notes up an octave", "Quantize selected notes to sixteenths at 50%" |
| Sends & buses | "Send the selected tracks to Reverb at −12 dB", "Make that send pre-fader", "Create a Reverb bus and send selected tracks to it" |
| Save | "Save", "Save a new version" (writes `song_v001.rpp`, `song_v002.rpp`, etc.; audio files stay shared) |
| Actions & scripts | "Normalize selected items", "Run SWS: Auto color/icon" (any installed Main-section action or registered script) |

Short exact commands ("Play", "Stop", "Record", "Save", bar jumps, "Hide selected tracks", exact named actions) run locally without calling a model. Relative bar jumps land on beat 1 of the target bar unless you ask to keep the current beat.

---

## Voice features

### Local wake and stop words
Installed automatically by `scripts/setup.sh` using [sherpa-onnx](https://k2-fsa.github.io/sherpa/onnx/kws/index.html) (~5.5 MB model under `Data/HeyComputer/models/kws-en`).

- Defaults: **"hey computer"** to start listening, **"execute"** to stop and send. Both are editable under **Settings → Voice input → Wake & stop words** (pick a stop phrase you won't accidentally say mid-command).
- Idle audio is processed locally on one CPU thread and immediately discarded. Nothing is saved or uploaded until you submit a command.
- **Pause wake word** releases the microphone when you don't want it listening (helpful around loud speakers or background speech).
- Optional **Keep a local activation log** writes event metadata (no audio) to `Data/HeyComputer/wake-activations.jsonl` (rotates around 1 MB).

### Spoken replies (macOS)
Enable under **Settings → Spoken replies** to hear questions, replies, and errors through built-in macOS voices (local, no API key). Successful command receipts stay silent, replies stay text-only while REAPER is recording, and wake detection pauses while the assistant is speaking.

### Keyboard shortcuts and toolbar buttons
Opening HeyComputer registers two Main-section actions in REAPER:
- **HeyComputer - Talk or stop and send** (opens the panel if closed, starts/stops recording, and tracks mic state on a toolbar button)
- **HeyComputer - Send** (submits typed text)

Bind them in REAPER's Actions list using **Global** scope if you want them to work while the panel is focused.

### Pronunciation and preferences
Under **Settings → Vocabulary**:
- **Pronunciation / spelling corrections** fixes transcription quirks locally before commands run (one per line, e.g. `pro cue = Pro-Q 4`, `ree eq = ReaEQ`).
- **Term definitions & preferences** tells the model your plugin and input aliases (e.g. `Pro-Q = VST3: FabFilter Pro-Q 4`, `Guitar 1 = Input 3`).

---

## Models and API providers

Settings are grouped into **Account and model**, **Voice input**, **Spoken replies**, **REAPER control**, and **Vocabulary**.

- **ChatGPT sign-in (default):** Uses your ChatGPT plan for conversation and optional visual control. Tokens live in an owner-only file under REAPER's `Data/HeyComputer`.
- **MLX Whisper (default on Apple Silicon):** Runs locally with `mlx-community/whisper-base.en-mlx` (editable). Downloads on first recording.
- **OpenRouter / Direct OpenAI / TypeSafe (optional):** Configure under **Account and model → Other providers**. OpenRouter defaults:

| Service | Model ID | Endpoint URL |
| --- | --- | --- |
| Jev (fast track-setup recipe) | `~typesafe/jev-latest` | `https://openrouter.ai/api/v1/systemone` |
| Conversation / fallback | `openai/gpt-6.1-sol` | `https://openrouter.ai/api/v1/responses` |
| Transcription | `openai/gpt-transcribe` | `https://openrouter.ai/api/v1/audio/transcriptions` |

Custom endpoints must use HTTPS (except `localhost`) and follow the same API contracts. API keys are never logged, sent as project data, or displayed after saving.

---

## Advanced & experimental features

### Direct plugin parameters vs. REAPER-only visual control (macOS 14+)
HeyComputer always tries REAPER's native parameter APIs first (read-only inspection of exposed parameters, verified readback, one undoable batch).

For plugin controls that aren't exposed natively, you can opt in to **Allow REAPER-only visual control** in Settings and grant Screen Recording + Accessibility permissions in macOS System Settings:
- Captures only the target REAPER window (via ScreenCaptureKit, max 1600 px JPEG in memory only, never saved to disk). Separately hosted plugin windows must be approved per session under **Refresh windows**.
- Press **Escape** or **Cancel request** to stop immediately. Tasks cap at 20 steps / 3 minutes.
- Visual edits aren't a single REAPER Undo block — use REAPER or the plugin's own Undo if you need to back out.
- **UI memory** (enabled by default for visual control) saves lightweight control-location notes to `Data/HeyComputer/ui-memory.json` (max 120 entries, editable/deletable anytime, no screenshots or project data).

### Reviewed custom Lua
Off by default. When enabled, generated Lua scripts pause for your manual review before running and execute with a restricted REAPER API (no file or network access).

---

## Current limits

- **No listening:** HeyComputer reads project metadata, not audio. It can't hear your mix, pick the best take, or detect chords/transients.
- **Stopped transport for most edits:** Stop and markers work while recording; track/MIDI/automation/repeat edits require stopped transport.
- **Scope limits:** Built-in plans cap at 64 operations. Native FX edits cover top-level normal track FX (no master/input/take FX or nested containers yet).
- **MIDI & automation gaps:** No CC, pitch bend, MPE, swing/humanization, automation items, or master/take/send-pan envelopes yet.
- **Routing:** Stereo channels 1/2 only, MIDI disabled on new sends, no sidechain channel maps or hardware outputs.
- **Media ops:** Doesn't render, freeze, glue, or destructively edit audio files unless you explicitly invoke a named REAPER action.

---

## Development & testing

Source lives in `heycomputer/` (Python helper) and `reaper/` (Lua panel and executor), with unit tests in `tests/` (mocked providers and REAPER fixtures — no paid API calls or mic recording). Running the Lua suites requires standalone `lua` 5.3+ and `luac`.

```sh
.venv/bin/python -m unittest discover -s tests -v
for test_file in tests/test_*.lua; do lua "$test_file" || exit 1; done
find reaper scripts tests -name '*.lua' -exec luac -p {} \;
```

- **Detailed technical reference:** Exact numeric limits, planner selectors (`result:N:K`), UI memory schema, and live REAPER test notes are archived in [docs/technical-reference.md](docs/technical-reference.md).
- **Live REAPER checks:** Opt-in scripts in `scripts/test_*_live.lua` test track layout, visibility, item repeat, MIDI, automation, and send automation against a disposable/stopped `Jev Testing` project.
- **CLI test harness:** Submit commands to an open panel without focusing its text box:
  ```sh
  .venv/bin/python scripts/test_command.py "What does hard panning mean? Do not change anything."
  ```
- **Releasing a ReaPack version:** Bump `@version` in `reaper/HeyComputer.lua` and `version` in `pyproject.toml`, update `@provides` and `index.xml` pointing at tag `vX.Y.Z`, run `python -m unittest discover -s tests -p test_reapack.py`, commit/tag/push, and verify **Run setup** from ReaPack on a clean install.

## Third-party notices

`reaper/lib/json.lua` is rxi's MIT-licensed JSON library (`reaper/lib/JSON-LICENSE.txt`). Python dependencies retain their respective licenses.
