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
from .privacy import scrub

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
    "save the updated note and pass the old note's id in replaces. Use source_messages from the current "
    "membrain-source block or source_event_ids from recorded history when available; never invent sources. "
    "Do not save routine requests or anything the "
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
SOURCE_FIELD = {"source_event_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 64,
                                     "description": "Original message IDs in this brain; never invent sources."}}
SOURCE_FIELD["source_messages"] = {"type": "array", "maxItems": 64, "items": {
    "type": "object", "properties": {"producer_ref": {"type": "string"}, "source_event_key": {"type": "string"},
                                      "source_revision": {"type": "integer", "minimum": 1}},
    "required": ["producer_ref", "source_event_key", "source_revision"], "additionalProperties": False}}
NOTE_PRODUCER = "membrain-notes"
# Notes copied from a host's own memory files carry the host in their producer, e.g. ``membrain-import:hermes``.
IMPORT_PREFIX = "membrain-import:"
HOST_NAMES = {"claude-code": "Claude Code", "codex": "Codex", "hermes": "Hermes", "openclaw": "OpenClaw",
              "workbuddy": "WorkBuddy"}


def imported_from(item: dict) -> str | None:
    producer = item.get("producer") or ""
    return producer[len(IMPORT_PREFIX):] if producer.startswith(IMPORT_PREFIX) else None


def list_notes(client, *, max_characters: int = 200_000) -> dict:
    """Current notes, newest first. Session start chooses among them within its own budget (``select_notes``)."""
    return client.timeline(roles=("assistant", "user"), conversation_id=NOTES_CONVERSATION, max_characters=max_characters,
                           max_item_characters=STORED_CHARACTERS, limit=200)


def _name(value, field: str) -> str:
    value = scrub(" ".join(str(value).split()))
    if not value or len(value) > 60 or any(mark in value for mark in "[]·,"):
        raise ValueError(f"{field} must be a short name without brackets, commas or '·'.")
    return value


def _date(value, field: str) -> str:
    value = str(value).strip()
    if not _DATE.fullmatch(value):
        raise ValueError(f"{field} must be a date as YYYY-MM-DD.")
    try:
        _datetime.date.fromisoformat(value if len(value) == 10 else value + "-01")
    except ValueError:
        raise ValueError(f"{field} must be a valid calendar date.") from None
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
    header = ' · '.join(tokens)
    stored = f"[{header}] {text}"
    if len(header) > 200 or len(stored) > STORED_CHARACTERS:
        raise ValueError("Shorten the note fields so the complete note fits within 480 characters.")
    return stored


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


def save_note(client, text: str, *, replaces=(), producer: str = NOTE_PRODUCER, source_event_ids=(), source_messages=(), **fields) -> dict:
    """Save one note; ``replaces`` lists IDs of notes it supersedes, which stop being shown.

    ``fields`` are the optional typed-note fields (``NOTE_FIELDS``); without them the note is plain text.
    """
    unknown = set(fields) - set(NOTE_FIELDS)
    if unknown:
        raise ValueError(f"Unknown note fields: {', '.join(sorted(unknown))}.")
    text = format_note(text, **fields)
    writer = getattr(client, "write_note", None)
    if callable(writer):
        parsed, sentence = parse_note(text)
        return writer(sentence, replaces=list(replaces), producer=producer,
                      source_event_ids=list(source_event_ids), source_messages=list(source_messages), **parsed)
    if source_event_ids or source_messages:
        raise ValueError("This client does not support source-linked notes.")
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


def _priority(entry) -> int:
    item, fields, _sentence = entry
    if fields.get("pinned"):
        return 0
    if item.get("actor_role") == "user":
        return 1  # The user's own corrections.
    if fields.get("kind") in ("constraint", "person"):
        return 2
    if fields.get("kind") in ("plan", "agent_commitment", "milestone") and fields.get("status", "intended") in (
            OPEN_STATUSES):
        return 3
    return 4


def select_notes(parsed: list, budget: int = NOTES_CHARACTERS) -> tuple[list, int]:
    """Notes that fit ``budget``: pinned, the user's own, constraints and people, open plans, then the newest.

    Returns the chosen notes in their original (oldest-first) order and how many were left out. Nothing is deleted.
    """
    order = sorted(range(len(parsed)), key=lambda index: (_priority(parsed[index]), -index))
    chosen, used = set(), 0
    for index in order:
        size = len(_line(*parsed[index])) + 35
        if used + size > budget:
            continue
        chosen.add(index)
        used += size
    return [entry for index, entry in enumerate(parsed) if index in chosen], len(parsed) - len(chosen)


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"\w+", text.casefold()) if len(word) > 2}


