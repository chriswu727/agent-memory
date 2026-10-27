"""Local multilingual embeddings that need no API key.

The model (multilingual-e5-small, int8 ONNX export, MIT licence) runs in-process with ONNX Runtime. Its files are
downloaded once into the installation directory, pinned to a revision and verified by SHA-256. Without the optional
``semantic`` extra or before the download finishes, recall stays lexical.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

import httpx

from .embeddings import EmbeddingRequest, EmbeddingResult, EmbeddingSpace
from .helpers import digest
from .outbound import provider_transport

MODEL = "multilingual-e5-small"
REPOSITORY = "Xenova/multilingual-e5-small"
REVISION = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
SPACE = EmbeddingSpace(MODEL, "xenova-onnx-int8-" + REVISION[:12], 384)
FILES = {
    "onnx/model_quantized.onnx": ("f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193", 118308185),
    "tokenizer.json": ("0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39", 17082730),
}
MAX_TOKENS = 512
PREFIXES = {"document": "passage: ", "query": "query: "}
_DIRECTORY = "models/" + MODEL + "-" + REVISION[:12]


class LocalEmbeddingError(RuntimeError):
    pass


def runtime_available() -> bool:
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
    except ImportError:
        return False
    return True


def model_directory(installation: Path) -> Path:
    return Path(installation) / _DIRECTORY


def installed(installation: Path) -> Path | None:
    """Return the model directory when every pinned file is present with its expected size."""
    directory = model_directory(installation)
    for name, (_, size) in FILES.items():
        path = directory / name
        if not path.is_file() or path.is_symlink() or path.stat().st_size != size:
            return None
    return directory


def _source(name: str) -> str:
    endpoint = (os.environ.get("HF_ENDPOINT") or "https://huggingface.co").rstrip("/")
    return f"{endpoint}/{REPOSITORY}/resolve/{REVISION}/{name}"


def download(installation: Path, *, timeout: float = 600.0, transport: httpx.BaseTransport | None = None) -> Path:
    """Fetch and verify the pinned model files; safe to repeat and to interrupt."""
    directory = model_directory(installation)
    for name, (expected, size) in FILES.items():
        target = directory / name
        if target.is_file() and target.stat().st_size == size:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        url = _source(name)
        hasher, received = hashlib.sha256(), 0
        with httpx.Client(transport=transport or provider_transport(url), follow_redirects=True, trust_env=False,
                          timeout=httpx.Timeout(timeout, connect=30)) as client, \
                client.stream("GET", url) as response:
            if response.status_code != 200:
                raise LocalEmbeddingError(f"Model download failed with HTTP {response.status_code}.")
            with partial.open("wb") as stream:
                for chunk in response.iter_bytes(1 << 20):
                    received += len(chunk)
                    if received > size:
                        raise LocalEmbeddingError("Model download is larger than expected.")
                    hasher.update(chunk)
                    stream.write(chunk)
        if received != size or hasher.hexdigest() != expected:
            partial.unlink(missing_ok=True)
            raise LocalEmbeddingError("Model download did not match its pinned checksum.")
        os.replace(partial, target)
    return directory


class LocalEmbeddingProvider:
    """In-process ``EmbeddingProvider`` for the pinned multilingual model."""

    metered = False  # No cost or network call: exempt from model-call budgets and the operation ledger.

    def __init__(self, directory: Path, *, threads: int = 2, max_batch_size: int = 64):
        self._directory = Path(directory)
        self._threads = threads
        self._max_batch_size = max_batch_size
        self._lock = threading.Lock()
        self._session = None
        self._tokenizer = None

    @property
    def space(self) -> EmbeddingSpace:
        return SPACE

    def close(self) -> None:
        self._session = self._tokenizer = None

    def __enter__(self) -> LocalEmbeddingProvider:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _load(self):
        with self._lock:
            if self._session is None:
                import onnxruntime
                from tokenizers import Tokenizer

                tokenizer = Tokenizer.from_file(str(self._directory / "tokenizer.json"))
                tokenizer.enable_truncation(MAX_TOKENS)
                tokenizer.enable_padding(pad_id=tokenizer.token_to_id("<pad>") or 0, pad_token="<pad>")
                options = onnxruntime.SessionOptions()
                options.intra_op_num_threads = max(1, min(self._threads, os.cpu_count() or 1))
                options.inter_op_num_threads = 1
                self._session = onnxruntime.InferenceSession(
                    str(self._directory / "onnx" / "model_quantized.onnx"), options,
                    providers=["CPUExecutionProvider"])
                self._inputs = {item.name for item in self._session.get_inputs()}
                self._tokenizer = tokenizer
        return self._session, self._tokenizer

    def _validated(self, request):
        if not isinstance(request, EmbeddingRequest):
            raise ValueError("request must be an EmbeddingRequest")
        if request.purpose not in PREFIXES:
            raise ValueError("Unknown embedding purpose")
        texts = tuple(request.texts) if isinstance(request.texts, (tuple, list)) else ()
        if not 1 <= len(texts) <= self._max_batch_size:
            raise ValueError("texts must be a bounded nonempty sequence")
        if any(not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 65536 for text in texts):
            raise ValueError("Embedding text must be nonempty and bounded")
        body = {"space": SPACE.descriptor(), "purpose": request.purpose, "texts": texts}
        return texts, digest(body), len(json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def request_metadata(self, request: EmbeddingRequest) -> dict[str, str | int]:
        _, fingerprint, size = self._validated(request)
        return {"input_bytes": size, "request_fingerprint": fingerprint}

    def embed(self, texts, *, timeout_seconds: float = 10.0, purpose: str = "document") -> EmbeddingResult:
        return self(EmbeddingRequest(texts=tuple(texts), timeout_seconds=timeout_seconds, purpose=purpose))

    def __call__(self, request: EmbeddingRequest) -> EmbeddingResult:
        import numpy

        texts, fingerprint, size = self._validated(request)
        session, tokenizer = self._load()
        encoded = tokenizer.encode_batch([PREFIXES[request.purpose] + text for text in texts])
        ids = numpy.array([item.ids for item in encoded], dtype=numpy.int64)
        mask = numpy.array([item.attention_mask for item in encoded], dtype=numpy.int64)
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feeds["token_type_ids"] = numpy.zeros_like(ids)
        hidden = session.run(None, feeds)[0]
        weights = mask[..., None].astype(numpy.float32)
        pooled = (hidden * weights).sum(axis=1) / numpy.maximum(weights.sum(axis=1), 1.0)
        pooled /= numpy.maximum(numpy.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
        tokens = int(mask.sum())
        vectors = tuple(tuple(float(value) for value in row) for row in pooled)
        return EmbeddingResult(SPACE, vectors, {"input_tokens": tokens, "total_tokens": tokens}, fingerprint, size)
