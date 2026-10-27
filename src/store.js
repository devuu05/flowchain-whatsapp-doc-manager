import fs from 'node:fs';
import path from 'node:path';

/**
 * Tiny JSON-file store for per-user state (current folder, last listing,
 * pending confirmations) and the rotating Microsoft refresh token.
 * Pass file = null for an in-memory store (tests).
 */
export class Store {
  constructor(file) {
    this.file = file;
    this.data = { users: {}, msRefreshToken: null };
    this.timer = null;
    if (file && fs.existsSync(file)) {
      try {
        this.data = { ...this.data, ...JSON.parse(fs.readFileSync(file, 'utf8')) };
      } catch (err) {
        console.error(`[store] could not parse ${file}, starting fresh:`, err.message);
      }
    }
  }

  user(number) {
    if (!this.data.users[number]) this.data.users[number] = { cwd: null, last: [], pending: null };
    return this.data.users[number];
  }

  get refreshToken() {
    return this.data.msRefreshToken;
  }

  set refreshToken(token) {
    this.data.msRefreshToken = token;
    this.save(true);
  }

  /** Debounced atomic write; immediate = flush now. */
  save(immediate = false) {
    if (!this.file) return;
    clearTimeout(this.timer);
    const write = () => {
      fs.mkdirSync(path.dirname(this.file), { recursive: true });
      const tmp = `${this.file}.tmp`;
      fs.writeFileSync(tmp, JSON.stringify(this.data, null, 2), { mode: 0o600 });
      fs.renameSync(tmp, this.file);
    };
    if (immediate) write();
    else this.timer = setTimeout(write, 300);
  }
}
