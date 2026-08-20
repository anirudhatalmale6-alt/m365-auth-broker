"""One-shot loopback HTTP listener that catches the OAuth redirect.

This is the piece that keeps tokens off any server. Microsoft redirects the
system browser to http://127.0.0.1:<port>/callback, this listener -- running
*inside the desktop process* -- reads the authorization code straight out of
the query string, and the code is redeemed locally using the PKCE verifier
that never left memory.

Hardening applied here:
  * binds 127.0.0.1 only, so nothing on the LAN can reach it
  * the OS picks the port at sign-in time (unless one is pinned), so there is
    no long-lived listener to squat on
  * serves exactly one callback then stops
  * rejects any path other than the configured redirect path
  * never writes the query string to a log
"""

import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .errors import CallbackTimeout

_PAGES = Path(__file__).parent / "pages"


def _page(name: str, **substitutions) -> bytes:
    html = (_PAGES / name).read_text(encoding="utf-8")
    for key, value in substitutions.items():
        html = html.replace("{{" + key + "}}", value)
    return html.encode("utf-8")


class _CallbackHandler(BaseHTTPRequestHandler):
    # Set by LoopbackReceiver before the server starts.
    expected_path = "/callback"
    result_box = None

    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - name mandated by BaseHTTPRequestHandler
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != self.expected_path:
            self._respond(404, _page("error.html", detail="Unknown path."))
            return

        params = {
            key: values[0]
            for key, values in urllib.parse.parse_qs(parsed.query).items()
        }

        if self.result_box is not None and not self.result_box.done:
            self.result_box.set(params)

        if "error" in params:
            description = params.get("error_description", params["error"])
            self._respond(400, _page("error.html", detail=_escape(description)))
        else:
            self._respond(200, _page("success.html"))

    def _respond(self, status, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The browser has no business caching or framing an auth result page.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        """Silence the default stderr access log.

        The request line contains the authorization code; printing it would
        undo the point of the whole design.
        """
        return


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


class _ResultBox:
    """Hands the callback parameters from the HTTP thread back to the caller."""

    def __init__(self):
        self._event = threading.Event()
        self._value = None

    @property
    def done(self) -> bool:
        return self._event.is_set()

    def set(self, value):
        self._value = value
        self._event.set()

    def wait(self, timeout):
        if not self._event.wait(timeout):
            return None
        return self._value


class LoopbackReceiver:
    """Context manager that owns the temporary loopback listener.

    Usage::

        with LoopbackReceiver() as receiver:
            webbrowser.open(build_url(redirect_uri=receiver.redirect_uri))
            params = receiver.wait_for_callback(timeout=300)
    """

    def __init__(self, host="127.0.0.1", port=0, path="/callback"):
        self._box = _ResultBox()

        handler = type(
            "_BoundCallbackHandler",
            (_CallbackHandler,),
            {"expected_path": path, "result_box": self._box},
        )

        self._server = HTTPServer((host, port), handler)
        self._host = host
        self._path = path
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="m365-loopback",
            daemon=True,
        )

    @property
    def port(self) -> int:
        """The port the OS actually gave us."""
        return self._server.server_address[1]

    @property
    def redirect_uri(self) -> str:
        return f"http://{self._host}:{self.port}{self._path}"

    def start(self):
        self._thread.start()
        return self

    def wait_for_callback(self, timeout: float) -> dict:
        params = self._box.wait(timeout)
        if params is None:
            raise CallbackTimeout(
                f"no sign-in response within {timeout:.0f}s - the browser window "
                "was probably closed or never completed."
            )
        return params

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc_info):
        self.close()
        return False
