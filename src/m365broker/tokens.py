"""Token value object and at-rest storage."""

import base64
import json
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .errors import BrokerError

# Keys we refuse to print in logs or diagnostics.
_SENSITIVE = ("access_token", "refresh_token", "id_token", "code", "code_verifier")


def _decode_jwt_claims(token: str) -> dict:
    """Read the claims out of a JWT payload *without* verifying the signature.

    This is safe for the narrow use it has here (showing which account signed
    in) because the id_token came to us directly from Microsoft's token
    endpoint over TLS, not through the browser. Never make an authorisation
    decision on these claims.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


@dataclass
class TokenSet:
    """A set of tokens plus everything needed to decide when to renew them."""

    access_token: str
    expires_at: float
    refresh_token: Optional[str] = None
    id_token: Optional[str] = None
    token_type: str = "Bearer"
    scope: str = ""
    account: dict = field(default_factory=dict)

    @classmethod
    def from_response(cls, payload: dict, *, previous: "TokenSet" = None) -> "TokenSet":
        """Build from a Microsoft token endpoint response.

        Microsoft rotates refresh tokens: each redemption returns a new one and
        retires the old. If a response omits it we keep the previous token so a
        renewal never silently downgrades the session to non-renewable.
        """
        expires_in = float(payload.get("expires_in", 3600))
        refresh = payload.get("refresh_token")
        if not refresh and previous is not None:
            refresh = previous.refresh_token

        id_token = payload.get("id_token")
        account = {}
        if id_token:
            claims = _decode_jwt_claims(id_token)
            account = {
                "name": claims.get("name"),
                "username": claims.get("preferred_username") or claims.get("upn"),
                "oid": claims.get("oid"),
                "tid": claims.get("tid"),
            }
        elif previous is not None:
            account = previous.account

        return cls(
            access_token=payload["access_token"],
            expires_at=time.time() + expires_in,
            refresh_token=refresh,
            id_token=id_token,
            token_type=payload.get("token_type", "Bearer"),
            scope=payload.get("scope", ""),
            account=account,
        )

    @property
    def expires_in(self) -> float:
        return max(0.0, self.expires_at - time.time())

    def is_expired(self, skew: float = 120.0) -> bool:
        """True when the token is gone or close enough to gone to renew now."""
        return time.time() >= (self.expires_at - skew)

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "expires_at": self.expires_at,
            "refresh_token": self.refresh_token,
            "id_token": self.id_token,
            "token_type": self.token_type,
            "scope": self.scope,
            "account": self.account,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TokenSet":
        return cls(
            access_token=data["access_token"],
            expires_at=float(data["expires_at"]),
            refresh_token=data.get("refresh_token"),
            id_token=data.get("id_token"),
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope", ""),
            account=data.get("account") or {},
        )

    def redacted(self) -> dict:
        """Safe-to-log view: structure and expiry, no secret material."""
        data = self.to_dict()
        for key in _SENSITIVE:
            if data.get(key):
                data[key] = f"<redacted:{len(data[key])} chars>"
        data["expires_in"] = round(self.expires_in)
        return data


class TokenStore:
    """Persists a TokenSet between runs of the desktop application.

    Two backends:
      * OS keychain via the optional ``keyring`` package (preferred - the
        secret is protected by the user's login credentials)
      * a 0600 file under the user's profile as the portable fallback

    The fallback is honest about what it is: file permissions, not encryption.
    On a machine where the OS account itself is trusted that is the same
    protection the user's own mail client relies on, but the keyring backend is
    what you want on shared or managed devices.
    """

    SERVICE_NAME = "m365-auth-broker"

    def __init__(self, path=None, *, account="default", use_keyring=None):
        self.account = account
        self.path = Path(path) if path else self._default_path()
        if use_keyring is None:
            use_keyring = self._keyring_available()
        self.use_keyring = use_keyring

    @staticmethod
    def _default_path() -> Path:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config"
        )
        return Path(base) / "m365-auth-broker" / "tokens.json"

    @staticmethod
    def _keyring_available() -> bool:
        try:
            import keyring  # noqa: F401
        except Exception:
            return False
        return True

    def save(self, tokens: TokenSet):
        blob = json.dumps(tokens.to_dict())
        if self.use_keyring:
            import keyring

            keyring.set_password(self.SERVICE_NAME, self.account, blob)
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a private temp file then rename, so a crash mid-write cannot
        # leave a truncated (or world-readable) token file behind.
        temp = self.path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(blob)
        except Exception:
            temp.unlink(missing_ok=True)
            raise
        os.replace(temp, self.path)
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)

    def load(self) -> Optional[TokenSet]:
        if self.use_keyring:
            import keyring

            blob = keyring.get_password(self.SERVICE_NAME, self.account)
            if not blob:
                return None
        else:
            if not self.path.exists():
                return None
            blob = self.path.read_text()

        try:
            return TokenSet.from_dict(json.loads(blob))
        except (ValueError, KeyError) as exc:
            raise BrokerError(
                "stored tokens are unreadable - sign in again", detail=str(exc)
            )

    def clear(self):
        if self.use_keyring:
            import keyring

            try:
                keyring.delete_password(self.SERVICE_NAME, self.account)
            except Exception:
                pass
            return
        self.path.unlink(missing_ok=True)
