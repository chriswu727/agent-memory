from membrain import notes


class Client:
    def __init__(self):
        self.events, self.forgotten = [], []

    def observe(self, text, **options):
        event_id = f"n{len(self.events) + 1}"
        self.events.append({"event_id": event_id, "text": text, "options": options,
                            "recorded_at": f"2026-04-0{len(self.events) + 1}T00:00:00Z"})
        return {"result": {"accepted_event_ids": [event_id]}}

    def timeline(self, **options):
        assert options["conversation_id"] == notes.NOTES_CONVERSATION and options["roles"] == ("assistant", "user")
        return {"complete": True, "items": [event for event in reversed(self.events)
                                            if event["event_id"] not in self.forgotten]}

    def dashboard(self):
        return {"brain": {"scope_generation": 7}}

    def forget(self, ids, **options):
        assert options["mode"] == "suppress_recall" and options["expected_scope_generation"] == 7
        self.forgotten.extend(ids)


def test_notes_are_saved_as_assistant_events_and_replace_outdated_ones():
    client = Client()
    first = notes.save_note(client, "  Mom's 70th birthday dinner: Sat May 9, Harbor Pearl.  ")
    assert first == {"note_id": "n1", "replaced": [], "unknown": []}
    stored = client.events[0]
    assert stored["text"] == "Mom's 70th birthday dinner: Sat May 9, Harbor Pearl."
    assert stored["options"]["role"] == "assistant" and stored["options"]["conversation_id"] == "membrain-notes"
    second = notes.save_note(client, "Mom's birthday dinner moved: Sun May 17, 6 pm, Jade Garden.",
                             replaces=["n1", "missing"])
    assert second == {"note_id": "n2", "replaced": ["n1"], "unknown": ["missing"]}
    rendered = notes.render_notes(notes.list_notes(client))
    assert "Jade Garden. (id: n2)" in rendered and "Harbor Pearl" not in rendered


def test_notes_must_be_short_single_facts():
    client = Client()
    for text in ("   ", "x" * (notes.NOTE_CHARACTERS + 1)):
        try:
            notes.save_note(client, text)
        except ValueError:
            continue
        raise AssertionError("expected a validation error")
    assert notes.render_notes({"items": []}) is None


def test_notes_written_by_the_user_are_labelled():
    rendered = notes.render_notes({"items": [
        {"event_id": "u1", "actor_role": "user", "recorded_at": "2026-05-01T00:00:00Z", "text": "I'm vegan now."},
        {"event_id": "a1", "actor_role": "assistant", "recorded_at": "2026-04-01T00:00:00Z", "text": "Vegetarian."}]})
    assert "- [2026-04-01] Vegetarian. (id: a1)" in rendered
    assert "- [2026-05-01 · written by the user] I'm vegan now. (id: u1)" in rendered
