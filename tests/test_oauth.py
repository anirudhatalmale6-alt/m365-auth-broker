"""End-to-end coverage of the authorization code + PKCE flow.

A fake browser stands in for the real one: it receives the authorization URL,
checks it, and drives the loopback redirect exactly as Microsoft would. That
exercises the whole path -- URL building, loopback capture, state validation,
code redemption -- without an Azure tenant.
"""

import urllib.error
import urllib.parse
import urllib.request

import pytest

from m365broker import oauth, pkce
from m365broker.errors import AuthorizationError, StateMismatch, TokenError
from m365broker.tokens import TokenSet

from .support import token_response

TOKEN_PATH = "/common/oauth2/v2.0/token"


def parse_query(url):
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}


def fake_browser(*, code="auth-code-1", state=None, error=None, extra=None):
    """Return an on_url callback that plays the part of the system browser."""

    def visit(url):
        params = parse_query(url)
        redirect_uri = params["redirect_uri"]
        response = {"state": state if state is not None else params["state"]}
        if error:
            response["error"] = error
            response["error_description"] = "simulated failure"
        else:
            response["code"] = code
        response.update(extra or {})
        try:
            urllib.request.urlopen(
                f"{redirect_uri}?{urllib.parse.urlencode(response)}", timeout=5
            ).read()
        except urllib.error.HTTPError as exc:
            # A real browser renders a 4xx error page rather than raising; the
            # callback has still been delivered, which is what matters here.
            exc.read()

    return visit


# -- authorization URL ---------------------------------------------------


def test_authorization_url_carries_every_required_parameter(config):
    url = oauth.build_authorization_url(
        config, redirect_uri="http://127.0.0.1:9/callback",
        challenge="the-challenge", state="the-state", nonce="the-nonce",
    )
    params = parse_query(url)

    assert url.startswith(config.authorize_endpoint)
    assert params["client_id"] == config.client_id
    assert params["response_type"] == "code"
    assert params["response_mode"] == "query"
    assert params["code_challenge"] == "the-challenge"
    assert params["code_challenge_method"] == "S256"
    assert params["state"] == "the-state"
    assert params["nonce"] == "the-nonce"
    assert params["redirect_uri"] == "http://127.0.0.1:9/callback"


def test_authorization_url_never_leaks_the_verifier(config):
    verifier = pkce.generate_code_verifier()
    url = oauth.build_authorization_url(
        config, redirect_uri="http://127.0.0.1:9/callback",
        challenge=pkce.code_challenge(verifier), state="s", nonce="n",
    )
    assert verifier not in url


def test_authorization_url_requests_the_mail_scopes(config):
    url = oauth.build_authorization_url(
        config, redirect_uri="http://127.0.0.1:9/callback",
        challenge="c", state="s", nonce="n",
    )
    scope = parse_query(url)["scope"]
    assert "Mail.Read" in scope and "Mail.Send" in scope and "offline_access" in scope


def test_optional_parameters_are_omitted_when_unset(config):
    url = oauth.build_authorization_url(
        config, redirect_uri="http://127.0.0.1:9/callback",
        challenge="c", state="s", nonce="n",
    )
    assert "prompt" not in parse_query(url)
    assert "login_hint" not in parse_query(url)


def test_prompt_and_login_hint_are_forwarded(config):
    url = oauth.build_authorization_url(
        config, redirect_uri="http://127.0.0.1:9/callback",
        challenge="c", state="s", nonce="n",
        prompt="select_account", login_hint="ada@contoso.com",
    )
    params = parse_query(url)
    assert params["prompt"] == "select_account"
    assert params["login_hint"] == "ada@contoso.com"


# -- code exchange -------------------------------------------------------


def test_exchange_code_posts_the_verifier_and_no_client_secret(config, stub):
    tokens = oauth.exchange_code(
        config, code="the-code", verifier="the-verifier",
        redirect_uri="http://127.0.0.1:9/callback",
    )

    assert isinstance(tokens, TokenSet)
    assert tokens.access_token == "access-token-1"

    _, _, _, body = stub.requests_to("POST", TOKEN_PATH)[0]
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "the-code"
    assert body["code_verifier"] == "the-verifier"
    assert body["client_id"] == config.client_id
    # A public client must not send a secret - sending one would be a bug.
    assert "client_secret" not in body


def test_exchange_code_surfaces_microsoft_errors(config, stub):
    stub.route("POST", TOKEN_PATH, lambda q, b: (400, {
        "error": "invalid_grant",
        "error_description": "AADSTS70008: The provided authorization code has expired.",
    }))

    with pytest.raises(TokenError) as excinfo:
        oauth.exchange_code(config, code="stale", verifier="v",
                            redirect_uri="http://127.0.0.1:9/callback")

    assert "AADSTS70008" in str(excinfo.value)
    assert excinfo.value.detail == "invalid_grant"


