import ssl

import httpcore
import pytest

from membrain import outbound

_PROXY_NAMES = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
                "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE")


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in _PROXY_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_provider_requests_use_the_configured_https_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
    assert outbound.proxy_for("https://api.deepseek.com") == "http://proxy.internal:3128"
    assert isinstance(outbound.provider_transport("https://api.deepseek.com")._pool, httpcore.HTTPProxy)
    assert outbound.proxy_for("http://models.lan/v1") is None


@pytest.mark.parametrize("no_proxy,host", [("internal.example", "api.internal.example"), (".example.com", "example.com"),
                                           ("*", "api.deepseek.com"), ("", "localhost"), ("", "127.0.0.1")])
def test_no_proxy_and_loopback_hosts_bypass_the_proxy(monkeypatch, no_proxy, host):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
    monkeypatch.setenv("NO_PROXY", no_proxy)
    assert outbound.proxy_for(f"https://{host}/v1") is None
    assert not isinstance(outbound.provider_transport(f"https://{host}/v1")._pool, httpcore.HTTPProxy)


def test_custom_ca_bundles_are_loaded_and_broken_settings_keep_default_verification(monkeypatch, tmp_path):
    assert outbound.verification() is True
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))
    assert outbound.verification() is True
    import certifi

    monkeypatch.setenv("SSL_CERT_FILE", certifi.where())
    context = outbound.verification()
    assert isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED


def test_general_http_environment_is_still_ignored(monkeypatch, tmp_path):
    netrc = tmp_path / ".netrc"
    netrc.write_text("machine api.deepseek.com login user password secret\n", encoding="utf-8")
    monkeypatch.setenv("NETRC", str(netrc))
    transport = outbound.provider_transport("https://api.deepseek.com")
    assert transport._pool is not None
    from membrain.chat_provider import ChatCompletionsGenerator

    generator = ChatCompletionsGenerator("https://api.deepseek.com", "key", "model")
    assert generator._http._trust_env is False
