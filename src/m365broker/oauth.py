"""The OAuth 2.0 authorization code + PKCE flow against Microsoft Entra ID.

Reference: RFC 8252 (OAuth 2.0 for Native Apps) and Microsoft's
"Desktop app that calls web APIs" guidance. The short version of why it is
built this way: a desktop app cannot keep a secret, so it uses PKCE and a
loopback redirect, and the token is delivered to the machine the user is
already sitting at rather than to a server that would have to be trusted with
everyone's mailbox.
"""

import urllib.parse
import webbrowser

from . import pkce
from ._http import post_form
from .config import BrokerConfig
from .errors import AuthorizationError, StateMismatch, TokenError
from .loopback import LoopbackReceiver
from .tokens import TokenSet


def build_authorization_url(
    config: BrokerConfig, *, redirect_uri: str, challenge: str, state: str, nonce: str,
    prompt: str = None, login_hint: str = None,
) -> str:
    """Assemble the /authorize URL the system browser should open."""
    params = {
        "client_id": config.client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": config.scope_string,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if prompt:
        params["prompt"] = prompt
    if login_hint:
        params["login_hint"] = login_hint
    return f"{config.authorize_endpoint}?{urllib.parse.urlencode(params)}"


def exchange_code(config: BrokerConfig, *, code: str, verifier: str, redirect_uri: str) -> TokenSet:
    """Redeem an authorization code for tokens.

    Note there is no client_secret: this is a public client, and the PKCE
    verifier is what proves the redemption comes from the same process that
    started the flow.
    """
    status, payload = post_form(
        config.token_endpoint,
        {
            "client_id": config.client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "scope": config.scope_string,
        },
        timeout=config.http_timeout,
    )
    _raise_for_token_error(status, payload)
    return TokenSet.from_response(payload)


def refresh_tokens(config: BrokerConfig, tokens: TokenSet) -> TokenSet:
    """Silently renew using the refresh token, preserving rotation."""
    if not tokens.refresh_token:
        raise TokenError(
            "no refresh token available - the app must request the "
            "'offline_access' scope to renew silently."
        )
    status, payload = post_form(
        config.token_endpoint,
        {
            "client_id": config.client_id,
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh_token,
            "scope": config.scope_string,
        },
        timeout=config.http_timeout,
    )
    _raise_for_token_error(status, payload)
    return TokenSet.from_response(payload, previous=tokens)


def interactive_login(
    config: BrokerConfig, *, open_browser=True, on_url=None, prompt=None, login_hint=None,
) -> TokenSet:
    """Run the full browser sign-in and return the resulting tokens.

    ``on_url`` is called with the authorization URL before the browser opens,
    which is how a GUI can render "click here to sign in" instead of relying on
    webbrowser.open succeeding (it fails on headless and some Linux desktops).
    """
    verifier = pkce.generate_code_verifier()
    challenge = pkce.code_challenge(verifier)
    state = pkce.generate_state()
    nonce = pkce.generate_nonce()

    with LoopbackReceiver(
        host=config.redirect_host, port=config.redirect_port, path=config.redirect_path
    ) as receiver:
        url = build_authorization_url(
            config,
            redirect_uri=receiver.redirect_uri,
            challenge=challenge,
            state=state,
            nonce=nonce,
            prompt=prompt,
            login_hint=login_hint,
        )
        if on_url:
            on_url(url)
        if open_browser:
            webbrowser.open(url)

        params = receiver.wait_for_callback(config.login_timeout)

    # Check state before anything else: if it does not match, the response did
    # not originate from the request we made and the code must not be redeemed.
    if not pkce.constant_time_equals(params.get("state", ""), state):
        raise StateMismatch(
            "the sign-in response did not match this request and was rejected."
        )

    if "error" in params:
        raise AuthorizationError(
            params.get("error_description") or params["error"],
            detail=params.get("error"),
        )

    code = params.get("code")
    if not code:
        raise AuthorizationError("the sign-in response contained no authorization code.")

    return exchange_code(
        config, code=code, verifier=verifier, redirect_uri=receiver.redirect_uri
    )


def _raise_for_token_error(status: int, payload: dict):
    if status == 200 and "access_token" in payload:
        return
    description = payload.get("error_description") or payload.get("error") or "unknown error"
    # Microsoft's error_description carries a correlation id and a numeric
    # AADSTS code; keep both, they are what support will ask for.
    raise TokenError(
        f"Microsoft rejected the token request (HTTP {status}): {description}",
        detail=payload.get("error"),
    )
