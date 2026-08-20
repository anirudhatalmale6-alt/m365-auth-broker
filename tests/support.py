"""A stand-in for Microsoft, so the suite runs offline on a fresh clone.

The tests drive real HTTP against a real local server rather than monkey
patching urllib. That way the request building, form encoding, error decoding
and timeout handling in _http.py are genuinely exercised -- a mock would have
agreed with whatever the code did.
"""

import base64
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer


def make_jwt(claims: dict) -> str:
    """Build an unsigned JWT-shaped string.

    Only the payload segment is ever read (for display), so a fake signature is
    sufficient and keeps the tests free of a crypto dependency.
    """

    def segment(data):
        raw = json.dumps(data).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{segment({'alg': 'none'})}.{segment(claims)}.signature"


class StubServer:
    """Tiny programmable HTTP server.

    routes: {(method, path): handler}
    handler(query: dict, body: dict|str) -> (status, payload)
    """

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.requests = []  # [(method, path, query, body)]
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _handle(self, method):
                parsed = urllib.parse.urlparse(self.path)
                query = {
                    k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()
                }

                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                content_type = self.headers.get("Content-Type", "")
                if "json" in content_type and raw:
                    body = json.loads(raw)
                elif raw:
                    body = {
                        k: v[0]
                        for k, v in urllib.parse.parse_qs(raw.decode()).items()
                    }
                else:
                    body = {}

                stub.requests.append((method, parsed.path, query, body))

                handler = stub.routes.get((method, parsed.path))
                if handler is None:
                    self._write(404, {"error": {"code": "notFound",
                                                "message": f"no stub for {method} {parsed.path}"}})
                    return
                status, payload = handler(query, body)
                self._write(status, payload)

            def do_GET(self):  # noqa: N802
                self._handle("GET")

            def do_POST(self):  # noqa: N802
                self._handle("POST")

            def _write(self, status, payload):
                raw = b"" if payload is None else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                if raw:
                    self.wfile.write(raw)

            def log_message(self, *args):
                return

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def route(self, method, path, handler):
        self.routes[(method, path)] = handler

    def requests_to(self, method, path):
        return [r for r in self.requests if r[0] == method and r[1] == path]

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        return False


def token_response(**overrides):
    """A realistic Microsoft token endpoint success body."""
    payload = {
        "token_type": "Bearer",
        "scope": "openid profile User.Read Mail.Read Mail.Send",
        "expires_in": 3600,
        "access_token": "access-token-1",
        "refresh_token": "refresh-token-1",
        "id_token": make_jwt(
            {
                "name": "Ada Lovelace",
                "preferred_username": "ada@contoso.onmicrosoft.com",
                "oid": "00000000-0000-0000-0000-000000000001",
                "tid": "00000000-0000-0000-0000-0000000000ff",
            }
        ),
    }
    payload.update(overrides)
    return payload
