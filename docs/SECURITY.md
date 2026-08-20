# Security design

What this component protects, how, and what it deliberately does not do.

## Threat model

The asset is a user's Microsoft 365 mailbox access. The realistic attackers:

| Threat | Mitigation |
|---|---|
| **Server breach exposes every user's mailbox** | There is no server. Tokens are issued to, and stay on, the user's own machine. This is the single biggest reason for the loopback design. |
| **Authorization code interception** on the loopback redirect (a hostile local process racing for the port, or reading the redirect) | PKCE (RFC 7636, S256). The code alone is worthless without the verifier, which never leaves the process memory. |
| **Authorization code injection** — attacker feeds their own code to the app | `state` is generated per sign-in and compared in constant time. On mismatch the flow aborts **before** the code is redeemed. |
| **Another device on the network reaching the listener** | Bound to `127.0.0.1` only, never `0.0.0.0`. |
| **A stale listener being hijacked later** | One-shot: it serves a single callback and shuts down. Port is OS-assigned per sign-in. |
| **Token theft from disk** | OS keychain when `keyring` is installed; otherwise a `0600` file written via a private temp file and atomic rename, so no truncated or world-readable window exists. |
| **Tokens leaking into logs** | The loopback access log is silenced (the request line contains the code). `TokenSet.redacted()` is provided for diagnostics. Refresh tokens are withheld from CLI output unless explicitly requested. |
| **Credentials sent in the clear** | The transport layer refuses any non-HTTPS URL except loopback. |
| **XSS via the error page** | `error_description` is attacker-influenced; it is HTML-escaped before rendering. Pages set `no-store`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`. |
| **Supply chain** | Zero runtime dependencies — standard library only. `keyring` is optional. |

## On "secure storage of secrets"

There is **no client secret in this design, and that is correct.**

A desktop application is a *public client*: it is distributed to end users, so
any secret compiled into it is readable by anyone who has a copy. Shipping one
would create a false sense of security. PKCE exists precisely to replace the
secret for this class of application, and it is what Microsoft's own guidance
and RFC 8252 prescribe.

What genuinely needs protecting at rest is the **user's refresh token**. That
is the keychain's job.

If you ever move to a design where a server holds tokens (see below), a client
secret *does* appear, and it belongs in a secret manager — never in the repo,
never in the desktop binary.

## The alternative design, and why it was not chosen

A web service could run the OAuth flow, store tokens in a database, and have
the desktop app fetch them over an authenticated API.

That is a legitimate architecture, and it is the right one if you need
server-side background processing of mail with no user present. But it means:

* the server holds every user's mail credentials, so a breach is a breach of
  *all* mailboxes at once
* you now need to secure, monitor, patch, back up and audit that server
* you need a second authentication system so the desktop app can prove who it
  is when collecting a token
* encryption at rest, key rotation and access logging all become your problem
* it is a much bigger compliance conversation with the customer's IT department

For "desktop app signs a user in and acts on their mail", the loopback design
gives the same capability with a fraction of the attack surface.

## Permission model

All permissions are **delegated**, not application permissions. Consequences,
in the terms an IT administrator will ask about:

* the app acts **only as the signed-in user**, never as a service account
* it can never exceed what that user can already do
* it cannot touch any other mailbox
* access ends when the user's account is disabled or their consent is revoked
* every action appears in the tenant's audit log attributed to that user

`Mail.Send` sends **as** the user — recipients see mail from their address, and
a copy lands in their Sent Items (`saveToSentItems`, on by default).

## Revocation

`m365-auth logout` clears the local session only.

To revoke access entirely:

* the user: <https://myaccount.microsoft.com/consent-and-permissions>
* an administrator: Entra ID → Enterprise applications → the app →
  Permissions → Review permissions

Revocation invalidates the refresh token, so the next silent renewal fails with
`AADSTS50173` and the app falls back to interactive sign-in — which is the
correct behaviour.

## Token lifetimes

* access token — about 1 hour (Microsoft decides, and it varies)
* refresh token — long-lived and **rotated on every use**; the old one is
  retired immediately

The broker renews 120 seconds before expiry by default (`expiry_skew`), so a
long Graph operation cannot start with a token that dies mid-flight.

Because rotation means a renewal response may omit the refresh token,
`TokenSet.from_response` carries the previous one forward. Without that, a
session would silently become non-renewable and the user would be asked to sign
in again for no visible reason.

## Concurrency

`Broker` serialises renewal behind a lock. Two GUI threads hitting an expired
token simultaneously would otherwise fire two refreshes, and the second would
present an already-rotated refresh token and be rejected.

## What is deliberately not implemented

* **`id_token` signature verification.** The id_token is read only to display
  which account signed in, and it arrives directly from Microsoft's token
  endpoint over TLS — not via the browser. It is never used for an
  authorisation decision, and the code says so at the point it is parsed.
  Verifying it would mean pulling in a JWT/JWKS dependency for no security
  gain in this flow. If you later pass the id_token to a backend as proof of
  identity, that backend **must** verify signature, issuer, audience, nonce and
  expiry.
* **The `plain` PKCE method.** Legal in RFC 7636, but useless against redirect
  interception. Only S256 is supported.
* **Device code flow.** Useful for devices with no browser; not needed here and
  it has a well-known phishing profile.
* **Token encryption in the file fallback.** The fallback's protection is file
  permissions. Encrypting with a key stored next to the file would be theatre.
  Install `keyring` for real at-rest protection.
