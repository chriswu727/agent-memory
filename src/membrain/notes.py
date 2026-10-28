"""Notes curated by the host model: short durable facts shown at the start of every session.

Raw messages grow without bound and similarity search cannot infer that a seafood menu matters to a shellfish
allergy. The host model can: when the user states a lasting fact, it saves a one-line note, and replaces the note
when the fact changes. Notes are stored as assistant-authored events in a reserved conversation, so they keep
attribution, deletion and backup behaviour, and are never presented as the user's own words.
"""
from __future__ import annotations

import datetime as _datetime
import re

from .helpers import uid

NOTES_CONVERSATION = "membrain-notes"
NOTE_CHARACTERS = 300  # The sentence; a typed note's header comes on top.
STORED_CHARACTERS = 480
NOTES_CHARACTERS = 4000
KINDS = ("person", "constraint", "preference", "plan", "routine", "milestone", "relationship", "agent_commitment",
         "other")
STATUSES = ("intended", "scheduled", "done", "cancelled", "superseded")
OPEN_STATUSES = ("intended", "scheduled")
COMING_UP_DAYS = 14
SECTIONS = (("person", "People"), ("constraint", "Health and constraints"), ("plan", "Plans"),
            ("agent_commitment", "Your commitments"), ("milestone", "Milestones and dates"),
            ("preference", "Preferences"), ("routine", "Routines"), ("relationship", "Your relationship"),
            ("other", "Other notes"))
_HEADER = re.compile(r"\[([^\[\]]{1,200})\] (.+)", re.DOTALL)
_DATE = re.compile(r"\d{4}-\d{2}(-\d{2})?")
# Shared by every host's note tool so typed notes mean the same everywhere.
NOTE_TOOL_DESCRIPTION = (
    "Save one short, lasting fact about the user (health, people, dates, places, preferences, decisions, "
    "constraints, plans and their changes) so it is shown at the start of every later session. One fact per note, "
    "under 300 characters, with dates when they matter. Set kind when it is clear: person (with about = their name, "
    "aliases, and the relation in the text), constraint, preference, plan (with status intended until the user "
    "confirms it is scheduled or done, and due or occurs_at as YYYY-MM-DD), routine, milestone, relationship, or "
    "agent_commitment for something you promised to do. When a fact changes (a plan is done, moved or cancelled), "
    "save the updated note and pass the old note's id in replaces. Do not save routine requests or anything the "
    "user asked you not to keep.")
NOTE_FIELDS = {
    "kind": {"type": "string", "enum": list(KINDS)},
    "about": {"type": "string", "description": "Who the fact is about, by name, if not the user."},
    "aliases": {"type": "array", "items": {"type": "string"},
                "description": "Other names for that person, e.g. 'the sparky' or a nickname."},
    "status": {"type": "string", "enum": list(STATUSES), "description": "For plans and commitments."},
    "due": {"type": "string", "description": "YYYY-MM-DD by which a plan or commitment should happen."},
    "occurs_at": {"type": "string", "description": "YYYY-MM-DD on which an event happens."},
    "pinned": {"type": "boolean", "description": "Always show this note."},
    "sensitive": {"type": "boolean", "description": "Health, money or other private matters."},
}
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
                           max_item_characters=STORED_CHARACTERS, limit=200)


def _name(value, field: str) -> str:
    value = " ".join(str(value).split())
    if not value or len(value) > 60 or any(mark in value for mark in "[]·,"):
        raise ValueError(f"{field} must be a short name without brackets, commas or '·'.")
    return value


def _date(value, field: str) -> str:
    value = str(value).strip()
    if not _DATE.fullmatch(value):
        raise ValueError(f"{field} must be a date as YYYY-MM-DD.")
    return value


def note_fields(arguments: dict) -> dict:
    """The typed-note fields of a tool call, without empty values."""
    return {key: arguments[key] for key in NOTE_FIELDS if arguments.get(key) not in (None, "", [], False)}


