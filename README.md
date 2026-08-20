# Microsoft 365 OAuth Broker

Secure Microsoft 365 sign-in for a **desktop application**, with delegated
permission to read and send the signed-in user's mail.

The user clicks "Sign in", their normal browser opens, they authenticate with
Microsoft, and the access token is delivered **straight back to the desktop
machine** — it never passes through, or rests on, a server.

```
  Desktop app                Browser                    Microsoft
      |                         |                            |
      |-- opens auth URL ------>|                            |
      |                         |--- user signs in --------->|
      |                         |<-- redirect w/ code -------|
      |<- 127.0.0.1:<port> -----|                            |
      |                                                      |
      |------ code + PKCE verifier (direct HTTPS) ---------->|
      |<----- access + refresh token ------------------------|
```

---

## Why it is built this way

A desktop application cannot keep a secret — ship it and every copy contains
the same one. So this uses the pattern Microsoft and RFC 8252 prescribe for
native apps:

| Decision | Reason |
|---|---|
| **Authorization Code + PKCE**, no client secret | A public client has no secret to protect. PKCE binds the code to the process that started the flow, so an intercepted redirect is useless to an attacker. |
| **Loopback redirect** (`http://127.0.0.1:<port>/callback`) | The token lands on the user's own machine. No server holds anyone's mailbox credentials, so there is no server to breach. |
| **OS-assigned port**, one-shot listener | Nothing long-lived to squat on, and it stops the moment sign-in completes. |
| **`offline_access` scope** | Yields a refresh token, so the user signs in once and the app renews silently. |
| **Refresh token rotation handled** | Microsoft retires the old refresh token on each use. Renewal keeps the new one and never downgrades the session. |
| **OS keychain for storage**, 0600 file fallback | Tokens at rest are protected by the user's login, not just by hope. |
| **Zero runtime dependencies** | This process handles mail tokens; the supply chain is kept to the standard library. |

The "secure storage of secrets" line in the original brief is worth being
precise about: **there is no client secret in this design, by design.** That is
the correct and more secure answer for a desktop client. What *is* stored is
the user's refresh token, and that is what the keychain protects.

---

## Quick start

```bash
pip install -e ".[dev]"            # add [keyring] for OS keychain storage
export M365_CLIENT_ID=<your app id>

m365-auth login                    # browser opens, sign in
m365-auth whoami                   # proves Mail/User scopes work
m365-auth mail list --top 5        # proves Mail.Read
m365-auth mail send --to you@example.com --subject Test --body "It works"
```

Register the app first — five minutes, walked through in
**[docs/AZURE_SETUP.md](docs/AZURE_SETUP.md)**.

---

## Using it from your desktop application

### Any language — spawn the CLI, read one line of JSON

This is the integration path that needs no Python knowledge on your side.

```bash
m365-auth login --json
```

```json
{
  "ok": true,
  "access_token": "eyJ0eXAiOi...",
  "token_type": "Bearer",
  "expires_at": 1755689421.7,
  "expires_in": 3599,
  "scope": "openid profile User.Read Mail.Read Mail.Send",
  "account": {
    "name": "Ada Lovelace",
    "username": "ada@contoso.onmicrosoft.com",
    "oid": "...", "tid": "..."
  }
}
```

**Contract**

* With `--json`, stdout carries **exactly one JSON object and nothing else**.
  Progress messages always go to stderr.
* Exit code `0` success, `1` handled error, `2` bad usage.
* On error: `{"ok": false, "error": "<code>", "message": "...", "detail": ...}`
  where `<code>` is one of `config_error`, `authorization_error`,
  `state_mismatch`, `callback_timeout`, `token_error`, `token_expired`,
  `graph_error`.
* `m365-auth token --json` returns a **currently valid** token, refreshing
  silently if needed. Call it before each batch of Graph work and you never
  have to think about expiry.

Worked examples: [examples/csharp_integration.cs](examples/csharp_integration.cs),
[examples/node_integration.js](examples/node_integration.js).

### From Python — import it

