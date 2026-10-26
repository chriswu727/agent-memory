from __future__ import annotations

import asyncio
import json
import math
import socket
import sys
import threading
import time
from contextlib import contextmanager
from typing import Literal

import httpx
import uvicorn

from agent_memory.chat_provider import ReasoningEffort, _validate_reasoning_effort
from agent_memory.runtime import ModelRequest


class HostProviderError(RuntimeError):
    def __init__(self, code: str, *, status_code: int | None = None):
        self.code, self.status_code = code, status_code
        super().__init__(f"Host provider request failed: {code}")


def _positive_seconds(value, name):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise ValueError(f"{name} must be finite and positive")


class ChatCompletionsHost:
    def __init__(
        self, base_url: str, api_key: str, model: str, *,
        transport: httpx.BaseTransport | None = None,
        max_output_tokens: int = 4096, timeout_seconds: float = 180,
        max_response_bytes: int = 65536,
        thinking: Literal["enabled", "disabled"] = "enabled", json_mode: bool = False,
        reasoning_effort: ReasoningEffort | None = None,
    ):
        try:
            url = httpx.URL(base_url)
        except (TypeError, ValueError, httpx.InvalidURL):
            raise ValueError("base_url must be an absolute HTTP or HTTPS URL") from None
        if (url.scheme not in {"http", "https"} or not url.host or url.userinfo
                or url.query or url.fragment):
            raise ValueError("base_url must have a host and no credentials, query, or fragment")
        if not isinstance(api_key, str) or not api_key.strip() or any(
            ord(character) < 33 or ord(character) > 126 for character in api_key
        ):
            raise ValueError("api_key must be a nonempty ASCII token")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be explicitly configured")
        if not isinstance(thinking, str) or thinking not in {"enabled", "disabled"}:
            raise ValueError("thinking must be enabled or disabled")
        if type(json_mode) is not bool:
            raise ValueError("json_mode must be a boolean")
        _validate_reasoning_effort(reasoning_effort, thinking)
        for value, name in ((max_output_tokens, "max_output_tokens"),
                            (max_response_bytes, "max_response_bytes")):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        _positive_seconds(timeout_seconds, "timeout_seconds")
        self._model = model
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.thinking, self.json_mode = thinking, json_mode
        self.reasoning_effort = reasoning_effort
        self._http = httpx.Client(
            base_url=str(url).rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}", "Accept-Encoding": "identity"},
            transport=transport if transport is not None else httpx.HTTPTransport(retries=0),
            follow_redirects=False, trust_env=False,
        )

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __call__(self, request: ModelRequest) -> str:
        body = {
            "model": self._model, "messages": list(request.messages),
            "max_tokens": self.max_output_tokens, "stream": False,
            "thinking": {"type": self.thinking},
        }
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.reasoning_effort is not None:
            body["reasoning_effort"] = self.reasoning_effort
        deadline = time.monotonic() + self.timeout_seconds
        try:
            with self._http.stream(
                "POST", "chat/completions", json=body, timeout=self.timeout_seconds,
            ) as response:
                if response.status_code != 200:
                    raise HostProviderError("http_error", status_code=response.status_code)
                data = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() >= deadline:
                        raise HostProviderError("timeout")
                    if len(data) + len(chunk) > self.max_response_bytes:
                        raise HostProviderError("response_too_large")
                    data.extend(chunk)
                if time.monotonic() >= deadline:
                    raise HostProviderError("timeout")
        except httpx.TimeoutException:
            raise HostProviderError("timeout") from None
        except httpx.HTTPError:
            raise HostProviderError("transport_error") from None
        try:
            value = json.loads(data)
            if not isinstance(value, dict) or "error" in value:
                raise ValueError
            choices = value["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError
            choice = choices[0]
            if choice["finish_reason"] != "stop":
                raise HostProviderError("incomplete_response")
            message = choice["message"]
            if message.get("tool_calls") or message.get("function_call"):
                raise HostProviderError("unexpected_tool_call")
            content = message["content"]
            if (message.get("role") != "assistant" or not isinstance(content, str)
                    or not content.strip()):
                raise ValueError
            content.encode("utf-8")
            return content
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
            raise HostProviderError("invalid_response") from None


class LoopbackServerError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"Loopback server failed: {code}")


@contextmanager
def loopback_server(app, *, startup_timeout_seconds=10, shutdown_timeout_seconds=10):
    _positive_seconds(startup_timeout_seconds, "startup_timeout_seconds")
    _positive_seconds(shutdown_timeout_seconds, "shutdown_timeout_seconds")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    state, stopped = {}, threading.Event()
    thread = None
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=0, log_level="critical", log_config=None,
            access_log=False, lifespan="on", loop="asyncio", http="h11", ws="none",
            timeout_graceful_shutdown=max(0.01, shutdown_timeout_seconds / 2),
        ))

        async def serve():
            state["loop"], state["task"] = asyncio.get_running_loop(), asyncio.current_task()
            try:
                await server.serve(sockets=[listener])
            except asyncio.CancelledError:
                pass

        def run():
            try:
                asyncio.run(serve())
            except BaseException:
                state["failed"] = True
            finally:
                stopped.set()

        thread = threading.Thread(target=run, name="memory-evaluation-http", daemon=True)
        thread.start()
        deadline = time.monotonic() + startup_timeout_seconds
        while not server.started:
            if stopped.is_set():
                raise LoopbackServerError("startup_failed")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LoopbackServerError("startup_timeout")
            stopped.wait(min(0.01, remaining))
        yield base_url
    finally:
        active_error = sys.exception()
        try:
            if thread is not None:
                deadline = time.monotonic() + shutdown_timeout_seconds
                server.should_exit = True
                thread.join(timeout=shutdown_timeout_seconds * 0.75)
                if thread.is_alive():
                    server.force_exit = True
                    loop, task = state.get("loop"), state.get("task")
                    if loop is not None and not loop.is_closed() and task is not None:
                        try:
                            loop.call_soon_threadsafe(task.cancel)
                        except RuntimeError:
                            pass
                    listener.close()
                    thread.join(timeout=max(0, deadline - time.monotonic()))
                if thread.is_alive():
                    if active_error is None:
                        raise LoopbackServerError("shutdown_timeout")
                    active_error.add_note("Loopback server cleanup failed: shutdown_timeout")
                elif state.get("failed") and active_error is None:
                    raise LoopbackServerError("server_failed")
        finally:
            listener.close()
