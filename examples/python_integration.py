"""Using the broker as a library from a Python desktop app (Tk, Qt, wx...).

    python examples/python_integration.py

Set M365_CLIENT_ID first - see docs/AZURE_SETUP.md.
"""

import os
import sys

from m365broker import Broker, BrokerConfig, BrokerError


def main() -> int:
    client_id = os.environ.get("M365_CLIENT_ID")
    if not client_id:
        print("set M365_CLIENT_ID first (see docs/AZURE_SETUP.md)", file=sys.stderr)
        return 2

    broker = Broker(BrokerConfig(client_id=client_id))

    # -- sign in ---------------------------------------------------------
    # login() reuses a cached session, so it is safe to call on every start.
    # In a GUI, run it off the UI thread: it blocks until the browser round
    # trip finishes, and on_url lets you show a "waiting for sign-in" state.
    if not broker.is_signed_in():
        print("Opening your browser...")

    broker.login(on_url=lambda url: print("If nothing opened, visit:\n" + url))
    print(f"Signed in as {broker.account().get('username')}")

    graph = broker.graph()

    # -- prove Mail.Read -------------------------------------------------
    messages = graph.list_messages(top=5)
    if messages:
        print(f"\nLast {len(messages)} message(s):")
        for message in messages:
            sender = (message.get("from") or {}).get("emailAddress", {}).get("address", "?")
            print(f"  {message.get('receivedDateTime', '')[:16]}  {sender}")
            print(f"      {message.get('subject') or '(no subject)'}")
    else:
        print("\nInbox is empty.")

    # -- prove Mail.Send -------------------------------------------------
    # Sends to the signed-in user, so running the example cannot spam anyone.
    own_address = broker.account().get("username")
    if own_address:
        graph.send_mail(
            to=own_address,
            subject="Microsoft 365 broker test",
            body="If you are reading this, OAuth and Mail.Send both work.",
        )
        print(f"\nTest message sent to {own_address}.")

    # -- token handling --------------------------------------------------
    # access_token() renews silently. Never cache the string yourself.
    token = broker.access_token()
    print(f"\nAccess token in hand ({len(token)} chars), "
          f"valid for {round(broker.tokens.expires_in)}s.")

    # For diagnostics, log this - never the token set itself.
    print(f"Safe-to-log view: {broker.tokens.redacted()}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokerError as exc:
        # Every broker failure carries a stable code you can branch on.
        print(f"\n{exc.code}: {exc.message}", file=sys.stderr)
        if exc.detail:
            print(f"  detail: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(1)
