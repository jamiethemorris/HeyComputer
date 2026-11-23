# HeyComputer / ReaAssist capability audit

Date: 2026-11-02. Task authority: `heycomputer/t_f5fa2c21`.

## Scope and evidence

Compared the current uncommitted HeyComputer worktree with ReaAssist v1.6.4 source at `975a9863a4793aea85a86bd0dd2a63ca1fd70d6c`. ReaAssist was cloned and read, not installed or executed. Its helper is shipped as binaries; this audit does not claim inspection of their internals. Source-backed mechanisms are not proof that a particular natural-language request will succeed. No application source, settings, live project, services or Git history were changed by this audit.

## Why the shorter-reverb request failed

The earlier command automated an **existing** clap-to-reverb send and the screenshot reports success. The new command asks to create a separate return/send, reuse the reverb with shorter decay, and automate the opposite claps. Those primitives largely exist; their composition is incomplete.

Confirmed local source findings:

1. `heycomputer/protocol.py:42–48` emits the exact screenshot error if an inspection preparation includes an operation outside its allowlist. Parent reproduced the exact error for `copy_fx`, `set_fx_param`, and `automation_points` before `inspect_fx`. This happens before native execution. The retained activation log records the request/error but not the model's raw plan, so the actual offending operation is unknown.
2. **The earlier preparation repair missed the panel boundary.** Python permits `send_create`/`send_set`, but `reaper/HeyComputer.lua:176–186,200–210` still excludes them. Even a Python-accepted send preparation can fail downstream. Earlier validator/executor tests did not exercise this boundary. Card `t_671b4ec5` retains this correction and its review gate.
3. Copying an FX with its settings exists (`heycomputer/fx_chain.py:16–59`; `reaper/lib/fx_chain.lua:93–121`), but immediately inspecting that copy is unsupported. `last_added` refers only to `add_fx`; the executor discards the new identity returned by `Chain.apply()` (`reaper/lib/executor.lua:266–295`). Planner guidance explicitly prohibits targeting a new copy in the same batch (`heycomputer/fx_chain.py:83`).
4. Send automation requires an existing unique route and a fresh snapshot after routing changes (`heycomputer/automation.py:25–36,74–88`). That safety boundary is reasonable; making the user issue separate commands to cross it is an orchestration limitation.
5. Only one inspection continuation exists. It passes remaining-task text with empty conversation history and rejects another inspection (`heycomputer/worker.py:284–314`). Ordinary native plans execute and finish (`reaper/HeyComputer.lua:250`); there is no general execute → refresh → continue mechanism. Setup can remain committed if a later phase fails.

**Conclusion:** another send feature or another allowlist exception alone will not solve the class of failure. The supported operations need reliable multi-stage execution.

## Capability comparison

“Broader via scripts” means ReaAssist can generate code using a broader host API; it does not mean every workflow has a dedicated tested implementation.

| Area | ReaAssist evidence | HeyComputer today / gap |
|---|---|---|
| Tracks, folders, sends | Native typed track creation/resolution/settings, folder organization and send creation; broader edits via Lua (`CodeRuntime.lua:2376–2601`).[2] | Native track creation/duplication/folders, settings/visibility, send create/set/delete. Not missing basic sends. |
| Plugin chain and parameters | Generated Lua, installed-plugin resolution, live parameter inspection and maintained product guides.[1][5] | Native insert/copy/move/delete/bypass/offline and inspected displayed-value writes. Missing reliable copy→inspect→adjust composition; normal track FX only. |
| Automation | General script execution can use envelope APIs; not a separate comprehensive typed-envelope implementation in the inspected executor.[2] | Dedicated track volume/pan/mute, exposed FX-parameter and send-volume envelopes. Master/take envelopes, send pan/mute and automation items remain outside the native path. |
| General REAPER editing | Broader generated-Lua runtime with validation, restricted environment, Undo and error/result capture (`CodeRuntime.lua:23837–24013,25244–25314`).[2] | Optional manually reviewed Lua exists, but its API is very small: existing-track/FX operations, no general item/MIDI/envelope/routing API. Most other work requires dedicated operations or named installed actions. |
| Repair and context gathering | Bounded validator retries, fresh snapshots/context retrieval, and execution-error context for a user follow-up (`ReaAssist.lua:45065–45144,49173–49189`). These are not a guarantee of automatic recovery from partially executed edits.[8] | Native planner validation normally surfaces an error immediately; no general repair/replan loop. Visual recovery is a separate bounded facility, not native planning recovery. |
| Project understanding | Targeted selected/per-track/project item and send readers (`Context.lua:3988–4305`), scoped FX inspection and reference retrieval.[4] | Rich snapshot already includes tracks/items/routing/tempo/meter/envelopes. Missing flexible repeated on-demand read steps, not basic project awareness. |
| Plugin-specific knowledge | Maintained mappings/recipes for stock and selected third-party plugins; live labels override curated tables (`Plugin_Pack.md:18–44`). Cached installed-plugin inspection does not instantiate a temporary plugin on a cache miss (`Context.lua:8373–8389`).[4][5] | Live formatter-based conversion, aliases/preferences and optional visual fallback. No comparable maintained product-specific reference pack found. |
| MIDI and audio editing | Generated ReaScript with MIDI reference retrieval and note-change evidence; drum/Dynamic Split supporting helpers. This is broader scripting, not proof of automatic musical understanding.[2][4] | Native MIDI notes, quantization and patterns; audio-item properties, targeting/repetition and named actions. CC/pitch-bend, comping and other advanced edits lack native coverage. |
| Custom JSFX effects | Explicit generated-effect Save/Save As/Add-to-selected-tracks/Undo workflow (`UI.lua:23816–23926`). Delivery is implemented; DSP quality is not established by that fact.[6] | No custom-JSFX creation/save/install workflow found. Loading an already installed effect is different. |
| Attachments | Images, PDFs and text/source files with limits and binary rejection (`ReaAssist.lua:17996–18058,18213–18215,19000–19074`). Not audio-file listening.[8] | Voice capture and visual screenshots exist; no equivalent general chat-attachment ingestion found. |
| Accessibility and model controls | Dedicated screen-reader interface documented; provider/model/reasoning and Ask/Auto-Run UI, usage/retry detail (`UI.lua:4223–4351`).[1][6] | Voice-first controls, wake word, spoken replies and experimental remote/vision; fewer provider controls, low reasoning hard-coded for native planning. Do not change the chosen model/settings merely to conceal an orchestration bug. |
| History and preferences | Persistent settings/FX metadata, sliding conversation context and chat export. Resumable saved-chat storage was not established in the targeted audit.[6][8] | In-memory conversation plus saved preferences/UI memory. Do not call persistent chat history a confirmed ReaAssist-only feature. |

