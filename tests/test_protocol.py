import copy
import unittest
from unittest.mock import patch

from heycomputer.protocol import PlanError, action, validate_result
from heycomputer import providers

SNAPSHOT = {
    "tracks": [{"guid": "{existing}", "name": "Vocal", "selected": True}],
    "plugins": [{"id": "VST3: Genome", "name": "VST3: Genome"},
                {"id": "VST3: Pro-Q 4", "name": "VST3: Pro-Q 4"}],
    "inputs": [{"index": 2, "name": "Guitar 1"}],
}


class Plans(unittest.TestCase):
    def plan(self, actions):
        return {"kind": "plan", "message": "", "lua": "", "actions": actions}

    def test_full_user_workflow(self):
        actions = [action("create_tracks", count=10), action("folder_created"),
                   action("pan", value="alternate_lr"),
                   action("add_fx", value="VST3: Genome"),
                   action("add_fx", value="VST3: Pro-Q 4"),
                   action("input", value="2"), action("arm", "first_created", "on")]
        self.assertEqual(validate_result(self.plan(actions), SNAPSHOT)["actions"], actions)

    def test_invalid_plans_rejected_before_execution(self):
        cases = [action("create_tracks", count=1000), action("input", "selected", "22"),
                 action("add_fx", "selected", "Missing plugin"), action("arm", "{missing}", "on"),
                 action("pan", value="left"), action("volume", "selected", "nan"),
                 action("volume", "selected", "inf"), action("folder_created"),
                 action("pan", "selected", "sometimes")]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(PlanError):
                validate_result(self.plan([case]), SNAPSHOT)

    def test_mixed_plugin_request_requires_valid_native_setup_and_one_exact_open(self):
        actions = [action('create_tracks', count=1), action('add_fx', value='VST3: Pro-Q 4'), action('open_fx', value='last_added')]
        result = dict(kind='vision', message='Set shelf 500 Hz -3 dB', actions=actions, lua='')
        self.assertEqual(validate_result(result, SNAPSHOT, allow_vision=True), result)
        for bad in [dict(result, message=''), dict(result, actions=actions[:-1]),
                    dict(result, actions=[action('open_fx', 'selected', 'last_added')]),
                    dict(result, actions=[action('create_tracks',count=2)]+actions[1:])]:
            with self.assertRaises(PlanError):
                validate_result(bad, SNAPSHOT, allow_vision=True)
        with self.assertRaises(PlanError):
            validate_result(result, SNAPSHOT)

    def test_conversation_cannot_mutate(self):
        with self.assertRaises(PlanError):
            validate_result({"kind": "reply", "message": "Which track?", "actions": [action("create_tracks",count=2)]}, SNAPSHOT)

    def test_lua_is_opt_in(self):
        with self.assertRaises(PlanError):
            validate_result({"kind": "lua", "lua": "return", "actions": []}, SNAPSHOT)

    def test_selected_requires_selection(self):
        snap = copy.deepcopy(SNAPSHOT)
        snap["tracks"][0]["selected"] = False
        with self.assertRaises(PlanError):
            validate_result(self.plan([action("arm", "selected", "on")]), snap)

    def jev_response(self, confidence=0.99):
        values = {"coverage": "supported", "count": "10", "folder": "yes", "target": "created",
                  "pan": "alternate_lr", "input": "2", "arm": "first",
                  "fx1": "p1", "fx2": "p0", "fx3": "none", "fx4": "none"}
        return {"answers": {k: {"choice": v, "confidence": confidence} for k, v in values.items()}}

    @patch("heycomputer.providers.api_key", return_value="test")
    @patch("heycomputer.providers.request_json")
    def test_jev_uses_one_call_and_preserves_fx_order(self, request, key):
        request.return_value = self.jev_response()
        result = providers.jev_plan("Create 10 tracks with Genome followed by Pro-Q", SNAPSHOT, {})
        self.assertEqual(request.call_count, 1)
        self.assertEqual([a["value"] for a in result["actions"] if a["op"] == "add_fx"],
                         ["VST3: Genome", "VST3: Pro-Q 4"])
        self.assertEqual(result["actions"][-1], action("arm", "first_created", "on"))
        payload = request.call_args.args[2]
        self.assertEqual(payload["questions"]["coverage"]["type"], "choice")
        self.assertIn("criteria", payload["questions"]["coverage"])

    @patch("heycomputer.providers.api_key", return_value="test")
    @patch("heycomputer.providers.request_json")
    def test_uncertain_jev_never_returns_partial_plan(self, request, key):
        request.return_value = self.jev_response(.3)
        self.assertIsNone(providers.jev_plan("Genome Pro-Q", SNAPSHOT, {}))

    @patch("heycomputer.providers.api_key", return_value="test")
    @patch("heycomputer.providers.openai_plan")
    @patch("heycomputer.providers.jev_plan")
    def test_clarification_preserves_conversation(self, jev, fallback, key):
        fallback.return_value = {"kind": "reply", "message": "Which track?"}
        history = [{"role": "assistant", "text": "Which Pro-Q version?", "kind": "reply"}]
        providers.plan("Version 4", SNAPSHOT, {}, history)
        jev.assert_not_called()
        self.assertEqual(fallback.call_args.args[-1], history)

    @patch("heycomputer.providers.api_key", return_value="test")
    @patch("heycomputer.providers.request_json")
    def test_incomplete_openai_output_cannot_execute(self, request, key):
        request.return_value = {"status": "incomplete", "output": []}
        with self.assertRaises(PlanError):
            providers.openai_plan("Create tracks", SNAPSHOT, {}, [])


if __name__ == "__main__":
    unittest.main()
