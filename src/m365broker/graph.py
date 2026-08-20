"""Microsoft Graph calls that prove the granted scopes actually work.

Deliberately small: read the profile, read mail, send mail. That is exactly
what Mail.Read and Mail.Send were requested for, and it is what makes a
successful sign-in verifiable rather than merely plausible.
"""

import urllib.parse

from ._http import request_json
from .errors import GraphError


class GraphClient:
    """Thin Graph wrapper driven by a callable that supplies a fresh token.

    Taking a *callable* rather than a token string means every call picks up a
    silently-refreshed token automatically -- the desktop app never has to
    think about expiry.
    """

    def __init__(self, token_provider, base_url="https://graph.microsoft.com/v1.0", timeout=30.0):
        if isinstance(token_provider, str):
            token = token_provider
            token_provider = lambda: token  # noqa: E731 - accept a plain token too
        self._token_provider = token_provider
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, path, *, method="GET", payload=None, params=None):
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        status, body = request_json(
            url,
            method=method,
            token=self._token_provider(),
            payload=payload,
            timeout=self.timeout,
        )
        if status >= 400:
            error = (body or {}).get("error") or {}
            message = error.get("message") or f"Graph returned HTTP {status}"
            raise GraphError(message, status=status, detail=error.get("code"))
        return body

    def me(self) -> dict:
        """The signed-in user's profile. Requires User.Read."""
        return self._call("/me")

    def list_messages(self, top: int = 10, folder: str = "inbox") -> list:
        """Most recent messages, newest first. Requires Mail.Read."""
        body = self._call(
            f"/me/mailFolders/{folder}/messages",
            params={
                "$top": max(1, min(int(top), 100)),
                "$select": "id,subject,receivedDateTime,from,isRead,bodyPreview",
                "$orderby": "receivedDateTime desc",
            },
        )
        return body.get("value", [])

    def send_mail(self, *, to, subject: str, body: str, html: bool = False,
                  cc=None, save_to_sent: bool = True) -> None:
        """Send mail as the signed-in user. Requires Mail.Send.

        Returns nothing: Graph answers 202 Accepted with an empty body.
        """
        if isinstance(to, str):
            to = [to]
        if isinstance(cc, str):
            cc = [cc]

        message = {
            "subject": subject,
            "body": {"contentType": "HTML" if html else "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": a}} for a in to],
        }
        if cc:
            message["ccRecipients"] = [{"emailAddress": {"address": a}} for a in cc]

        self._call(
            "/me/sendMail",
            method="POST",
            payload={"message": message, "saveToSentItems": save_to_sent},
        )
