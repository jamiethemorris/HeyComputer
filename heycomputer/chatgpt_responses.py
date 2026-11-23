"""Sign in with ChatGPT transport; callers own the credential lifecycle."""
from contextlib import closing
import http.client
import json
import re

from .protocol import PlanError

HOST = "api.openai.com"
RESPONSES_PATH = "/v1/responses"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def _usage_message(code):
    if code == "subscription_sharing_usage_limit_exceeded":
        return "Your ChatGPT plan usage limit was reached. Check ChatGPT Settings → Usage."
    if code == "subscription_sharing_usage_unavailable":
        return "ChatGPT plan usage is temporarily unavailable. Try again later."


def _http_error(response):
    if response.status in (429, 503):
        try:
            body = json.loads(response.read(8192))
            error = body.get("error") if isinstance(body, dict) else None
            message = _usage_message(error.get("code")) if isinstance(error, dict) else None
            if message:
                return PlanError(message)
        except (ValueError, OSError, http.client.HTTPException, RecursionError):
            pass  # Fall back to the status; never relay the provider's error body.
    return PlanError(f"ChatGPT returned HTTP {response.status}. Check your sign-in and model access.")


def _request(path, access_token, media_type, read, payload=None, *, timeout=60):
    require_token(access_token)
    headers = {"Authorization": "Bearer " + access_token, "Accept": media_type,
               "User-Agent": "HeyComputer/0.1"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode()
    try:
        # A fixed TLS host and a single request: http.client never follows redirects.
        with closing(http.client.HTTPSConnection(HOST, timeout=timeout)) as connection:
            connection.request("POST" if body is not None else "GET", path, body=body, headers=headers)
            with closing(connection.getresponse()) as response:
                if response.status != 200:
                    raise _http_error(response)
                if response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != media_type:
                    raise PlanError("ChatGPT returned an unexpected response format.")
                result = read(response)
                reject_reflected_token(result, access_token)
                return result
    except (OSError, http.client.HTTPException):
        raise PlanError("The ChatGPT connection was interrupted.") from None
    except PlanError:
        raise
    except (ValueError, RecursionError):
        raise PlanError("ChatGPT returned an invalid response.") from None


def require_token(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", value):
        raise PlanError("Sign in with ChatGPT in Settings first.")


def reject_reflected_token(data, value):
    if value in json.dumps(data):
        raise PlanError("ChatGPT returned an invalid response.")


def _completed(result):
    invalid = PlanError("ChatGPT returned an invalid completion.")
    if not isinstance(result, dict) or result.get("status") != "completed" or result.get("error"):
        raise invalid
    output = result.get("output")
    if not isinstance(output, list):
        raise invalid
    for item in output:
        if not isinstance(item, dict):
            raise invalid
        if item.get("type") == "message":
            content = item.get("content")
            if not isinstance(content, list) or any(
                not isinstance(part, dict) or (part.get("type") == "output_text" and not isinstance(part.get("text"), str))
                for part in content
            ):
                raise invalid
    return result


def _reconcile_stream_calls(output, added, finished, argument_events):
    invalid = PlanError("ChatGPT returned inconsistent streamed tool calls.")
    calls = {i: item for i, item in enumerate(output) if item.get("type") == "function_call"}
    streamed = argument_events.keys() | {
        i for items in (added, finished) for i, item in items.items() if item.get("type") == "function_call"
    }
    # Some turns carry calls only in the final envelope. Once any are streamed,
    # each needs its own done item and must match the final output at that index.
    if streamed and (streamed != calls.keys() or not streamed <= finished.keys()):
        raise invalid

    identities = ("id", "call_id", "name")
    call_ids = set()
    item_ids = {}
    for index, call in calls.items():
        args = call.get("arguments")
        if (call.get("status") not in (None, "completed") or not isinstance(args, str) or not args
                or not all(isinstance(call.get(key), str) and call[key] for key in ("call_id", "name"))
                or call["call_id"] in call_ids):
            raise invalid
        call_ids.add(call["call_id"])
        item_id = call.get("id")
        if item_id is not None or index in streamed:
            if not isinstance(item_id, str) or not item_id or item_id in item_ids:
                raise invalid
            item_ids[item_id] = index

        fragments = []
        for item, done in ((added.get(index), False), (finished.get(index), True)):
            if item is None:
                continue
            statuses = (None, "completed") if done else (None, "in_progress", "completed")
            if (item.get("type") != "function_call" or item.get("status") not in statuses
                    or any(item.get(key) != call[key] for key in identities)):
                raise invalid
            item_args = item.get("arguments", "")
            if not isinstance(item_args, str) or (done and item_args != args):
                raise invalid
            if not done and item_args:
                fragments.append(item_args)

        args_done = False
        for event in argument_events.get(index, ()):
            if (args_done or event.get("item_id") != item_id
                    or any(key in event and event[key] != call[key] for key in ("call_id", "name"))):
                raise invalid
            if event["type"] == "response.function_call_arguments.delta":
                fragment = event.get("delta")
                if not isinstance(fragment, str):
                    raise invalid
                fragments.append(fragment)
            else:
                if event.get("arguments") != args:
                    raise invalid
                args_done = True
        # Compare the actual argument strings, without normalizing JSON values.
        if fragments and "".join(fragments) != args:
            raise invalid

    for items in (enumerate(output), added.items(), finished.items()):
        for index, item in items:
            item_id = item.get("id")
            if isinstance(item_id, str) and item_ids.get(item_id, index) != index:
                raise invalid


def _read_stream(response, *, check=None, replay=False):
    remaining = MAX_RESPONSE_BYTES
    data = []
    added = {}
    finished = {}
    argument_events = {}
    while True:
        if check is not None:
            check()
        line = response.readline(remaining + 1)
        if not line:
            break
        remaining -= len(line)
        if remaining < 0:
            raise PlanError("The ChatGPT response exceeded its size limit.")
        line = line.rstrip(b"\r\n")
        if line.startswith(b"data:"):
            data.append(line[5:].removeprefix(b" "))
        elif not line and data:
            raw = b"\n".join(data)
            data.clear()
            if raw == b"[DONE]":
                break
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise PlanError("ChatGPT returned an invalid stream event.")
            kind = event.get("type")
            if kind == "response.incomplete":
                raise PlanError("ChatGPT did not finish its response.")
            if kind in ("response.failed", "error"):
                result = event.get("response")
                error = result.get("error") if isinstance(result, dict) else None
                code = error.get("code") if isinstance(error, dict) else event.get("code")
                raise PlanError(_usage_message(code) or "ChatGPT reported an error before completion.")
            if replay and kind in ("response.output_item.added", "response.output_item.done"):
                item = event.get("item")
                index = event.get("output_index")
                if type(index) is not int or index < 0 or not isinstance(item, dict):
                    raise PlanError("ChatGPT returned an invalid stream item.")
                if kind == "response.output_item.added":
                    if item.get("type") == "function_call" or added.get(index, {}).get("type") == "function_call":
                        if index in added or index in finished or index in argument_events:
                            raise PlanError("ChatGPT returned an out-of-order or duplicate stream item.")
                    added[index] = item
                elif index in finished:
                    raise PlanError("ChatGPT returned a duplicate stream item.")
                else:
                    finished[index] = item
            if replay and kind in ("response.function_call_arguments.delta", "response.function_call_arguments.done"):
                index = event.get("output_index")
                if type(index) is not int or index < 0 or index in finished:
                    raise PlanError("ChatGPT returned an invalid stream argument event.")
                argument_events.setdefault(index, []).append(event)
            if kind == "response.completed":
                result = _completed(event.get("response"))
                if (added or finished) and not result["output"]:
                    if added.keys() - finished.keys() or finished.keys() != set(range(len(finished))):
                        raise PlanError("ChatGPT returned an incomplete set of stream items.")
                    result["output"] = [finished[index] for index in range(len(finished))]
                else:
                    for index, item in enumerate(result["output"]):
                        done = finished.get(index)
                        # Use the done event's complete encrypted reasoning item for stateless replay.
                        if (done and item.get("type") == done.get("type") == "reasoning"
                                and done.get("id") and item.get("id") == done["id"]
                                and done.get("encrypted_content")):
                            result["output"][index] = done
                if replay:
                    _reconcile_stream_calls(result["output"], added, finished, argument_events)
                return result
    raise PlanError("The ChatGPT stream ended before completion.")


def request_response(access_token, payload, *, timeout=60, check=None, replay=False):
    payload = {**payload, "store": False, "stream": True}
    # SIWC rejects this field; existing API-key requests still send their caps.
    payload.pop("max_output_tokens", None)
    if isinstance(payload["input"], str):
        payload["input"] = [{"role": "user", "content": payload["input"]}]
    return _request(RESPONSES_PATH, access_token, "text/event-stream",
                    lambda response: _read_stream(response, check=check, replay=replay), payload, timeout=timeout)


def list_models(access_token):
    def read_catalog(response):
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise PlanError("The ChatGPT model catalog exceeded its size limit.")
        catalog = json.loads(body)
        models = catalog.get("models") if isinstance(catalog, dict) else None
        if not isinstance(models, list):
            raise PlanError("ChatGPT returned an invalid model catalog.")
        visible = []
        for model in models:
            if not isinstance(model, dict):
                raise PlanError("ChatGPT returned an invalid model catalog.")
            if model.get("visibility") != "list":
                continue
            slug, name = model.get("slug"), model.get("display_name")
            if not isinstance(slug, str) or not slug or not isinstance(name, str) or not name.strip():
                raise PlanError("ChatGPT returned an invalid model catalog.")
            visible.append({"slug": slug, "display_name": name})
        return visible

    return _request("/v1/models", access_token, "application/json", read_catalog)
