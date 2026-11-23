import io
import unittest
from unittest.mock import patch

from heycomputer.chatgpt_responses import _read_stream
from heycomputer.mcp_loop import run_loop
from heycomputer.mcp_responses import make_turn
from heycomputer.protocol import PlanError
from test_mcp_loop import Client, message
from test_mcp_responses import StreamResponse, completed, event, inline_provider


MUTE = {"type": "function_call", "id": "fc_mute", "call_id": "call_mute",
        "name": "track_set_mute", "status": "completed", "arguments": '{"track_index":0,"mute":true}'}
UNMUTE = dict(MUTE, arguments=MUTE["arguments"].replace("true", "false"))
INSPECT = dict(MUTE, id="fc_read", call_id="call_read", name="track_get_all", arguments="{}")
START = dict(MUTE, status="in_progress", arguments="")


def item_event(stage, item, index=0):
    return event("response.output_item." + stage, output_index=index, item=item)


def arguments_event(stage, arguments, index=0, **fields):
    payload = {"delta" if stage == "delta" else "arguments": arguments}
    return event("response.function_call_arguments." + stage, output_index=index,
                 **({"item_id": MUTE["id"]} | fields | payload))


def completion(*items):
    return event("response.completed", response=completed(list(items)))


class StreamReconciliationTests(unittest.TestCase):
    def assert_dispatches(self, stream, expected_writes):
        client = Client()
        check = lambda: None
        turn = make_turn({"fallback_provider": "chatgpt"}, check, access_token="synthetic-stream-fixture")
        response = StreamResponse(stream + completion(MUTE))
        with inline_provider(), patch("http.client.HTTPSConnection") as connect:
            connection = connect.return_value
            connection.getresponse.side_effect = [StreamResponse(completion(INSPECT)), response,
                                                  StreamResponse(completion(message("Kick is muted.")))]
            try:
                answer = run_loop(client, turn, "Mute the kick", {}, check, lambda: 10)
            except PlanError as error:
                answer = error
        writes = [call for call in client.calls if call[0] == "track_set_mute"]
        self.assertEqual(writes, expected_writes)
        self.assertIs(client.tracks[0]["mute"], bool(expected_writes))
        if expected_writes:
            self.assertEqual(answer, "Kick is muted.")
            self.assertEqual(connection.getresponse.call_count, 3)
        else:
            self.assertIsInstance(answer, PlanError)
            self.assertEqual(client.calls, [("track_get_all", {})])
            self.assertEqual(connection.getresponse.call_count, 2)
        self.assertTrue(response.closed)

    def test_contradictory_finished_item_dispatches_zero_writes(self):
        self.assert_dispatches(item_event("added", START) + item_event("done", UNMUTE), [])

    def test_contradictory_argument_delta_dispatches_zero_writes(self):
        self.assert_dispatches(item_event("added", START) + arguments_event("delta", UNMUTE["arguments"])
                               + arguments_event("done", MUTE["arguments"]) + item_event("done", MUTE), [])

    def test_unfinished_second_announced_call_dispatches_zero_writes(self):
        self.assert_dispatches(item_event("added", START) + item_event("done", MUTE)
                               + item_event("added", dict(START, id="fc_second", call_id="call_second"), 1), [])

    def test_final_envelope_cannot_finish_an_announced_call(self):
        self.assert_dispatches(item_event("added", START), [])

    def test_final_envelope_only_call_dispatches_exactly_one_write(self):
        self.assert_dispatches(b"", [("track_set_mute", {"track_index": 0, "mute": True})])

    def test_consistent_fragmented_call_dispatches_exactly_one_write(self):
        args = MUTE["arguments"]
        self.assert_dispatches(item_event("added", START) + arguments_event("delta", args[:15])
                               + arguments_event("delta", args[15:]) + arguments_event("done", args)
                               + item_event("done", MUTE), [("track_set_mute", {"track_index": 0, "mute": True})])

    def test_invalid_tool_evidence_never_releases_completed_output(self):
        args = MUTE["arguments"]
        cases = {
            "added name changes": item_event("added", dict(START, name="track_set_solo")),
            "added call_id changes": item_event("added", dict(START, call_id="call_wrong")),
            "done item id changes": item_event("done", dict(MUTE, id="fc_wrong")),
            "done item incomplete": item_event("done", dict(MUTE, status="incomplete")),
            "done item index changes": item_event("done", MUTE, 1),
            "duplicate added index": item_event("added", START) * 2,
            "stream type changes": item_event("added", message("Wrong type")) + item_event("done", MUTE),
            "argument index out of range": arguments_event("done", args, 2),
            "boolean argument index": arguments_event("done", args, False),
            "wrong argument item id": arguments_event("delta", args, item_id="fc_wrong"),
            "wrong argument name": arguments_event("done", args, name="track_set_solo"),
            "non-string argument fragment": arguments_event("delta", False),
            "contradictory arguments done": arguments_event("done", UNMUTE["arguments"]),
            "duplicate arguments done": arguments_event("done", args) * 2,
            "late delta": item_event("done", MUTE) + arguments_event("delta", args),
            "partial arguments": arguments_event("delta", args[:-1]),
        }
        for case, stream in cases.items():
            with self.subTest(case=case), self.assertRaises(PlanError):
                _read_stream(io.BytesIO(stream + completion(MUTE)), replay=True)

    def test_empty_envelope_does_not_override_conflicting_arguments(self):
        stream = arguments_event("done", UNMUTE["arguments"]) + item_event("done", MUTE)
        with self.assertRaises(PlanError):
            _read_stream(io.BytesIO(stream + completion()), replay=True)

    def test_empty_envelope_replays_consistent_finished_call(self):
        stream = arguments_event("done", MUTE["arguments"]) + item_event("done", MUTE)
        self.assertEqual(_read_stream(io.BytesIO(stream + completion()), replay=True), completed([MUTE]))

    def test_nonempty_envelope_cannot_insert_or_reuse_another_call(self):
        for evidence, second_call in (
            (b"", INSPECT),
            (item_event("done", dict(INSPECT, id=MUTE["id"]), 1), dict(INSPECT, id=MUTE["id"])),
            (item_event("done", dict(INSPECT, call_id=MUTE["call_id"]), 1), dict(INSPECT, call_id=MUTE["call_id"])),
        ):
            with self.subTest(second_call=second_call), self.assertRaises(PlanError):
                stream = item_event("done", MUTE) + evidence + completion(MUTE, second_call)
                _read_stream(io.BytesIO(stream), replay=True)