def format_note(text: str, *, kind=None, about=None, aliases=(), status=None, due=None, occurs_at=None,
                pinned=False, sensitive=False) -> str:
    """The stored form: plain text, or ``[kind · status · due DATE · about NAME · aka A, B] sentence``."""
    text = " ".join(str(text).split())
    if not text:
        raise ValueError("A note needs text.")
    if len(text) > NOTE_CHARACTERS:
        raise ValueError(f"Keep a note under {NOTE_CHARACTERS} characters; save separate facts separately.")
    if not any((kind, about, aliases, status, due, occurs_at, pinned, sensitive)):
        if _HEADER.fullmatch(text) and parse_note(text)[0]:
            raise ValueError("A note cannot start with a bracketed header; pass the fields instead.")
        return text
    kind = kind or "other"
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}.")
    if status is not None and status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}.")
    tokens = [kind] + ([status] if status else [])
    tokens += [f"due {_date(due, 'due')}"] if due else []
    tokens += [f"on {_date(occurs_at, 'occurs_at')}"] if occurs_at else []
    tokens += [f"about {_name(about, 'about')}"] if about else []
    names = [_name(alias, "aliases") for alias in (aliases or ())]
    tokens += [f"aka {', '.join(names)}"] if names else []
    tokens += ["pinned"] * bool(pinned) + ["sensitive"] * bool(sensitive)
    return f"[{' · '.join(tokens)}] {text}"


def parse_note(stored: str) -> tuple[dict, str]:
    """(fields, sentence) of a stored note; plain notes have no fields."""
    stored = " ".join(str(stored).split())
    match = _HEADER.fullmatch(stored)
    if not match:
        return {}, stored
    tokens = match.group(1).split(" · ")
    if tokens[0] not in KINDS:
        return {}, stored
    fields = {"kind": tokens[0]}
    for token in tokens[1:]:
        word, _, value = token.partition(" ")
        if token in STATUSES:
            fields["status"] = token
        elif token in ("pinned", "sensitive"):
            fields[token] = True
        elif word == "due" and _DATE.fullmatch(value):
            fields["due"] = value
        elif word == "on" and _DATE.fullmatch(value):
            fields["occurs_at"] = value
        elif word == "about" and value:
            fields["about"] = value
        elif word == "aka" and value:
            fields["aliases"] = [alias.strip() for alias in value.split(",") if alias.strip()]
        else:
            return {}, stored  # Not a header this module wrote.
    return fields, match.group(2)


def save_note(client, text: str, *, replaces=(), producer: str = NOTE_PRODUCER, **fields) -> dict:
    """Save one note; ``replaces`` lists IDs of notes it supersedes, which stop being shown.

    ``fields`` are the optional typed-note fields (``NOTE_FIELDS``); without them the note is plain text.
    """
    unknown = set(fields) - set(NOTE_FIELDS)
    if unknown:
        raise ValueError(f"Unknown note fields: {', '.join(sorted(unknown))}.")
    text = format_note(text, **fields)
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


def _when(fields: dict) -> str:
    parts = [f"due {fields['due']}"] if fields.get("due") else []
    parts += [f"on {fields['occurs_at']}"] if fields.get("occurs_at") else []
    return ", ".join(parts)


def _line(item: dict, fields: dict, sentence: str) -> str:
    tags = []
    if fields.get("kind") in ("plan", "agent_commitment"):
        status = fields.get("status", "intended")
        tags.append(f"{status}, not confirmed done" if status in OPEN_STATUSES else status)
    if _when(fields):
        tags.append(_when(fields))
    if fields.get("sensitive"):
        tags.append("sensitive: mention only if the user brings it up")
    who = ""
    if fields.get("about"):
        who = fields["about"] + (f" (aka {', '.join(fields['aliases'])})" if fields.get("aliases") else "") + ": "
    detail = f" ({'; '.join(tags)})" if tags else ""
    pin = "pinned · " if fields.get("pinned") else ""
    return (f"- [{pin}{(item.get('recorded_at') or '')[:10]}{_label(item)}] {who}{sentence}{detail} "
            f"(id: {item['event_id']})")


