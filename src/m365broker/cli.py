"""Command line interface -- and the language-neutral integration surface.

A desktop app written in C#, Delphi, C++, Electron or anything else can drive
the whole flow by spawning this as a child process and reading one line of
JSON from stdout::

    m365-auth login --json
    m365-auth token --json

Contract (stable, versioned by the package version):
  * with --json, stdout carries exactly one JSON object and nothing else
  * human-readable chatter always goes to stderr, so stdout stays parseable
  * exit code 0 on success, 1 on a handled error, 2 on bad usage
  * on error the JSON object is {"ok": false, "error": "<code>", "message": ...}
"""

import argparse
import json
import sys

from . import __version__
from .broker import Broker
from .config import BrokerConfig, DEFAULT_SCOPES
from .errors import BrokerError
from .tokens import TokenStore


def _emit(data: dict, as_json: bool, human=None):
    """Write a result to stdout in whichever shape the caller asked for."""
    if as_json:
        json.dump(data, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    elif human is not None:
        print(human)
    sys.stdout.flush()


def _note(message: str):
    """Progress chatter. Never stdout - it would corrupt the JSON contract."""
    print(message, file=sys.stderr, flush=True)


def _build_broker(args) -> Broker:
    overrides = {}
    if args.client_id:
        overrides["client_id"] = args.client_id
    if args.tenant:
        overrides["tenant"] = args.tenant
    if args.scopes:
        overrides["scopes"] = args.scopes.split()
    if args.redirect_port is not None:
        overrides["redirect_port"] = args.redirect_port
    config = BrokerConfig.from_env(**overrides)
    store = TokenStore(
        path=args.token_file,
        account=args.account,
        use_keyring=False if args.no_keyring else None,
    )
    return Broker(config, store)


# -- commands ------------------------------------------------------------


def cmd_login(args, broker: Broker):
    if not args.json:
        _note("Opening your browser to sign in to Microsoft 365...")

    def show_url(url):
        if args.no_browser:
            _note(f"Open this URL to sign in:\n{url}")
        else:
            _note("Waiting for sign-in to complete in the browser...")

    broker.login(
        open_browser=not args.no_browser,
        on_url=show_url,
        force=args.force,
        login_hint=args.login_hint,
    )
    result = broker.export_result(include_refresh_token=args.include_refresh_token)
    account = result["account"].get("username") or "(unknown account)"
    _emit(
        {"ok": True, **result},
        args.json,
        human=f"Signed in as {account}. Access token valid for "
              f"{result['expires_in']}s.",
    )


def cmd_token(args, broker: Broker):
    token = broker.access_token()
    tokens = broker.tokens
    _emit(
        {
            "ok": True,
            "access_token": token,
            "token_type": tokens.token_type,
            "expires_at": tokens.expires_at,
            "expires_in": round(tokens.expires_in),
            "scope": tokens.scope,
        },
        args.json,
        human=token,
    )


def cmd_status(args, broker: Broker):
    tokens = broker.tokens
    if tokens is None:
        _emit({"ok": True, "signed_in": False}, args.json, human="Not signed in.")
        return
    payload = {
        "ok": True,
        "signed_in": True,
        "account": tokens.account,
        "scope": tokens.scope,
        "expires_in": round(tokens.expires_in),
        "expired": tokens.is_expired(broker.config.expiry_skew),
        "can_refresh": bool(tokens.refresh_token),
    }
    human = (
        f"Signed in as {tokens.account.get('username') or 'unknown'}\n"
        f"  expires in : {payload['expires_in']}s\n"
        f"  can refresh: {'yes' if payload['can_refresh'] else 'no'}\n"
        f"  scopes     : {tokens.scope or '(not reported)'}"
    )
    _emit(payload, args.json, human=human)


def cmd_whoami(args, broker: Broker):
    profile = broker.graph().me()
    human = (
        f"{profile.get('displayName')} <"
        f"{profile.get('mail') or profile.get('userPrincipalName')}>"
    )
    _emit({"ok": True, "profile": profile}, args.json, human=human)


def cmd_mail_list(args, broker: Broker):
    messages = broker.graph().list_messages(top=args.top)
    if args.json:
        _emit({"ok": True, "messages": messages}, True)
        return
    if not messages:
        print("Inbox is empty.")
        return
    for item in messages:
        sender = (item.get("from") or {}).get("emailAddress", {}).get("address", "?")
        flag = " " if item.get("isRead") else "*"
        print(f"{flag} {item.get('receivedDateTime', '')[:16]}  {sender:<34.34}  "
              f"{item.get('subject') or '(no subject)'}")


def cmd_mail_send(args, broker: Broker):
    broker.graph().send_mail(
        to=args.to,
        subject=args.subject,
        body=args.body,
        html=args.html,
        cc=args.cc,
    )
    _emit(
        {"ok": True, "sent_to": args.to, "subject": args.subject},
        args.json,
        human=f"Sent to {', '.join(args.to)}.",
    )


def cmd_logout(args, broker: Broker):
    broker.logout()
    _emit(
        {"ok": True, "signed_in": False},
        args.json,
        human="Signed out on this machine. To revoke consent entirely, visit "
              "https://myaccount.microsoft.com/consent-and-permissions",
    )


# -- wiring --------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="m365-auth",
        description="Microsoft 365 OAuth broker for desktop applications.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--client-id", help="Entra ID application (client) ID [env M365_CLIENT_ID]")
    common.add_argument("--tenant", help="common | organizations | consumers | <tenant guid>")
    common.add_argument("--scopes", help=f"space separated. default: {' '.join(DEFAULT_SCOPES)}")
    common.add_argument("--redirect-port", type=int,
                        help="pin the loopback port (default: OS-assigned)")
    common.add_argument("--token-file", help="override where tokens are cached")
    common.add_argument("--account", default="default", help="named slot for multi-account use")
    common.add_argument("--no-keyring", action="store_true",
                        help="force the 0600 file store instead of the OS keychain")
    common.add_argument("--json", action="store_true", help="emit one JSON object on stdout")

    subparsers = parser.add_subparsers(dest="command", required=True)

    login = subparsers.add_parser("login", parents=[common], help="interactive sign-in")
    login.add_argument("--force", action="store_true", help="always show the account picker")
    login.add_argument("--no-browser", action="store_true",
                       help="print the URL instead of opening a browser")
    login.add_argument("--login-hint", help="pre-fill the username")
    login.add_argument("--include-refresh-token", action="store_true",
                       help="include the refresh token in the output (handle with care)")
    login.set_defaults(func=cmd_login)

    token = subparsers.add_parser("token", parents=[common],
                                  help="print a valid access token, refreshing if needed")
    token.set_defaults(func=cmd_token)

    status = subparsers.add_parser("status", parents=[common], help="show local session state")
    status.set_defaults(func=cmd_status)

    whoami = subparsers.add_parser("whoami", parents=[common],
                                   help="fetch the signed-in profile from Graph")
    whoami.set_defaults(func=cmd_whoami)

    mail = subparsers.add_parser("mail", help="mailbox operations")
    mail_sub = mail.add_subparsers(dest="mail_command", required=True)

    mail_list = mail_sub.add_parser("list", parents=[common], help="list recent inbox messages")
    mail_list.add_argument("--top", type=int, default=10)
    mail_list.set_defaults(func=cmd_mail_list)

    mail_send = mail_sub.add_parser("send", parents=[common], help="send a message")
    mail_send.add_argument("--to", required=True, action="append", help="repeatable")
    mail_send.add_argument("--cc", action="append")
    mail_send.add_argument("--subject", required=True)
    mail_send.add_argument("--body", required=True)
    mail_send.add_argument("--html", action="store_true", help="treat --body as HTML")
    mail_send.set_defaults(func=cmd_mail_send)

    logout = subparsers.add_parser("logout", parents=[common], help="clear the local session")
    logout.set_defaults(func=cmd_logout)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        broker = _build_broker(args)
        args.func(args, broker)
        return 0
    except BrokerError as exc:
        payload = {"ok": False, **exc.to_dict()}
        if args.json:
            _emit(payload, True)
        else:
            print(f"error: {exc.message}", file=sys.stderr)
            if exc.detail:
                print(f"       {exc.detail}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ncancelled", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
