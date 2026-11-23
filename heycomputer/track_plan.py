"""Resolve track results in plan order; simulate folder structure before editing."""
import copy
import json
import re

from .protocol import PlanError

OPS = {"create_tracks", "duplicate_tracks", "folder_created", "folder_tracks"}


def check(ok, message):
    if not ok:
        raise PlanError(message)


class TrackPlan:
    def __init__(self, actions, snapshot):
        self.original = {t["guid"]: dict(t) for t in snapshot.get("tracks", [])}
        self.tracks = dict(self.original)
        self.results, self.created_before, self.layouts = {}, {}, {}
        self.order = list(self.original)
        self.parents = {}
        stack = []
        self.balanced = True
        for t in self.original.values():
            self.parents[t["guid"]] = stack[-1] if stack else None
            depth = t.get("folder_depth", 0)
            if type(depth) not in (int, float) or int(depth) != depth or depth > 1:
                self.balanced = False
                continue
            if depth == 1:
                stack.append(t["guid"])
            elif depth < 0:
                if -depth > len(stack): self.balanced = False
                del stack[max(0, len(stack) + int(depth)):]
        self.balanced = self.balanced and not stack
        created, total = [], 0
        for index, a in enumerate(actions, 1):
            self.created_before[index] = list(created)
            if a["op"] not in OPS:
                continue
            check(self.balanced, "The existing folder layout is unbalanced. Fix its folder boundaries first.")
            output = []

            def new(source=None):
                nonlocal total
                total += 1
                check(total <= 64, "Create or duplicate at most 64 tracks per plan, including folder parents and children.")
                guid = f"@track:{index}:{len(output) + 1}"
                t = copy.deepcopy(self.tracks[source]) if source else {}
                t.update(guid=guid, selected=False, planned=True, fx_instances=[])
                self.tracks[guid] = t
                output.append(guid)
                return guid

            if a["op"] == "create_tracks":
                check(1 <= a["count"] <= 64, "Create 1–64 tracks per operation.")
                for _ in range(a["count"]):
                    guid = new()
                    self.parents[guid] = None
                    self.order.append(guid)
                created = list(output)
            elif a["op"] == "duplicate_tracks":
                check(1 <= a["count"] <= 64, "Request 1–64 copies per source track.")
                sources = self.roots([t["guid"] for t in self.resolve(a["target"], index)])
                for source in sources:
                    branch = self.branch(source)
                    insertion = self.order.index(branch[-1]) + 1
                    for _ in range(a["count"]):
                        mapping = {key: new(key) for key in branch}
                        for key in branch:
                            self.parents[mapping[key]] = mapping.get(self.parents[key], self.parents[key])
                        clones = [mapping[key] for key in branch]
                        self.order[insertion:insertion] = clones
                        insertion += len(clones)
                # Result indices cover every new track in project order, including folder children.
            else:
                if a["op"] == "folder_created":
                    members = list(created)
                    check(bool(members), "Create the child tracks before their folder.")
                else:
                    try: params = json.loads(a["value"])
                    except ValueError: raise PlanError("Invalid folder parameters.") from None
                    check(isinstance(params, dict) and set(params) == {"tracks", "name"}
                          and isinstance(params["name"], str) and len(params["name"]) <= 256
                          and isinstance(params["tracks"], list) and 1 <= len(params["tracks"]) <= 64,
                          "A folder needs a name and a list of track references.")
                    members = [t["guid"] for ref in params["tracks"] for t in self.resolve(ref, index)]
                members = self.roots(members)
                check(bool(members), "Choose tracks for the folder.")
                parent = self.parents[members[0]]
                check(all(self.parents[key] == parent for key in members),
                      "Folder members must share the same immediate parent; choose tracks at one folder level.")
                branches = [key for member in members for key in self.branch(member)]
                insertion = self.order.index(members[0])
                guid = new()
                self.order = [key for key in self.order if key not in branches]
                self.order[insertion:insertion] = [guid] + branches
                self.parents[guid] = parent
                for member in members: self.parents[member] = guid
            self.results[index] = list(output)
            self.layouts[index] = (list(self.order), dict(self.parents))

    def branch(self, root):
        return [key for key in self.order if key == root or self.descendant(key, root)]

    def descendant(self, key, ancestor):
        while self.parents.get(key):
            key = self.parents[key]
            if key == ancestor: return True
        return False

    def roots(self, keys):
        wanted = set(keys)
        return [key for key in self.order if key in wanted and
                not any(self.descendant(key, other) for other in wanted if other != key)]

    def resolve(self, selector, index):
        check(isinstance(selector, str), "Invalid track reference.")
        if selector in ("created", "first_created"):
            keys = self.created_before.get(index, [])
            if selector == "first_created": keys = keys[:1]
        elif selector in ("selected", "all"):
            keys = [k for k, t in self.original.items() if selector == "all" or t.get("selected")]
        elif selector in self.original:
            keys = [selector]
        else:
            match = re.fullmatch(r"result:([1-9]\d*)(?::([1-9]\d*))?", selector)
            check(match and int(match[1]) < index, "Track references must point to an earlier track-producing step.")
            keys = self.results.get(int(match[1]), [])
            if match[2]: keys = keys[int(match[2])-1:int(match[2])]
        check(bool(keys), "No tracks match that reference; create or identify them first.")
        return [self.tracks[k] for k in keys]


