"""Tests for the subprocess integration contract.

This is the surface the client's desktop application actually consumes, so the
promises are asserted literally: one JSON object on stdout, chatter on stderr,
documented exit codes.
"""

import json

import pytest

from m365broker import cli, oauth

from .support import token_response
from .test_oauth import fake_browser

TOKEN_PATH = "/common/oauth2/v2.0/token"


@pytest.fixture
def env(monkeypatch, stub, tmp_path):
    """Point the CLI at the stub and at a throwaway token file."""
    monkeypatch.setenv("M365_CLIENT_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("M365_AUTHORITY", stub.base_url)
    monkeypatch.setenv("M365_GRAPH_BASE_URL", f"{stub.base_url}/graph/v1.0")
    monkeypatch.setattr(oauth.webbrowser, "open", fake_browser())
    return ["--no-keyring", "--token-file", str(tmp_path / "tokens.json")]


def run(argv, capsys):
    """Run the CLI, returning (exit_code, stdout, stderr)."""
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def run_json(argv, capsys):
    code, out, err = run(argv, capsys)
    # The contract is exactly one JSON object and nothing else on stdout.
    return code, json.loads(out), err


# -- login ---------------------------------------------------------------


def test_login_json_contract(env, capsys):
    code, payload, err = run_json(["login", "--json", *env], capsys)

    assert code == 0
    assert payload["ok"] is True
    assert payload["access_token"] == "access-token-1"
    assert payload["token_type"] == "Bearer"
    assert payload["expires_in"] > 0
    assert payload["account"]["username"] == "ada@contoso.onmicrosoft.com"


def test_login_json_withholds_the_refresh_token(env, capsys):
    _, payload, _ = run_json(["login", "--json", *env], capsys)
    assert "refresh_token" not in payload


def test_login_can_include_the_refresh_token_on_request(env, capsys):
    _, payload, _ = run_json(
        ["login", "--json", "--include-refresh-token", *env], capsys
    )
    assert payload["refresh_token"] == "refresh-token-1"


def test_progress_chatter_never_pollutes_stdout(env, capsys):
    """A stray print on stdout would break every JSON consumer."""
    code, out, err = run(["login", "--json", *env], capsys)

    assert code == 0
    json.loads(out)  # parses cleanly, so nothing else was written
    assert out.count("\n") == 1


def test_human_output_is_readable(env, capsys):
    code, out, _ = run(["login", *env], capsys)

    assert code == 0
    assert "Signed in as ada@contoso.onmicrosoft.com" in out


# -- session commands ----------------------------------------------------


def test_status_before_and_after_login(env, capsys):
    _, payload, _ = run_json(["status", "--json", *env], capsys)
    assert payload["signed_in"] is False

    run(["login", "--json", *env], capsys)

    _, payload, _ = run_json(["status", "--json", *env], capsys)
    assert payload["signed_in"] is True
    assert payload["can_refresh"] is True
    assert "Mail.Send" in payload["scope"]


def test_token_command_prints_a_bare_token_in_human_mode(env, capsys):
    run(["login", *env], capsys)

    code, out, _ = run(["token", *env], capsys)

    assert code == 0
    assert out.strip() == "access-token-1"


def test_token_command_refreshes_a_stale_session(env, capsys, stub):
    run(["login", "--json", *env], capsys)
    # Return an already-stale token from login, then a fresh one on refresh.
    stub.route("POST", TOKEN_PATH,
               lambda q, b: (200, token_response(access_token="access-token-2")))

    # Force staleness by rewriting the cached expiry.
    import json as _json
    from pathlib import Path

    token_file = Path(env[env.index("--token-file") + 1])
    data = _json.loads(token_file.read_text())
    data["expires_at"] = 0
    token_file.write_text(_json.dumps(data))

    _, payload, _ = run_json(["token", "--json", *env], capsys)

    assert payload["access_token"] == "access-token-2"


def test_logout_clears_the_session(env, capsys):
    run(["login", *env], capsys)
    code, payload, _ = run_json(["logout", "--json", *env], capsys)

    assert code == 0
    assert payload["signed_in"] is False

    _, payload, _ = run_json(["status", "--json", *env], capsys)
    assert payload["signed_in"] is False


# -- graph commands ------------------------------------------------------


def test_whoami_reads_the_profile(env, capsys, stub):
    stub.route("GET", "/graph/v1.0/me", lambda q, b: (200, {
        "displayName": "Ada Lovelace", "mail": "ada@contoso.onmicrosoft.com"}))
    run(["login", *env], capsys)

    code, payload, _ = run_json(["whoami", "--json", *env], capsys)

    assert code == 0
    assert payload["profile"]["displayName"] == "Ada Lovelace"


def test_mail_list_human_output(env, capsys, stub):
    stub.route("GET", "/graph/v1.0/me/mailFolders/inbox/messages", lambda q, b: (200, {
        "value": [{"id": "1", "subject": "Quarterly report", "isRead": False,
                   "receivedDateTime": "2026-08-20T09:15:00Z",
                   "from": {"emailAddress": {"address": "boss@contoso.com"}}}]}))
    run(["login", *env], capsys)

    code, out, _ = run(["mail", "list", "--top", "1", *env], capsys)

    assert code == 0
    assert "Quarterly report" in out
    assert "boss@contoso.com" in out


def test_mail_send_reports_success(env, capsys, stub):
    stub.route("POST", "/graph/v1.0/me/sendMail", lambda q, b: (202, None))
    run(["login", *env], capsys)

    code, payload, _ = run_json(
        ["mail", "send", "--to", "a@example.com", "--subject", "Hi",
         "--body", "Hello", "--json", *env], capsys)

    assert code == 0
    assert payload["ok"] is True
    _, _, _, body = stub.requests_to("POST", "/graph/v1.0/me/sendMail")[0]
    assert body["message"]["subject"] == "Hi"


def test_mail_send_accepts_repeated_recipients(env, capsys, stub):
    stub.route("POST", "/graph/v1.0/me/sendMail", lambda q, b: (202, None))
    run(["login", *env], capsys)

    run(["mail", "send", "--to", "a@example.com", "--to", "b@example.com",
         "--subject", "Hi", "--body", "Hello", *env], capsys)

    _, _, _, body = stub.requests_to("POST", "/graph/v1.0/me/sendMail")[0]
    assert len(body["message"]["toRecipients"]) == 2


# -- error contract ------------------------------------------------------


def test_errors_are_json_when_json_was_requested(env, capsys):
    """No session yet, so `token` must fail in the documented shape."""
    code, payload, _ = run_json(["token", "--json", *env], capsys)

    assert code == 1
    assert payload["ok"] is False
    assert payload["error"] == "token_expired"
    assert payload["message"]


def test_errors_go_to_stderr_in_human_mode(env, capsys):
    code, out, err = run(["token", *env], capsys)

    assert code == 1
    assert out == ""
    assert "error:" in err


def test_graph_errors_carry_the_status(env, capsys, stub):
    stub.route("GET", "/graph/v1.0/me", lambda q, b: (403, {
        "error": {"code": "ErrorAccessDenied", "message": "Access is denied."}}))
    run(["login", *env], capsys)

    code, payload, _ = run_json(["whoami", "--json", *env], capsys)

    assert code == 1
    assert payload["error"] == "graph_error"
    assert payload["status"] == 403


def test_missing_client_id_is_a_config_error(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("M365_CLIENT_ID", raising=False)

    code, payload, _ = run_json(
        ["status", "--json", "--no-keyring",
         "--token-file", str(tmp_path / "t.json")], capsys)

    assert code == 1
    assert payload["error"] == "config_error"


# -- flag handling -------------------------------------------------------


def test_command_line_flags_override_the_environment(monkeypatch, stub, tmp_path, capsys):
    monkeypatch.setenv("M365_CLIENT_ID", "from-env")
    monkeypatch.setenv("M365_AUTHORITY", stub.base_url)
    stub.route("POST", "/organizations/oauth2/v2.0/token",
               lambda q, b: (200, token_response()))
    monkeypatch.setattr(oauth.webbrowser, "open", fake_browser())

    code, payload, _ = run_json([
        "login", "--json", "--no-keyring",
        "--token-file", str(tmp_path / "t.json"),
        "--client-id", "from-flag",
        "--tenant", "organizations",
        "--scopes", "Mail.Send offline_access",
        "--redirect-port", "0",
    ], capsys)

    assert code == 0
    assert payload["ok"] is True
    # The tenant flag changed which endpoint was called...
    _, _, _, body = stub.requests_to("POST", "/organizations/oauth2/v2.0/token")[0]
    # ...and the client-id flag beat the environment variable.
    assert body["client_id"] == "from-flag"
    assert body["scope"] == "Mail.Send offline_access"


def test_no_browser_prints_the_url_for_manual_use(env, capsys, monkeypatch):
    """Headless and locked-down desktops need the URL, not a browser call."""
    opened = []
    monkeypatch.setattr(oauth.webbrowser, "open", lambda url: opened.append(url))

    # Drive the redirect from the printed URL, exactly as a human would.
    def on_url_side_effect(url):
        fake_browser()(url)

    monkeypatch.setattr(cli, "_note", lambda message: on_url_side_effect(
        message.split("\n")[-1]) if message.startswith("Open this URL") else None)

    code, _, _ = run(["login", "--no-browser", *env], capsys)

    assert code == 0
    assert opened == []  # no browser was launched


def test_bad_usage_exits_two(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["nonsense-command"])
    assert excinfo.value.code == 2
