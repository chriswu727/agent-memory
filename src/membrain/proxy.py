"""OpenAI-compatible memory proxy: give any bot that lets you set a ``base_url`` long-term memory.

Point the bot's ``base_url`` at the proxy instead of the model provider (DeepSeek, OpenAI, a local model...). For each
chat completion the proxy adds the person's memory as a system message just before their latest message, forwards
the request upstream (streaming or not), and stores the message and the reply. Everything else is passed through.

People are told apart by the request's ``user`` field or the ``X-Membrain-User`` header (with optional
``X-Membrain-Platform`` and ``X-Membrain-Group``); requests without either share one memory. The memory system
message is placed after the stable start of the conversation so providers' prompt caches keep working.

By default the proxy listens on loopback and forwards the client's own API key. To serve other machines, store the
provider key in ``MEMBRAIN_UPSTREAM_KEY`` and set ``MEMBRAIN_PROXY_TOKEN``; clients then use the token as their key.
"""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .adapters.chatbot import ChatMemory, Speaker

PRODUCER = "membrain-proxy"
DEFAULT_PORT = 8790
_FORWARDED = ("content-type", "accept", "openai-organization", "openai-project", "anthropic-version")
_logger = logging.getLogger("membrain.proxy")


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content
                         if isinstance(part, dict) and part.get("type") in ("text", "input_text"))
    return ""


def _latest_user(messages: list) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], dict) and messages[index].get("role") == "user":
            return index
    return None


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def create_proxy_app(memory: ChatMemory, upstream: str, *, upstream_key: str | None = None,
                     token: str | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """The proxy app. With ``token`` clients must present it and ``upstream_key`` is sent upstream instead."""
    if bool(token) != bool(upstream_key):
        raise ValueError("Set both the upstream key and the proxy token, or neither.")
    upstream = upstream.rstrip("/")
    client = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=15), transport=transport)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await client.aclose()

    app = FastAPI(title="memBrain memory proxy", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def headers(request: Request) -> dict | None:
        forwarded = {name: request.headers[name] for name in _FORWARDED if name in request.headers}
        supplied = request.headers.get("authorization", "")
        if token:
            presented = supplied.removeprefix("Bearer ").strip()
            if not hmac.compare_digest(presented.encode(), token.encode()):
                return None
            forwarded["authorization"] = f"Bearer {upstream_key}"
        elif supplied:
            forwarded["authorization"] = supplied
        return forwarded

    def target(path: str) -> str:
        path = path.lstrip("/")
        return f"{upstream}/{path.removeprefix('v1/') if path.startswith('v1/') else path}"

    def speaker(request: Request, body: dict) -> Speaker:
        user = request.headers.get("x-membrain-user") or body.get("user")
        platform = request.headers.get("x-membrain-platform") or "api"
        group = request.headers.get("x-membrain-group") or None
        return Speaker(platform, str(user) if user else "default", group)

    async def chat(request: Request, path: str) -> Response:
        forwarded = headers(request)
        if forwarded is None:
            return JSONResponse({"error": {"message": "Invalid proxy token.", "type": "invalid_request_error"}}, 401)
        raw = await request.body()
        try:
            body = json.loads(raw)
            messages = body["messages"]
            assert isinstance(messages, list)
        except (ValueError, KeyError, AssertionError, TypeError):
            return await passthrough(request, path, raw, forwarded)
        person, index = speaker(request, body), _latest_user(messages)
        session = request.headers.get("x-membrain-session") or memory.session(person)
        turn, prompt = uuid.uuid4().hex[:20], _text(messages[index]["content"]) if index is not None else ""
        if prompt.strip():
            try:
                block = await asyncio.to_thread(memory.context, person, prompt, session=session, turn_id=turn)
            except Exception as error:  # Memory must never stop the bot from answering.
                _logger.warning("memory failed: %s", type(error).__name__)
                block = None
            if block:
                body["messages"] = [*messages[:index], {"role": "system", "content": block}, *messages[index:]]

        async def store(reply: str) -> None:
            if prompt.strip() and reply.strip():
                try:
                    await asyncio.to_thread(memory.record, person, reply, session=session, turn_id=turn,
                                            prompt=prompt)
                except Exception as error:
                    _logger.warning("recording failed: %s", type(error).__name__)

        outgoing = client.build_request("POST", target(path), headers=forwarded, json=body)
        if not body.get("stream"):
            response = await client.send(outgoing)
            if response.status_code == 200:
                try:
                    await store(_text(response.json()["choices"][0]["message"].get("content")))
                except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                    pass
            return Response(response.content, response.status_code,
                            media_type=response.headers.get("content-type"))
        response = await client.send(outgoing, stream=True)

        async def relay():
            parts = []
            try:
                async for line in response.aiter_lines():
                    yield line + "\n"
                    if line.startswith("data:") and response.status_code == 200:
                        try:
                            delta = json.loads(line[5:])["choices"][0]["delta"].get("content")
                        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                            continue
                        if isinstance(delta, str):
                            parts.append(delta)
            finally:
                await response.aclose()
            await store("".join(parts))

        return StreamingResponse(relay(), response.status_code,
                                 media_type=response.headers.get("content-type", "text/event-stream"))

    async def passthrough(request: Request, path: str, raw: bytes | None = None, forwarded: dict | None = None):
        forwarded = forwarded if forwarded is not None else headers(request)
        if forwarded is None:
            return JSONResponse({"error": {"message": "Invalid proxy token.", "type": "invalid_request_error"}}, 401)
        response = await client.request(request.method, target(path), headers=forwarded,
                                        params=request.query_params,
                                        content=raw if raw is not None else await request.body())
        return Response(response.content, response.status_code, media_type=response.headers.get("content-type"))

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def route(path: str, request: Request):
        if request.method == "POST" and path.strip("/").removeprefix("v1/") == "chat/completions":
            return await chat(request, path)
        return await passthrough(request, path)

    return app


def serve(upstream: str, *, host: str = "127.0.0.1", port: int = DEFAULT_PORT, directory: Path | None = None,
          group_memory: str = "separate", upstream_key: str | None = None, token: str | None = None) -> None:
    import uvicorn

    from . import host as host_module

    if not is_loopback(host) and not token:
        raise SystemExit("Listening beyond this computer needs MEMBRAIN_PROXY_TOKEN and MEMBRAIN_UPSTREAM_KEY, so "
                         "only your bots can use the proxy and read memory.")
    directory = host_module.profile_directory(directory)
    host_module.ensure_installation(directory)
    memory = ChatMemory(directory, group_memory=group_memory, producer=PRODUCER, tools=None)
    memory.start()
    print(f"memBrain proxy: set your bot's base_url to http://{host}:{port}/v1 (forwarding to {upstream})",
          flush=True)
    uvicorn.run(create_proxy_app(memory, upstream, upstream_key=upstream_key, token=token), host=host, port=port,
                log_level="warning")
