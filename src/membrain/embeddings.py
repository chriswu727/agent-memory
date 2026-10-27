from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import httpx

from .helpers import digest
from .outbound import provider_transport


def _bounded_integer(value, name, minimum=1, maximum=2**31 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be a bounded integer")
    return value


def _name(value, name):
    if (not isinstance(value, str) or not value.strip() or len(value) > 256
            or any(ord(character) < 32 for character in value)):
        raise ValueError(f"{name} must be explicitly configured")
    return value


@dataclass(frozen=True)
class EmbeddingSpace:
    model: str
    model_version: str
    dimensions: int

    def __post_init__(self):
        _name(self.model, "model")
        _name(self.model_version, "model_version")
        _bounded_integer(self.dimensions, "dimensions", maximum=65536)

    def descriptor(self) -> dict[str, str | int]:
        return asdict(self)

    @property
    def fingerprint(self) -> str:
        return digest(self.descriptor())


@dataclass(frozen=True)
class EmbeddingRequest:
    texts: tuple[str, ...]
    timeout_seconds: float = 10.0
    purpose: str = "document"  # "query" for search text; providers without asymmetric encoders ignore it


@dataclass(frozen=True)
class EmbeddingResult:
    space: EmbeddingSpace
    vectors: tuple[tuple[float, ...], ...]
    usage: dict[str, int | None] | None
    request_fingerprint: str
    input_bytes: int


class EmbeddingProvider(Protocol):
    @property
    def space(self) -> EmbeddingSpace: ...

    def request_metadata(self, request: EmbeddingRequest) -> dict[str, str | int]: ...

    def __call__(self, request: EmbeddingRequest) -> EmbeddingResult: ...

    def embed(self, texts: list[str] | tuple[str, ...], *, timeout_seconds: float = 10.0) -> EmbeddingResult: ...


class EmbeddingProviderError(RuntimeError):
    def __init__(self, code: str, *, status_code: int | None = None):
        self.code = code
        self.status_code = status_code
        super().__init__(f"Embedding provider request failed: {code}")


class ChatCompletionsEmbeddingProvider:
    def __init__(self, base_url: str, api_key: str, model: str, *, model_version: str, dimensions: int,
                 max_batch_size: int = 64, max_text_bytes: int = 65536, max_request_bytes: int = 262144,
                 max_response_bytes: int = 8388608, transport: httpx.BaseTransport | None = None):
        try:
            url = httpx.URL(base_url)
        except (TypeError, ValueError, httpx.InvalidURL):
            raise ValueError("base_url must be an absolute HTTP or HTTPS URL") from None
        if (url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.query or url.fragment):
            raise ValueError("base_url must have a host and no credentials, query, or fragment")
        if not isinstance(api_key, str) or not api_key or any(ord(character) < 33 or ord(character) > 126 for character in api_key):
            raise ValueError("api_key must be a nonempty ASCII token")
        self._space = EmbeddingSpace(model, model_version, dimensions)
        self._max_batch_size = _bounded_integer(max_batch_size, "max_batch_size", maximum=2048)
        self._max_text_bytes = _bounded_integer(max_text_bytes, "max_text_bytes", maximum=16777216)
        self._max_request_bytes = _bounded_integer(max_request_bytes, "max_request_bytes", maximum=67108864)
        self._max_response_bytes = _bounded_integer(max_response_bytes, "max_response_bytes", maximum=67108864)
        self._base_url = str(url).rstrip("/") + "/"
        self._http = httpx.Client(base_url=self._base_url, headers={"Authorization": f"Bearer {api_key}"},
                                  transport=transport if transport is not None else provider_transport(self._base_url),
                                  follow_redirects=False, trust_env=False)

    @property
    def space(self) -> EmbeddingSpace:
        return self._space

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ChatCompletionsEmbeddingProvider:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _request(self, request):
        if not isinstance(request, EmbeddingRequest):
            raise ValueError("request must be an EmbeddingRequest")
        timeout = request.timeout_seconds
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= 300):
            raise ValueError("timeout_seconds must be finite, positive, and at most 300")
        if (not isinstance(request.texts, (tuple, list))
                or not 1 <= len(request.texts) <= self._max_batch_size):
            raise ValueError("texts must be a bounded nonempty sequence")
        texts = tuple(request.texts)
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Embedding text must be nonempty")
            try:
                text_bytes = len(text.encode("utf-8"))
            except UnicodeEncodeError:
                raise ValueError("Embedding text must be valid UTF-8") from None
            if text_bytes > self._max_text_bytes:
                raise ValueError("Embedding text exceeds byte budget")
        body = {"model": self.space.model, "input": texts, "encoding_format": "float"}
        content = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(content) > self._max_request_bytes:
            raise ValueError("Embedding request exceeds byte budget")
        fingerprint = digest({"endpoint": self._base_url, "space": self.space.descriptor(), "body": body})
        return content, fingerprint, len(texts)

    def request_metadata(self, request: EmbeddingRequest) -> dict[str, str | int]:
        content, fingerprint, _ = self._request(request)
        return {"input_bytes": len(content), "request_fingerprint": fingerprint}

    def embed(self, texts: list[str] | tuple[str, ...], *, timeout_seconds: float = 10.0) -> EmbeddingResult:
        return self(EmbeddingRequest(texts=texts, timeout_seconds=timeout_seconds))

    def __call__(self, request: EmbeddingRequest) -> EmbeddingResult:
        content, fingerprint, count = self._request(request)
        deadline = time.monotonic() + request.timeout_seconds
        try:
            with self._http.stream("POST", "embeddings", content=content,
                                   headers={"Content-Type": "application/json"}, timeout=request.timeout_seconds) as response:
                if response.status_code != 200:
                    raise EmbeddingProviderError("http_error", status_code=response.status_code)
                data = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() >= deadline:
                        raise EmbeddingProviderError("timeout")
                    if len(data) + len(chunk) > self._max_response_bytes:
                        raise EmbeddingProviderError("response_too_large")
                    data.extend(chunk)
                if time.monotonic() >= deadline:
                    raise EmbeddingProviderError("timeout")
        except httpx.TimeoutException:
            raise EmbeddingProviderError("timeout") from None
        except httpx.HTTPError:
            raise EmbeddingProviderError("transport_error") from None
        try:
            body = json.loads(data)
            if not isinstance(body, dict) or body.get("model", self.space.model) != self.space.model:
                raise ValueError
            entries = body["data"]
            if not isinstance(entries, list) or len(entries) != count:
                raise ValueError
            vectors = [None] * count
            for entry in entries:
                index, vector = entry["index"], entry["embedding"]
                if type(index) is not int or not 0 <= index < count or vectors[index] is not None:
                    raise ValueError
                if not isinstance(vector, list) or len(vector) != self.space.dimensions:
                    raise EmbeddingProviderError("dimension_mismatch")
                if any(type(value) not in (float, int) or not math.isfinite(value) for value in vector):
                    raise ValueError
                if not any(vector):
                    raise ValueError
                vectors[index] = tuple(float(value) for value in vector)
            usage = body.get("usage")
            if usage is not None:
                if not isinstance(usage, dict):
                    raise ValueError
                usage = {"input_tokens": usage.get("prompt_tokens"), "total_tokens": usage.get("total_tokens")}
                for value in usage.values():
                    if value is not None and (type(value) is not int or not 0 <= value <= 2**63 - 1):
                        raise ValueError
            return EmbeddingResult(self.space, tuple(vectors), usage, fingerprint, len(content))
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, RecursionError):
            raise EmbeddingProviderError("invalid_response") from None
