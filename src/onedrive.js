import { UserError, joinPath, splitPath } from './paths.js';

const GRAPH = 'https://graph.microsoft.com/v1.0';
const SIMPLE_UPLOAD_MAX = 4 * 1024 * 1024;
const CHUNK = 320 * 1024 * 12; // must be a multiple of 320 KiB
const DELEGATED_SCOPE = 'offline_access Files.ReadWrite';

export class NotConnectedError extends Error {
  constructor(message = 'OneDrive is not connected') {
    super(message);
  }
}

export class GraphError extends Error {
  constructor(status, code, message) {
    super(message || `Graph error ${status}`);
    this.status = status;
    this.code = code;
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const encodePath = (p) => splitPath(p).map(encodeURIComponent).join('/');

/**
 * Minimal Microsoft Graph client for one OneDrive, scoped to a base folder.
 * All `rel` paths are relative to that base folder.
 */
export class OneDrive {
  constructor({ graph, storage }, store, { fetchImpl = fetch, log = console } = {}) {
    this.cfg = graph;
    this.base = storage.baseFolder;
    this.store = store;
    this.fetch = fetchImpl;
    this.log = log;
    this.token = null;
    this.tokenExp = 0;
    this.tokenPromise = null;

    if (graph.driveId) this.drive = `/drives/${encodeURIComponent(graph.driveId)}`;
    else if (graph.authMode === 'app') this.drive = `/users/${encodeURIComponent(graph.driveUser)}/drive`;
    else this.drive = '/me/drive';
  }

  // ---------- auth ----------

  isConnected() {
    return this.cfg.authMode === 'app' || !!(this.store.refreshToken || this.cfg.refreshToken);
  }

  async getToken() {
    if (this.token && Date.now() < this.tokenExp - 60_000) return this.token;
    this.tokenPromise ??= this.#fetchToken().finally(() => (this.tokenPromise = null));
    return this.tokenPromise;
  }

  get #tokenUrl() {
    return `https://login.microsoftonline.com/${encodeURIComponent(this.cfg.tenant)}/oauth2/v2.0`;
  }

  async #fetchToken() {
    const { clientId, clientSecret, authMode } = this.cfg;
    const body = new URLSearchParams({ client_id: clientId });
    if (authMode === 'app') {
      body.set('grant_type', 'client_credentials');
      body.set('client_secret', clientSecret);
      body.set('scope', 'https://graph.microsoft.com/.default');
    } else {
      const refresh = this.store.refreshToken || this.cfg.refreshToken;
      if (!refresh) throw new NotConnectedError();
      body.set('grant_type', 'refresh_token');
      body.set('refresh_token', refresh);
      body.set('scope', DELEGATED_SCOPE);
      if (clientSecret) body.set('client_secret', clientSecret);
    }
    const res = await this.fetch(`${this.#tokenUrl}/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.access_token) {
      if (data.error === 'invalid_grant' && authMode !== 'app') {
        // Refresh token expired or revoked: an admin has to /connect again.
        this.store.refreshToken = null;
        this.cfg.refreshToken = '';
        throw new NotConnectedError('OneDrive sign-in expired');
      }
      throw new Error(`Microsoft sign-in failed: ${data.error_description || data.error || res.status}`);
    }
    // Refresh tokens rotate; keep the newest one so the bot survives restarts.
    if (data.refresh_token && data.refresh_token !== this.store.refreshToken) {
      this.store.refreshToken = data.refresh_token;
    }
    this.token = data.access_token;
    this.tokenExp = Date.now() + (data.expires_in || 3600) * 1000;
    return this.token;
  }

  /**
   * Start a device-code sign-in. Returns the code to show the admin and a
   * promise that resolves with the account name once they have signed in on
   * any device at the verification URL.
   */
  async startDeviceLogin() {
    if (this.cfg.authMode === 'app') throw new UserError('This bot uses app-only access; no sign-in needed.');
    const res = await this.fetch(`${this.#tokenUrl}/devicecode`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ client_id: this.cfg.clientId, scope: `${DELEGATED_SCOPE} User.Read` }),
    });
    const dc = await res.json().catch(() => ({}));
    if (!dc.device_code) {
      throw new Error(`Could not start Microsoft sign-in: ${dc.error_description || dc.error || res.status}`);
    }

    const done = (async () => {
      let interval = (dc.interval || 5) * 1000;
      const deadline = Date.now() + (dc.expires_in || 900) * 1000;
      while (Date.now() < deadline) {
        await sleep(interval);
        const body = new URLSearchParams({
          grant_type: 'urn:ietf:params:oauth:grant-type:device_code',
          client_id: this.cfg.clientId,
          device_code: dc.device_code,
        });
        if (this.cfg.clientSecret) body.set('client_secret', this.cfg.clientSecret);
        const tok = await this.fetch(`${this.#tokenUrl}/token`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body,
        }).then((r) => r.json().catch(() => ({})));
        if (tok.access_token) {
          this.store.refreshToken = tok.refresh_token;
          this.token = tok.access_token;
          this.tokenExp = Date.now() + (tok.expires_in || 3600) * 1000;
          return this.account();
        }
        if (tok.error === 'slow_down') interval += 5000;
        else if (tok.error !== 'authorization_pending') {
          throw new Error(tok.error_description || tok.error || 'sign-in failed');
        }
      }
      throw new Error('The sign-in code expired before it was used.');
    })();

