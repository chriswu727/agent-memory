import datetime

import pytest

from membrain import desktop, embedded_database, host, notes
from membrain.notes import alias_cues, format_note, parse_note, render_coming_up, render_notes


@pytest.fixture(autouse=True)
def no_model_download(monkeypatch):
    monkeypatch.setenv("MEMBRAIN_SEMANTIC", "0")


def item(event_id, text, recorded="2026-10-20T09:00:00Z", **extra):
    return {"event_id": event_id, "text": text, "actor_role": "assistant", "producer": "membrain-notes",
            "recorded_at": recorded, **extra}


def test_typed_notes_round_trip_and_plain_text_stays_plain():
    stored = format_note("Book a sesame-free restaurant for Mei's birthday.", kind="plan", status="intended",
                         due="2026-11-14", about="Mei", aliases=["Meimei", "little sis"], pinned=True)
    assert stored == ("[plan · intended · due 2026-11-14 · about Mei · aka Meimei, little sis · pinned] "
                      "Book a sesame-free restaurant for Mei's birthday.")
    fields, sentence = parse_note(stored)
    assert fields == {"kind": "plan", "status": "intended", "due": "2026-11-14", "about": "Mei",
                      "aliases": ["Meimei", "little sis"], "pinned": True}
    assert sentence == "Book a sesame-free restaurant for Mei's birthday."
    assert format_note("  Prefers   short replies. ") == "Prefers short replies."
    assert parse_note("[sic] a quote in brackets") == ({}, "[sic] a quote in brackets")
    assert parse_note("[plan · whenever] odd") == ({}, "[plan · whenever] odd")


@pytest.mark.parametrize("fields", [{"kind": "wish"}, {"kind": "plan", "status": "maybe"}, {"due": "14 Nov"},
                                    {"about": "Mei, Tom"}, {"aliases": ["a" * 61]}])
def test_invalid_fields_are_rejected(fields):
    with pytest.raises(ValueError):
        format_note("A fact.", **fields)


def test_text_cannot_impersonate_a_header():
    with pytest.raises(ValueError):
        format_note("[plan · done] Booked the restaurant.")
    with pytest.raises(ValueError):
        format_note("x" * (notes.NOTE_CHARACTERS + 1), kind="other")


def test_session_notes_are_grouped_and_plans_show_their_status():
    response = {"complete": True, "items": [  # Newest first, as the timeline returns them.
        item("n5", "Prefers short replies."),
        item("n4", format_note("Mei is the user's younger sister.", kind="person", about="Mei", aliases=["Meimei"])),
        item("n3", format_note("Buy visitor insurance for Mom.", kind="plan", due="2026-11-01")),
        item("n2", format_note("Passport renewal sent.", kind="plan", status="done")),
        item("n1", format_note("Takes insulin twice a day.", kind="constraint", sensitive=True)),
    ]}
    rendered = render_notes(response, tool="membrain_note")
    people, health, plans, other = (rendered.index(title) for title in
                                    ("People:", "Health and constraints:", "Plans:", "Notes:"))
    assert people < health < plans < other
    assert "Mei (aka Meimei): Mei is the user's younger sister. (id: n4)" in rendered
    assert "Buy visitor insurance for Mom. (intended, not confirmed done; due 2026-11-01) (id: n3)" in rendered
    assert "Passport renewal sent. (done) (id: n2)" in rendered
    assert "sensitive: mention only if the user brings it up" in rendered
    assert "- [2026-10-20] Prefers short replies. (id: n5)" in rendered and "membrain_note" in rendered


def test_coming_up_lists_soon_overdue_and_open_promises_but_not_finished_ones():
    today = datetime.date(2026, 10, 28)
    response = {"items": [
        item("a", format_note("Dinner for Mei's birthday.", kind="plan", occurs_at="2026-11-14", about="Mei")),
        item("b", format_note("Collect the passport.", kind="plan", status="scheduled", due="2026-10-29")),
        item("c", format_note("Book the vet.", kind="plan", due="2026-10-20")),
        item("d", format_note("Renew the lease.", kind="plan", status="done", due="2026-10-30")),
        item("e", format_note("Remind the user about the dentist.", kind="agent_commitment")),
        item("f", format_note("Anniversary.", kind="milestone", occurs_at="2026-11-02")),
        item("g", format_note("Trip to Lisbon.", kind="plan", due="2027-03-01")),
        item("h", format_note("Old plan.", kind="plan", status="cancelled", due="2026-10-01")),
    ]}
    rendered = render_coming_up(response, today=today)
    assert rendered.startswith("<membrain-coming-up>\nToday is 2026-10-28 (Wednesday).")
    assert "- 2026-10-29 (tomorrow): Collect the passport. (id: b)" in rendered
    assert "- 2026-11-02 (in 5 days): Anniversary. (id: f)" in rendered
    assert "2026-10-20 has passed and this is still marked intended: Book the vet. (id: c)" in rendered
    assert "- Your open commitment: Remind the user about the dentist. (id: e)" in rendered
    for absent in ("Renew the lease", "Trip to Lisbon", "Old plan", "Dinner for Mei"):  # Done, far, cancelled.
        assert absent not in rendered
    assert render_coming_up({"items": [item("x", "Plain note.")]}, today=today) is None