HELP = """
Track workflows are composable native operations. create_tracks, duplicate_tracks, folder_tracks and folder_created each produce track results. target result:N means all tracks produced by earlier action N (1-based action position); result:N:K means its Kth track. References MUST point backward to a track-producing step. They work with pan, rename, color, volume, mute/solo, input, arm, add_fx/open_fx/inspect_fx, built-in automation, sends, and plugin-transfer destinations. Never guess new GUIDs or renumbered track numbers. created/first_created remain aliases for the most recent create_tracks batch; selected/all always refer to the ORIGINAL snapshot. Multiple creation/duplication/folder steps are allowed, up to 64 newly added tracks total including folders and descendants. References to new FX identities still require inspection; use last_added for a newly inserted FX on a resolved track.
duplicate_tracks target=existing track GUID, selected/all, or earlier result; count=number of copies PER source (1..64); value=optional base name for the copies, empty to retain source names. Uses REAPER's native full track duplication, preserving items, FX settings, envelopes and routing with new native identities. Copies follow each source. Selecting a folder includes its entire branch once; results include copied parents AND descendants in project order, source by source then copy by copy. Do not duplicate parent and child separately. folder_tracks target=project, count=0, value={"tracks":["exact original GUID","result:1"],"name":"Guitars"} wraps those tracks/complete folder branches in a NEW parent. Members must share an immediate parent; nonadjacent siblings are gathered at the first member in project order. Existing nested folders remain intact. folder_created is shorthand for wrapping the last create_tracks batch; its result is also the new parent. New folders have no FX until added and no automatic input/arm changes.
Example duplicate guitar twice, pan copies, group original and copies, EQ folder: 1 duplicate_tracks(GUITAR_GUID,count=2,value=""), 2 pan(result:1,alternate_lr), 3 folder_tracks(project,value={"tracks":[GUITAR_GUID,"result:1"],"name":"Guitars"}), 4 add_fx(result:3,EXACT_PLUGIN_ID). A single copy can be renamed with result:1:1. A new bus can receive sends via its result reference. Track numbers identify ORIGINAL snapshot tracks only; later operations use stable references. Do not ask permission for implementation steps or break a complete supported request into partial plans. These capabilities belong to HeyComputer's native executor; lack of a custom-Lua API is irrelevant when native operations cover the request. If any part truly remains unsupported, identify that specific missing operation without claiming REAPER itself cannot do it.
"""
