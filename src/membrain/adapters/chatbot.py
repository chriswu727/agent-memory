"""Memory for chat bots that serve many people on messaging platforms (QQ, WeChat, Telegram, Discord, Feishu...).

Bot frameworks call ``context`` before each model request and ``record`` after the reply. Each person gets their own
memory (``membrain.host`` user brains), so notes, history and recall never cross between users. By default what
someone says in a group is kept apart from their private chats with the bot, so a group reply cannot reveal a private
conversation; ``group_memory="shared"`` joins them.

Bots keep one conversation per chat for months, while memBrain leaves the current conversation out of recall because
the host already shows it. Sessions are therefore one calendar day of a chat (and restart when the bot framework
starts a new conversation): today's messages are in the framework's own context, earlier days come from memory.
"""
from __future__ import annotations

import datetime as _datetime
import hashlib
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .. import host

TOOLS = {"note": "membrain_note", "recall": "membrain_search", "pause": "membrain_pause"}
GROUP_MEMORY = ("separate", "shared")
BLOCK_SECONDS = 300
MAX_SESSION = 200


@dataclass(frozen=True)
class Speaker:
    """Who is talking: the platform (adapter type or instance), their ID there, and the group if not a private chat."""

    platform: str
    user: str
    group: str | None = None

    def usable(self) -> bool:
        return bool(str(self.platform).strip() and str(self.user).strip())


def _short(value: str, size: int = 24) -> str:
    value = str(value)
    return value if len(value) <= size else hashlib.sha256(value.encode()).hexdigest()[:size]


class ChatMemory:
    """One memBrain installation serving every user of a bot. Methods block; async hosts run them in a thread."""

    def __init__(self, directory: Path, *, group_memory: str = "separate", record_groups: bool = True,
                 producer: str = "membrain-chatbot", tools: dict | None = TOOLS,
                 block_seconds: float = BLOCK_SECONDS):
        if group_memory not in GROUP_MEMORY:
            raise ValueError(f"group_memory must be one of {', '.join(GROUP_MEMORY)}.")
        self.directory = Path(directory).expanduser()
        self.group_memory, self.record_groups, self.producer, self.tools = group_memory, record_groups, producer, tools
        self.block_seconds = block_seconds
        self._blocks: dict[str, tuple[float, str, str]] = {}
        self._lock = threading.Lock()
        host.state_directory(self.directory)

    def start(self) -> None:
        """Start the local service in the background if it is not running; returns at once."""
        client = host.ready_client(self.directory)
        if client is None:
            host.start_in_background(self.directory)
        else:
            client.close()

    def user_id(self, speaker: Speaker) -> str:
        user = f"{_short(speaker.platform, 40)}:{_short(speaker.user, 80)}"
        if speaker.group and self.group_memory == "separate":
            return f"{_short(speaker.platform, 40)}:group:{_short(speaker.group, 60)}:{_short(speaker.user, 80)}"
        return user

    def session(self, speaker: Speaker, conversation: str | None = None, *, today: _datetime.date | None = None) -> str:
        """The memBrain session for this speaker today in this chat (or framework conversation)."""
        day = (today or _datetime.date.today()).isoformat()
        chat = conversation or speaker.group or "private"
        return f"{_short(speaker.platform, 40)}:{_short(chat, 60)}:{_short(speaker.user, 60)}:{day}"[:MAX_SESSION]

    def _recording(self, speaker: Speaker) -> bool:
        return speaker.usable() and (self.record_groups or not speaker.group)

    def _block(self, session: str, user: str) -> str:
        now = time.monotonic()
        with self._lock:
            cached = self._blocks.get(session)
        if cached and now - cached[0] < self.block_seconds:
            return cached[2]
        block = host.session_context(self.directory, session, tools=self.tools, user_id=user)
        with self._lock:
            self._blocks[session] = (now, user, block)
        return block

    def _forget_blocks(self, user: str) -> None:
        with self._lock:
            for session in [key for key, value in self._blocks.items() if value[1] == user]:
                del self._blocks[session]

    def context(self, speaker: Speaker, text: str, *, session: str, turn_id: str | None = None) -> str | None:
        """Memory to add to the system prompt for this message: notice, notes, history and related messages.

        The message is staged and stored with the reply by ``record``. Returns ``None`` when nothing applies.
        """
        if not self._recording(speaker):
            return None
        user = self.user_id(speaker)
        block = self._block(session, user)
        recalled = host.prompt_context(self.directory, session, text, turn_id=turn_id, producer=self.producer,
                                       user_id=user)
        return "\n\n".join(part for part in (block, recalled) if part) or None

    def record(self, speaker: Speaker, reply: str | None, *, session: str, turn_id: str | None = None,
               prompt: str | None = None) -> None:
        """Store the staged message (or ``prompt``) and the reply, unless this user paused recording."""
        if self._recording(speaker):
            host.finish_turn(self.directory, session, reply, turn_id=turn_id, producer=self.producer, prompt=prompt,
                             user_id=self.user_id(speaker))

    def note(self, speaker: Speaker, text: str, replaces=()) -> dict:
        from ..notes import save_note

        user = self.user_id(speaker)
        client = self._client(user)
        if client is None:
            return {"error": "memBrain is starting; try again in a few seconds."}
        with client:
            result = save_note(client, host.scrub(str(text or "")), replaces=tuple(replaces or ()))
        self._forget_blocks(user)
        return result

    def search(self, speaker: Speaker, query: str, *, session: str | None = None) -> dict:
        client = self._client(self.user_id(speaker))
        if client is None:
            return {"error": "memBrain is starting; try again in a few seconds."}
        with client:
            found = host.recall_context(client, host.scrub(str(query or "")), current_conversation=session)
        return {"context": found or "No matching records."}

    def pause(self, speaker: Speaker, hours: float | None = None) -> dict:
        user = self.user_id(speaker)
        result = host.pause(self.directory, hours, user_id=user)
        self._forget_blocks(user)
        return result

    def resume(self, speaker: Speaker) -> dict:
        user = self.user_id(speaker)
        result = host.resume(self.directory, user_id=user)
        self._forget_blocks(user)
        return result

    def _client(self, user: str):
        client = host.ready_client(self.directory, user)
        if client is None:
            host.start_in_background(self.directory)
        return client
