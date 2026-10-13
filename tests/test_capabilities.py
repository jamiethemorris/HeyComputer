import copy
import json
import unittest
from unittest.mock import patch

from heycomputer import providers
from heycomputer.capabilities import quick_plan, action_candidates
from heycomputer.protocol import action, validate_result, PlanError

SNAPSHOT = {
    "timeline": {"cursor": 0, "selection_start": 0, "selection_end": 8},
    "tracks": [{"guid": "guitar", "selected": True}, {"guid": "reverb", "selected": False}],
    "items": [{"guid": "kick", "selected": True, "gain": 1}],
    "markers": [{"key": "region:2", "name": "Chorus"}],
    "actions": [{"id": "40012", "name": "Item: Split items at edit cursor", "requires": "items"}],
    "plugins": [], "inputs": [],
}


def op(name, value, target="project"):
    return action(name, target, json.dumps(value) if isinstance(value, dict) else value)


def plan(ops):
    return {"kind": "plan", "actions": ops, "message": "", "lua": ""}


class CapabilitiesTests(unittest.TestCase):
    @patch("heycomputer.providers.request_json", side_effect=AssertionError("No model call expected"))
    @patch("heycomputer.providers.api_key", side_effect=AssertionError("No key needed"))
    def test_exact_installed_script_launch_uses_corrections_and_no_model(self, key, request):
        snapshot = dict(SNAPSHOT, launch_actions=[{"id": "_RSmain", "name": "Script: ReaSplice.lua"},
                                                 {"id": "_RSreload", "name": "Script: Reload ReaSplice.lua"}])
        config = {"speech_corrections": "ReSplice = ReaSplice"}
        for text in ("open ReaSplice", "run ReaSplice Lua script", "please launch Rea Splice", "open ReSplice"):
            result = providers.plan(text, snapshot, config, [{"kind": "reply"}])
            self.assertEqual(result["actions"], [op("launch_action", "_RSmain")])
        self.assertIsNone(quick_plan("open ReaSplice and find a kick", snapshot))
        self.assertIsNone(quick_plan("open Missing Script", snapshot))
        snapshot["launch_actions"].append({"id": "_RSother", "name": "Custom: ReaSplice.lua"})
        self.assertIsNone(quick_plan("open ReaSplice", snapshot))

    def test_launch_only_accepts_installed_identity_as_a_standalone_operation(self):
        snapshot = dict(SNAPSHOT, launch_actions=[{"id": "_RSmain", "name": "Script: ReaSplice.lua"}])
        self.assertTrue(validate_result(plan([op("launch_action", "_RSmain")]), snapshot))
        for ops in ([op("launch_action", "_RSunknown")], [op("launch_action", "/tmp/script.lua")],
                    [op("launch_action", "_RSmain", "guitar")],
                    [op("item_name", "Kick", "kick"), op("launch_action", "_RSmain")]):
            with self.assertRaises(PlanError):
                validate_result(plan(ops), snapshot)

    def test_launcher_search_prefers_tool_name_over_generic_script_words(self):
        actions = [{"id": "_RSother", "name": "Script: Some other tool.lua"},
                   {"id": "_RSmain", "name": "Script: ReaSplice.lua"}]
        self.assertEqual(action_candidates("open rea splice script", actions), [actions[1]])

    def test_valid_named_range_item_and_send_workflow(self):
        operations = [
            op("set_range", {"start": {"bar": 9}, "end": {"bar": 13}, "loop": True}),
            op("region_add", {"start": {"bar": 9}, "end": {"bar": 13}, "name": "Chorus", "color": "#ff0000"}),
            op("item_name", "Kick", "kick"), op("item_color", "#00ff00", "selected"),
            op("item_gain", {"db": -6, "relative": True}, "kick"),
            op("send_create", {"destination": "reverb", "volume_db": -12, "mode": "post_fader"}, "guitar"),
            op("transport", "play"),
        ]
        self.assertEqual(validate_result(plan(operations), SNAPSHOT)["actions"], operations)

    def test_missing_objects_invalid_values_and_unavailable_actions_rejected(self):
        invalid = [op("item_name", "Kick", "missing"), op("item_color", "green", "selected"),
                   op("item_gain", {"db": float("nan"), "relative": True}, "kick"),
                   op("send_create", {"destination": "missing"}, "guitar"),
                   op("send_create", {"destination": "reverb", "mode": "hardware"}, "guitar"),
                   op("marker_update", {"name": "Intro"}, "marker:300"),
                   op("seek", {"position": {"anchor": "region:missing"}}),
                   op("seek", {"bars": 2.5}), op("seek", {"bars": 2, "seconds": 5}),
                   op("run_action", "99999"), op("save", "/tmp/overwrite.rpp")]
        for operation in invalid:
            with self.subTest(operation=operation), self.assertRaises(PlanError):
                validate_result(plan([operation]), SNAPSHOT)

    def test_terminal_operations_cannot_precede_edits(self):
        for terminal in (op("transport", "record"), op("save", "current"), op("run_action", "40012")):
            with self.subTest(terminal=terminal), self.assertRaises(PlanError):
                validate_result(plan([terminal, op("item_name", "Kick", "kick")]), SNAPSHOT)
        with self.assertRaises(PlanError):
            validate_result(plan([op("item_name", "Kick", "kick"), op("run_action", "40012")]), SNAPSHOT)

    def test_new_bus_and_send_destination_are_validated_in_order(self):
        send = op("send_create", {"destination": "first_created"}, "selected")
        with self.assertRaises(PlanError):
            validate_result(plan([send]), SNAPSHOT)
        self.assertTrue(validate_result(plan([action("create_tracks", value="Reverb", count=1), send]), SNAPSHOT))

    @patch("heycomputer.providers.request_json", side_effect=AssertionError("No model call expected"))
    @patch("heycomputer.providers.api_key", side_effect=AssertionError("No credential lookup expected"))
    def test_simple_transport_navigation_and_save_need_no_model_or_key(self, key, request):
        for text in ("play", "stop", "record", "pause", "rewind 4 bars", "forward 8 bars", "save", "save a new version"):
            result = providers.plan(text, SNAPSHOT, {}, [])
            self.assertEqual(result["kind"], "plan")
            self.assertEqual(result["model"], "Direct command")
        self.assertEqual(json.loads(quick_plan("rewind 4 bars", SNAPSHOT)["actions"][0]["value"]),
                         {"bars": -4, "preserve_beat": False})
        self.assertIsNone(quick_plan("play and color the kick red", SNAPSHOT))

    @patch("heycomputer.providers.request_json", side_effect=AssertionError("Stop must not wait for a model"))
    def test_stop_is_immediate_even_during_conversation(self, request):
        self.assertEqual(providers.plan("stop", SNAPSHOT, {}, [{"kind": "reply", "text": "Which track?"}])["actions"][0]["value"], "stop")

    @patch("heycomputer.providers.api_key", return_value="fixture")
    @patch("heycomputer.providers.jev_plan")
    @patch("heycomputer.providers.openai_plan", return_value={"kind": "reply", "message": "Which range?"})
    def test_extended_requests_bypass_the_track_recipe(self, fallback, jev, key):
        providers.plan("Create a red region called Chorus", SNAPSHOT, {}, [])
        jev.assert_not_called(); fallback.assert_called_once()

    def test_large_project_cannot_silently_edit_only_some_items(self):
        snapshot = copy.deepcopy(SNAPSHOT); snapshot["items_truncated"] = True
        with self.assertRaises(PlanError):
            validate_result(plan([op("item_color", "#ff0000", "all")]), snapshot)
        self.assertTrue(validate_result(plan([op("item_color", "#ff0000", "selected")]), snapshot))

    def test_action_search_recognizes_common_synonyms(self):
        self.assertEqual(action_candidates("cut the selected item", SNAPSHOT["actions"])[0]["id"], "40012")


if __name__ == "__main__":
    unittest.main()
