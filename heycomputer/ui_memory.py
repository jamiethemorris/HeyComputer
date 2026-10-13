"""Small, editable UI hints. No screenshots, screen positions or executable steps."""
import fcntl
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

MAX_ENTRIES = 120
MAX_BYTES = 256_000


def control_anchor(scope, control, note="", anchor=""):
    # Older files may contain coordinates for Pro-Q's moving band panel.
    # Ignore those even if an older model mistakenly labelled them stable.
    if re.search(r"\bpro[- ]?q\s*4\b", scope, re.I) and not re.search(r"\b(output|global|master)\b", control, re.I):
        if re.search(r"\b(band|filter|shape|freq|frequency|gain|q|slope|stereo)\b", control + " " + note, re.I):
            return "selected-band controls panel, which moves with the selected EQ point"
    return anchor


def scope_for(frame, plugin=None):
    """Stable product/version/format identity, never track names or instance IDs."""
    name = plugin.get("name", "") if plugin else ""
    if not name:
        match = re.match(r"^((?:VST3?i?|AUi?|CLAPi?|JS): .+?) - (?:Track |Master)", frame.window.title)
        name = match.group(1) if match else ""
    if name and len(name) <= 180:
        return "plugin:" + name.strip().casefold()
    match = re.search(r" - REAPER v([\d.]+)$", frame.window.title)
    if match:
        return "reaper:main:" + match.group(1)
    return None  # Unknown dialogs must not contaminate plugin or main-window hints.


def valid_entry(entry):
    if not isinstance(entry, dict) or set(entry) not in (
            {"scope", "control", "note", "position", "updated"},
            {"scope", "control", "note", "position", "updated", "anchor"}):
        return False
    anchor = entry.get("anchor", "")
    if not isinstance(anchor, str) or len(anchor) > 180 or any(ord(c) < 32 for c in anchor):
        return False
    for key, limit in (("scope", 200), ("control", 80), ("note", 400)):
        value = entry[key]
        if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
            return False
    if not entry["scope"].startswith(("plugin:", "reaper:main:")):
        return False
    if type(entry["updated"]) not in (int, float) or not math.isfinite(entry["updated"]):
        return False
    p = entry["position"]
    if p is not None:
        if not isinstance(p, dict) or set(p) != {"x", "y", "window_width", "window_height"}:
            return False
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in p.values()):
            return False
        if not (0 <= p["x"] < 1 and 0 <= p["y"] < 1 and 0 < p["window_width"] < 20000 and 0 < p["window_height"] < 20000):
            return False
    return True


def observations(scope, frame, records):
    """Convert observed image pixels into normalized window-local hints."""
    if not scope or not isinstance(records, list):
        return []
    out = []
    for record in records[:3]:
        if not isinstance(record, dict) or set(record) not in (
                {"control", "note", "has_position", "x", "y"},
                {"control", "note", "has_position", "x", "y", "anchor"}):
            continue
        if type(record["has_position"]) is not bool:
            continue
        if not all(isinstance(record.get(k, ""), str) for k in ("control", "note", "anchor")):
            continue
        anchor = control_anchor(scope, record["control"], record["note"], record.get("anchor", ""))
        position = None
        if record["has_position"]:
            try:
                frame.point(record["x"], record["y"])
                if not anchor:
                    position = dict(x=record["x"]/frame.width, y=record["y"]/frame.height,
                                    window_width=frame.window.bounds[2], window_height=frame.window.bounds[3])
            except (ValueError, TypeError, ZeroDivisionError):
                continue
        entry = dict(scope=scope, control=record["control"], note=record["note"], anchor=anchor, position=position, updated=int(time.time()))
        if valid_entry(entry):
            out.append(entry)
    return out


class UIMemory:
    def __init__(self, path):
        self.path = Path(path)

    def _read(self):
        if not self.path.exists():
            return []
        if self.path.stat().st_size > MAX_BYTES:
            raise ValueError("UI memory is too large")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("entries"), list):
            raise ValueError("Unrecognized UI memory format")
        if len(data["entries"]) > MAX_ENTRIES or not all(valid_entry(e) for e in data["entries"]):
            raise ValueError("Invalid UI memory entries")
        return data["entries"]

    def hints(self, scope, frame):
        try:
            entries = self._read()
        except (OSError, ValueError):
            return []  # Bad/manual edits must never stop an otherwise valid task.
        out = []
        for entry in sorted(entries, key=lambda e: e["updated"], reverse=True):
            if entry["scope"] != scope:
                continue
            hint = {"control": entry["control"], "note": entry["note"]}
            anchor = control_anchor(scope, entry["control"], entry["note"], entry.get("anchor", ""))
            if anchor:
                hint["anchor"] = anchor
            p = entry["position"]
            if p and not anchor and abs(p["window_width"] - frame.window.bounds[2]) < 1 and abs(p["window_height"] - frame.window.bounds[3]) < 1:
                hint["approximate_image_point"] = [min(frame.width-1, round(p["x"] * frame.width)), min(frame.height-1, round(p["y"] * frame.height))]
            # Resizing invalidates positions, but section/label notes remain useful.
            out.append(hint)
            if len(out) == 12:
                break
        return out

    def save(self, entries, forget=()):
        """Merge successful observations; corrupt user files are never overwritten."""
        if not all(valid_entry(e) for e in entries):
            return False
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.with_suffix(".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                existing = self._read()
                forgotten = {(scope, control.casefold()) for scope, control in forget}
                merged = {(e["scope"], e["control"].casefold()): e for e in existing
                          if (e["scope"], e["control"].casefold()) not in forgotten}
                for entry in entries:
                    merged[(entry["scope"], entry["control"].casefold())] = entry
                ordered = sorted(merged.values(), key=lambda e: e["updated"], reverse=True)[:MAX_ENTRIES]
                data = json.dumps({"version": 1, "entries": ordered}, indent=2, ensure_ascii=False) + "\n"
                if len(data.encode()) > MAX_BYTES:
                    return False
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, prefix="ui-memory-", suffix=".tmp", delete=False) as f:
                    temporary = Path(f.name)
                    f.write(data)
                os.replace(temporary, self.path)
                return True
        except (OSError, ValueError):
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
