"""The transport guard: credentials must never leave over plain HTTP."""

import pytest

from m365broker import _http
from m365broker.errors import BrokerError


@pytest.mark.parametrize(
    "url",
    [
        "http://login.microsoftonline.com/common/oauth2/v2.0/token",
        "http://evil.example.com/token",
        "http://127.0.0.1.evil.com/token",  # loopback-looking but not loopback
        "ftp://example.com/token",
    ],
)
def test_insecure_urls_are_refused(url):
    with pytest.raises(BrokerError) as excinfo:
        _http.post_form(url, {"client_id": "x"})
    assert "insecure" in str(excinfo.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "http://127.0.0.1:8400/token",  # the local stub used by the tests
        "http://localhost:8400/token",
    ],
)
def test_https_and_loopback_pass_the_guard(url, monkeypatch):
    """The guard must let the real endpoint and the loopback stub through."""
    monkeypatch.setattr(_http, "_send", lambda request, timeout: (200, {"ok": True}))
    assert _http.post_form(url, {"client_id": "x"}) == (200, {"ok": True})


def test_unreachable_host_raises_a_broker_error():
    with pytest.raises(BrokerError) as excinfo:
        # Port 1 on loopback: nothing listens there, so connect fails fast.
        _http.request_json("http://127.0.0.1:1/anything", timeout=2)
    assert "could not reach" in str(excinfo.value)


def test_non_json_error_body_is_still_returned(monkeypatch):
    """Microsoft occasionally returns an HTML error page; don't crash on it."""
    assert _http._decode(b"<html>gateway timeout</html>") == {
        "raw": "<html>gateway timeout</html>"
    }


def test_empty_body_decodes_to_empty_dict():
    assert _http._decode(b"") == {}
