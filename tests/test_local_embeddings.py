import hashlib
import os
from pathlib import Path

import httpx
import pytest

from membrain import local_embeddings
from membrain.config import Settings
from membrain.embeddings import EmbeddingRequest

MODEL = os.environ.get("MEMBRAIN_TEST_EMBEDDING_MODEL")


def _files(monkeypatch, contents):
    monkeypatch.setattr(local_embeddings, "FILES", {
        name: (hashlib.sha256(data).hexdigest(), len(data)) for name, data in contents.items()})


def test_download_verifies_pinned_files_and_is_idempotent(tmp_path, monkeypatch):
    contents = {"onnx/model_quantized.onnx": b"model-bytes" * 100, "tokenizer.json": b'{"tokenizer": true}'}
    _files(monkeypatch, contents)
    monkeypatch.setenv("HF_ENDPOINT", "https://mirror.example")
    requested = []

    def respond(request):
        requested.append(str(request.url))
        name = str(request.url).split(local_embeddings.REVISION + "/", 1)[1]
        return httpx.Response(200, content=contents[name])

    assert local_embeddings.installed(tmp_path) is None
    directory = local_embeddings.download(tmp_path, transport=httpx.MockTransport(respond))
    assert local_embeddings.installed(tmp_path) == directory
    assert all(url.startswith("https://mirror.example/Xenova/multilingual-e5-small/resolve/") for url in requested)
    local_embeddings.download(tmp_path, transport=httpx.MockTransport(respond))
    assert len(requested) == 2


@pytest.mark.parametrize("body,status", [(b"tampered-bytes-of-same-size!", 200), (b"", 404), (b"x" * 5000, 200)])
def test_download_rejects_wrong_content_and_leaves_no_partial_model(tmp_path, monkeypatch, body, status):
    _files(monkeypatch, {"tokenizer.json": b"expected-bytes-of-same-size!!"[:len(b"tampered-bytes-of-same-size!")]})
    with pytest.raises(local_embeddings.LocalEmbeddingError):
        local_embeddings.download(tmp_path, transport=httpx.MockTransport(lambda _: httpx.Response(status, content=body)))
    assert local_embeddings.installed(tmp_path) is None
    assert not (local_embeddings.model_directory(tmp_path) / "tokenizer.json").exists()


def test_installed_requires_every_file_with_its_pinned_size(tmp_path):
    directory = local_embeddings.model_directory(tmp_path)
    for name, (_, size) in local_embeddings.FILES.items():
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(b"\0" * (size if name == "tokenizer.json" else 3))
    assert local_embeddings.installed(tmp_path) is None


def test_requests_are_validated_before_the_model_loads(tmp_path):
    provider = local_embeddings.LocalEmbeddingProvider(tmp_path)
    for request in (EmbeddingRequest(()), EmbeddingRequest((" ",)), EmbeddingRequest(("x",), purpose="other")):
        with pytest.raises(ValueError):
            provider.request_metadata(request)
    document = provider.request_metadata(EmbeddingRequest(("same",)))
    query = provider.request_metadata(EmbeddingRequest(("same",), purpose="query"))
    assert document["request_fingerprint"] != query["request_fingerprint"]
    assert provider.space.dimensions == 384 and provider._session is None


def test_local_model_and_remote_provider_are_exclusive():
    with pytest.raises(ValueError):
        Settings(database_url="postgresql://x/y", embedding_local_directory="/models",
                 embedding_base_url="https://api.example/v1", embedding_api_key="k", embedding_model="m",
                 embedding_model_version="1", embedding_dimensions=3)


@pytest.mark.skipif(not MODEL or not local_embeddings.runtime_available(),
                    reason="set MEMBRAIN_TEST_EMBEDDING_MODEL to an installation directory containing the model")
def test_real_model_matches_across_languages_and_paraphrase():
    directory = local_embeddings.installed(Path(MODEL))
    assert directory is not None
    provider = local_embeddings.LocalEmbeddingProvider(directory)
    statements = ["I'm vegetarian.", "My sister is getting married in December.", "My laptop fan is noisy."]
    documents = provider.embed(statements).vectors

    def best(query):
        vector = provider.embed([query], purpose="query").vectors[0]
        scores = [sum(a * b for a, b in zip(vector, document, strict=True)) for document in documents]
        return statements[scores.index(max(scores))]

    assert best("比赛前一晚吃什么比较好？") == "I'm vegetarian."
    assert best("What do you know about the wedding?") == "My sister is getting married in December."
    assert best("¿Por qué hace ruido mi ordenador?") == "My laptop fan is noisy."
    assert all(abs(sum(value * value for value in vector) - 1) < 1e-4 for vector in documents)