    return {
      code: dc.user_code,
      url: dc.verification_uri || 'https://microsoft.com/devicelogin',
      expiresInMin: Math.round((dc.expires_in || 900) / 60),
      done,
    };
  }

  /** Who owns the connected drive, plus quota. */
  async account() {
    const d = await this.request('GET', `${this.drive}?$select=owner,quota,driveType`);
    return {
      name: d.owner?.user?.displayName || d.owner?.user?.email || 'unknown',
      email: d.owner?.user?.email,
      used: d.quota?.used,
      total: d.quota?.total,
      driveType: d.driveType,
    };
  }

  // ---------- low level ----------

  async request(method, url, { json, body, headers = {}, raw = false, attempt = 0 } = {}) {
    const full = url.startsWith('http') ? url : GRAPH + url;
    const token = await this.getToken();
    const init = { method, headers: { Authorization: `Bearer ${token}`, ...headers } };
    if (json !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(json);
    } else if (body !== undefined) init.body = body;

    const res = await this.fetch(full, init);
    if (res.status === 401 && attempt === 0) {
      this.token = null;
      return this.request(method, url, { json, body, headers, raw, attempt: 1 });
    }
    if ((res.status === 429 || res.status === 503 || res.status === 504) && attempt < 3) {
      const wait = Number(res.headers.get('retry-after')) || 2 ** attempt;
      await sleep(Math.min(wait, 30) * 1000);
      return this.request(method, url, { json, body, headers, raw, attempt: attempt + 1 });
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new GraphError(res.status, err.error?.code, err.error?.message);
    }
    if (raw) return res;
    if (res.status === 204) return null;
    const text = await res.text();
    return text ? JSON.parse(text) : null;
  }

  /** Graph address of a path relative to the base folder. */
  addr(rel) {
    const full = joinPath(this.base, rel);
    return full ? `${this.drive}/root:/${encodePath(full)}:` : `${this.drive}/root`;
  }

  /** Convert a Graph driveItem into the shape the bot uses. */
  entry(item, fallbackRel) {
    let rel = fallbackRel ?? null;
    const p = item.parentReference?.path;
    if (p && p.includes('root:')) {
      let parent = p.slice(p.indexOf('root:') + 5);
      try {
        parent = decodeURIComponent(parent);
      } catch {}
      const relParent = this.relFromDrive(parent);
      if (relParent !== null) rel = joinPath(relParent, item.name);
    }
    return {
      id: item.id,
      name: item.name,
      isFolder: !!item.folder,
      size: item.size ?? 0,
      childCount: item.folder?.childCount ?? 0,
      mimeType: item.file?.mimeType,
      modified: item.lastModifiedDateTime,
      webUrl: item.webUrl,
      downloadUrl: item['@microsoft.graph.downloadUrl'],
      path: rel,
    };
  }

  /** Drive-root path -> path relative to base, or null when outside the base. */
  relFromDrive(drivePath) {
    const parts = splitPath(drivePath);
    const base = splitPath(this.base);
    if (parts.length < base.length) return null;
    for (let i = 0; i < base.length; i++) {
      if (parts[i].toLowerCase() !== base[i].toLowerCase()) return null;
    }
    return parts.slice(base.length).join('/');
  }

  // ---------- operations ----------

  async getItem(rel) {
    try {
      return this.entry(await this.request('GET', this.addr(rel)), rel);
    } catch (e) {
      if (e.status === 404) return null;
      throw e;
    }
  }

  async getById(id) {
    return this.entry(await this.request('GET', `${this.drive}/items/${encodeURIComponent(id)}`));
  }

  async list(rel) {
    const out = [];
    let url = `${this.addr(rel)}/children?$top=200`;
    while (url) {
      const page = await this.request('GET', url);
      for (const it of page.value || []) out.push(this.entry(it, joinPath(rel, it.name)));
      url = page['@odata.nextLink'] || null;
    }
    return out.sort((a, b) => b.isFolder - a.isFolder || a.name.localeCompare(b.name, undefined, { numeric: true }));
  }

  /** Create every missing folder in `rel` (like mkdir -p). */
  async ensureFolder(rel) {
    let current = '';
    let item = await this.getItemOrCreateBase();
    for (const seg of splitPath(rel)) {
      const next = joinPath(current, seg);
      let found = await this.getItem(next);
      if (!found) {
        try {
          found = this.entry(
            await this.request('POST', `${this.addr(current)}/children`, {
              json: { name: seg, folder: {}, '@microsoft.graph.conflictBehavior': 'fail' },
            }),
            next,
          );
        } catch (e) {
          if (e.status !== 409) throw e;
          found = await this.getItem(next); // created concurrently
        }
      }
      if (!found.isFolder) throw new UserError(`"/${next}" is a file, not a folder.`);
      current = next;
      item = found;
    }
    return item;
  }

  async getItemOrCreateBase() {
    const root = await this.getItem('');
    if (root) return root;
    // Base folder missing: create it step by step from the drive root.
    let parent = '';
    for (const seg of splitPath(this.base)) {
      const url = parent ? `${this.drive}/root:/${encodePath(parent)}:/children` : `${this.drive}/root/children`;
      try {
        await this.request('POST', url, { json: { name: seg, folder: {}, '@microsoft.graph.conflictBehavior': 'fail' } });
      } catch (e) {
        if (e.status !== 409) throw e;
      }
      parent = joinPath(parent, seg);
    }
    return this.getItem('');
  }

  /** Upload a buffer into folder `rel`. Existing names get auto-renamed ("file 1.pdf"). */
  async upload(rel, name, buffer, mimeType = 'application/octet-stream') {
    await this.ensureFolder(rel);
    const target = this.addr(joinPath(rel, name));
    if (buffer.length <= SIMPLE_UPLOAD_MAX) {
      const item = await this.request('PUT', `${target}/content?@microsoft.graph.conflictBehavior=rename`, {
        body: buffer,
        headers: { 'Content-Type': mimeType },
      });
      return this.entry(item, joinPath(rel, item.name));
    }

    const session = await this.request('POST', `${target}/createUploadSession`, {
      json: { item: { '@microsoft.graph.conflictBehavior': 'rename' } },
    });
    let item = null;
    for (let start = 0; start < buffer.length; start += CHUNK) {
      const end = Math.min(start + CHUNK, buffer.length);
      // Upload URLs are pre-authenticated: no Authorization header allowed.
      const res = await this.fetch(session.uploadUrl, {
        method: 'PUT',
        headers: {
          'Content-Length': String(end - start),
          'Content-Range': `bytes ${start}-${end - 1}/${buffer.length}`,
        },
        body: buffer.subarray(start, end),
      });
      if (!res.ok) {
        await this.fetch(session.uploadUrl, { method: 'DELETE' }).catch(() => {});
        throw new GraphError(res.status, 'uploadFailed', `Upload failed at byte ${start}`);
      }
      if (res.status === 200 || res.status === 201) item = await res.json();
    }
    return this.entry(item, joinPath(rel, item.name));
  }

  async rename(id, newName) {
    try {
      const item = await this.request('PATCH', `${this.drive}/items/${encodeURIComponent(id)}`, {
        json: { name: newName, '@microsoft.graph.conflictBehavior': 'fail' },
      });
      return this.entry(item);
    } catch (e) {
      if (e.status === 409) throw new UserError(`An item named "${newName}" already exists there.`);
      throw e;
    }
  }

  async move(id, destFolderRel) {
    const dest = await this.ensureFolder(destFolderRel);
    try {
      const item = await this.request('PATCH', `${this.drive}/items/${encodeURIComponent(id)}`, {
        json: { parentReference: { id: dest.id }, '@microsoft.graph.conflictBehavior': 'fail' },
      });
      return this.entry(item);
    } catch (e) {
      if (e.status === 409) throw new UserError('An item with the same name already exists in that folder.');
      throw e;
    }
  }

  /** Deleted items go to the OneDrive recycle bin and can be restored there. */
  async remove(id) {
    await this.request('DELETE', `${this.drive}/items/${encodeURIComponent(id)}`);
  }

  async search(query) {
    const q = encodeURIComponent(String(query).replace(/'/g, "''"));
    const out = [];
    let url = `${this.addr('')}/search(q='${q}')?$top=50`;
    let pages = 0;
    while (url && pages++ < 4) {
      const page = await this.request('GET', url);
      for (const it of page.value || []) {
        const e = this.entry(it);
        if (e.path) out.push(e); // drop hits outside the base folder (or the base itself)
      }
      url = page['@odata.nextLink'] || null;
    }
    return out;
  }

  /** Short-lived pre-authenticated download URL (about 1 hour). */
  async downloadUrl(id) {
    const item = await this.getById(id);
    if (!item.downloadUrl) throw new UserError('OneDrive did not provide a download link for this file.');
    return item.downloadUrl;
  }

  async shareLink(id) {
    const url = `${this.drive}/items/${encodeURIComponent(id)}/createLink`;
    for (const scope of ['anonymous', 'organization']) {
      try {
        const res = await this.request('POST', url, { json: { type: 'view', scope } });
        return res.link.webUrl;
      } catch (e) {
        if (scope === 'organization' || ![400, 403].includes(e.status)) throw e;
      }
    }
  }
}
