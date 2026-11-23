"""One stateless Responses turn. The caller owns tool execution and history."""
import http.client
import json
import math
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import chatgpt_responses, providers
from .protocol import PlanError
from .settings import service_config


def _request_json(endpoint, key, payload, remaining):
    if not key:
        raise PlanError("Add your API key in Settings first.")
    request = urllib.request.Request(endpoint, json.dumps(payload).encode(), {
        "Authorization": "Bearer " + key, "Content-Type": "application/json",
        "User-Agent": "HeyComputer/0.1"})
    opener = urllib.request.build_opener(providers.NoRedirect())
    seconds = remaining()
    try:
        with opener.open(request, timeout=seconds) as response:
            body = response.read(chatgpt_responses.MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise PlanError(f"The model service returned HTTP {code}. Check your API key, endpoint, model access and billing.") from None
    except (OSError, http.client.HTTPException):
        raise PlanError("The model service connection was interrupted.") from None
    except PlanError:
        raise  # Preserve the redirect refusal from the shared client handler.
    except ValueError:
        raise PlanError("The model service request was invalid.") from None
    if len(body) > chatgpt_responses.MAX_RESPONSE_BYTES:
        raise PlanError("The model response exceeded its size limit.")
    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        raise PlanError("The model service did not return valid JSON.") from None


def _message_part(part):
    if not isinstance(part, dict):
        return False
    if part.get("type") == "output_text":
        return isinstance(part.get("text"), str)
    return part.get("type") == "refusal" and isinstance(part.get("refusal"), str)


def _replay_output(response, credential):
    invalid = PlanError("The model did not return a usable Responses turn.")
    if not isinstance(response, dict) or response.get("status") != "completed" or response.get("error"):
        raise invalid
    output = response.get("output")
    if not isinstance(output, list):
        raise invalid
    for item in output:
        if not isinstance(item, dict) or item.get("status") not in (None, "completed"):
            raise invalid
        kind = item.get("type")
        if kind == "function_call":
            if not all(isinstance(item.get(field), str) for field in ("name", "call_id", "arguments")):
                raise invalid
            if not item["name"] or not item["call_id"]:
                raise invalid
        elif kind == "message":
            content = item.get("content")
            if (item.get("role") != "assistant" or not isinstance(content, list)
                    or not all(_message_part(part) for part in content)):
                raise invalid
        elif kind != "reasoning":
            raise invalid
    try:
        encoded = json.dumps(output, allow_nan=False)
    except (ValueError, RecursionError):
        raise invalid from None
    if json.dumps(credential)[1:-1] in encoded:
        raise invalid
    return output


def _seconds_left(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise PlanError("The model turn timed out.")
    return seconds


def _provider_output(args, remaining):
    provider, endpoint, credential, payload = args
    if provider == "chatgpt":
        response = chatgpt_responses.request_response(
            credential, payload, timeout=remaining(), check=remaining, replay=True)
    else:
        response = _request_json(endpoint, credential, payload, remaining)
    return _replay_output(response, credential)


def _wait_for_provider(args, deadline, remaining):
    # A socket timeout measures inactivity, not elapsed time. Isolate only the
    # provider operation so cancellation can close even a blocked DNS/header/read.
    # Credentials and payload travel over stdin, never in argv or a temp file.
    data = json.dumps([args, deadline]).encode()
    remaining()
    with subprocess.Popen([sys.executable, '-m', __name__], cwd=Path(__file__).resolve().parents[1],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as child:
        with ThreadPoolExecutor(max_workers=1) as pipes:
            try:
                remaining()
                # Drain both pipes while the caller continues to run project/cancel checks.
                io = pipes.submit(child.communicate, data)
                while not wait([io], timeout=min(.05, remaining())).done:
                    pass
                reply, _ = io.result()
            finally:
                if child.poll() is None:
                    child.kill()  # Before joining the pipe task and reaping the child.
        remaining()
        if child.returncode:
            raise PlanError("The model service connection was interrupted.")
        error, output = json.loads(reply)
        if error is not None:
            raise PlanError(error)
        return output


def make_turn(settings, check, *, access_token=None):
    """Use fallback settings; supply ChatGPT's token via the existing caller-owned auth path.

    check() raises on cancellation. timeout is a positive budget in seconds;
    the caller enforces it independently of provider I/O.
    """
    provider, endpoint, model = service_config(settings, "fallback")

    def turn(instructions, input_items, tools, timeout):
        if type(timeout) not in (int, float) or not 0 < timeout < math.inf:
            raise PlanError("Use a positive, finite model turn timeout.")
        deadline = time.monotonic() + timeout

        def remaining():
            check()
            return _seconds_left(deadline)

        remaining()
        if not isinstance(tools, list) or any(
            not isinstance(tool, dict) or tool.get("type") != "function" for tool in tools
        ):
            raise PlanError("Only client-side function tools are supported.")
        payload = {"model": model, "instructions": instructions, "input": input_items,
                   "tools": tools, "parallel_tool_calls": False, "store": False, "stream": False,
                   "include": ["reasoning.encrypted_content"], "reasoning": {"effort": "low"},
                   "max_output_tokens": 6000}
        credential = access_token if provider == "chatgpt" else providers.api_key(provider)
        output = _wait_for_provider((provider, endpoint, credential, payload), deadline, remaining)
        remaining()
        return output

    return turn


if __name__ == '__main__':
    args, deadline = json.load(sys.stdin.buffer)
    try:
        result = (None, _provider_output(args, lambda: _seconds_left(deadline)))
    except PlanError as exc:
        result = (str(exc), None)  # Only the transports' sanitized errors leave the child.
    json.dump(result, sys.stdout)
