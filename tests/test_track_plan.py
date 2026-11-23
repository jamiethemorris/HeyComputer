import copy
import json
import unittest
from unittest.mock import patch

from heycomputer.protocol import action, validate_result, PlanError
from heycomputer.track_plan import TrackPlan
from heycomputer import providers
from heycomputer.settings import DEFAULTS


def folder(*refs, name="Guitars"):
    return action("folder_tracks", "project", json.dumps(dict(tracks=list(refs), name=name)))


class TrackPlanTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {"tracks": [{"guid": "guitar", "name": "Guitar", "selected": True,
                                    "folder_depth": 0, "fx_instances": [{"guid": "fx", "name": "EQ"}]}],
                         "plugins": [{"id": "Pro-Q"}], "inputs": [], "timeline": {"play_state": 0}}

    def validate(self, actions, **extra):
        return validate_result(dict(kind="plan", actions=actions, message="", lua="", **extra), self.snapshot)

    def test_requested_duplicate_pan_folder_and_eq_workflow(self):
        actions = [action("duplicate_tracks", "guitar", count=2), action("pan", "result:1", "alternate_lr"),
                   folder("guitar", "result:1"), action("add_fx", "result:3", "Pro-Q")]
        self.validate(actions)
        plan = TrackPlan(actions, self.snapshot)
        self.assertEqual(len(plan.resolve("result:1", 3)), 2)
        order, parents = plan.layouts[3]
        parent = plan.resolve("result:3", 4)[0]["guid"]
        self.assertEqual(order[0], parent)
        self.assertTrue(all(parents[k] == parent for k in order[1:]))

    def test_multiple_batches_can_be_targeted_individually_and_combined(self):
        actions = [action("create_tracks", value="Guitar", count=2),
                   action("create_tracks", value="Bus", count=1),
                   action("rename", "result:1:2", "Guitar R"),
                   action("send_create", "result:1", json.dumps({"destination": "result:2"})),
                   folder("result:1", "result:2"), action("add_fx", "result:5", "Pro-Q"),
                   action("open_fx", "result:5:1", "last_added")]
        self.validate(actions)

    def test_copy_plugin_to_folder_and_automate_duplicate(self):
        self.validate([folder("guitar"), action("copy_fx", "guitar", json.dumps(
            dict(fx="fx", destination="result:1", position="last", anchor="")))])
        self.validate([action("duplicate_tracks", "guitar", count=1),
                       action("automation_points", "result:1", json.dumps({"envelope": {"kind": "volume"},
                           "points": [{"at": {"seconds": 0}, "value": "-6 dB", "shape": "linear"}], "replace": True}))])

    def test_forward_self_missing_and_non_track_results_are_rejected(self):
        cases = [[action("pan", "result:2", "left"), action("create_tracks", count=1)],
                 [action("duplicate_tracks", "result:1", count=1)],
                 [action("create_tracks", count=1), action("pan", "result:1:2", "left")],
                 [action("pan", "guitar", "left"), action("rename", "result:1", "Bad")],
                 [action("create_tracks", count=2), action("send_create", "guitar", json.dumps({"destination": "result:1"}))],
                 [action("create_tracks", count=1), action("pan", "@track:1:1", "left")],
                 [action("duplicate_tracks", "guitar", count=65)]]
        for actions in cases:
            with self.subTest(actions=actions), self.assertRaises(PlanError): self.validate(actions)

    def test_nested_folders_and_nonadjacent_siblings_preserve_unselected_tracks(self):
        self.snapshot["tracks"] = [{"guid": k, "folder_depth": d} for k, d in
                                   [("outer", 1), ("a", 0), ("untouched", 0), ("inner", 1), ("child", -1), ("last", -1)]]
        plan = TrackPlan([folder("a", "inner")], self.snapshot)
        order, parents = plan.layouts[1]
        self.assertEqual(order, ["outer", "@track:1:1", "a", "inner", "child", "untouched", "last"])
        self.assertEqual(parents["child"], "inner")
        self.assertEqual(parents["untouched"], "outer")
        with self.assertRaises(PlanError): self.validate([folder("a", "child")])

    def test_duplicate_folder_outputs_and_selection_are_stable(self):
        self.snapshot["tracks"] = [{"guid": k, "folder_depth": d, "selected": True} for k, d in
                                   [("parent", 1), ("child", -1)]]
        actions = [action("duplicate_tracks", "selected", count=2), action("mute", "all", "on")]
        self.validate(actions)
        plan = TrackPlan(actions, self.snapshot)
        self.assertEqual(len(plan.results[1]), 4)
        self.assertEqual(len(plan.resolve("all", 2)), 2)
        self.assertTrue(all(not t["fx_instances"] for t in plan.resolve("result:1", 2)))

    def test_invalid_layout_and_total_growth_rejected(self):
        with self.assertRaises(PlanError): self.validate([action("create_tracks", count=40), action("create_tracks", count=25)])
        self.snapshot["tracks"][0]["folder_depth"] = 1
        with self.assertRaises(PlanError): self.validate([action("duplicate_tracks", "guitar", count=1)])
        # Unrelated ordinary edits remain usable in an unfinished folder layout.
        self.validate([action("pan", "guitar", "left")])

    @patch("heycomputer.providers.api_key", return_value="fixture")
    @patch("heycomputer.providers.request_json")
    def test_planner_receives_composable_operations_and_examples(self, request, key):
        request.return_value = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text":
            json.dumps(dict(kind="plan", actions=[action("duplicate_tracks", "guitar", count=2), folder("guitar", "result:1")], message="", lua=""))}]}]}
        result = providers.openai_plan("Duplicate guitar twice and put all three in a folder", self.snapshot, DEFAULTS, [])
        self.assertEqual(result["kind"], "plan")
        instructions = request.call_args.args[2]["instructions"]
        self.assertIn("result:N:K", instructions)
        self.assertIn("duplicate_tracks(GUITAR_GUID", instructions)
