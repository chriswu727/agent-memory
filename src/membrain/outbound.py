"""Outbound HTTP transport for model providers.

Provider clients do not read general HTTP environment configuration such as ``.netrc``, but they honour the
standard proxy and CA-bundle variables. Without them, learning silently fails behind corporate or regional
proxies and TLS-inspecting gateways while replies keep working.
"""
from __future__ import annotations

import os
import ssl
from urllib.parse import urlsplit

import httpx

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _first(*names: str) -> str | None:
    return next((os.environ[name] for name in names if os.environ.get(name)), None)


def _bypasses_proxy(host: str) -> bool:
    if host in _LOCAL_HOSTS:
        return True
    for entry in (_first("NO_PROXY", "no_proxy") or "").split(","):
        entry = entry.strip().lower().lstrip(".")
        if entry == "*" or (entry and (host == entry or host.endswith("." + entry))):
            return True
    return False


def proxy_for(base_url: str) -> str | None:
    parts = urlsplit(str(base_url))
    host = (parts.hostname or "").lower()
    if not host or _bypasses_proxy(host):
        return None
    if parts.scheme == "https":
        return _first("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")
    return _first("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")


def verification() -> ssl.SSLContext | bool:
    cafile, capath = _first("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"), _first("SSL_CERT_DIR")
    if not cafile and not capath:
        return True
    try:
        return ssl.create_default_context(cafile=cafile or None, capath=capath or None)
    except (OSError, ssl.SSLError):
        return True  # A broken bundle setting must not disable verification; use the default trust store.


def provider_transport(base_url: str) -> httpx.HTTPTransport:
    return httpx.HTTPTransport(retries=0, trust_env=False, proxy=proxy_for(base_url), verify=verification())
