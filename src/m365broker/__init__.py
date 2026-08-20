"""Secure Microsoft 365 OAuth broker for desktop applications.

Public API::

    from m365broker import Broker, BrokerConfig

    broker = Broker(BrokerConfig(client_id="<your app id>"))
    broker.login()
    broker.graph().send_mail(to="someone@example.com", subject="Hi", body="Hello")
"""

from .broker import Broker
from .config import BrokerConfig, DEFAULT_SCOPES
from .errors import (
    AuthorizationError,
    BrokerError,
    CallbackTimeout,
    ConfigError,
    GraphError,
    StateMismatch,
    TokenError,
    TokenExpired,
)
from .graph import GraphClient
from .tokens import TokenSet, TokenStore

__version__ = "1.0.0"

__all__ = [
    "Broker",
    "BrokerConfig",
    "DEFAULT_SCOPES",
    "GraphClient",
    "TokenSet",
    "TokenStore",
    "BrokerError",
    "ConfigError",
    "AuthorizationError",
    "StateMismatch",
    "CallbackTimeout",
    "TokenError",
    "TokenExpired",
    "GraphError",
    "__version__",
]