HeyComputer source anchors: `heycomputer/protocol.py:4–9`; `heycomputer/providers.py:151–203`; `heycomputer/settings.py:6–26`; `heycomputer/automation.py:91–96`; `heycomputer/fx_chain.py:80–84`; `reaper/lib/executor.lua:11–16,359–387`; README sections **Models and execution**, **MIDI items and notes**, **Direct plugin parameters**, **Current limits**. Searches across Python/Lua source found no general attachment, JSFX-authoring or screen-reader workflow.

Documentation caveat: README's Current limits paragraph still says send-envelope editing is absent, while the newer Automation section and actual code implement send **volume**. The broad old statement is stale; send pan/mute remain unsupported.

## Recommended direction—not yet implemented or authorized as a feature batch

### First: make existing capabilities compose

Extend the existing continuation mechanism rather than replace the application with an unrestricted agent:

- Keep the original request, relevant conversation, completed execution receipts and remaining work together.
- Execute one validated batch; read actual created/copied identities and a fresh snapshot; continue automatically across necessary boundaries.
- For this incident: create return and copy reverb → create/verify route → inspect copied instance → adjust decay and draw odd-clap send automation.
- Unify phase-permission policy with conformance tests across Python, panel and native execution. Keep independent validation at the execution boundary.
- Add bounded **pre-execution** repair using the actual validation error. Do not blindly retry after partial writes or repeat setup.
- Preserve project/revision checks, exact route/FX identity, cancellation, recording safeguards, finite step/call budgets, per-batch rollback and honest partial-completion reporting.

### Acceptance should test user workflows, not only primitives

Exercise the full panel→worker→planner→validator→executor→fresh-snapshot path, including new delay return + wet/time settings, copy reverb + shorter decay + opposite-clap automation, multiple FX inspections, invalid-plan repair without duplicate setup, cancellation between phases, and project changes during a request. Fixture/model-response tests establish plumbing; isolated host tests establish native effects; live-model trials separately assess interpretation. No test result should be presented as universal plugin or natural-language compatibility.

### Then: decide which broader capabilities matter

A carefully expanded reviewed-Lua route could reduce one-feature-at-a-time development for uncommon edits, but it requires scoped API access, verification and undo—not simply enabling unrestricted code. Plugin-specific reference knowledge is useful for complex controls. Advanced routing/MIDI/envelopes, JSFX authoring, attachments and accessibility are distinct feature choices, not prerequisites for fixing this clap workflow.

ReaAssist demonstrates a broader approach, not proof it would complete this exact request correctly. Its source license does not permit repackaging as our own; use the comparison to guide an independent implementation, not copy its code.[7]

## Tracking

This report is an evidence snapshot, not a parallel task list. Audit completion and any subsequently approved implementation belong on the `heycomputer` Kanban board. No implementation or live trial was performed during this comparison.

## Sources

[1] https://github.com/michaelbriggsaudio/mbriggs-reaper/blob/975a9863a4793aea85a86bd0dd2a63ca1fd70d6c/ReaAssist/App/README.md
[2] https://github.com/michaelbriggsaudio/mbriggs-reaper/blob/975a9863a4793aea85a86bd0dd2a63ca1fd70d6c/ReaAssist/App/Resources/CodeRuntime.lua
[4] https://github.com/michaelbriggsaudio/mbriggs-reaper/blob/975a9863a4793aea85a86bd0dd2a63ca1fd70d6c/ReaAssist/App/Resources/Context.lua
[5] https://github.com/michaelbriggsaudio/mbriggs-reaper/blob/975a9863a4793aea85a86bd0dd2a63ca1fd70d6c/ReaAssist/App/Resources/Plugin_Pack.md
[6] https://github.com/michaelbriggsaudio/mbriggs-reaper/blob/975a9863a4793aea85a86bd0dd2a63ca1fd70d6c/ReaAssist/App/Resources/UI.lua
[7] https://github.com/michaelbriggsaudio/mbriggs-reaper/blob/975a9863a4793aea85a86bd0dd2a63ca1fd70d6c/ReaAssist/App/LICENSE.txt
[8] https://github.com/michaelbriggsaudio/mbriggs-reaper/blob/975a9863a4793aea85a86bd0dd2a63ca1fd70d6c/ReaAssist/App/ReaAssist.lua
