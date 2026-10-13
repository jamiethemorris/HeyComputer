"""Validation and model instructions for timeline, item, routing and action work."""
import json
import math
import re
from .protocol import PlanError, action

EXTENDED = {"transport", "seek", "set_range", "repeat", "marker_add", "region_add", "marker_update",
            "item_name", "item_color", "item_gain", "track_color", "send_create", "send_set",
            "send_delete", "save", "run_action", "launch_action"}


def require(ok, message="Invalid command parameters."):
    if not ok:
        raise PlanError(message)


def number(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def color(value):
    return isinstance(value, str) and (value == "default" or re.fullmatch(r"#[0-9a-fA-F]{6}", value))


def params(value, allowed, required=()):
    try:
        data = json.loads(value)
    except ValueError:
        raise PlanError("Invalid command parameters.") from None
    require(isinstance(data, dict) and set(required) <= set(data) <= set(allowed))
    return data


def position(p, snapshot):
    require(isinstance(p, dict))
    if "bar" in p:
        require(set(p) <= {"bar", "beat"} and type(p["bar"]) is int and -10000 <= p["bar"] <= 100000)
        require(number(p.get("beat", 1), 1, 128))
    elif "seconds" in p:
        require(set(p) == {"seconds"} and number(p["seconds"], 0, 604800))
    else:
        require(set(p) == {"anchor"})
        known = {"cursor", "selection_start", "selection_end", "loop_start", "loop_end"}
        known |= {m["key"] + suffix for m in snapshot.get("markers", []) for suffix in ("", ":end")}
        require(p["anchor"] in known, "That timeline position is unavailable.")


def tracks(target, snapshot, created):
    if target in ("created", "first_created"):
        require(created > 0, "Create the destination/source tracks first.")
    elif target == "selected":
        require(any(t.get("selected") for t in snapshot.get("tracks", [])), "Select tracks first.")
    elif target == "all":
        require(bool(snapshot.get("tracks")), "There are no existing tracks.")
    else:
        require(target in {t["guid"] for t in snapshot.get("tracks", [])}, "Track unavailable.")


def validate_extended(a, snapshot, created, index, actions):
    op, target, value = a["op"], a["target"], a["value"]
    if op not in EXTENDED:
        return False
    require("timeline" in snapshot, "Reopen HeyComputer to enable the new command set.")
    if op in {"transport", "save", "run_action", "launch_action"}:
        require(index == len(actions)-1, "Transport, saving, or a native action must be the last step.")
    if op == "run_action":
        require(len(actions) == 1, "Run a native action as a separate command.")
        require(value in {str(c["id"]) for c in snapshot.get("actions", [])}, "That action is not in the supported action list.")
    if op == "launch_action":
        require(len(actions) == 1, "Launch an installed action or script as a separate command.")
        require(value in {c["id"] for c in snapshot.get("launch_actions", [])}, "That action or script is not installed in REAPER's Main action list.")
    if op in {"transport", "seek", "set_range", "repeat", "marker_add", "region_add", "save", "run_action", "launch_action"}:
        require(target == "project")
    if op == "transport":
        require(value in {"play", "record", "stop", "pause", "skip_selection"})
    elif op == "save":
        require(value in {"current", "new_version"})
    elif op == "repeat":
        require(value in {"on", "off"})
    elif op == "seek":
        p = params(value, {"position", "bars", "seconds", "preserve_beat"})
        require(sum(k in p for k in ("position", "bars", "seconds")) == 1)
        require(type(p.get("preserve_beat", False)) is bool)
        if "position" in p: position(p["position"], snapshot)
        if "bars" in p: require(type(p["bars"]) is int and -10000 <= p["bars"] <= 10000)
        if "seconds" in p: require(number(p["seconds"], -604800, 604800))
    elif op in {"set_range", "region_add", "marker_add", "marker_update"}:
        fields = {"set_range": ({"start", "end", "loop"}, {"start", "end", "loop"}),
                  "region_add": ({"start", "end", "name", "color"}, {"start", "end", "name", "color"}),
                  "marker_add": ({"at", "name", "color"}, {"at", "name", "color"}),
                  "marker_update": ({"name", "color"}, set())}
        p = params(value, *fields[op]); require(bool(p))
        for k in ("start", "end", "at"):
            if k in p: position(p[k], snapshot)
        if "name" in p: require(isinstance(p["name"], str) and len(p["name"]) <= 512)
        if "color" in p: require(color(p["color"]))
        if "loop" in p: require(type(p["loop"]) is bool)
        if op == "marker_update": require(target in {m["key"] for m in snapshot.get("markers", [])}, "Marker or region unavailable.")
    elif op.startswith("item_"):
        items = snapshot.get("items", [])
        if target == "selected": require(any(i["selected"] for i in items), "Select the items first.")
        elif target == "all": require(bool(items) and not snapshot.get("items_truncated"), "Too many items; select a smaller set.")
        else: require(target in {i["guid"] for i in items}, "Item unavailable.")
        if op == "item_name": require(bool(value) and len(value) <= 512)
        if op == "item_color": require(color(value))
        if op == "item_gain":
            p = params(value, {"db", "relative"}, {"db", "relative"})
            require(number(p["db"], -150, 24) and type(p["relative"]) is bool)
    elif op == "track_color":
        tracks(target, snapshot, created); require(color(value))
    elif op.startswith("send_"):
        tracks(target, snapshot, created)
        p = params(value, {"destination", "volume_db", "pan", "mode", "mute"}, {"destination"})
        dest = p["destination"]
        require(isinstance(dest, str) and dest not in {"selected", "all", "created"}, "A send needs one destination track.")
        tracks(dest, snapshot, created)
        if "volume_db" in p: require(number(p["volume_db"], -150, 12))
        if "pan" in p: require(number(p["pan"], -1, 1))
        if "mode" in p: require(p["mode"] in {"post_fader", "post_fx", "pre_fx"})
        if "mute" in p: require(type(p["mute"]) is bool)
        if op == "send_delete": require(set(p) == {"destination"})
        if op == "send_set": require(len(p) > 1)
    return True


HELP = """
Project context: timing contains effective tempo_bpm, numerator/denominator, displayed musical/bar/beat positions at project start, cursor, selection and loop endpoints, plus tempo_markers with linear_to_next and tempo_map_complete. Bar beats follow the denominator: one quarter note is denominator/4 beats (two beats in /8); use bar/beat positions so REAPER resolves tempo ramps exactly. Never guess seconds from a single BPM. If the map is incomplete, do not extrapolate a missing range. Track number is 1-based; volume/gain are linear amplitudes, solo is REAPER's mode integer (0=off), folder_parent is structural, parent is the enabled folder routing edge. Item start/end_musical, fades, muted/locked and take_type describe existing items. Read these fields before requesting context.
Extended operations below use target=project unless specified. count=0. For operations with several parameters, value is a JSON-encoded object string. Never invent actions or omit part of a request.
Timeline positions are objects: {"bar":9,"beat":1} (displayed bar/beat numbers; beat defaults to 1), {"seconds":12.5}, or {"anchor":"cursor"|"selection_start"|"selection_end"|"loop_start"|"loop_end"|exact marker key|region key|region key + ":end"}. Use actual snapshot markers; clarify duplicate names. Ranges are start-inclusive/end-exclusive: loop bars 9 through 12 means start bar 9, end bar 13. 'Between bars 9 and 13' ends at bar 13. Never infer a range when missing.
transport value play/record/stop/pause/skip_selection. skip_selection uses REAPER's native Play (skip time selection), starts playback and skips the time selection. 'Skip this area' means that playback behavior; set_range first if given a range, using loop=false. If 'cycle area' is requested for skipping use loop_start/loop_end anchors. Does not remove audio. Normal play exits skipping. Do not start recording unless explicitly requested.
seek value {"bars":-4,"preserve_beat":false} moves relative to the current play position when playing, edit cursor otherwise, landing at the START of the destination bar by default. Preserve within-bar beat only if explicitly requested. Or {"seconds":-3} for relative seconds, or {"position":{"bar":17}} for absolute positions. Bar movement respects tempo and time-signature changes and displayed bar offset.
set_range value {"start":position,"end":position,"loop":true/false}. Always sets time selection; loop=true also sets loop points and enables repeat. loop=false disables repeat for the selected area. repeat value on/off.
marker_add value {"at":position,"name":"Verse","color":"#RRGGBB" or "default"}. region_add has start,end,name,color. marker_update target=exact snapshot marker/region key, value contains name and/or color, preserves position and omitted properties. Use sensible standard RGB colors for ordinary named colors; don't ask for hex values unnecessarily.
item_name target selected/all/exact item GUID, value nonempty literal name; edits active take name (empty items use notes). item_color same targets, value #RRGGBB or default. item_gain same targets, value {"db":-6,"relative":true}; relative=false sets absolute item gain. track_color target track selectors/GUID, value #RRGGBB or default.
send_create/send_set/send_delete target SOURCE track selectors/GUID, value {"destination":exact track GUID or first_created,"volume_db":-12,"pan":0,"mode":"post_fader"|"post_fx"|"pre_fx","mute":false}. Optional properties are preserved for send_set; send_create defaults to stereo 1/2, post-fader, unity, centered, unmuted with MIDI disabled. send_delete takes destination only. No self/feedback sends, no hardware outputs or channel remapping. Multiple existing sends to the same destination require clarification. Use create_tracks + send_create for a new bus, first_created as destination. Never disable source master send unless requested; that feature isn't supported yet.
save value current/new_version. New versions use the next unused _vNNN.rpp filename beside the current project and switch to it. Unsaved projects open the native Save As dialog and require the user to finish it. Saving and transport must be the LAST operation; only one terminal operation per plan.
run_action value an exact id STRING from project.actions. Run this alone, not mixed with other operations. These are installed, supported Main-section actions only; use them for splitting/duplicating/trimming/normalizing items, selection, zoom, snapping, grid, metronome, etc. Respect each action's requires field and current selection; never guess an action number. Prefer structured operations above. Actions needing dialogs cannot fill them automatically. If no suitable supported action exists, explain the limitation rather than improvising a script outside lua_api.
launch_action value is an exact id STRING from project.launch_actions, target=project. This catalog includes installed Main actions, registered scripts, custom actions and extension actions. Use ONLY when the user explicitly requests running/opening a named action, tool or script (e.g. 'open ReaSplice'). Return one launch_action by itself; never combine with edits or infer an arbitrary script from a general editing goal. Prefer the exact main tool over similarly named reload/update/uninstall/helper scripts; clarify ambiguous matches. Term definitions/preferences can associate a spoken tool name with its installed action. Running an installed script does not require enabling reviewed custom Lua. Never invent a command ID or load a script from a file path. Say the action ran, not that its internal work or window opening was verified. A launcher starts a tool; it does not grant sample-browser search or control of the tool's internals. If the user also requests unsupported work inside the tool, explain that limitation before doing a partial launch.
Jev's routine recipe remains limited to track setup. These extended commands are executed by native code from validated plans, not generated Lua. The assistant has no audio listening/analysis, sample browser access, arbitrary plugin GUI/preset access or filesystem browsing. Only scripts/custom actions already registered in REAPER's Main action list can be launched by name.
"""


def tool_name(name):
    name = re.sub(r"^(?:(?:script|custom):\s*)+", "", name, flags=re.I)
    name = re.sub(r"\.(?:lua|eel|py)$", "", name, flags=re.I)
    return "".join(c for c in name.casefold() if c.isalnum())


def quick_plan(text, snapshot):
    """Exact, standalone everyday commands need no model roundtrip."""
    if "timeline" not in snapshot:
        return None
    normalized = text.strip().lower().rstrip(".! ")
    launch = re.fullmatch(r"(?:please\s+)?(?:open|launch|run|show)\s+(?:the\s+)?(.+?)(?:\s+please)?", normalized)
    if launch:
        requested = re.sub(r"\s+(?:lua\s+script|script|action|tool)$", "", launch[1], flags=re.I)
        # Exact names after harmless spelling/spacing normalization only. A
        # compound command or near match goes to the model for interpretation.
        matches = [a for a in snapshot.get("launch_actions", []) if tool_name(a["name"]) == tool_name(requested)]
        if len(matches) == 1:
            return {"kind": "plan", "actions": [action("launch_action", "project", matches[0]["id"])],
                    "message": "", "lua": "", "model": "Direct command"}
    commands = {"play": ("transport", "play"), "start playback": ("transport", "play"),
                "stop": ("transport", "stop"), "stop playback": ("transport", "stop"),
                "stop recording": ("transport", "stop"), "record": ("transport", "record"),
                "start recording": ("transport", "record"), "pause": ("transport", "pause"),
                "save": ("save", "current"), "save project": ("save", "current"),
                "save a new version": ("save", "new_version"), "save new version": ("save", "new_version"),
                "loop off": ("repeat", "off"), "loop on": ("repeat", "on")}
    selected = commands.get(normalized)
    match = re.fullmatch(r"(rewind|go back|back|forward|go forward|fast forward) (\d+) bars?", normalized)
    if match:
        sign = -1 if match[1] in ("rewind", "go back", "back") else 1
        selected = ("seek", json.dumps({"bars": int(match[2])*sign, "preserve_beat": False}))
    if selected:
        return {"kind": "plan", "actions": [action(selected[0], "project", selected[1])], "message": "", "lua": "", "model": "Direct command"}


def action_candidates(text, actions):
    words = set(re.findall(r"[a-z]+", text.lower())) - {"a", "the", "it", "to", "and", "of", "on", "in"}
    aliases = {"cut": "split", "copy": "duplicate", "louder": "volume", "quiet": "volume", "click": "metronome", "colour": "color"}
    words |= {aliases[w] for w in list(words) if w in aliases}
    generic = {"script", "lua", "action", "tool", "open", "run", "show", "launch", "please", "custom"}
    words -= generic
    compact = tool_name(text)
    ranked = [(len(words & set(re.findall(r"[a-z]+", a["name"].lower()))) +
               (4 if tool_name(a["name"]) and tool_name(a["name"]) in compact else 0), a) for a in actions]
    return [a for score, a in sorted(ranked, key=lambda pair: -pair[0]) if score][:64]
