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


def list_notes(client, *, max_characters: int = NOTES_CHARACTERS) -> dict:
    return client.timeline(roles=("assistant",), conversation_id=NOTES_CONVERSATION, max_characters=max_characters,
                           max_item_characters=NOTE_CHARACTERS, limit=200)


def save_note(client, text: str, *, replaces=()) -> dict:
    """Save one note; ``replaces`` lists IDs of notes it supersedes, which stop being shown."""
    text = " ".join(str(text).split())
    if not text:
        raise ValueError("A note needs text.")
    if len(text) > NOTE_CHARACTERS:
        raise ValueError(f"Keep a note under {NOTE_CHARACTERS} characters; save separate facts separately.")
    response = client.observe(text, conversation_id=NOTES_CONVERSATION, branch_id="main",
                              source_event_key="note-" + uid(), role="assistant", actor_ref="assistant",
                              producer_ref="membrain-notes", delivery_state="unknown")
    note_id = response["result"]["accepted_event_ids"][0]
    current = {item["event_id"] for item in list_notes(client, max_characters=200_000)["items"]} if replaces else set()
    stale = [value for value in dict.fromkeys(replaces) if value in current and value != note_id]
    if stale:
        generation = client.dashboard()["brain"]["scope_generation"]
        client.forget(stale, kind="events", mode="suppress_recall", expected_scope_generation=generation,
                      conversation_id=NOTES_CONVERSATION, branch_id="main")
    return {"note_id": note_id, "replaced": stale, "unknown": [value for value in replaces if value not in stale]}


def render_notes(response: dict) -> str | None:
    items = [item for item in response.get("items", []) if isinstance(item.get("text"), str) and item["text"].strip()]
    if not items:
        return None
    lines = [f"- [{(item.get('recorded_at') or '')[:10]}] {' '.join(item['text'].split())} (id: {item['event_id']})"
             for item in reversed(items)]
    more = "" if response.get("complete", True) else " Older notes did not fit and are not shown."
    return ("<membrain-notes>\nNotes you saved about this user in earlier sessions, oldest first. They are your "
            "own summaries, not the user's words; the user's current message takes precedence. When a fact "
            "changes, save the new note with memory_note and pass the old note's id in replaces." + more + "\n"
            + "\n".join(lines) + "\n</membrain-notes>")
