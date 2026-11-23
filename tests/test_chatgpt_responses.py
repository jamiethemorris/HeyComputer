import io
import http.client
import json
import threading
import unittest
from unittest.mock import Mock, patch

from heycomputer import providers, vision
from heycomputer.protocol import PlanError
from heycomputer.settings import DEFAULTS


BEARER = "synthetic-siwc-fixture"
CONFIG = dict(DEFAULTS, fallback_provider="chatgpt", fallback_model="fixture-model",
              fallback_endpoint="https://unused.invalid/v1/responses", use_jev=False)
REPLY = {"kind": "reply", "message": "Try this.", "actions": [], "lua": ""}


def event(kind, **fields):
    data = json.dumps(dict(type=kind, **fields)).encode()
    return b"event: " + kind.encode() + b"\r\ndata: " + data + b"\r\n\r\n"


def completed(result):
    return event("response.completed", response={"status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": json.dumps(result)}]},
    ]})


class FixtureResponse(io.BytesIO):
    def __init__(self, body, status, media):
        super().__init__(body)
        self.status = status
        self.media = media

    def getheader(self, name, default=None):
        return self.media if name.lower() == "content-type" else default


class ChatGPTResponsesTests(unittest.TestCase):
    def setUp(self):
        self.key = self.enterContext(patch.object(providers, "api_key", return_value="api-key-fixture"))
        self.connect = self.enterContext(patch("http.client.HTTPSConnection"))
        self.connection = self.connect.return_value

    def respond(self, body, status=200, media="text/event-stream"):
        response = FixtureResponse(body, status, media)
        self.connection.getresponse.return_value = response
        return response

    def test_plan_uses_only_completed_output_and_siwc_request_fields(self):
        response = self.respond(b": ping\r\n\r\n" + event("response.output_text.delta", delta="not the final JSON")
                                + completed(REPLY))
        result = providers.plan("Explain compression", {}, CONFIG, [], access_token=BEARER)
        self.assertEqual(result, dict(REPLY, model="fixture-model"))
        self.key.assert_not_called()
        self.assertEqual(self.connect.call_args.args, ("api.openai.com",))
        args, kwargs = self.connection.request.call_args
        self.assertEqual(args, ("POST", "/v1/responses"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + BEARER)
        payload = json.loads(kwargs["body"])
        self.assertNotIn(BEARER, kwargs["body"].decode())
        self.assertEqual(payload["model"], "fixture-model")
        self.assertIs(payload["store"], False)
        self.assertIs(payload["stream"], True)
        self.assertNotIn("max_output_tokens", payload)
        self.assertEqual(payload["text"]["format"]["name"], "heycomputer_result")
        self.assertEqual(payload["input"][0]["role"], "user")
        self.assertEqual(json.loads(payload["input"][0]["content"])["request"], "Explain compression")
        self.assertTrue(response.closed)
        self.connection.close.assert_called_once()

    def test_explicit_failure_is_terminal_even_if_completion_follows(self):
        failures = [
            (event("error", message=BEARER), "reported an error"),
            (event("response.failed", response={"error": {"message": BEARER}}), "reported an error"),
            (event("response.incomplete", response={"status": "incomplete"}), "did not finish"),
            (event("response.failed", response={"error": {"code": "subscription_sharing_usage_limit_exceeded",
                                                            "message": BEARER}}), "usage limit"),
        ]
        for failure, message in failures:
            with self.subTest(message=message):
                response = self.respond(event("response.output_text.delta", delta=json.dumps(REPLY))
                                        + failure + completed(REPLY))
                with self.assertRaisesRegex(PlanError, message) as raised:
                    providers.openai_plan("Explain compression", {}, CONFIG, [], access_token=BEARER)
                self.assertNotIn(BEARER, str(raised.exception))
                self.assertTrue(response.closed)

    def test_eof_done_sentinel_or_unframed_completion_is_not_success(self):
        delta = event("response.output_text.delta", delta=json.dumps(REPLY))
        for suffix in (b"", b"data: [DONE]\n\n", completed(REPLY).rstrip(b"\r\n")):
            with self.subTest(suffix=suffix):
                self.respond(delta + suffix)
                with self.assertRaisesRegex(PlanError, "ended before completion"):
                    providers.openai_plan("Explain compression", {}, CONFIG, [], access_token=BEARER)

    def test_socket_interruption_does_not_expose_provider_exception(self):
        response = self.respond(b"")
        with patch.object(response, "readline", side_effect=http.client.IncompleteRead(BEARER.encode())):
            with self.assertRaisesRegex(PlanError, "connection was interrupted") as raised:
                providers.openai_plan("Explain compression", {}, CONFIG, [], access_token=BEARER)
        self.assertNotIn(BEARER, str(raised.exception))
        self.assertTrue(response.closed)
        self.connection.close.assert_called_once()

    def test_redirect_is_not_followed(self):
        response = self.respond(BEARER.encode(), status=307)
        with patch.object(response, "getheader", return_value="https://unused.invalid/") as headers:
            with self.assertRaisesRegex(PlanError, "HTTP 307") as raised:
                providers.openai_plan("Explain compression", {}, CONFIG, [], access_token=BEARER)
        self.assertNotIn(BEARER, str(raised.exception))
        headers.assert_not_called()
        self.connection.request.assert_called_once()
        self.assertTrue(response.closed)

    def test_http_usage_error_is_sanitized(self):
        self.respond(json.dumps({"error": {"code": "subscription_sharing_usage_limit_exceeded",
                                          "message": BEARER}}).encode(), status=429, media="application/json")
        with self.assertRaisesRegex(PlanError, "usage limit") as raised:
            providers.openai_plan("Explain compression", {}, CONFIG, [], **{"access_token": BEARER})
        self.assertNotIn(BEARER, str(raised.exception))

    def test_completed_output_cannot_reflect_the_bearer(self):
        self.respond(completed(dict(REPLY, message=BEARER)))
        with self.assertRaisesRegex(PlanError, "invalid response") as raised:
            providers.openai_plan("Explain compression", {}, CONFIG, [], **{"access_token": BEARER})
        self.assertNotIn(BEARER, str(raised.exception))

    def test_malformed_final_output_is_a_controlled_error(self):
        for output in (None, [None], [{"type": "message", "content": None}]):
            with self.subTest(output=output):
                self.respond(event("response.completed", response={"status": "completed", "output": output}))
                with self.assertRaisesRegex(PlanError, "invalid completion"):
                    providers.openai_plan("Explain compression", {}, CONFIG, [], **{"access_token": BEARER})

    def test_missing_or_unsafe_token_never_uses_other_credentials_or_network(self):
        for value in (None, "", "bad\r\nheader"):
            with self.subTest(value=value), self.assertRaisesRegex(PlanError, "Sign in with ChatGPT"):
                providers.plan("Explain compression", {}, CONFIG, [], access_token=value)
        self.key.assert_not_called()
        self.connect.assert_not_called()

    def test_catalog_keeps_list_visibility_and_order_and_only_picker_fields(self):
        self.respond(json.dumps({"models": [
            {"slug": "z", "display_name": "Zed", "visibility": "list", "extra": BEARER},
            {"slug": "private", "display_name": "Private", "visibility": "hide"},
            {"slug": "missing", "display_name": "Missing"},
            {"slug": "a", "display_name": "Aye", "visibility": "list"},
        ]}).encode(), media="application/json; charset=utf-8")
        self.assertEqual(providers.chatgpt_list_models(BEARER), [
            {"slug": "z", "display_name": "Zed"}, {"slug": "a", "display_name": "Aye"},
        ])
        args, kwargs = self.connection.request.call_args
        self.assertEqual(args, ("GET", "/v1/models"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + BEARER)
        self.assertIsNone(kwargs["body"])
        self.key.assert_not_called()

    def test_catalog_rejects_invalid_shape_but_allows_empty_list(self):
        for invalid in ({"data": []}, {"models": [None]},
                        {"models": [{"slug": "x", "display_name": None, "visibility": "list"}]}):
            with self.subTest(invalid=invalid):
                self.respond(json.dumps(invalid).encode(), media="application/json")
                with self.assertRaisesRegex(PlanError, "invalid model catalog"):
                    providers.chatgpt_list_models(BEARER)
        self.respond(b'{"models":[]}', media="application/json")
        self.assertEqual(providers.chatgpt_list_models(BEARER), [])

    def test_catalog_cannot_reflect_the_bearer_in_picker_fields(self):
        self.respond(json.dumps({"models": [
            {"slug": "safe-model", "display_name": BEARER, "visibility": "list"},
        ]}).encode(), media="application/json")
        with self.assertRaisesRegex(PlanError, "invalid response"):
            providers.chatgpt_list_models(BEARER)

    def test_vision_uses_same_transport_and_refuses_unsafe_completed_actions(self):
        window = vision.Window(1, 1, "Fixture", (0, 0, 800, 600), "synthetic")
        frame = vision.Frame(window, b"jpeg-fixture", 800, 600, [])
        backend = Mock()
        backend.escape_pressed.return_value = False
        backend.windows.return_value = [window]
        backend.capture.return_value = frame
        backend.assert_current = Mock()
        backend.input_sent = False
        step = dict(op="key", x=0, y=0, end_x=0, end_y=0, text="cmd+tab", target=0, amount=0, memory=[])
        for action, expected in ((dict(step, op="done", text="Verified"), "visual_done"), (step, "error")):
            self.respond(completed(action))
            result = vision.run("Do a visual task", [], dict(CONFIG, vision_enabled=True), backend,
                                threading.Event(), lambda: None, Mock(), **{"access_token": BEARER})
            self.assertEqual(result["kind"], expected, result["message"])
            if expected == "error":
                self.assertIn("keyboard shortcut is not supported", result["message"])
        backend.execute.assert_not_called()
        self.key.assert_not_called()
        payload = json.loads(self.connection.request.call_args.kwargs["body"])
        self.assertEqual(payload["text"]["format"]["name"], "heycomputer_visual_step")
        self.assertEqual(payload["input"][0]["content"][1]["image_url"], "data:image/jpeg;base64,anBlZy1maXh0dXJl")
        self.assertNotIn("max_output_tokens", payload)

    def test_vision_without_chatgpt_token_refuses_before_touching_desktop(self):
        backend = Mock()
        with self.assertRaisesRegex(PlanError, "Sign in with ChatGPT"):
            vision.run("Do a visual task", [], dict(CONFIG, vision_enabled=True), backend,
                       threading.Event(), lambda: None, Mock())
        self.assertEqual(backend.mock_calls, [])
        self.connect.assert_not_called()
