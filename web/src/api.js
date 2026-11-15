/* Thin wrapper over the JSON API.
 *
 * Every call funnels through `request`, which turns a non-2xx response into a
 * thrown Error carrying the server's `detail` string. The server always
 * answers /api/ routes with JSON (see main.unhandled_error), so callers can
 * show the real cause -- "blank filtering is on but no blank group was
 * selected" -- instead of a generic failure. */

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

async function request(url, options = {}) {
  let response;
  try {
    response = await fetch(url, { credentials: 'same-origin', ...options });
  } catch (cause) {
    throw new ApiError('Could not reach the server. Is it still running?', 0, null);
  }

  const type = response.headers.get('content-type') || '';
  let body = null;
  if (type.includes('application/json')) {
    body = await response.json().catch(() => null);
  } else if (!response.ok) {
    body = { detail: await response.text().catch(() => response.statusText) };
  }

  if (!response.ok) {
    const detail = (body && (body.detail || body.message)) || response.statusText;
    // A session that has expired should send you back to the sign-in screen,
    // not surface as "sign in to use this server" on top of a dead app.
    if (response.status === 401 && !url.startsWith('/api/auth/')) {
      window.dispatchEvent(new CustomEvent('mpact:unauthenticated'));
    }
    throw new ApiError(typeof detail === 'string' ? detail : JSON.stringify(detail),
                       response.status, body);
  }
  return body;
}

const json = (method, url, payload) => request(url, {
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload ?? {}),
});

/* One feature's fields are asked for twice on every selection -- once by the
 * status bar, once by the details rail -- so the promise is cached and both
 * callers share the single request. Keyed by run as well as compound, capped
 * so a long session cannot grow it without bound, and dropped wholesale after
 * a database search, which is the one thing that changes a feature's fields
 * mid-run (its `hits` count). */
const featureCache = new Map();
const FEATURE_CACHE_MAX = 300;

export const api = {
  health: () => request('/api/health'),

  // --- sign-in ----------------------------------------------------------
  authStatus: () => request('/api/auth/status'),
  login: (username, password) => json('POST', '/api/auth/login', { username, password }),
  logout: () => json('POST', '/api/auth/logout', {}),

  // --- library ---------------------------------------------------------
  listFiles: () => request('/api/files'),
  deleteFile: (id) => request(`/api/files/${id}`, { method: 'DELETE' }),
  groupsFor: (inputs) => json('POST', '/api/files/groups', inputs),
  fileDownloadUrl: (id) => `/api/files/${id}/download`,
  injections: (id) => request(`/api/files/${id}/injections`),
  readTable: (id) => request(`/api/files/${id}/table`),
  replaceTable: (id, table) => json('PUT', `/api/files/${id}/table`, table),
  createTable: (table) => json('POST', '/api/files/table', table),
  demoInfo: () => request('/api/files/demo'),
  loadDemo: (id) => json('POST', '/api/files/demo', id ? { id } : {}),

  /** Open a saved .mpct session: the desktop's File > Open. Returns the
   *  library records it created plus the settings to restore. */
  importSession(file, onProgress) {
    return this.postFile('/api/files/session', { file }, onProgress);
  },

  uploadFile(kind, file, onProgress) {
    return this.postFile('/api/files', { kind, file }, onProgress);
  },

  /** Post a multipart form with progress. XHR rather than fetch: peak tables
   *  run to hundreds of megabytes and fetch still has no upload-progress
   *  event. */
  postFile(url, fields, onProgress) {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      for (const [key, value] of Object.entries(fields)) {
        if (value !== null && value !== undefined) form.append(key, value);
      }
      const xhr = new XMLHttpRequest();
      xhr.open('POST', url);
      xhr.withCredentials = true;
      xhr.upload.addEventListener('progress', (event) => {
        if (onProgress && event.lengthComputable) onProgress(event.loaded / event.total);
      });
      xhr.addEventListener('load', () => {
        let body = null;
        try { body = JSON.parse(xhr.responseText); } catch { /* non-JSON error page */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(body);
        else reject(new ApiError((body && body.detail) || `Upload failed (${xhr.status})`,
                                 xhr.status, body));
      });
      xhr.addEventListener('error', () => reject(new ApiError('Upload failed', 0, null)));
      xhr.addEventListener('abort', () => reject(new ApiError('Upload cancelled', 0, null)));
      xhr.send(form);
    });
  },

  // --- runs ------------------------------------------------------------
  defaults: () => request('/api/runs/defaults'),
  listRuns: () => request('/api/runs'),
  createRun: (payload) => json('POST', '/api/runs', payload),
  runStatus: (id) => request(`/api/runs/${id}/status`),
  runLog: (id) => request(`/api/runs/${id}/log`),
  runRequest: (id) => request(`/api/runs/${id}/request`),
  cancelRun: (id) => json('POST', `/api/runs/${id}/cancel`),
  deleteRun: (id) => request(`/api/runs/${id}`, { method: 'DELETE' }),

  // --- results ---------------------------------------------------------
  catalogue: (id) => request(`/api/runs/${id}/plots`),
  plot: (id, name, options) => json('POST', `/api/runs/${id}/plots/${name}`, options),
  features: (id, options) => json('POST', `/api/runs/${id}/features`, options),
  feature(id, compound) {
    const key = `${id}\u0000${compound}`;
    const cached = featureCache.get(key);
    if (cached) return cached;

    const pending = request(`/api/runs/${id}/feature?compound=${encodeURIComponent(compound)}`)
      .catch((error) => { featureCache.delete(key); throw error; });
    featureCache.set(key, pending);
    if (featureCache.size > FEATURE_CACHE_MAX) {
      featureCache.delete(featureCache.keys().next().value);
    }
    return pending;
  },
  abundance: (id, compound) =>
    request(`/api/runs/${id}/feature/abundance?compound=${encodeURIComponent(compound)}`),
  spectrum: (id, compound) =>
    request(`/api/runs/${id}/feature/spectrum?compound=${encodeURIComponent(compound)}`),
  matches: (id, compound, filter) =>
    request(`/api/runs/${id}/feature/matches?compound=${encodeURIComponent(compound)}`
            + (filter ? `&filter=${encodeURIComponent(JSON.stringify(filter))}` : '')),
  taxaAll: (level, q) =>
    request(`/api/taxa?level=${encodeURIComponent(level)}&q=${encodeURIComponent(q)}`),
  taxa: (id, level, q) =>
    request(`/api/runs/${id}/taxa?level=${encodeURIComponent(level)}&q=${encodeURIComponent(q)}`),
  checkSubstructure: (id, query) =>
    request(`/api/runs/${id}/substructure/check?query=${encodeURIComponent(query)}`),
  dbsearch(id) {
    // Hit counts are part of a feature's fields, so the cache above is stale
    // the moment a search finishes.
    featureCache.clear();
    return json('POST', `/api/runs/${id}/dbsearch`, {});
  },
  msmsSearch: (id, options) => json('POST', `/api/runs/${id}/msmssearch`, options),
  msms: (id, compound, filter) =>
    request(`/api/runs/${id}/feature/msms?compound=${encodeURIComponent(compound)}`
            + (filter ? `&filter=${encodeURIComponent(JSON.stringify(filter))}` : '')),
  downloads: (id) => request(`/api/runs/${id}/downloads`),
  downloadUrl: (id, key) => `/api/runs/${id}/downloads/${key}`,
  downloadAllUrl: (id) => `/api/runs/${id}/downloads.zip`,
  structureUrl: (id, smiles) =>
    `/api/runs/${id}/structure?smiles=${encodeURIComponent(smiles)}`,
};
