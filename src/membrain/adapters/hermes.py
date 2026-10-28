"""Hermes Agent memory provider (Nous Research), registered through the ``hermes_agent.memory_providers`` entry point.

Enable it with ``memory.provider: membrain`` in Hermes's ``config.yaml`` (or ``hermes memory setup``). The provider
maps Hermes's lifecycle onto ``membrain.host``: the static system-prompt block carries the session notice, saved notes
and recent history; ``prefetch`` returns related earlier messages for the turn (Hermes fences it as memory context);
``sync_turn`` stores the user message and reply together; and ``on_memory_write`` mirrors writes to Hermes's built-in
``MEMORY.md`` / ``USER.md`` into memBrain notes. Memory is shared with other hosts using the same installation, so
the same person is remembered in Claude Code and Hermes alike.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .. import host
from ..notes import NOTE_FIELDS, NOTE_TOOL_DESCRIPTION

try:  # Hermes is only present when this module is loaded by Hermes; tests run without it.
    from agent.memory_provider import MemoryProvider as _Base
except ImportError:  # pragma: no cover - exercised only without Hermes installed
    _Base = object

PRODUCER = "membrain-hermes"
IMPORT_PRODUCER = "membrain-import:hermes"  # Notes that mirror Hermes's own USER.md.
TOOLS = {"note": "membrain_note", "recall": "membrain_search", "pause": "membrain_pause"}
_SCHEMAS = [
    {"name": "membrain_note", "description": NOTE_TOOL_DESCRIPTION,
     "parameters": {"type": "object", "properties": {
         "text": {"type": "string"}, "replaces": {"type": "array", "items": {"type": "string"}}, **NOTE_FIELDS},
         "required": ["text"]}},
    {"name": "membrain_search", "description": (
        "Search the user's earlier conversations and notes by a few concrete keywords (names, places, activities, "
        "dates) when a request depends on personal context that is not already shown."),
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "membrain_pause", "description": (
        "Stop recording, for example when the user asks you not to remember something. The current exchange is not "
        "stored either. Without hours it lasts until membrain_resume."),
     "parameters": {"type": "object", "properties": {"hours": {"type": "number"}}}},
    {"name": "membrain_resume", "description": "Resume recording after membrain_pause, when the user asks.",
     "parameters": {"type": "object", "properties": {}}},
]


def _default_directory(hermes_home: str | None) -> Path:
    """Share the user's memBrain installation for the default Hermes profile; keep other profiles separate."""
    if hermes_home:
        configured = Path(hermes_home) / "membrain.json"
        try:
            value = json.loads(configured.read_text(encoding="utf-8")).get("directory")
            if value:
                return Path(value).expanduser()
        except (OSError, ValueError, AttributeError):
            pass
        default_home = Path(os.environ.get("HOME", "~")).expanduser() / ".hermes"
        if Path(hermes_home).expanduser().resolve() != default_home.resolve():
            return Path(hermes_home) / "membrain"
    return host.profile_directory()