```python
from m365broker import Broker, BrokerConfig

broker = Broker(BrokerConfig(client_id="<your app id>"))
broker.login()                       # opens the browser, once

broker.graph().send_mail(
    to="someone@example.com",
    subject="Sent from the desktop app",
    body="Hello",
)

for message in broker.graph().list_messages(top=10):
    print(message["subject"])
```

`broker.access_token()` always returns a valid token, renewing behind the
scenes. See [examples/python_integration.py](examples/python_integration.py).

---

## Commands

| Command | Purpose |
|---|---|
| `login` | Interactive sign-in. `--force` shows the account picker, `--no-browser` prints the URL instead of opening one. |
| `token` | Print a valid access token, refreshing if required. |
| `status` | Local session state — account, scopes, seconds to expiry, renewable or not. |
| `whoami` | Fetch the signed-in profile from Graph. |
| `mail list` | Recent inbox messages (`--top N`). |
| `mail send` | Send a message (`--to` repeatable, `--cc`, `--html`). |
| `logout` | Clear the local session. |

Shared flags: `--client-id`, `--tenant`, `--scopes`, `--redirect-port`,
`--token-file`, `--account`, `--no-keyring`, `--json`.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `M365_CLIENT_ID` | *(required)* | Application (client) ID from Entra ID. |
| `M365_TENANT` | `common` | `common`, `organizations`, `consumers`, or a tenant GUID to lock sign-in to one organisation. |
| `M365_SCOPES` | see below | Space separated. |
| `M365_REDIRECT_PORT` | `0` (OS-assigned) | Pin only if your Entra app registration requires a fixed redirect URI. |
| `M365_AUTHORITY` | `https://login.microsoftonline.com` | Change for sovereign clouds. |
| `M365_GRAPH_BASE_URL` | `https://graph.microsoft.com/v1.0` | Change for sovereign clouds. |

Default scopes: `offline_access openid profile User.Read Mail.Read Mail.Send`

None of these are secrets, so a plain `.env` file is fine. See `.env.example`.

## Multiple accounts

```bash
m365-auth login  --account work     --json
m365-auth token  --account personal --json
```

Each `--account` is an independent slot in the keychain or token file.

---

## Tests

The suite runs offline, on a fresh clone, with **no Azure tenant and no
network** — a stub Microsoft is started on loopback and the whole flow is
driven end to end against it, including a fake browser that performs the real
redirect.

```bash
pip install -e ".[dev]"
pytest
```

123 tests, 96% statement coverage. What they actually assert, beyond the happy
path:

* the PKCE challenge matches the **RFC 7636 Appendix B** worked vector
* the redeemed verifier really hashes to the challenge that was sent
* a **mismatched `state` aborts the flow and the code is never redeemed**
  (authorization-code injection)
* no `client_secret` is ever posted
* refresh-token rotation preserves the token when Microsoft omits it
* the success page never contains the authorization code
* `error_description` is HTML-escaped before rendering
* the token file is `0600` and no `.tmp` remnant is left behind
* credentials are refused over non-loopback plain HTTP
* the CLI's stdout stays parseable — one JSON object, chatter on stderr

```bash
pytest --cov=m365broker --cov-report=term-missing
```

## CI

`.github/workflows/ci.yml` runs the suite on Python 3.9–3.12 on every push and
pull request.

---

## Security notes

* **Tokens never touch a server.** The only party that receives them is the
  desktop machine that asked for them.
* **Refresh tokens are withheld** from `login --json` unless
  `--include-refresh-token` is passed — a refresh token in a log file is a
  long-lived key to a mailbox.
* **The loopback listener binds `127.0.0.1` only** and rejects any path but the
  configured callback.
* **The HTTP access log is silenced** — the request line contains the
  authorization code.
* **`redacted()`** gives a safe-to-log view of a token set for diagnostics.
* `logout` clears the session on that machine. To revoke consent entirely the
  user visits <https://myaccount.microsoft.com/consent-and-permissions>, or an
  admin removes it in the Entra portal.

Full detail, including what an administrator will ask you about the
permissions: **[docs/SECURITY.md](docs/SECURITY.md)**.

## Requirements

Python 3.9+. No runtime dependencies. `keyring` optional, for OS keychain
storage. Windows, macOS and Linux.

## Licence

MIT — see [LICENSE](LICENSE).