def duplicate_pairs(parsed: list, threshold: float = 0.75) -> list[tuple[str, str]]:
    """Pairs of your own notes about the same thing that say nearly the same (by word overlap)."""
    pairs = []
    own = [(item, fields, _words(sentence)) for item, fields, sentence in parsed if item.get("actor_role") != "user"]
    for index, (first, first_fields, first_words) in enumerate(own):
        for second, second_fields, second_words in own[index + 1:]:
            same = (first_fields.get("kind"), first_fields.get("about")) == (
                second_fields.get("kind"), second_fields.get("about"))
            union = first_words | second_words
            if same and union and len(first_words & second_words) / len(union) >= threshold:
                pairs.append((first["event_id"], second["event_id"]))
    return pairs


def render_notes(response: dict, *, tool: str | None = "memory_note", skip_imports_from=(),
                 real_life: bool = False, budget: int = NOTES_CHARACTERS) -> str | None:
    """The notes block, grouped by kind; ``skip_imports_from`` leaves out notes copied from hosts that show their
    own memory files. ``real_life`` renders the person's non-sensitive real-life notes for a role-play session."""
    parsed = [entry for entry in _parsed(response, skip_imports_from)
              if not (real_life and entry[1].get("sensitive"))]
    if not parsed:
        return None
    parsed, omitted = select_notes(parsed, budget)
    duplicates = [] if real_life or tool is None else duplicate_pairs(parsed)
    groups: dict[str, list[str]] = {}
    for item, fields, sentence in parsed:
        groups.setdefault(fields.get("kind") or "", []).append(_line(item, fields, sentence))
    body = []
    for kind, title in SECTIONS:
        if groups.get(kind):
            body += [f"{title}:", *groups[kind]]
    if groups.get(""):
        body += (["Notes:"] if body else []) + groups[""]
    more = "" if response.get("complete", True) and not omitted else (
        f" {omitted or 'Some'} older or less important notes did not fit and are not shown; search for them when "
        "a request needs them.")
    if duplicates:
        body.append("Possible duplicates (merge each pair into one note with replaces when convenient): " + "; ".join(
            f"{first} and {second}" for first, second in duplicates[:5]) + ".")
    if real_life:
        return ("<membrain-real-life-notes>\nNotes about the user's real life, which they chose to share with "
                "this role-play. They are facts about the user, not about the story; keep them apart from the "
                "fiction.\n" + "\n".join(body) + "\n</membrain-real-life-notes>")
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


def render_coming_up(response: dict, *, today: _datetime.date | None = None, skip_imports_from=(),
                     budget: int = 1500) -> str | None:
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
    header = ("<membrain-coming-up>\nToday is " + f"{today.isoformat()} ({today.strftime('%A')}). Plans and promises "
            "that are due soon, overdue or still open. Follow up naturally when it fits (\"Did the passport "
            "arrive?\"); never assume a planned action happened, and when the user confirms or cancels one, replace "
            "its note with the new status.\n")
    footer = "\n</membrain-coming-up>"
    more = "\nMore plans did not fit; search when needed."
    if len(header) + len(footer) + len(more) > budget:
        return None
    chosen, used = [], len(header) + len(footer) + len(more)
    for line in lines:
        if used + len(line) + 1 <= budget:
            chosen.append(line)
            used += len(line) + 1
    return header + "\n".join(chosen) + (more if len(chosen) < len(lines) else "") + footer


def alias_cues(response: dict, text: str) -> tuple[str, ...]:
    """Names of people whose aliases appear in ``text``, so recall also searches by the real name."""
    lowered, cues = f" {text.casefold()} ", []
    for _item, fields, _sentence in _parsed(response):
        if fields.get("about") and any(f" {alias.casefold()}" in lowered for alias in fields.get("aliases", ())):
            cues.append(fields["about"])
    return tuple(dict.fromkeys(cues))


