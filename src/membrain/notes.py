"""Notes curated by the host model: short durable facts shown at the start of every session.

Raw messages grow without bound and similarity search cannot infer that a seafood menu matters to a shellfish
allergy. The host model can: when the user states a lasting fact, it saves a one-line note, and replaces the note
when the fact changes. Notes are stored as assistant-authored events in a reserved conversation, so they keep
attribution, deletion and backup behaviour, and are never presented as the user's own words.
"""
from __future__ import annotations

from .helpers import uid

NOTES_CONVERSATION = "membrain-notes"
NOTE_CHARACTERS = 300
NOTES_CHARACTERS = 3500
NOTE_PRODUCER = "membrain-notes"
# Notes copied from a host's own memory files carry the host in their producer, e.g. ``membrain-import:hermes``.
IMPORT_PREFIX = "membrain-import:"
HOST_NAMES = {"claude-code": "Claude Code", "codex": "Codex", "hermes": "Hermes", "openclaw": "OpenClaw",
              "workbuddy": "WorkBuddy"}


def imported_from(item: dict) -> str | None:
    producer = item.get("producer") or ""
    return producer[len(IMPORT_PREFIX):] if producer.startswith(IMPORT_PREFIX) else None


def list_notes(client, *, max_characters: int = NOTES_CHARACTERS) -> dict:
    return client.timeline(roles=("assistant", "user"), conversation_id=NOTES_CONVERSATION, max_characters=max_characters,
                           max_item_characters=NOTE_CHARACTERS, limit=200)


def save_note(client, text: str, *, replaces=(), producer: str = NOTE_PRODUCER) -> dict:
    """Save one note; ``replaces`` lists IDs of notes it supersedes, which stop being shown."""
    text = " ".join(str(text).split())
    if not text:
        raise ValueError("A note needs text.")
    if len(text) > NOTE_CHARACTERS:
        raise ValueError(f"Keep a note under {NOTE_CHARACTERS} characters; save separate facts separately.")
    response = client.observe(text, conversation_id=NOTES_CONVERSATION, branch_id="main",
                              source_event_key="note-" + uid(), role="assistant", actor_ref="assistant",
                              producer_ref=producer, delivery_state="unknown")
    note_id = response["result"]["accepted_event_ids"][0]
    current = {item["event_id"] for item in list_notes(client, max_characters=200_000)["items"]} if replaces else set()
    stale = [value for value in dict.fromkeys(replaces) if value in current and value != note_id]
    if stale:
        generation = client.dashboard()["brain"]["scope_generation"]
        client.forget(stale, kind="events", mode="suppress_recall", expected_scope_generation=generation,
                      conversation_id=NOTES_CONVERSATION, branch_id="main")
    return {"note_id": note_id, "replaced": stale, "unknown": [value for value in replaces if value not in stale]}


def _label(item: dict) -> str:
    source = imported_from(item)
    if source:
        return f" · imported from {HOST_NAMES.get(source, source)}"
    return " · written by the user" if item.get("actor_role") == "user" else ""


def render_notes(response: dict, *, tool: str | None = "memory_note", skip_imports_from=()) -> str | None:
    """The notes block; ``skip_imports_from`` leaves out notes copied from hosts that show their own memory files."""
    items = [item for item in response.get("items", []) if isinstance(item.get("text"), str) and item["text"].strip()
             and imported_from(item) not in skip_imports_from]
    if not items:
        return None
    lines = [f"- [{(item.get('recorded_at') or '')[:10]}{_label(item)}] "
             f"{' '.join(item['text'].split())} (id: {item['event_id']})" for item in reversed(items)]
    more = "" if response.get("complete", True) else " Older notes did not fit and are not shown."
    return ("<membrain-notes>\nNotes about this user from earlier sessions, oldest first. Unless marked as written "
            "by the user or imported, they are your own summaries, not the user's words; notes the user wrote or "
            "corrected take precedence over yours, and the user's current message over both."
            + (f" When a fact changes, save the new note with {tool} and pass the old note's id in replaces."
               if tool else "") + more + "\n"
            + "\n".join(lines) + "\n</membrain-notes>")