def test_token_response_without_access_token_is_an_error(config, stub):
    stub.route("POST", TOKEN_PATH, lambda q, b: (200, {"token_type": "Bearer"}))

    with pytest.raises(TokenError):
        oauth.exchange_code(config, code="c", verifier="v",
                            redirect_uri="http://127.0.0.1:9/callback")


# -- refresh -------------------------------------------------------------


def test_refresh_uses_the_refresh_grant_and_rotates(config, stub):
    stub.route("POST", TOKEN_PATH, lambda q, b: (200, token_response(
        access_token="access-token-2", refresh_token="refresh-token-2")))
    existing = TokenSet.from_response(token_response())

    renewed = oauth.refresh_tokens(config, existing)

    assert renewed.access_token == "access-token-2"
    assert renewed.refresh_token == "refresh-token-2"

    _, _, _, body = stub.requests_to("POST", TOKEN_PATH)[0]
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "refresh-token-1"


def test_refresh_without_a_refresh_token_explains_offline_access(config):
    tokens = TokenSet(access_token="a", expires_at=0, refresh_token=None)

    with pytest.raises(TokenError) as excinfo:
        oauth.refresh_tokens(config, tokens)

    assert "offline_access" in str(excinfo.value)


def test_revoked_refresh_token_reports_the_microsoft_reason(config, stub):
    stub.route("POST", TOKEN_PATH, lambda q, b: (400, {
        "error": "invalid_grant",
        "error_description": "AADSTS50173: The provided grant has expired.",
    }))
    tokens = TokenSet.from_response(token_response())

    with pytest.raises(TokenError) as excinfo:
        oauth.refresh_tokens(config, tokens)

    assert "AADSTS50173" in str(excinfo.value)


# -- full interactive flow ----------------------------------------------


def test_interactive_login_completes_end_to_end(config, stub):
    tokens = oauth.interactive_login(config, open_browser=False, on_url=fake_browser())

    assert tokens.access_token == "access-token-1"
    assert tokens.account["username"] == "ada@contoso.onmicrosoft.com"


def test_interactive_login_redeems_the_matching_verifier(config, stub):
    """The verifier posted must hash to the challenge that was sent."""
    seen = {}

    def capture_then_redirect(url):
        seen["challenge"] = parse_query(url)["code_challenge"]
        fake_browser()(url)

    oauth.interactive_login(config, open_browser=False, on_url=capture_then_redirect)

    _, _, _, body = stub.requests_to("POST", TOKEN_PATH)[0]
    assert pkce.code_challenge(body["code_verifier"]) == seen["challenge"]


def test_interactive_login_rejects_a_mismatched_state(config, stub):
    """Guards against authorization code injection."""
    with pytest.raises(StateMismatch):
        oauth.interactive_login(
            config, open_browser=False, on_url=fake_browser(state="attacker-state")
        )

    # Critically: the code must never have been redeemed.
    assert stub.requests_to("POST", TOKEN_PATH) == []


def test_interactive_login_reports_user_cancellation(config, stub):
    with pytest.raises(AuthorizationError) as excinfo:
        oauth.interactive_login(
            config, open_browser=False, on_url=fake_browser(error="access_denied")
        )

    assert excinfo.value.detail == "access_denied"
    assert stub.requests_to("POST", TOKEN_PATH) == []


def test_interactive_login_rejects_a_response_with_no_code(config, stub):
    def redirect_without_code(url):
        params = parse_query(url)
        urllib.request.urlopen(
            f"{params['redirect_uri']}?state={params['state']}", timeout=5
        ).read()

    with pytest.raises(AuthorizationError):
        oauth.interactive_login(config, open_browser=False, on_url=redirect_without_code)


def test_each_login_uses_a_fresh_state_and_verifier(config, stub):
    states = []
    oauth.interactive_login(
        config, open_browser=False,
        on_url=lambda url: (states.append(parse_query(url)["state"]), fake_browser()(url)),
    )
    oauth.interactive_login(
        config, open_browser=False,
        on_url=lambda url: (states.append(parse_query(url)["state"]), fake_browser()(url)),
    )

    assert states[0] != states[1]
    verifiers = [r[3]["code_verifier"] for r in stub.requests_to("POST", TOKEN_PATH)]
    assert verifiers[0] != verifiers[1]


def test_loopback_listener_is_closed_after_a_failed_login(config, stub):
    """A failed sign-in must not leave a listener running on the machine."""
    ports = []

    def note_port_then_fail(url):
        ports.append(int(urllib.parse.urlparse(parse_query(url)["redirect_uri"]).port))
        fake_browser(error="access_denied")(url)

    with pytest.raises(AuthorizationError):
        oauth.interactive_login(config, open_browser=False, on_url=note_port_then_fail)

    with pytest.raises(Exception):
        urllib.request.urlopen(f"http://127.0.0.1:{ports[0]}/callback", timeout=2)