def store_note(service, principal, brain_id: str, arguments: dict) -> dict:
    from psycopg.types.json import Jsonb

    from .helpers import DomainError, digest
    from .privacy import scrub

    try:
        text = format_note(scrub(arguments["text"]), **note_fields(arguments))
    except (TypeError, ValueError) as error:
        raise DomainError("validation_failed", str(error)) from None
    request_id = arguments["request_id"]
    if arguments.get("producer") in ("memory-service", "archive-import"):
        raise DomainError("invalid_evidence", "Reserved producers cannot write notes.")
    params = service._params(principal, brain_id)
    fingerprint = digest(arguments)
    with service.database.transaction(principal) as conn:
        conn.execute("SET LOCAL lock_timeout = '5s'")
        brain = service._brain(conn, principal, brain_id)
        cached = conn.execute(
            "SELECT request_hash,response FROM memory_requests WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND action='note' AND key=%s", (*params, request_id)).fetchone()
        if cached:
            if cached["request_hash"] != fingerprint:
                raise DomainError("idempotency_conflict", "This note request already has different content.")
            if cached["response"] is None:
                raise DomainError("version_conflict", "This note request was invalidated; use a new request ID.")
            return cached["response"]
        replaces = list(dict.fromkeys(arguments.get("replaces", [])))
        old = conn.execute(
            "SELECT id,document FROM memory_events WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND id=ANY(%s::text[]) AND conversation_id=%s AND is_current AND NOT deleted AND NOT suppressed "
            "AND NOT retired AND document IS NOT NULL", (*params, replaces, NOTES_CONVERSATION)).fetchall()
        stale = [row["id"] for row in old]
        if len(stale) != len(replaces):
            raise DomainError("version_conflict", "A note changed or is unavailable. Refresh before correcting it.")
        sources = set(arguments.get("source_event_ids", []))
        sources.update(row["input_event_id"] for row in conn.execute(
            "SELECT input_event_id FROM memory_event_dependencies WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND output_event_id=ANY(%s::text[])", (*params, stale)))
        available = service._events(conn, principal, brain, event_ids=list(sources), limit=64) if sources else []
        if len(available) != len(sources) or any(
                event["scope"]["conversation_id"] == NOTES_CONVERSATION for event in available):
            raise DomainError("source_unavailable", "A note source is unavailable in this brain.")
        from .note_sources import reference

        references = list(arguments.get("source_messages", []))
        references.extend(reference(event["observed"]) for event in available)
        references.extend(ref for row in old for ref in row["document"]["observed"].get("source_references", []))
        references = list({digest(ref): ref for ref in references}.values())
        if len(references) > 64:
            raise DomainError("validation_failed", "A note can reference at most 64 original messages.")
        incoming = {"source_event_key": "note-" + request_id, "source_revision": 1,
                    "producer_ref": arguments.get("producer", NOTE_PRODUCER),
                    "actor_ref": arguments.get("role", "assistant"), "actor_role": arguments.get("role", "assistant"),
                    "event_kind": "message", "content": {"media_type": "text/plain", "text": text},
                    "occurred_time": {"start": None, "end": None, "precision": "unknown", "basis": "unknown"},
                    "interpretation_hint": "unknown", "delivery_state": "received" if arguments.get("role") == "user" else "unknown",
                    "parent_source_event_keys": [], "source_references": references}
        target = {"brain_ref": brain_id, "conversation_ref": NOTES_CONVERSATION, "branch_ref": "main"}
        note_id, _, _ = service._append_event(conn, principal, brain, target, incoming)
        if stale:
            service._forget(conn, principal, brain, {
                "request_id": "replace-" + request_id, "target": target,
                "payload": {"idempotency_key": "replace-" + request_id, "selector": {"kind": "events", "ids": stale},
                            "mode": "suppress_recall", "expected_scope_generation": brain["scope_generation"]}})
        for source_id in sorted(sources):
            conn.execute(
                "INSERT INTO memory_event_dependencies(tenant_id,owner_id,brain_id,input_event_id,output_event_id) "
                "VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", (*params, source_id, note_id))
        linked = [row["input_event_id"] for row in conn.execute(
            "SELECT input_event_id FROM memory_event_dependencies WHERE tenant_id=%s AND owner_id=%s AND brain_id=%s "
            "AND output_event_id=%s ORDER BY input_event_id", (*params, note_id))]
        result = {"note_id": note_id, "replaced": stale, "unknown": [value for value in replaces if value not in stale],
                  "source_event_ids": linked, "pending_source_count": max(0, len(references) - len(linked))}
        conn.execute(
            "INSERT INTO memory_requests(tenant_id,owner_id,brain_id,action,key,request_hash,response) "
            "VALUES(%s,%s,%s,'note',%s,%s,%s)", (*params, request_id, fingerprint, Jsonb(result)))
        return result
