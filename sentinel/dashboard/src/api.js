const BASE = '/api';
const KEY = 'sentinel.token';

export const getToken = () => sessionStorage.getItem(KEY);
export const setToken = (t) => (t ? sessionStorage.setItem(KEY, t) : sessionStorage.removeItem(KEY));

// FastAPI sends `detail` as a string, or as a list of {loc,msg} for validation errors.
const explain = (d, fallback) => (typeof d === 'string' ? d : Array.isArray(d) ? d.map((x) => x.msg).join('; ') : fallback);

async function call(path, { method = 'GET', auth = true, raw = false, body } = {}) {
  const headers = {};
  if (auth) headers.authorization = `Bearer ${getToken()}`;
  if (body !== undefined) headers['content-type'] = 'application/json';
  const r = await fetch(BASE + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) {
    let detail;
    try { detail = (await r.json()).detail; } catch { /* non-JSON body */ }
    const e = new Error(explain(detail, r.statusText));
    e.status = r.status;
    throw e;
  }
  return raw ? r : r.json();
}

export const api = {
  personas: () => call('/demo/personas', { auth: false }),
  // /health answers 503 with a JSON body when degraded; that body is the useful part, so only a network failure yields null.
  health: async () => { try { return await (await fetch(`${BASE}/health`)).json(); } catch { return null; } },
  me: () => call('/me'),
  overview: () => call('/overview'),
  cases: () => call('/cases'),
  caseDetail: (id) => call(`/cases/${id}`),
  series: (workload, service) => call(`/series?workload=${encodeURIComponent(workload)}&service=${encodeURIComponent(service)}`),
  dryRun: (id) => call(`/cases/${id}/dry-run`, { method: 'POST' }),
  integrations: () => call('/integrations'),
  setInterest: (provider, interested) => call(`/integrations/${provider}/interest`, { method: 'PUT', body: { interested } }),
  settings: () => call('/settings'),
  saveSettings: (patch) => call('/settings', { method: 'PUT', body: patch }),
  exportAudit: async () => (await call('/audit/export', { raw: true })).blob(),
};
