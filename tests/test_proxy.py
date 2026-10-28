import json

import httpx
import pytest
from fastapi.testclient import TestClient

from membrain import desktop, embedded_database, host
from membrain.adapters.chatbot import ChatMemory
from membrain.proxy import create_proxy_app, is_loopback


@pytest.fixture(autouse=True)
def no_model_download(monkeypatch):
    monkeypatch.setenv("MEMBRAIN_SEMANTIC", "0")


class Upstream:
    """A stand-in model provider that records requests and answers with a fixed reply."""

    def __init__(self, reply="Sure."):
        self.requests, self.reply = [], reply

    def __call__(self, request: httpx.Request) -> httpx.Response:
        try:
            body = json.loads(request.content)
        except ValueError:
            body = {"raw": request.content.decode()}
        self.requests.append({"url": str(request.url), "auth": request.headers.get("authorization"), "body": body})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "deepseek-chat"}]})
        if "raw" in body:
            return httpx.Response(400, json={"error": "bad request"})
        if body.get("stream"):
            words = self.reply.split(" ")
            chunks = [f'data: {json.dumps({"choices": [{"delta": {"content": w + (" " if i < len(words) - 1 else "")}}]})}\n\n'
                      for i, w in enumerate(words)]
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content="".join(chunks + ["data: [DONE]\n\n"]).encode())
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": self.reply}}]})


class Memory(ChatMemory):
    def __init__(self, directory):
        super().__init__(directory)
        self.contexts, self.records = [], []

    def context(self, speaker, text, *, session, turn_id=None):
        self.contexts.append((speaker, text, session))
        return f"<membrain-notes>notes for {speaker.user}</membrain-notes>"

    def record(self, speaker, reply, *, session, turn_id=None, prompt=None):
        self.records.append((speaker.user, prompt, reply))


def _client(memory, upstream, **options):
    app = create_proxy_app(memory, "https://api.deepseek.com/v1", transport=httpx.MockTransport(upstream), **options)
    return TestClient(app)


def test_memory_goes_just_before_the_latest_message_and_the_reply_is_stored(tmp_path):
    memory, upstream = Memory(tmp_path), Upstream("Peanut-free cookies it is.")
    messages = [{"role": "system", "content": "You are Xiaomei."}, {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": [{"type": "text", "text": "Bake something for me?"}]}]
    with _client(memory, upstream) as client:
        response = client.post("/v1/chat/completions", headers={"Authorization": "Bearer sk-user"},
                               json={"model": "deepseek-chat", "messages": messages, "user": "qq:1001"})
    assert response.status_code == 200 and response.json()["choices"][0]["message"]["content"].startswith("Peanut")
    sent = upstream.requests[0]
    assert sent["url"] == "https://api.deepseek.com/v1/chat/completions" and sent["auth"] == "Bearer sk-user"
    assert [m["role"] for m in sent["body"]["messages"]] == ["system", "user", "assistant", "system", "user"]
    assert sent["body"]["messages"][3]["content"] == "<membrain-notes>notes for qq:1001</membrain-notes>"
    assert sent["body"]["messages"][:3] == messages[:3]  # The cached prefix is untouched.
    assert memory.records == [("qq:1001", "Bake something for me?", "Peanut-free cookies it is.")]


def test_streamed_replies_are_relayed_unchanged_and_stored_whole(tmp_path):
    memory, upstream = Memory(tmp_path), Upstream("Happy birthday, Mei!")
    with _client(memory, upstream) as client:
        with client.stream("POST", "/chat/completions", headers={"X-Membrain-User": "bili:42",
                                                                 "X-Membrain-Platform": "bilibili"},
                           json={"model": "m", "stream": True, "messages": [{"role": "user", "content": "It's Mei's day"}]}) as response:
            text = "".join(response.iter_text())
    assert text.count("data: ") == 4 and text.rstrip().endswith("data: [DONE]")
    assert memory.contexts[0][0].platform == "bilibili" and memory.contexts[0][0].user == "bili:42"
    assert memory.records == [("bili:42", "It's Mei's day", "Happy birthday, Mei!")]


def test_other_endpoints_pass_through_and_anonymous_requests_share_one_memory(tmp_path):
    memory, upstream = Memory(tmp_path), Upstream()
    with _client(memory, upstream) as client:
        assert client.get("/v1/models").json()["data"][0]["id"] == "deepseek-chat"
        client.post("/v1/chat/completions", json={"model": "m", "messages": [{"role": "user", "content": "hey"}]})
        assert client.post("/v1/chat/completions", content=b"not json").status_code == 400
    assert upstream.requests[-1]["body"] == {"raw": "not json"}
    assert upstream.requests[0]["url"] == "https://api.deepseek.com/v1/models"
    assert memory.contexts[0][0].user == "default" and len(memory.contexts) == 1


def test_a_shared_proxy_requires_its_token_and_uses_the_stored_key(tmp_path):
    memory, upstream = Memory(tmp_path), Upstream()
    with pytest.raises(ValueError):
        create_proxy_app(memory, "https://x", token="t")
    with _client(memory, upstream, token="proxy-secret", upstream_key="sk-stored") as client:
        body = {"model": "m", "messages": [{"role": "user", "content": "hey"}], "user": "qq:1"}
        assert client.post("/v1/chat/completions", json=body, headers={"Authorization": "Bearer sk-guess"}).status_code == 401
        assert client.get("/v1/models").status_code == 401
        assert client.post("/v1/chat/completions", json=body,
                           headers={"Authorization": "Bearer proxy-secret"}).status_code == 200
    assert [request["auth"] for request in upstream.requests] == ["Bearer sk-stored"] and memory.contexts
    assert is_loopback("127.0.0.1") and is_loopback("localhost") and not is_loopback("0.0.0.0")


@pytest.mark.skipif(not embedded_database.available(), reason="embedded PostgreSQL binaries are not installed")
def test_proxy_gives_each_viewer_their_own_memory_through_a_real_service(tmp_path):
    directory = tmp_path / "brain"
    upstream = Upstream("Noted!")
    try:
        host.start(directory)
        memory = ChatMemory(directory, producer="membrain-proxy", tools=None)
        with _client(memory, upstream) as client:
            def say(user, text, session):
                client.post("/v1/chat/completions", headers={"X-Membrain-Session": session},
                            json={"model": "m", "user": user, "messages": [
                                {"role": "system", "content": "You are a VTuber."}, {"role": "user", "content": text}]})
                return upstream.requests[-1]["body"]["messages"]

            say("douyin:7", "My dog Doudou turns three on Sunday.", "day-1:douyin:7")
            say("douyin:8", "I only watch you during lunch break.", "day-1:douyin:8")
            later = say("douyin:7", "What should I get my dog?", "day-2:douyin:7")
            other = say("douyin:8", "Anything fun today?", "day-2:douyin:8")
        assert len(later) == 3 and later[1]["role"] == "system" and "Doudou" in later[1]["content"]
        assert "Doudou" not in json.dumps(other) and "lunch break" in other[1]["content"]
        assert "membrain_" not in later[1]["content"] and "tool" not in later[1]["content"]  # No tools to call.
    finally:
        try:
            desktop.stop(directory)
        except desktop.DesktopError:
            pass
