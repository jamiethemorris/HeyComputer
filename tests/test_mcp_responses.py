import io
import json
import unittest
import urllib.error
from email.message import Message
from unittest.mock import Mock, patch

from heycomputer import mcp_responses, providers
from heycomputer.mcp_responses import make_turn
from heycomputer.protocol import PlanError


KEY = "synthetic-key-for-responses-fixture"
INPUT = [{"role": "user", "content": "Inspect the selection."}]
TOOLS = [{"type": "function", "name": "inspect_selection", "parameters": {"type": "object"}}]
REASONING = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque"}
CALL = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "status": "completed",
        "name": "inspect_selection", "arguments": '{"with_notes": true}'}
MESSAGE = {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
           "content": [{"type": "output_text", "text": "One item selected.", "annotations": []}]}


def completed(output):
    return {"status": "completed", "output": output}


def inline_provider():
    """Keep network mocks in-process for parser, payload and worker tests."""
    return patch.object(mcp_responses, '_wait_for_provider',
                        lambda args, deadline, remaining: mcp_responses._provider_output(args, remaining))


class APIKeyTurnTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(inline_provider())
        self.key = self.enterContext(patch.object(providers, "api_key", return_value=KEY))
        self.build = self.enterContext(patch("urllib.request.build_opener"))
        self.open = self.build.return_value.open
        self.https = self.enterContext(patch("http.client.HTTPSConnection"))

    def test_both_key_providers_replay_calls_and_reasoning_without_mutation(self):
        for provider in ("openai", "openrouter"):
            with self.subTest(provider=provider):
                first = io.BytesIO(json.dumps(completed([REASONING, CALL])).encode())
                second = io.BytesIO(json.dumps(completed([MESSAGE])).encode())
                self.open.side_effect = [first, second]
                endpoint = "https://fixture.invalid/custom/responses"
                check = Mock()
                turn = make_turn({"fallback_provider": provider, "fallback_endpoint": endpoint,
                                  "fallback_model": "fixture-model"}, check)
                output = turn("Use only the given tools.", INPUT, TOOLS, 10)
                self.assertEqual(output, [REASONING, CALL])
                replay = INPUT + output + [{"type": "function_call_output", "call_id": "call_1", "output": "1"}]
                self.assertEqual(turn("Use only the given tools.", replay, TOOLS, 10), [MESSAGE])
                self.key.assert_called_with(provider)
                self.assertIsInstance(self.build.call_args.args[0], providers.NoRedirect)
                request = self.open.call_args.args[0]
                self.assertEqual(request.full_url, endpoint)
                self.assertEqual(request.get_header("Authorization"), "Bearer " + KEY)
                self.assertNotIn(KEY.encode(), request.data)
                payload = json.loads(request.data)
                self.assertEqual(payload["model"], "fixture-model")
                self.assertEqual(payload["instructions"], "Use only the given tools.")
                self.assertEqual(payload["input"], replay)
                self.assertEqual(payload["tools"], TOOLS)
                self.assertIs(payload["store"], False)
                self.assertIs(payload["stream"], False)
                self.assertIs(payload["parallel_tool_calls"], False)
                self.assertEqual(payload["include"], ["reasoning.encrypted_content"])
                self.assertNotIn("previous_response_id", payload)
                self.assertGreater(self.open.call_args.kwargs["timeout"], 0)
                self.assertLessEqual(self.open.call_args.kwargs["timeout"], 10)
                self.assertTrue(first.closed and second.closed)
                check.assert_called()
        self.https.assert_not_called()

    def test_api_error_or_partial_output_never_releases_calls(self):
        turn = make_turn({"fallback_provider": "openai"}, lambda: None)
        for result in ({"status": "incomplete", "output": [CALL]},
                       {"output": [CALL]},
                       {**completed([CALL]), "error": {"message": KEY}},
                       completed([CALL, {**MESSAGE, "content": None}]),
                       completed([{**CALL, "arguments": None}]),
                       completed([{**CALL, "status": "in_progress"}]),
                       completed([{**CALL, "arguments": json.dumps({"echo": KEY})}])):
            with self.subTest(status=result.get("status")):
                self.open.return_value = io.BytesIO(json.dumps(result).encode())
                with self.assertRaises(PlanError) as raised:
                    turn("", INPUT, TOOLS, 10)
                self.assertNotIn(KEY, str(raised.exception))
                self.assertTrue(self.open.return_value.closed)

    def test_guard_refusal_prevents_lookup_and_dispatch(self):
        stopped = RuntimeError("stopped")
        turn = make_turn({"fallback_provider": "openrouter"}, Mock(side_effect=stopped))
        with self.assertRaises(RuntimeError) as raised:
            turn("", INPUT, TOOLS, 10)
        self.assertIs(raised.exception, stopped)
        self.key.assert_not_called()
        self.build.assert_not_called()

    def test_provider_tools_are_rejected_before_dispatch(self):
        turn = make_turn({"fallback_provider": "openai"}, lambda: None)
        with self.assertRaisesRegex(PlanError, "client-side function tools"):
            turn("", INPUT, [{"type": "mcp", "server_url": "https://unused.invalid"}], 10)
        self.key.assert_not_called()
        self.build.assert_not_called()

    def test_http_failure_is_sanitized_without_retry(self):
        body = io.BytesIO(KEY.encode())
        self.open.side_effect = urllib.error.HTTPError("https://fixture.invalid/responses", 401, KEY, Message(), body)
        turn = make_turn({"fallback_provider": "openai"}, lambda: None)
        with self.assertRaisesRegex(PlanError, "HTTP 401") as raised:
            turn("", INPUT, TOOLS, 10)
        self.assertNotIn(KEY, str(raised.exception))
        self.assertTrue(body.closed)
        self.open.assert_called_once()

    def test_late_result_is_not_released(self):
        turn = make_turn({"fallback_provider": "openai"}, lambda: None)
        self.open.return_value = io.BytesIO(json.dumps(completed([CALL])).encode())
        with patch("time.monotonic", side_effect=[0, 0, 0, 12, 12]):
            with self.assertRaisesRegex(PlanError, "timed out"):
                turn("", INPUT, TOOLS, 10)
        self.open.assert_called_once()
        self.assertTrue(self.open.return_value.closed)


