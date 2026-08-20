"""The one class the desktop application actually needs.

    broker = Broker(BrokerConfig(client_id="..."))
    broker.login()                 # opens the browser, once
    token = broker.access_token()  # valid token, refreshed silently forever
    broker.graph().send_mail(to="a@b.com", subject="hi", body="hello")

Everything below that line is an implementation detail.
"""

import threading

from .config import BrokerConfig
from .errors import TokenExpired
from .graph import GraphClient
from .oauth import interactive_login, refresh_tokens
from .tokens import TokenSet, TokenStore


class Broker:
    def __init__(self, config: BrokerConfig, store: TokenStore = None):
        self.config = config
        self.store = store if store is not None else TokenStore()
        self._tokens = None
        # Two threads in a GUI hitting an expired token at once must not fire
        # two refreshes -- the second would present an already-rotated refresh
        # token and be rejected.
        self._lock = threading.Lock()

    # -- session ---------------------------------------------------------

    @property
    def tokens(self) -> TokenSet:
        if self._tokens is None:
            self._tokens = self.store.load()
        return self._tokens

    def is_signed_in(self) -> bool:
        tokens = self.tokens
        return bool(tokens and (not tokens.is_expired(self.config.expiry_skew)
                                or tokens.refresh_token))

    def login(self, *, open_browser=True, on_url=None, force=False, login_hint=None) -> TokenSet:
        """Sign in interactively, or reuse an existing session.

        Set ``force=True`` to always show the account picker (useful for a
        "switch account" menu item).
        """
        with self._lock:
            if not force and self.tokens and not self.tokens.is_expired(self.config.expiry_skew):
                return self._tokens

            tokens = interactive_login(
                self.config,
                open_browser=open_browser,
                on_url=on_url,
                prompt="select_account" if force else None,
                login_hint=login_hint,
            )
            self._persist(tokens)
            return tokens

    def access_token(self) -> str:
        """A currently-valid access token, renewing silently when needed."""
        with self._lock:
            tokens = self.tokens
            if tokens is None:
                raise TokenExpired("not signed in - call login() first.")
            if not tokens.is_expired(self.config.expiry_skew):
                return tokens.access_token

            renewed = refresh_tokens(self.config, tokens)
            self._persist(renewed)
            return renewed.access_token

    def logout(self):
        """Forget the local session.

        This clears tokens on this machine. It does not revoke Microsoft-side
        consent -- that is done by the user or an admin in the Microsoft
        account portal, and the README says where.
        """
        with self._lock:
            self._tokens = None
            self.store.clear()

    def account(self) -> dict:
        tokens = self.tokens
        return dict(tokens.account) if tokens else {}

    # -- graph -----------------------------------------------------------

    def graph(self) -> GraphClient:
        return GraphClient(
            self.access_token,
            base_url=self.config.graph_base_url,
            timeout=self.config.http_timeout,
        )

    # -- handoff ---------------------------------------------------------

    def export_result(self, *, include_refresh_token=False) -> dict:
        """The payload handed to the desktop application after sign-in.

        ``refresh_token`` is withheld unless explicitly asked for: most callers
        only need a valid access token, and a refresh token that escapes into a
        log file or an IPC transcript is a long-lived key to someone's mailbox.
        """
        tokens = self.tokens
        if tokens is None:
            raise TokenExpired("not signed in.")
        result = {
            "access_token": self.access_token(),
            "token_type": tokens.token_type,
            "expires_at": tokens.expires_at,
            "expires_in": round(tokens.expires_in),
            "scope": tokens.scope,
            "account": tokens.account,
        }
        if include_refresh_token:
            result["refresh_token"] = tokens.refresh_token
        return result

    def _persist(self, tokens: TokenSet):
        self._tokens = tokens
        self.store.save(tokens)
