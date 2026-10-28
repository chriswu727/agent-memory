import json

import pytest

from membrain import desktop, embedded_database, host, notes
from membrain.adapters.chatbot import ChatMemory, Speaker


@pytest.fixture(autouse=True)
def no_model_download(monkeypatch):
    monkeypatch.setenv("MEMBRAIN_SEMANTIC", "0")


def test_roleplay_memory_identities_and_pause(tmp_path):
    assert host.roleplay_user(None, None) is None and host.roleplay_user("qq:1", None) == "qq:1"
    story = host.roleplay_user("qq:1", "  Captain   Vale ")
    assert story == "qq:1|roleplay:Captain Vale" and host.is_roleplay(story)
    assert host.real_life_user(story) == "qq:1" and host.real_life_user("roleplay:Vale") is None
    assert host.user_brain_id(story) != host.user_brain_id("qq:1")
    assert host.user_brain_id(host.roleplay_user("x" * 190, "y" * 80))  # Long bot identities still fit.
    with pytest.raises(ValueError):
        host.roleplay_user("qq:1", "z" * 81)
    record = host.message_record("s", "user", "I draw my sword.", user_id=story)
    assert record["interpretation_hint"] == "roleplay"
    assert "interpretation_hint" not in host.message_record("s", "user", "Hi", user_id="qq:1")
    host.pause(tmp_path, user_id="qq:1")
    assert host.paused(tmp_path, story) and not host.paused(tmp_path, host.roleplay_user("qq:2", "Vale"))


def test_real_life_notes_shared_with_roleplay_leave_out_sensitive_ones():
    response = {"items": [
        {"event_id": "a", "text": "Prefers to be called Hao.", "actor_role": "assistant", "recorded_at": "2026-10-01"},
        {"event_id": "b", "text": notes.format_note("Takes antidepressants.", kind="constraint", sensitive=True),
         "actor_role": "assistant", "recorded_at": "2026-10-02"},
    ]}
    shared = notes.render_notes(response, tool=None, real_life=True)
    assert shared.startswith("<membrain-real-life-notes>") and "Prefers to be called Hao." in shared
    assert "antidepressants" not in shared


@pytest.mark.skipif(not embedded_database.available(), reason="embedded PostgreSQL binaries are not installed")
def test_roleplay_and_real_life_never_mix_on_a_real_service(tmp_path):
    directory = tmp_path / "brain"
    try:
        host.start(directory)
        bot = ChatMemory(directory, producer="membrain-test")
        hao = Speaker("discord", "hao")
        real = bot.session(hao)
        bot.context(hao, "My real sister Mei is allergic to sesame.", session=real, turn_id="1")
        bot.record(hao, "Noted.", session=real, turn_id="1")
        bot.note(hao, "Hao's sister Mei is allergic to sesame.", kind="person", about="Mei")
        bot.note(hao, "Hao takes insulin.", kind="constraint", sensitive=True)
        story = bot.session(hao, "roleplay:Captain Vale")
        bot.context(hao, "In the story, I am a smuggler hiding gold in the lighthouse.", session=story,
                    turn_id="2", roleplay="Captain Vale")
        bot.record(hao, "Captain Vale narrows her eyes.", session=story, turn_id="2", roleplay="Captain Vale")
        bot.note(hao, "Hao's character hid gold in the lighthouse.", roleplay="Captain Vale")

        bot._blocks.clear()
        real_life = bot.context(hao, "What do you know about me?", session="real-2", turn_id="3")
        assert "Mei is allergic to sesame" in real_life
        assert "lighthouse" not in real_life and "smuggler" not in real_life

        closed = bot.context(hao, "Where did I hide the gold?", session="story-2", turn_id="4", roleplay="Captain Vale")
        assert "role-play with the character Captain Vale" in closed and "lighthouse" in closed
        assert "sesame" not in closed and "insulin" not in closed

        shared = bot.context(hao, "Continue the tale.", session="story-3", turn_id="5", roleplay="Captain Vale",
                             share_real_life=True)
        assert "<membrain-real-life-notes>" in shared and "Mei is allergic to sesame" in shared
        assert "insulin" not in shared  # Sensitive real-life notes stay out even when sharing.

        with host.ready_client(directory, host.roleplay_user(bot.user_id(hao), "Captain Vale")) as client:
            stored = client.timeline(roles=("user",), max_characters=200_000)["items"]
        assert [item["text"] for item in stored] == ["In the story, I am a smuggler hiding gold in the lighthouse."]
    finally:
        try:
            desktop.stop(directory)
        except desktop.DesktopError:
            pass


def test_proxy_roleplay_header_selects_the_story_memory(tmp_path):
    import httpx
    from fastapi.testclient import TestClient

    from membrain.proxy import create_proxy_app

    calls = []

    class Memory(ChatMemory):
        def context(self, speaker, text, *, session, turn_id=None, roleplay=None, share_real_life=None):
            calls.append(("context", session, roleplay, share_real_life))
            return None

        def record(self, speaker, reply, *, session, turn_id=None, prompt=None, roleplay=None):
            calls.append(("record", session, roleplay))

    upstream = httpx.MockTransport(lambda request: httpx.Response(
        200, json={"choices": [{"message": {"role": "assistant", "content": "Aye."}}]}))
    app = create_proxy_app(Memory(tmp_path), "https://api.example/v1", transport=upstream)
    with TestClient(app) as client:
        client.post("/v1/chat/completions", headers={"X-Membrain-Roleplay": "Captain  Vale",
                                                      "X-Membrain-Share-Real-Life": "1"},
                    json={"model": "m", "user": "st:hao", "messages": [{"role": "user", "content": "Ahoy"}]})
    (kind, session, roleplay, share), record = calls
    assert roleplay == "Captain Vale" and share is True and "roleplay:Captain Vale" in session
    assert record == ("record", session, "Captain Vale")
    assert json.dumps(calls)
