"""Bounded action plans shared by Jev, the conversation model and REAPER."""
import math

OPS = ["create_tracks", "folder_created", "pan", "add_fx", "open_fx", "inspect_fx", "set_fx_param", "delete_fx", "bypass_fx", "offline_fx", "move_fx", "copy_fx", "input", "arm",
       "mute", "solo", "volume", "rename", "transport", "seek", "set_range", "repeat",
       "marker_add", "region_add", "marker_update", "item_name", "item_color", "item_gain",
       "automation_points", "automation_clear", "automation_state", "automation_mode",
       "track_color", "send_create", "send_set", "send_delete", "save", "run_action", "launch_action"]
ACTION_PROPERTIES = {
    "op": {"type": "string", "enum": OPS},
    "target": {"type": "string"},
    "count": {"type": "integer"},
    "value": {"type": "string"},
}
RESPONSE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "kind": {"type": "string", "enum": ["plan", "reply", "lua", "inspect"]},
        "message": {"type": "string"},
        "lua": {"type": "string"},
        "actions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": ACTION_PROPERTIES, "required": list(ACTION_PROPERTIES)}},
    }, "required": ["kind", "message", "lua", "actions"],
}


class PlanError(ValueError):
    pass


def validate_result(result, snapshot, allow_lua=False, allow_vision=False):
    if not isinstance(result, dict) or result.get("kind") not in ("plan", "reply", "lua", "vision", "inspect"):
        raise PlanError("The model did not return a valid result. No changes were made.")
    kind = result["kind"]
    if kind == "vision" and (not allow_vision or result.get("lua")):
        raise PlanError("Visual control is disabled or was combined with a script.")
    if not isinstance(result.get("message", ""), str):
        raise PlanError("Invalid reply.")
    actions = result.get("actions", [])
    if kind == "inspect":
        if result.get("lua") or not isinstance(actions, list) or not actions or not all(isinstance(a, dict) for a in actions):
            raise PlanError("Invalid plugin inspection preparation.")
        if actions[-1].get("op") != "inspect_fx" or not result.get("message", "").strip() or len(result["message"]) > 12000:
            raise PlanError("Inspection needs a remaining task and must end with inspect_fx.")
        if any(a.get("op") not in {"create_tracks", "folder_created", "pan", "add_fx", "inspect_fx", "offline_fx", "input", "arm", "mute", "solo", "volume", "rename"} for a in actions):
            raise PlanError("Inspection preparation only supports track setup.")
    if kind == "vision" and actions:
        if not isinstance(actions, list) or not all(isinstance(a, dict) for a in actions):
            raise PlanError("Invalid visual preparation.")
        if not result.get("message", "").strip() or len(result["message"]) > 12000:
            raise PlanError("A mixed request needs the remaining visual task.")
        if actions[-1].get("op") != "open_fx" or any(a.get("op") not in
                {"create_tracks", "folder_created", "pan", "add_fx", "open_fx", "input", "arm", "mute", "solo", "volume", "rename"} for a in actions):
            raise PlanError("Visual preparation must end by opening one plugin and use only track setup operations.")
    if kind not in ("plan", "inspect") and not (kind == "vision" and actions):
        if actions:
            raise PlanError("A conversational reply cannot also change the project.")
        if kind == "lua" and (not allow_lua or not isinstance(result.get("lua"), str)
                              or not result["lua"].strip() or len(result["lua"]) > 24000):
            raise PlanError("Custom Lua is disabled or the script is invalid.")
        return result
    if not isinstance(actions, list) or not 1 <= len(actions) <= 64:
        raise PlanError("A plan must contain between 1 and 64 operations.")
    tracks = {t["guid"] for t in snapshot.get("tracks", [])}
    selected = any(t.get("selected") for t in snapshot.get("tracks", []))
    plugins = {p["id"] for p in snapshot.get("plugins", [])}
    inputs = {str(i["index"]) for i in snapshot.get("inputs", [])}
    created = 0
    folder = False
    from .capabilities import validate_extended
    for index, a in enumerate(actions):
        if not isinstance(a, dict) or set(a) != set(ACTION_PROPERTIES):
            raise PlanError("Invalid action fields.")
        op, target, count, value = (a[k] for k in ACTION_PROPERTIES)
        if op not in OPS or not isinstance(target, str) or not isinstance(value, str):
            raise PlanError("Invalid operation.")
        if type(count) is not int or len(value) > 4096:
            raise PlanError("Invalid action value.")
        if op != "create_tracks" and count != 0:
            raise PlanError("Only track creation uses count.")
        if validate_extended(a, snapshot, created, index, actions):
            continue
        if op == "create_tracks":
            if created or not 1 <= count <= 64:
                raise PlanError("Create 1–64 tracks in a single batch.")
            created = count
            continue
        if op == "folder_created":
            if not created or folder:
                raise PlanError("The folder must contain a preceding batch of new tracks.")
            folder = True
            continue
        if target in ("created", "first_created"):
            if not created:
                raise PlanError("No new tracks exist for this operation.")
        elif target == "selected":
            if not selected:
                raise PlanError("Select the tracks you want to change, then try again.")
        elif target == "all":
            if not tracks:
                raise PlanError("This project has no existing tracks.")
        elif target not in tracks:
            raise PlanError("The target track could not be found.")
        if op == "add_fx" and value not in plugins:
            raise PlanError("That plugin is not in REAPER’s installed plugin list.")
        if op == "inspect_fx" and kind != "inspect":
            raise PlanError("Plugin inspection requires an inspection result.")
        if op.startswith("automation_"):
            from .automation import validate_automation
            validate_automation(a, snapshot, created)
        if op == "set_fx_param":
            from .fx_parameters import validate_parameter
            validate_parameter(a, snapshot)
        if op in ("open_fx", "inspect_fx"):
            targets = ([{}] * (created if target == "created" else 1) if target in ("created", "first_created")
                       else [t for t in snapshot.get("tracks", []) if target == t["guid"] or target == "all" or target == "selected" and t.get("selected")])
            if index != len(actions) - 1 or len(targets) != 1:
                raise PlanError("Open one plugin on one track as the last operation.")
            if value == "last_added":
                if not any(a["op"] == "add_fx" and (a["target"] == target or a["target"] == "created" and target == "first_created") for a in actions[:index]):
                    raise PlanError("No plugin was added to this target in the plan.")
            elif not any(f.get("guid") == value for f in targets[0].get("fx_instances", [])):
                raise PlanError("The exact plugin instance is unavailable.")
        if op == "input" and value not in inputs:
            raise PlanError("That audio input is unavailable.")
        if op in ("arm", "mute", "solo") and value not in ("on", "off"):
            raise PlanError("Expected on or off.")
        if op == "pan" and value not in ("left", "right", "center", "alternate_lr", "alternate_rl"):
            raise PlanError("Unsupported pan pattern.")
        if op == "volume":
            try:
                db = float(value)
            except ValueError:
                raise PlanError("Invalid volume.") from None
            if not math.isfinite(db) or not -150 <= db <= 12:
                raise PlanError("Volume must be between −150 and +12 dB.")
    from .fx_chain import validate_chain
    validate_chain(actions, snapshot)
    from .automation import validate_combinations
    validate_combinations(actions)
    return result


def action(op, target="created", value="", count=0):
    return {"op": op, "target": target, "count": count, "value": value}