def test_aliases_become_recall_cues():
    response = {"items": [item("p", format_note("Dave is the user's electrician.", kind="person", about="Dave",
                                                aliases=["the sparky", "DJ"]))]}
    assert alias_cues(response, "Did the sparky send the invoice?") == ("Dave",)
    assert alias_cues(response, "dj said hi") == ("Dave",)
    assert alias_cues(response, "The sparkling water is gone") == ()


@pytest.mark.skipif(not embedded_database.available(), reason="embedded PostgreSQL binaries are not installed")
def test_typed_notes_drive_session_context_and_alias_recall_on_a_real_service(tmp_path):
    directory = tmp_path / "brain"
    try:
        host.start(directory)
        host.prompt_context(directory, "s1", "Dave quoted 400 pounds to rewire the kitchen next month.", turn_id="1")
        host.finish_turn(directory, "s1", "Good to know.", turn_id="1")
        host.prompt_context(directory, "s1", "Filler about the weather and the garden.", turn_id="2")
        host.finish_turn(directory, "s1", "Nice.", turn_id="2")
        before = host.prompt_context(directory, "s0", "Has the sparky confirmed the price?") or ""
        assert "400 pounds" not in before  # Without the alias the electrician's message is not found.
        with host.ready_client(directory) as client:
            notes.save_note(client, "Dave is the user's electrician.", kind="person", about="Dave",
                            aliases=["the sparky"])
            today = datetime.date.today()
            soon = (today + datetime.timedelta(days=3)).isoformat()
            plan = notes.save_note(client, "Pay Dave's deposit.", kind="plan", due=soon, about="Dave")
            notes.save_note(client, "Paid Dave's deposit.", kind="plan", status="done", due=soon, about="Dave",
                            replaces=[plan["note_id"]])
            notes.save_note(client, "Send the user the rewiring checklist.", kind="agent_commitment")
        context = host.session_context(directory, "s2")
        assert "People:" in context and "Dave (aka the sparky): Dave is the user's electrician." in context
        assert "Your open commitment: Send the user the rewiring checklist." in context
        assert "Pay Dave's deposit" not in context and "Paid Dave's deposit. (done; due" in context
        recalled = host.prompt_context(directory, "s3", "Has the sparky confirmed the price?") or ""
        assert "400 pounds" in recalled
    finally:
        try:
            desktop.stop(directory)
        except desktop.DesktopError:
            pass


def test_notes_over_budget_keep_pinned_user_constraints_and_open_plans_first():
    filler = [item(f"f{i}", f"Small talk fact number {i} about the weather and tea.") for i in range(40)]
    important = [
        item("pin", format_note("Call me Hao, never Mr Wu.", kind="preference", pinned=True)),
        item("allergy", format_note("Severe peanut allergy.", kind="constraint")),
        item("plan", format_note("Book the passport appointment.", kind="plan", due="2026-11-03")),
        item("mine", "I moved to Leeds in 2024.", actor_role="user"),
    ]
    response = {"complete": True, "items": filler[:20] + important + filler[20:]}  # Newest first.
    rendered = render_notes(response, budget=900)
    for event_id in ("pin", "allergy", "plan", "mine"):
        assert f"(id: {event_id})" in rendered
    assert "older or less important notes did not fit" in rendered
    assert "(id: f0)" in rendered and "(id: f39)" not in rendered  # Then the newest of the rest.
    assert "did not fit" not in render_notes({"complete": True, "items": important})


def test_near_duplicate_notes_are_offered_for_merging():
    response = {"items": [
        item("a", format_note("Mei is allergic to sesame seeds.", kind="person", about="Mei")),
        item("b", format_note("Mei is allergic to sesame.", kind="person", about="Mei")),
        item("c", format_note("Mei is allergic to sesame.", kind="constraint")),  # Different kind: not a pair.
        item("d", "Mei is allergic to sesame seeds.", actor_role="user"),  # The user's own: never merged by you.
    ]}
    rendered = render_notes(response, tool="memory_note")
    assert "Possible duplicates (merge each pair into one note with replaces when convenient): b and a." in rendered
    assert notes.duplicate_pairs([]) == []
