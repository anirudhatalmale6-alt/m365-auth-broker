# Registering the application in Microsoft Entra ID

Five minutes, done once. You need an account that can register applications in
your tenant — otherwise ask an administrator to do steps 1–4 and send you the
Application (client) ID.

## 1. Create the registration

1. Go to <https://entra.microsoft.com> → **Applications** → **App registrations**
2. **New registration**
3. **Name**: whatever your desktop product is called — the user sees this name
   on the consent screen, so use the real product name
4. **Supported account types**:
   * *Accounts in this organizational directory only* — your company only
   * *Accounts in any organizational directory* — any Microsoft 365 tenant
   * *…and personal Microsoft accounts* — also outlook.com / hotmail.com
5. Leave **Redirect URI** blank for now
6. **Register**

Copy the **Application (client) ID** from the overview page. That is
`M365_CLIENT_ID`. It is not a secret — it ships in your desktop app quite
safely.

## 2. Add the loopback redirect URI

1. **Authentication** → **Add a platform** → **Mobile and desktop applications**
2. Tick the suggested `https://login.microsoftonline.com/common/oauth2/nativeclient`
   box if you like, but the one that matters is the custom one below
3. Under **Custom redirect URIs** add:

   ```
   http://localhost
   ```

   Entra ID treats `http://localhost` specially for desktop apps: **any port is
   accepted**. That is what lets this broker ask the OS for a free port at
   sign-in time instead of hard-coding one.

4. **Configure** → **Save**

> **If your security policy requires an exact redirect URI**, register
> `http://localhost:PORT/callback` with a fixed port instead, and run the
> broker with `--redirect-port PORT` (or `M365_REDIRECT_PORT`). Note the
> trade-off: a pinned port can be occupied by another process, and sign-in then
> fails until it is free.

## 3. Confirm it is a public client

**Authentication** → scroll to **Advanced settings** → **Allow public client
flows**.

This can stay **No** for the authorization code + PKCE flow used here. Only
switch it to *Yes* if you additionally need device-code or
username/password flows — and you do not.

Do **not** create a client secret. A desktop app cannot protect one, and this
broker deliberately never sends one.

## 4. Add the API permissions

**API permissions** → **Add a permission** → **Microsoft Graph** →
**Delegated permissions**, then add:

| Permission | What it allows |
|---|---|
| `offline_access` | Renew access silently, so the user signs in once |
| `openid`, `profile` | Identify the signed-in user |
| `User.Read` | Read the signed-in user's own profile |
| `Mail.Read` | Read the signed-in user's mailbox |
| `Mail.Send` | Send mail **as** the signed-in user |

**Delegated** is the important word. The app only ever acts as the person who
signed in, limited to what that person can already do. It cannot read anyone
else's mailbox, and access disappears when their account is disabled.

### Admin consent

Individual users can normally consent to these themselves. Many organisations
disable that, in which case an administrator clicks **Grant admin consent for
\<tenant\>** once and every user in the tenant is covered.

If you skip this and consent is restricted, sign-in fails with
`AADSTS65001` — that is the signal to go and ask the administrator.

## 5. Test it

```bash
export M365_CLIENT_ID=<the Application (client) ID>
m365-auth login
m365-auth whoami
m365-auth mail list --top 3
m365-auth mail send --to you@yourdomain.com --subject "Broker test" --body "Working."
```

If all four succeed you are done: OAuth works, the scopes were granted, and
mail can be read and sent.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `AADSTS50011` redirect URI mismatch | `http://localhost` is not registered under **Mobile and desktop applications**. Step 2. It must be `localhost`, not `127.0.0.1`, in the portal. |
| `AADSTS65001` consent required | The user or tenant has not consented. Ask an admin for **Grant admin consent**. Step 4. |
| `AADSTS700016` application not found | Wrong `M365_CLIENT_ID`, or the app is registered in a different tenant than `M365_TENANT` points at. |
| `AADSTS7000218` client_assertion required | The registration is marked as a *confidential* client. It must be a public/native client — remove any client secret and re-check step 3. |
| `ErrorAccessDenied` from Graph on mail calls | Signed in fine but `Mail.Read`/`Mail.Send` was not consented. Step 4. |
| Browser opens, nothing happens after sign-in | A firewall is blocking the loopback listener, or the user closed the tab. Try `--redirect-port` with a known-open port. |
| Sign-in works but every renewal fails | `offline_access` was not requested. Check `M365_SCOPES`. |

Every Microsoft error carries an `AADSTS` code and a correlation ID; the broker
passes both straight through in the error message, and that is exactly what
Microsoft support will ask for.