class StreamResponse(io.BytesIO):
    status = 200

    def getheader(self, name, default=None):
        return "text/event-stream; charset=utf-8" if name.lower() == "content-type" else default


def event(kind, **fields):
    return b"data: " + json.dumps(dict(type=kind, **fields)).encode() + b"\n\n"


class ChatGPTTurnTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(inline_provider())
        self.connect = self.enterContext(patch("http.client.HTTPSConnection"))
        self.connection = self.connect.return_value
        self.key = self.enterContext(patch.object(providers, "api_key"))
        self.settings = {"fallback_provider": "chatgpt", "fallback_model": "fixture-chatgpt",
                         "fallback_endpoint": "https://ignored.invalid/responses"}

    def test_stream_replays_completed_items_in_order(self):
        final_reasoning = {"type": "reasoning", "id": "rs_1", "summary": []}
        response = StreamResponse(event("response.output_item.added", item={**CALL, "arguments": ""}, output_index=1)
                                  + event("response.output_item.done", item=REASONING, output_index=0)
                                  + event("response.output_item.done", item=CALL, output_index=1)
                                  + event("response.completed", response=completed([final_reasoning, CALL, MESSAGE])))
        self.connection.getresponse.return_value = response
        turn = make_turn(self.settings, lambda: None, access_token=KEY)
        self.assertEqual(turn("Fixture instructions", INPUT, TOOLS, 10), [REASONING, CALL, MESSAGE])
        self.assertEqual(self.connect.call_args.args, ("api.openai.com",))
        self.assertLessEqual(self.connect.call_args.kwargs["timeout"], 10)
        args, kwargs = self.connection.request.call_args
        self.assertEqual(args, ("POST", "/v1/responses"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + KEY)
        payload = json.loads(kwargs["body"])
        self.assertEqual(payload["model"], "fixture-chatgpt")
        self.assertEqual(payload["input"], INPUT)
        self.assertEqual(payload["tools"], TOOLS)
        self.assertEqual(payload["include"], ["reasoning.encrypted_content"])
        self.assertIs(payload["stream"], True)
        self.assertIs(payload["store"], False)
        self.assertNotIn("max_output_tokens", payload)
        self.key.assert_not_called()
        self.assertTrue(response.closed)
        self.connection.close.assert_called_once()

    def test_truncated_stream_does_not_release_a_finished_function_item(self):
        response = StreamResponse(event("response.output_item.done", item=CALL, output_index=0))
        self.connection.getresponse.return_value = response
        turn = make_turn(self.settings, lambda: None, access_token=KEY)
        with self.assertRaisesRegex(PlanError, "ended before completion"):
            turn("", INPUT, TOOLS, 10)
        self.assertTrue(response.closed)

    def test_completed_stream_can_replay_finished_items_when_the_envelope_is_empty(self):
        first = StreamResponse(event("response.output_item.done", item=CALL, output_index=1)
                               + event("response.output_item.done", item=REASONING, output_index=0)
                               + event("response.completed", response=completed([])))
        second = StreamResponse(event("response.completed", response=completed([MESSAGE])))
        self.connection.getresponse.side_effect = [first, second]
        turn = make_turn(self.settings, lambda: None, access_token=KEY)
        output = turn("", INPUT, TOOLS, 10)
        self.assertEqual(output, [REASONING, CALL])
        replay = INPUT + output + [{"type": "function_call_output", "call_id": "call_1", "output": "1"}]
        self.assertEqual(turn("", replay, TOOLS, 10), [MESSAGE])
        self.assertEqual(json.loads(self.connection.request.call_args.kwargs["body"])["input"], replay)
        self.assertTrue(first.closed and second.closed)

    def test_empty_final_envelope_does_not_release_a_gapped_stream(self):
        turn = make_turn(self.settings, lambda: None, access_token=KEY)
        for events in (
            event("response.output_item.done", item=CALL, output_index=2),
            event("response.output_item.done", item=CALL, output_index=0)
            + event("response.output_item.added", item=MESSAGE, output_index=1),
        ):
            self.connection.getresponse.return_value = StreamResponse(
                events + event("response.completed", response=completed([])))
            with self.assertRaisesRegex(PlanError, "incomplete set of stream items"):
                turn("", INPUT, TOOLS, 10)

    def test_guard_cancels_stream_before_completion(self):
        cancelled = False
        response = StreamResponse(event("response.output_item.done", item=CALL, output_index=0)
                                  + event("response.completed", response=completed([CALL])))
        readline = response.readline

        def read_first_line(limit):
            nonlocal cancelled
            cancelled = True
            return readline(limit)

        def check():
            if cancelled:
                raise PlanError("Fixture cancelled")

        self.connection.getresponse.return_value = response
        turn = make_turn(self.settings, check, access_token=KEY)
        with patch.object(response, "readline", side_effect=read_first_line) as read:
            with self.assertRaisesRegex(PlanError, "Fixture cancelled"):
                turn("", INPUT, TOOLS, 10)
        read.assert_called_once()
        self.assertTrue(response.closed)

    def test_missing_sign_in_never_falls_back_to_api_key(self):
        turn = make_turn(self.settings, lambda: None)
        with self.assertRaisesRegex(PlanError, "Sign in with ChatGPT"):
            turn("", INPUT, TOOLS, 10)
        self.key.assert_not_called()
        self.connect.assert_not_called()
