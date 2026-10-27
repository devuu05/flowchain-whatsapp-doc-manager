// Paths used by the bot are always relative to ONEDRIVE_BASE_FOLDER,
// written without leading/trailing slashes ("" = the base folder itself).

export class UserError extends Error {}

const ILLEGAL = /["*:<>?\\|\u0000-\u001f]/g;

/** Make a single file/folder name safe for OneDrive. */
export function sanitizeName(name) {
  const clean = String(name || '')
    .replace(ILLEGAL, '_')
    .replace(/\//g, '_')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^\.+|[.\s]+$/g, '');
  if (!clean) throw new UserError('That name is empty or not allowed.');
  if (clean.length > 200) throw new UserError('Name is too long (max 200 characters).');
  return clean;
}

/**
 * Resolve `input` against `cwd`. "/x" is absolute (from the base folder),
 * ".." goes up. Escaping above the base folder is rejected.
 */
export function resolvePath(cwd, input) {
  const raw = String(input || '').trim().replace(/\\/g, '/');
  const parts = raw.startsWith('/') ? [] : splitPath(cwd);
  for (const seg of raw.split('/')) {
    const s = seg.trim();
    if (!s || s === '.') continue;
    if (s === '..') {
      if (!parts.length) throw new UserError('You cannot go above the main folder.');
      parts.pop();
      continue;
    }
    parts.push(sanitizeName(s));
  }
  return parts.join('/');
}

export const splitPath = (p) => String(p || '').split('/').filter(Boolean);
export const joinPath = (...ps) => ps.flatMap(splitPath).join('/');
export const parentOf = (p) => splitPath(p).slice(0, -1).join('/');
export const baseName = (p) => splitPath(p).pop() || '';
export const extOf = (name) => {
  const m = /\.([A-Za-z0-9]{1,8})$/.exec(name || '');
  return m ? m[0] : '';
};

/** Display form for users: "/" for the base, "/Invoices/2026" otherwise. */
export const display = (p) => `/${p || ''}`;