class MembrainProvider(_Base):
    """memBrain as a Hermes memory provider. One instance per Hermes profile, bound in ``initialize``."""

    def __init__(self):
        self._directory: Path | None = None
        self._hermes_home: Path | None = None
        self._session = ""
        self._writes = True
        self._block = None

    @property
    def name(self) -> str:
        return "membrain"

    def is_available(self) -> bool:
        return True  # Local and keyless: the service is created and started on demand.

    def unavailable_reason(self) -> str:
        return ""

    def initialize(self, session_id: str, **kwargs) -> None:
        self._directory = _default_directory(kwargs.get("hermes_home"))
        self._hermes_home = Path(kwargs["hermes_home"]).expanduser() if kwargs.get("hermes_home") else None
        self._session = session_id or "hermes"
        self._writes = kwargs.get("agent_context", "primary") == "primary"
        host.state_directory(self._directory)
        if host.ready_client(self._directory) is None:
            host.start_in_background(self._directory)

    def system_prompt_block(self) -> str:
        if self._directory is None:
            return ""
        if self._block is None:
            if self._writes:
                self._import_user_file()
            # Hermes puts its own USER.md and MEMORY.md in the prompt, so notes copied from them are not repeated.
            self._block = host.session_context(self._directory, self._session, tools=TOOLS,
                                               skip_imports_from=("hermes",))
        return self._block

    def _import_user_file(self) -> None:
        """Copy entries of Hermes's USER.md that memBrain does not have yet, once per change of the file."""
        from ..imports import Source, import_notes

        if self._hermes_home is None:
            return
        path = self._hermes_home / "memories" / "USER.md"
        marker = host.state_directory(self._directory) / "hermes-user-import.json"
        try:
            stat = path.stat()
            signature = f"{path.resolve()}:{stat.st_mtime_ns}:{stat.st_size}"
            if marker.exists() and marker.read_text(encoding="utf-8") == signature:
                return
            client = host.ready_client(self._directory)
            if client is None:
                return
            with client:
                import_notes(client, [Source("hermes", path, True, "hermes")])
            marker.write_text(signature, encoding="utf-8")
        except FileNotFoundError:
            return
        except Exception as error:  # Importing is a convenience; it must never stop a Hermes session.
            host.log(self._directory, f"hermes import failed: {type(error).__name__}")

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._directory is None or not self._writes:
            return ""
        return host.prompt_context(self._directory, session_id or self._session, query, producer=PRODUCER) or ""

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "",
                  messages: list[dict[str, Any]] | None = None, **kwargs) -> None:
        if self._directory is None or not self._writes:
            return
        host.finish_turn(self._directory, session_id or self._session, assistant_content, prompt=user_content,
                         producer=PRODUCER)

    def on_session_switch(self, new_session_id: str, *, parent_session_id: str = "", reset: bool = False,
                          rewound: bool = False, **kwargs) -> None:
        self._session = new_session_id or self._session
        if reset:
            self._block = None

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return list(_SCHEMAS)

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs) -> str:
        from ..notes import note_fields, save_note

        try:
            if tool_name == "membrain_pause":
                return json.dumps(host.pause(self._directory, args.get("hours")))
            if tool_name == "membrain_resume":
                return json.dumps(host.resume(self._directory))
            client = host.ready_client(self._directory)
            if client is None:
                host.start_in_background(self._directory)
                return json.dumps({"error": "memBrain is starting; try again in a few seconds."})
            with client:
                if tool_name == "membrain_note":
                    return json.dumps(save_note(client, args.get("text", ""), replaces=args.get("replaces") or (),
                                                **note_fields(args)))
                if tool_name == "membrain_search":
                    found = host.recall_context(client, host.scrub(str(args.get("query", ""))),
                                                current_conversation=self._session)
                    return json.dumps({"context": found or "No matching records."}, ensure_ascii=False)
        except ValueError as error:
            return json.dumps({"error": str(error)})
        return json.dumps({"error": f"Unknown tool {tool_name}"})

    def on_memory_write(self, action: str, target: str, content: str, metadata: dict[str, Any] | None = None) -> None:
        """Mirror Hermes's writes about the user (USER.md) as notes, replacing or removing the matching earlier note.

        Writes to MEMORY.md describe Hermes's environment and stay in Hermes.
        """
        if self._directory is None or not self._writes or target != "user" or host.paused(self._directory):
            return
        from ..notes import list_notes, save_note

        previous = (metadata or {}).get("previous_content")
        client = host.ready_client(self._directory)
        if client is None:
            return
        with client:
            matching = [item["event_id"] for item in list_notes(client, max_characters=200_000)["items"]
                        if isinstance(previous, str) and item["text"] == " ".join(previous.split())]
            if action == "remove":
                if matching:
                    generation = client.dashboard()["brain"]["scope_generation"]
                    client.forget(matching, kind="events", mode="suppress_recall",
                                  expected_scope_generation=generation, conversation_id="membrain-notes",
                                  branch_id="main")
                return
            text = " ".join(host.scrub(content or "").split())
            if text:
                save_note(client, text[:300], replaces=matching, producer=IMPORT_PRODUCER)

    def get_config_schema(self) -> list[dict[str, Any]]:
        return [{"key": "directory", "required": False, "default": "", "type": "text", "description": (
            "memBrain installation directory. Leave empty to share memory with other hosts on this computer "
            "(non-default Hermes profiles get their own installation).")}]

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        path = Path(hermes_home) / "membrain.json"
        path.write_text(json.dumps({"directory": values.get("directory") or ""}), encoding="utf-8")

    def shutdown(self) -> None:
        return None


def register(ctx) -> None:
    ctx.register_memory_provider(MembrainProvider())
