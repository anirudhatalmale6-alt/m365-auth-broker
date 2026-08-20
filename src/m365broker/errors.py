"""Exception hierarchy for the broker.

Every error carries a machine-readable ``code`` so the desktop application can
branch on it without string-matching human text.
"""


class BrokerError(Exception):
    """Base class for every error raised by this package."""

    code = "broker_error"

    def __init__(self, message, *, detail=None):
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_dict(self):
        return {"error": self.code, "message": self.message, "detail": self.detail}


class ConfigError(BrokerError):
    """Missing or invalid configuration (e.g. no client id)."""

    code = "config_error"


class AuthorizationError(BrokerError):
    """Microsoft returned an error on the authorization redirect."""

    code = "authorization_error"


class StateMismatch(BrokerError):
    """The redirect did not carry back the state we generated.

    Treated as hostile: it is the signature of a CSRF / authorization-code
    injection attempt, so the flow is aborted rather than retried.
    """

    code = "state_mismatch"


class CallbackTimeout(BrokerError):
    """The user never completed the browser sign-in within the time limit."""

    code = "callback_timeout"


class TokenError(BrokerError):
    """The token endpoint refused the code, the refresh token, or the request."""

    code = "token_error"


class TokenExpired(BrokerError):
    """No valid access token and no way to silently obtain one."""

    code = "token_expired"


class GraphError(BrokerError):
    """Microsoft Graph returned a non-success status."""

    code = "graph_error"

    def __init__(self, message, *, status=None, detail=None):
        super().__init__(message, detail=detail)
        self.status = status

    def to_dict(self):
        data = super().to_dict()
        data["status"] = self.status
        return data
