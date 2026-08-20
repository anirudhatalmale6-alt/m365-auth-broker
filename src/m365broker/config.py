"""Broker configuration.

Everything the flow needs is here so the desktop app can construct one object
and hand it around, rather than threading a dozen strings through call sites.
"""

import os
from dataclasses import dataclass, field
from typing import List

from .errors import ConfigError

DEFAULT_AUTHORITY = "https://login.microsoftonline.com"

# Delegated Microsoft Graph permissions.
#   offline_access -> issues the refresh token (without it there is no silent renewal)
#   openid/profile -> identifies the signed-in user
#   User.Read      -> read the user's own profile
#   Mail.Read      -> read the user's mailbox
#   Mail.Send      -> send mail as the signed-in user
DEFAULT_SCOPES = (
    "offline_access",
    "openid",
    "profile",
    "User.Read",
    "Mail.Read",
    "Mail.Send",
)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


@dataclass
class BrokerConfig:
    """Immutable-ish description of the Entra ID application to authenticate against.

    ``tenant`` accepts the usual Microsoft values:
        common        - any work/school or personal Microsoft account
        organizations - any work/school account (Microsoft 365)
        consumers     - personal accounts only
        <tenant guid> - lock sign-in to a single organisation
    """

    client_id: str
    tenant: str = "common"
    scopes: List[str] = field(default_factory=lambda: list(DEFAULT_SCOPES))
    authority: str = DEFAULT_AUTHORITY
    graph_base_url: str = GRAPH_BASE_URL

    # Loopback redirect. The host is pinned to IPv4 loopback because Entra ID
    # treats 127.0.0.1 and localhost as different redirect URIs, and 127.0.0.1
    # is the one Microsoft documents for native apps.
    redirect_host: str = "127.0.0.1"
    redirect_path: str = "/callback"
    redirect_port: int = 0  # 0 = ask the OS for a free port at sign-in time

    # How long we wait for the human to finish signing in, in seconds.
    login_timeout: float = 300.0
    # Network timeout for calls to Microsoft, in seconds.
    http_timeout: float = 30.0
    # Renew an access token this many seconds before it actually expires.
    expiry_skew: float = 120.0

    def __post_init__(self):
        if not self.client_id:
            raise ConfigError(
                "client_id is required - register an app in Entra ID and pass its "
                "Application (client) ID, or set M365_CLIENT_ID."
            )
        if not self.redirect_path.startswith("/"):
            raise ConfigError("redirect_path must start with '/'")
        if isinstance(self.scopes, str):
            self.scopes = self.scopes.split()
        self.authority = self.authority.rstrip("/")
        self.graph_base_url = self.graph_base_url.rstrip("/")

    @property
    def authorize_endpoint(self) -> str:
        return f"{self.authority}/{self.tenant}/oauth2/v2.0/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"{self.authority}/{self.tenant}/oauth2/v2.0/token"

    @property
    def scope_string(self) -> str:
        return " ".join(self.scopes)

    @classmethod
    def from_env(cls, **overrides) -> "BrokerConfig":
        """Build a config from M365_* environment variables.

        Nothing secret lives here -- a public client has no secret -- so a plain
        .env file is an acceptable place for these values.
        """
        scopes = os.environ.get("M365_SCOPES")
        # M365_AUTHORITY / M365_GRAPH_BASE_URL exist for the sovereign clouds
        # (US Government, 21Vianet), which use different hostnames.
        values = {
            "client_id": os.environ.get("M365_CLIENT_ID", ""),
            "tenant": os.environ.get("M365_TENANT", "common"),
            "authority": os.environ.get("M365_AUTHORITY", DEFAULT_AUTHORITY),
            "graph_base_url": os.environ.get("M365_GRAPH_BASE_URL", GRAPH_BASE_URL),
        }
        if scopes:
            values["scopes"] = scopes.split()
        port = os.environ.get("M365_REDIRECT_PORT")
        if port:
            values["redirect_port"] = int(port)
        values.update(overrides)
        return cls(**values)
