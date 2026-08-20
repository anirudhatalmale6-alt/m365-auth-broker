import time

import pytest

from m365broker import Broker
from m365broker.errors import TokenExpired
from m365broker.tokens import TokenSet

from .support import token_response
from .test_oauth import fake_browser

TOKEN_PATH = "/common/oauth2/v2.0/token"


@pytest.fixture
def broker(config, store):
    return Broker(config, store)


def signed_in(broker):
    broker.login(open_browser=False, on_url=fake_browser())
    return broker


def test_login_persists_the_session_for_the_next_run(broker, stub, config, store):
    signed_in(broker)

    # A brand new Broker over the same store must not need the browser again.
    reloaded = Broker(config, store)
    assert reloaded.is_signed_in()
    assert reloaded.account()["username"] == "ada@contoso.onmicrosoft.com"


def test_login_is_skipped_when_a_valid_session_exists(broker, stub):
    signed_in(broker)
    calls_before = len(stub.requests_to("POST", TOKEN_PATH))

    def must_not_run(url):
        raise AssertionError("browser opened despite a valid cached session")

    broker.login(open_browser=False, on_url=must_not_run)

    assert len(stub.requests_to("POST", TOKEN_PATH)) == calls_before


def test_force_login_reopens_the_account_picker(broker, stub):
    signed_in(broker)
    seen = {}

    def capture(url):
        seen["url"] = url
        fake_browser()(url)

    broker.login(open_browser=False, on_url=capture, force=True)

    assert "prompt=select_account" in seen["url"]


def test_access_token_refreshes_silently_when_expired(broker, stub):
    signed_in(broker)
    stub.route("POST", TOKEN_PATH,
               lambda q, b: (200, token_response(access_token="access-token-2")))

    # Age the cached token past the renewal window.
    broker._tokens.expires_at = time.time() + 5

    assert broker.access_token() == "access-token-2"
    assert stub.requests_to("POST", TOKEN_PATH)[-1][3]["grant_type"] == "refresh_token"


def test_refreshed_token_is_written_back_to_the_store(broker, stub, config, store):
    signed_in(broker)
    stub.route("POST", TOKEN_PATH,
               lambda q, b: (200, token_response(access_token="access-token-2")))
    broker._tokens.expires_at = time.time() + 5
    broker.access_token()

    assert Broker(config, store).tokens.access_token == "access-token-2"


def test_valid_token_is_reused_without_a_network_call(broker, stub):
    signed_in(broker)
    calls_before = len(stub.requests_to("POST", TOKEN_PATH))

    broker.access_token()
    broker.access_token()

    assert len(stub.requests_to("POST", TOKEN_PATH)) == calls_before


def test_access_token_without_a_session_says_so(broker):
    with pytest.raises(TokenExpired):
        broker.access_token()


def test_logout_clears_the_local_session(broker, stub, config, store):
    signed_in(broker)
    broker.logout()

    assert broker.is_signed_in() is False
    assert Broker(config, store).tokens is None


def test_is_signed_in_is_true_while_a_refresh_token_survives(broker):
    broker._tokens = TokenSet(
        access_token="a", expires_at=time.time() - 10, refresh_token="rt"
    )
    assert broker.is_signed_in() is True


def test_is_signed_in_is_false_when_expired_and_unrenewable(broker):
    broker._tokens = TokenSet(
        access_token="a", expires_at=time.time() - 10, refresh_token=None
    )
    assert broker.is_signed_in() is False


# -- the handoff to the desktop application ------------------------------


def test_export_result_gives_the_desktop_app_what_it_needs(broker, stub):
    signed_in(broker)

    result = broker.export_result()

    assert result["access_token"] == "access-token-1"
    assert result["token_type"] == "Bearer"
    assert result["expires_in"] > 0
    assert result["account"]["username"] == "ada@contoso.onmicrosoft.com"
    assert "Mail.Send" in result["scope"]


def test_export_result_withholds_the_refresh_token_by_default(broker, stub):
    signed_in(broker)
    assert "refresh_token" not in broker.export_result()


def test_export_result_includes_the_refresh_token_only_on_request(broker, stub):
    signed_in(broker)
    assert broker.export_result(include_refresh_token=True)["refresh_token"] == "refresh-token-1"


def test_export_result_hands_over_a_freshly_renewed_token(broker, stub):
    """The desktop app must never receive an already-expired token."""
    signed_in(broker)
    stub.route("POST", TOKEN_PATH,
               lambda q, b: (200, token_response(access_token="access-token-2")))
    broker._tokens.expires_at = time.time() + 5

    assert broker.export_result()["access_token"] == "access-token-2"


def test_graph_client_is_wired_to_the_live_token(broker, stub, config):
    signed_in(broker)
    stub.route("GET", "/graph/v1.0/me", lambda q, b: (200, {"displayName": "Ada"}))

    assert broker.graph().me()["displayName"] == "Ada"
