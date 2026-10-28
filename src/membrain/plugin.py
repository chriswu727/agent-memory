"""Claude Code and Codex adapter: hook commands over the host-independent operations in ``membrain.host``.

Hooks never own the host's turn and always exit 0. ``SessionStart`` adds saved notes and recent history,
``UserPromptSubmit`` adds related earlier messages and stages the prompt, and ``Stop`` stores the prompt
and the reply together. Before the runtime is installed, the hook commands queue raw hook input in the
plugin data directory with plain shell; it is imported on the next start. Codex runs the same hooks with the
same input fields, except that it identifies a turn by ``turn_id`` instead of ``prompt_id``.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

from . import host
from .host import (  # noqa: F401  (re-exported for callers of the previous plugin API)
    CONTEXT_CHARACTERS,
    HISTORY_CHARACTERS,
    HISTORY_ITEM_CHARACTERS,
    MAX_MESSAGES,
    MESSAGE_CHARACTERS,
    NOTES_NOTICE,
    PRODUCER,
    RECALL_TOKENS,
    SESSION_CONTEXT_CHARACTERS,
    compact_message,
    ensure_installation,
    fetch_model,
    profile_directory,
    recall,
    recall_budget,
    recall_context,
    render_context,
    render_history,
    semantic_state,
)

MAX_INPUT_BYTES = 4 * 1024 * 1024
PRODUCERS = {"claude-code": PRODUCER, "codex": "membrain-codex", "openclaw": "membrain-openclaw",
             "workbuddy": "membrain-workbuddy"}
TRANSCRIPT_TAIL_BYTES = 2 * 1024 * 1024
# Hosts that run the MCP bridge use its tool names; others call ``membrain plugin tool`` under these names.
HOST_TOOLS = {"openclaw": {"note": "membrain_note", "recall": "membrain_search", "pause": "membrain_pause"}}
TOOLS = ("note", "search", "pause", "resume")


def _conversation(payload: dict) -> str:
    session = payload.get("session_id")
    if not isinstance(session, str) or not 1 <= len(session) <= 200:
        raise ValueError("Hook input has no usable session_id.")
    return session


def _turn(payload: dict) -> str | None:
    turn = payload.get("prompt_id") or payload.get("turn_id")
    return turn if isinstance(turn, str) and 1 <= len(turn) <= 120 else None


def _import_host_queue(directory: Path, producer: str = PRODUCER) -> int:
    """Spool hook inputs that the host queued before the runtime was installed.

    Hook commands append raw hook JSON to ``$CLAUDE_PLUGIN_DATA/pending-hooks.jsonl`` with plain shell
    when the runtime cannot start yet, so a first session's messages are not lost while it installs.
    """
    root = os.environ.get("CLAUDE_PLUGIN_DATA") or os.environ.get("PLUGIN_DATA")  # Codex sets both.
    if not root:
        return 0
    queue = Path(root) / "pending-hooks.jsonl"
    if not queue.exists():
        return 0
    claimed = queue.with_name(f"pending-hooks.importing-{os.getpid()}-{time.time_ns()}.jsonl")
    try:
        os.replace(queue, claimed)  # New appends start a fresh file; this process owns the claimed one.
    except OSError:
        return 0
    imported = 0
    for line in claimed.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            payload = json.loads(line)
            if isinstance(payload.get("prompt"), str) and payload["prompt"].strip():
                role, text = "user", payload["prompt"]
            elif isinstance(payload.get("last_assistant_message"), str) and payload["last_assistant_message"].strip():
                role, text = "assistant", payload["last_assistant_message"]
            else:
                continue
            record = host.message_record(_conversation(payload), role, text, turn_id=_turn(payload),
                                         producer=producer)
        except (ValueError, AttributeError):
            continue  # A torn or unrelated line cannot be recorded and must not block the rest.
        if host.deliver(directory, None, record):  # Spooled in order; replayed by the next ready call.
            imported += 1
    claimed.unlink(missing_ok=True)
    return imported


def start(directory: Path) -> dict:
    imported = _import_host_queue(directory)
    result = host.start(directory)
    result["host_queue_imported"] = imported
    return result


def session_start(payload: dict, directory: Path, producer: str = PRODUCER) -> str | None:
    _import_host_queue(directory, producer)
    tools = next((names for name, names in HOST_TOOLS.items() if PRODUCERS[name] == producer), host.TOOL_NAMES)
    return host.session_context(directory, _conversation(payload), tools=tools)


def user_prompt(payload: dict, directory: Path, producer: str = PRODUCER) -> str | None:
    _import_host_queue(directory, producer)
    return host.prompt_context(directory, _conversation(payload), payload.get("prompt"), turn_id=_turn(payload),
                               producer=producer)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content
                         if isinstance(part, dict) and part.get("type") in ("text", "output_text")
                         and isinstance(part.get("text"), str))
    return ""


def last_assistant_message(transcript: str | None) -> str | None:
    """The final reply in a host transcript (JSON lines), for hosts whose Stop hook does not include it.

    Reads entries shaped like Claude Code's (``{"type": "assistant", "message": {...}}``) or plain chat messages
    (``{"role": "assistant", "content": ...}``), newest first, and stops at the user's prompt.
    """
    if not isinstance(transcript, str) or not transcript:
        return None
    try:
        with open(transcript, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - TRANSCRIPT_TAIL_BYTES))
            lines = stream.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        message = entry.get("message") if isinstance(entry.get("message"), dict) else entry
        role = message.get("role") or entry.get("type")
        text = _text(message.get("content"))
        if role == "assistant" and text.strip():
            return text
        if role == "user" and text.strip():
            return None  # The prompt came after the last reply: this turn has no text reply.
    return None


def stop(payload: dict, directory: Path, producer: str = PRODUCER) -> None:
    _import_host_queue(directory, producer)
    reply = payload.get("last_assistant_message")
    if not isinstance(reply, str) or not reply.strip():
        reply = last_assistant_message(payload.get("transcript_path"))
    host.finish_turn(directory, _conversation(payload), reply, turn_id=_turn(payload), producer=producer)


HANDLERS = {
    "session-start": ("SessionStart", session_start),
    "user-prompt": ("UserPromptSubmit", user_prompt),
    "stop": ("Stop", stop),
}


def run_hook(event: str, directory: Path | None = None, stream=None, *, host_name: str = "claude-code") -> int:
    """Run one hook. Always returns 0 so a memory failure never blocks the host."""
    directory = profile_directory(directory)
    try:
        raw = (stream or sys.stdin.buffer).read(MAX_INPUT_BYTES + 1)
        payload = json.loads(raw) if raw and len(raw) <= MAX_INPUT_BYTES else None
    except (ValueError, OSError):
        payload = None
    if not isinstance(payload, dict) or event not in HANDLERS:
        return 0
    name, handler = HANDLERS[event]
    try:
        context = handler(payload, directory, PRODUCERS[host_name])
    except Exception:
        host.log(directory, f"{event} failed: {traceback.format_exc(limit=3)}")
        return 0
    if context:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": name, "additionalContext": context}},
                                    ensure_ascii=False))
        sys.stdout.flush()
    return 0


def run_tool(name: str, directory: Path | None = None, stream=None) -> int:
    """Run one memory tool for hosts without MCP: JSON arguments on stdin, a JSON result on stdout."""
    from .notes import save_note

    directory = profile_directory(directory)
    try:
        raw = (stream or sys.stdin.buffer).read(MAX_INPUT_BYTES + 1)
        arguments = json.loads(raw) if raw.strip() else {}
        if not isinstance(arguments, dict) or name not in TOOLS:
            raise ValueError("Tool arguments must be a JSON object.")
        if name == "pause":
            result = host.pause(directory, arguments.get("hours"))
        elif name == "resume":
            result = host.resume(directory)
        else:
            client = host.ready_client(directory)
            if client is None:
                host.start_in_background(directory)
                result = {"error": "memBrain is starting; try again in a few seconds."}
            else:
                with client:
                    if name == "note":
                        result = save_note(client, host.scrub(str(arguments.get("text") or "")),
                                           replaces=tuple(arguments.get("replaces") or ()))
                    else:
                        found = host.recall_context(client, host.scrub(str(arguments.get("query") or "")),
                                                    current_conversation=arguments.get("session_id"))
                        result = {"context": found or "No matching records."}
    except Exception as error:  # A tool reports failure to the model instead of crashing the host.
        result = {"error": str(error) if isinstance(error, ValueError) else type(error).__name__}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()
    return 0
