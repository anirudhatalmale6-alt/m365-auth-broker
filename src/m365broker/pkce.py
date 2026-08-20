"""PKCE (RFC 7636) and anti-CSRF value generation.

A desktop application is a *public* client: it ships to end users, so it cannot
hold a client secret. PKCE is what replaces the secret. The verifier is created
fresh per sign-in, never leaves the process, and only its SHA-256 hash travels
in the browser URL -- so an attacker who intercepts the redirect still cannot
redeem the authorization code.
"""

import base64
import hashlib
import secrets

# RFC 7636 section 4.1 allows 43-128 characters. 64 random bytes of entropy
# encoded base64url lands at 86 characters, comfortably inside that window.
_VERIFIER_ENTROPY_BYTES = 64


def _b64url(raw: bytes) -> str:
    """base64url encode without padding, as every OAuth RFC requires."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def generate_code_verifier() -> str:
    """Return a fresh high-entropy PKCE code verifier."""
    return _b64url(secrets.token_bytes(_VERIFIER_ENTROPY_BYTES))


def code_challenge(verifier: str) -> str:
    """Derive the S256 code challenge for ``verifier``.

    Only the S256 method is implemented. The ``plain`` method is legal in the
    RFC but offers no protection against redirect interception, so it is
    deliberately unsupported.
    """
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _b64url(digest)


def generate_state() -> str:
    """Opaque anti-CSRF value echoed back on the redirect."""
    return _b64url(secrets.token_bytes(32))


def generate_nonce() -> str:
    """Replay guard bound into the returned id_token."""
    return _b64url(secrets.token_bytes(32))


def constant_time_equals(left: str, right: str) -> bool:
    """Compare two opaque values without leaking a timing signal."""
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return secrets.compare_digest(left, right)
