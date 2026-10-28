"""Import the memory files hosts keep themselves (Claude Code, Hermes, OpenClaw, Codex) as memBrain notes.

Host memory files mix facts about the person with project and environment conventions. memBrain is the memory for
the person, so by default only person-level files are imported: Claude Code auto memories of type ``user`` or
``feedback``, Hermes ``USER.md`` and OpenClaw ``USER.md``. ``include_general`` adds the rest (Hermes and OpenClaw
``MEMORY.md``, Claude Code ``project`` and ``reference`` memories, Codex memories).

Each entry becomes one note with producer ``membrain-import:<host>``, so it is labelled as imported, can be left out
of that host's own session context, and is never presented as the user's words. Entries already present as notes
are skipped, which makes importing again safe. Source files are only read.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .notes import IMPORT_PREFIX, NOTE_CHARACTERS, NOTES_CONVERSATION, save_note

HOSTS = ("claude-code", "hermes", "openclaw", "codex")
HERMES_DELIMITER = "\n§\n"
PERSON_TYPES = ("user", "feedback")


@dataclass(frozen=True)
class Source:
    host: str
    path: Path
    person: bool  # About the person (imported by default) rather than projects or the environment.
    kind: str = "markdown"  # markdown, hermes or claude-memory


def _home(variable: str, default: str) -> Path:
    return Path(os.environ.get(variable) or Path.home() / default).expanduser()


def discover(hosts=HOSTS, *, include_general: bool = False) -> list[Source]:
    """Memory files of the given hosts that exist on this computer."""
    found: list[Source] = []
    if "claude-code" in hosts:
        for path in sorted(_home("CLAUDE_CONFIG_DIR", ".claude").glob("projects/*/memory/*.md")):
            if path.name != "MEMORY.md":  # MEMORY.md is only an index of the topic files.
                found.append(Source("claude-code", path, _front_matter(path).get("type") in PERSON_TYPES,
                                    "claude-memory"))
    if "hermes" in hosts:
        memories = _home("HERMES_HOME", ".hermes") / "memories"
        found += [Source("hermes", memories / "USER.md", True, "hermes"),
                  Source("hermes", memories / "MEMORY.md", False, "hermes")]
    if "openclaw" in hosts:
        workspace = _home("OPENCLAW_WORKSPACE", ".openclaw/workspace")
        found += [Source("openclaw", workspace / "USER.md", True), Source("openclaw", workspace / "MEMORY.md", False)]
    if "codex" in hosts:
        memories = _home("CODEX_HOME", ".codex") / "memories"
        found += [Source("codex", memories / "MEMORY.md", False), Source("codex", memories / "memory_summary.md", False)]
    return [source for source in found if source.path.is_file() and (source.person or include_general)]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def _split_front_matter(text: str) -> tuple[dict, str]:
    match = re.match(r"---\n(.*?)\n---\n?", text, re.DOTALL)
    if not match:
        return {}, text
    fields = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() and not key.startswith(" "):
            fields[key.strip()] = value.strip().strip("'\"")
    return fields, text[match.end():]


def _front_matter(path: Path) -> dict:
    try:
        return _split_front_matter(_read(path))[0]
    except OSError:
        return {}


def markdown_entries(text: str) -> list[str]:
    """Bullets and paragraphs of a Markdown memory file, without headings, code, comments or front matter."""
    text = _split_front_matter(text)[1]
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"```.*?(```|$)", "", text, flags=re.DOTALL)
    entries, paragraph, in_bullet = [], [], False

    def close():
        if paragraph:
            entries.append(" ".join(paragraph))
            paragraph.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "|")) or set(line) <= set("-=*_ "):
            close()
            continue
        bullet = re.match(r"(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(.*)", line)
        if bullet:
            close()
            paragraph.append(bullet.group(1))
            in_bullet = True
        elif in_bullet and not raw.startswith((" ", "\t")):
            close()  # An unindented line after a bullet starts a new entry.
            paragraph.append(line)
            in_bullet = False
        else:
            paragraph.append(line)  # Paragraph text or an indented continuation of a bullet.
    close()
    return [_plain(entry) for entry in entries if _plain(entry)]


def _plain(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # Links keep their text.
    text = re.sub(r"(\*\*|__|`)", "", text)
    return " ".join(text.split())


def entries(source: Source) -> list[str]:
    text = _read(source.path)
    if source.kind == "hermes":
        return [_plain(entry) for entry in text.split(HERMES_DELIMITER) if _plain(entry)]
    if source.kind == "claude-memory":
        fields, body = _split_front_matter(text)
        found = markdown_entries(body)
        if not found and fields.get("description"):
            found = [_plain(fields["description"])]
        return found
    return markdown_entries(text)


def shorten(text: str, limit: int = NOTE_CHARACTERS) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    end = max(cut.rfind(". "), cut.rfind("。"), cut.rfind("; "))
    cut = cut[:end + 1] if end > limit // 2 else cut[:cut.rfind(" ")] if " " in cut[limit // 2:] else cut
    return cut.rstrip() + "…"


def _key(text: str) -> str:
    return " ".join(text.casefold().split()).rstrip(" .。!！")


def import_notes(client, sources: list[Source], *, dry_run: bool = False) -> dict:
    """Save new entries of ``sources`` as imported notes; returns counts per file."""
    from .host import scrub

    existing = client.timeline(roles=("assistant", "user"), conversation_id=NOTES_CONVERSATION,
                               max_characters=200_000, max_item_characters=NOTE_CHARACTERS, limit=1000)
    seen = {_key(item["text"]) for item in existing.get("items", []) if isinstance(item.get("text"), str)}
    report = {"imported": 0, "duplicates": 0, "shortened": 0, "dry_run": dry_run, "files": []}
    for source in sources:
        counts = {"host": source.host, "path": str(source.path), "imported": 0, "duplicates": 0}
        for entry in entries(source):
            text = " ".join(scrub(entry).split())
            if not text or text in ("[private]", "[secret removed]"):
                continue
            short = shorten(text)
            if _key(short) in seen or _key(text) in seen:
                counts["duplicates"] += 1
                continue
            seen.add(_key(short))
            if not dry_run:
                save_note(client, short, producer=IMPORT_PREFIX + source.host)
            counts["imported"] += 1
            report["shortened"] += short != text
        report["imported"] += counts["imported"]
        report["duplicates"] += counts["duplicates"]
        report["files"].append(counts)
    return report
