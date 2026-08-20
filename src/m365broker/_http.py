"""Thin urllib wrapper.

Kept deliberately small and dependency-free. Every call is HTTPS with an
explicit timeout, and error bodies are parsed as JSON so callers get
Microsoft's structured error rather than a stack trace.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from .errors import BrokerError

USER_AGENT = "m365-auth-broker/1.0"


def _decode(body: bytes):
    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"raw": body[:2048].decode("utf-8", "replace")}


def _require_https(url: str):
    """Refuse to send credentials anywhere but HTTPS.

    Tests point the broker at a local http:// stub, which is allowed only for
    loopback addresses -- never for a remote host.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "https":
        return
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and host in ("127.0.0.1", "::1", "localhost"):
        return
    raise BrokerError(f"refusing to send credentials over an insecure URL: {url}")


def post_form(url: str, fields: dict, timeout: float = 30.0):
    """POST an application/x-www-form-urlencoded body, return (status, parsed_json)."""
    _require_https(url)
    data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    return _send(request, timeout)


def request_json(url: str, *, method="GET", token=None, payload=None, timeout=30.0):
    """Call a JSON API, optionally with a bearer token. Returns (status, parsed)."""
    _require_https(url)
    body = None
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    return _send(request, timeout)


def _send(request, timeout):
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, _decode(response.read())
    except urllib.error.HTTPError as exc:
        # Microsoft puts the useful diagnosis in the error body, so read it
        # rather than discarding it with the exception.
        return exc.code, _decode(exc.read())
    except urllib.error.URLError as exc:
        raise BrokerError(f"could not reach {request.full_url}", detail=str(exc.reason))
    except TimeoutError as exc:
        raise BrokerError(f"timed out calling {request.full_url}", detail=str(exc))