def _parsed(response: dict, skip_imports_from=()) -> list[tuple[dict, dict, str]]:
    items = [item for item in response.get("items", []) if isinstance(item.get("text"), str) and item["text"].strip()
             and imported_from(item) not in skip_imports_from]
    return [(item, *parse_note(item["text"])) for item in reversed(items)]  # Oldest first.


def render_notes(response: dict, *, tool: str | None = "memory_note", skip_imports_from=()) -> str | None:
    """The notes block, grouped by kind; ``skip_imports_from`` leaves out notes copied from hosts that show their
    own memory files."""
    parsed = _parsed(response, skip_imports_from)
    if not parsed:
        return None
    groups: dict[str, list[str]] = {}
    for item, fields, sentence in parsed:
        groups.setdefault(fields.get("kind") or "", []).append(_line(item, fields, sentence))
    body = []
    for kind, title in SECTIONS:
        if groups.get(kind):
            body += [f"{title}:", *groups[kind]]
    if groups.get(""):
        body += (["Notes:"] if body else []) + groups[""]
    more = "" if response.get("complete", True) else " Older notes did not fit and are not shown."
    return ("<membrain-notes>\nNotes about this user from earlier sessions, oldest first within each group. Unless "
            "marked as written by the user or imported, they are your own summaries, not the user's words; notes "
            "the user wrote or corrected take precedence over yours, and the user's current message over both. A plan "
            "marked intended or scheduled has not been confirmed done: never assume it happened."
            + (f" When a fact changes, save the new note with {tool} and pass the old note's id in replaces."
               if tool else "") + more + "\n" + "\n".join(body) + "\n</membrain-notes>")


def _as_date(value: str | None) -> _datetime.date | None:
    if not value:
        return None
    try:
        return _datetime.date.fromisoformat(value if len(value) == 10 else value + "-01")
    except ValueError:
        return None


def render_coming_up(response: dict, *, today: _datetime.date | None = None, skip_imports_from=()) -> str | None:
    """Open plans and commitments that are due soon, overdue, or undated, with today's date."""
    today = today or _datetime.date.today()
    soon, passed, undated = [], [], []
    for item, fields, sentence in _parsed(response, skip_imports_from):
        if fields.get("kind") not in ("plan", "agent_commitment", "milestone"):
            continue
        status = fields.get("status", "intended" if fields["kind"] != "milestone" else None)
        if status not in OPEN_STATUSES + (None,):
            continue
        when = _as_date(fields.get("due") or fields.get("occurs_at"))
        who = f"{fields['about']}: " if fields.get("about") else ""
        label = f"{who}{sentence} (id: {item['event_id']})"
        if when is None:
            if fields["kind"] == "agent_commitment":
                undated.append(f"- Your open commitment: {label}")
        elif when < today and status:
            passed.append(f"- {when.isoformat()} has passed and this is still marked {status}: {label}")
        elif today <= when <= today + _datetime.timedelta(days=COMING_UP_DAYS):
            days = (when - today).days
            relative = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
            soon.append(f"- {when.isoformat()} ({relative}): {label}")
    lines = sorted(soon) + passed + undated
    if not lines:
        return None
    return ("<membrain-coming-up>\nToday is " + f"{today.isoformat()} ({today.strftime('%A')}). Plans and promises "
            "that are due soon, overdue or still open. Follow up naturally when it fits (\"Did the passport "
            "arrive?\"); never assume a planned action happened, and when the user confirms or cancels one, replace "
            "its note with the new status.\n" + "\n".join(lines) + "\n</membrain-coming-up>")


def alias_cues(response: dict, text: str) -> tuple[str, ...]:
    """Names of people whose aliases appear in ``text``, so recall also searches by the real name."""
    lowered, cues = f" {text.casefold()} ", []
    for _item, fields, _sentence in _parsed(response):
        if fields.get("about") and any(f" {alias.casefold()}" in lowered for alias in fields.get("aliases", ())):
            cues.append(fields["about"])
    return tuple(dict.fromkeys(cues))
