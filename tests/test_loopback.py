import threading
import urllib.error
import urllib.request

import pytest

from m365broker.errors import CallbackTimeout
from m365broker.loopback import LoopbackReceiver


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_binds_loopback_only_and_reports_its_uri():
    with LoopbackReceiver() as receiver:
        assert receiver.redirect_uri.startswith("http://127.0.0.1:")
        assert receiver.redirect_uri.endswith("/callback")
        assert receiver.port > 0


def test_os_assigns_a_different_port_each_time():
    with LoopbackReceiver() as first, LoopbackReceiver() as second:
        assert first.port != second.port


def test_captures_the_authorization_code_and_shows_the_success_page():
    with LoopbackReceiver() as receiver:
        result = {}

        def wait():
            result["params"] = receiver.wait_for_callback(timeout=5)

        waiter = threading.Thread(target=wait)
        waiter.start()

        status, html = get(f"{receiver.redirect_uri}?code=the-code&state=the-state")
        waiter.join(timeout=5)

    assert status == 200
    assert "signed in" in html.lower()
    assert result["params"] == {"code": "the-code", "state": "the-state"}


def test_success_page_never_contains_the_code():
    """The browser must not be able to scrape the token/code off the page."""
    with LoopbackReceiver() as receiver:
        threading.Thread(
            target=lambda: receiver.wait_for_callback(timeout=5), daemon=True
        ).start()
        _, html = get(f"{receiver.redirect_uri}?code=super-secret-code&state=s")

    assert "super-secret-code" not in html


def test_error_redirect_renders_the_error_page():
    with LoopbackReceiver() as receiver:
        result = {}
        waiter = threading.Thread(
            target=lambda: result.update(params=receiver.wait_for_callback(timeout=5))
        )
        waiter.start()

        status, html = get(
            f"{receiver.redirect_uri}?error=access_denied"
            f"&error_description=The+user+cancelled&state=s"
        )
        waiter.join(timeout=5)

    assert status == 400
    assert "The user cancelled" in html
    assert result["params"]["error"] == "access_denied"


def test_error_description_is_html_escaped():
    """The description is attacker-influenced text rendered into HTML."""
    with LoopbackReceiver() as receiver:
        threading.Thread(
            target=lambda: receiver.wait_for_callback(timeout=5), daemon=True
        ).start()
        _, html = get(
            f"{receiver.redirect_uri}?error=bad"
            f"&error_description=%3Cscript%3Ealert(1)%3C/script%3E&state=s"
        )

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_unknown_paths_are_rejected_without_consuming_the_callback():
    with LoopbackReceiver() as receiver:
        status, _ = get(f"http://127.0.0.1:{receiver.port}/not-the-callback?code=x")
        assert status == 404

        # The real callback still works afterwards.
        result = {}
        waiter = threading.Thread(
            target=lambda: result.update(params=receiver.wait_for_callback(timeout=5))
        )
        waiter.start()
        get(f"{receiver.redirect_uri}?code=real&state=s")
        waiter.join(timeout=5)

    assert result["params"]["code"] == "real"


def test_timeout_raises_when_the_user_never_finishes():
    with LoopbackReceiver() as receiver:
        with pytest.raises(CallbackTimeout):
            receiver.wait_for_callback(timeout=0.3)


def test_custom_path_is_honoured():
    with LoopbackReceiver(path="/oauth/done") as receiver:
        assert receiver.redirect_uri.endswith("/oauth/done")
        status, _ = get(f"http://127.0.0.1:{receiver.port}/callback")
        assert status == 404


def test_port_is_released_after_close():
    receiver = LoopbackReceiver()
    receiver.start()
    port = receiver.port
    receiver.close()

    # Re-binding the same port proves the listener really went away.
    rebound = LoopbackReceiver(port=port)
    try:
        assert rebound.port == port
    finally:
        rebound._server.server_close()
