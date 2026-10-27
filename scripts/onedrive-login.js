// One-time Microsoft sign-in (device code flow) for MS_AUTH_MODE=delegated.
// Prints a code, you sign in at microsoft.com/devicelogin, and the refresh
// token is saved to data/state.json (and printed for MS_REFRESH_TOKEN).
import { loadConfig } from '../src/config.js';
import { Store } from '../src/store.js';

const SCOPE = 'offline_access Files.ReadWrite.All User.Read';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const config = loadConfig();
const { clientId, clientSecret, tenant } = config.graph;
if (!clientId) {
  console.error('Set MS_CLIENT_ID in .env first.');
  process.exit(1);
}
const base = `https://login.microsoftonline.com/${encodeURIComponent(tenant)}/oauth2/v2.0`;

const dc = await fetch(`${base}/devicecode`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  body: new URLSearchParams({ client_id: clientId, scope: SCOPE }),
}).then((r) => r.json());

if (!dc.device_code) {
  console.error('Could not start sign-in:', dc.error_description || dc);
  console.error('Check that "Allow public client flows" is enabled on the app registration.');
  process.exit(1);
}
console.log(`\n${dc.message}\n`);

let interval = (dc.interval || 5) * 1000;
const deadline = Date.now() + (dc.expires_in || 900) * 1000;
while (Date.now() < deadline) {
  await sleep(interval);
  const body = new URLSearchParams({
    grant_type: 'urn:ietf:params:oauth:grant-type:device_code',
    client_id: clientId,
    device_code: dc.device_code,
  });
  if (clientSecret) body.set('client_secret', clientSecret);
  const tok = await fetch(`${base}/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  }).then((r) => r.json());

  if (tok.access_token) {
    const me = await fetch('https://graph.microsoft.com/v1.0/me/drive', {
      headers: { Authorization: `Bearer ${tok.access_token}` },
    }).then((r) => r.json());
    const store = new Store(config.dataFile);
    store.refreshToken = tok.refresh_token;
    console.log(`✅ Signed in. OneDrive owner: ${me.owner?.user?.displayName || 'unknown'}`);
    console.log(`Refresh token saved to ${config.dataFile}`);
    console.log(`\nIf you deploy elsewhere, set this in .env:\nMS_REFRESH_TOKEN=${tok.refresh_token}\n`);
    process.exit(0);
  }
  if (tok.error === 'slow_down') interval += 5000;
  else if (tok.error !== 'authorization_pending') {
    console.error('Sign-in failed:', tok.error_description || tok.error);
    process.exit(1);
  }
}
console.error('Sign-in timed out, run the command again.');
process.exit(1);
