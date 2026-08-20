/**
 * Driving the broker from Node.js or Electron.
 *
 * In Electron, run this in the MAIN process, never the renderer -- the token
 * should not be exposed to page context. Pass only what the UI needs (the
 * account name, a signed-in flag) over IPC.
 *
 *   node examples/node_integration.js
 */

'use strict';

const { execFile } = require('node:child_process');

class BrokerError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'BrokerError';
    this.code = code;
  }
}

class M365Broker {
  constructor(clientId, executable = 'm365-auth') {
    this.clientId = clientId;
    this.executable = executable;
  }

  /** Interactive sign-in. Opens the user's browser. */
  login({ force = false } = {}) {
    const args = ['login', '--json'];
    if (force) args.push('--force');
    return this.#run(args, { timeoutMs: 5 * 60_000 }); // humans are slow
  }

  /** A valid access token, refreshed silently when needed. */
  async accessToken() {
    const { access_token: token } = await this.#run(['token', '--json']);
    return token;
  }

  /** Local session state without triggering a sign-in. */
  status() {
    return this.#run(['status', '--json']);
  }

  logout() {
    return this.#run(['logout', '--json']);
  }

  #run(args, { timeoutMs = 60_000 } = {}) {
    const argv = [...args, '--client-id', this.clientId];

    return new Promise((resolve, reject) => {
      execFile(
        this.executable,
        argv,
        { timeout: timeoutMs, maxBuffer: 4 * 1024 * 1024 },
        (error, stdout, stderr) => {
          const raw = stdout.trim();

          // Exit code 1 still produces a JSON error object on stdout, so parse
          // before treating the non-zero exit as fatal.
          if (raw) {
            let payload;
            try {
              payload = JSON.parse(raw);
            } catch {
              return reject(new BrokerError('bad_output', `unparseable output: ${raw}`));
            }
            return payload.ok
              ? resolve(payload)
              : reject(new BrokerError(payload.error, payload.message));
          }

          if (error?.killed) {
            return reject(new BrokerError('timeout', 'the broker did not respond in time'));
          }
          reject(new BrokerError('spawn_failed', stderr.trim() || String(error)));
        },
      );
    });
  }
}

async function main() {
  const broker = new M365Broker(
    process.env.M365_CLIENT_ID || '11111111-2222-3333-4444-555555555555',
  );

  const state = await broker.status();
  if (!state.signed_in) {
    console.log('Opening browser for sign-in...');
    await broker.login();
  }

  const session = await broker.status();
  console.log(`Signed in as ${session.account.username}`);

  // Call Graph directly with the token.
  const token = await broker.accessToken();
  const response = await fetch(
    'https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$top=5',
    { headers: { Authorization: `Bearer ${token}` } },
  );
  const inbox = await response.json();

  for (const message of inbox.value ?? []) {
    console.log(`- ${message.subject}`);
  }
}

if (require.main === module) {
  main().catch((error) => {
    console.error(`${error.code}: ${error.message}`);
    process.exit(1);
  });
}

module.exports = { M365Broker, BrokerError };
