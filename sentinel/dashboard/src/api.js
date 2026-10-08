const BASE = '/api';
const KEY = 'sentinel.token';

export const getToken = () => sessionStorage.getItem(KEY);
export const setToken = (t) => (t ? sessionStorage.setItem(KEY, t) : sessionStorage.removeItem(KEY));

async function call(path, { method = 'GET', auth = true, raw = false } = {}) {
  const headers = {};
  if (auth) headers.authorization = `Bearer ${getToken()}`;
  const r = await fetch(BASE + path, { method, headers });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch { /* non-JSON body */ }
    const e = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    e.status = r.status;
    throw e;
  }
  return raw ? r : r.json();
}

export const api = {
  personas: () => call('/demo/personas', { auth: false }),
  me: () => call('/me'),
  summary: () => call('/summary'),
  cases: () => call('/cases'),
  caseDetail: (id) => call(`/cases/${id}`),
  series: (workload, service) => call(`/series?workload=${encodeURIComponent(workload)}&service=${encodeURIComponent(service)}`),
  dryRun: (id) => call(`/cases/${id}/dry-run`, { method: 'POST' }),
  exportAudit: async () => {
    const r = await call('/audit/export', { raw: true });
    return r.blob();
  },
};
