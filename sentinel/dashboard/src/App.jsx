import { useCallback, useEffect, useRef, useState } from 'react';
import { api, getToken, setToken } from './api.js';
import { Icon } from './icons.jsx';
import { applyPrefs, loadPrefs, savePrefs } from './prefs.js';
import { Avatar, ToastProvider, useToast } from './ui.jsx';
import Dashboard from './pages/Dashboard.jsx';
import Integrations from './pages/Integrations.jsx';
import Settings from './pages/Settings.jsx';

const PAGES = [
  { id: 'dashboard', label: 'Dashboard', icon: 'dashboard' },
  { id: 'integrations', label: 'Integrations', icon: 'plug' },
  { id: 'settings', label: 'Settings', icon: 'settings' },
];

const routeOf = () => { const h = location.hash.replace(/^#\/?/, ''); return PAGES.some((p) => p.id === h) ? h : 'dashboard'; };
function useRoute() {
  const [r, setR] = useState(routeOf);
  useEffect(() => { const f = () => { setR(routeOf()); scrollTo({ top: 0 }); }; addEventListener('hashchange', f); return () => removeEventListener('hashchange', f); }, []);
  return r;
}

const Brand = () => <div className="brand"><span className="logo"><Icon name="shield" size={18} /></span><span><b>Sentinel</b><small>FinOps</small></span></div>;

function Login({ onDone }) {
  const [people, setPeople] = useState(null);
  const [err, setErr] = useState('');
  useEffect(() => { api.personas().then(setPeople).catch((e) => setErr(e.message)); }, []);
  const byTenant = (people || []).reduce((m, p) => ((m[p.tenant_id] ||= []).push(p), m), {});
  return (
    <main className="login">
      <div className="login-hero"><Brand /><h1>Spend less.<br />Stay in control.</h1><p>Sentinel finds cloud waste, explains it, and waits for a named person to decide. Nothing changes without a human.</p></div>
      <div className="login-card">
        <h2>Choose a demo persona</h2>
        <p className="muted">Local demo sign-in. Each persona is a seeded principal; the server derives tenant and role from its token.</p>
        {err && <div className="notice err" role="alert"><Icon name="alert" size={16} />{err} — is the stack running with DEMO_MODE=true?</div>}
        {!people && !err && <p className="muted">Loading personas…</p>}
        {Object.entries(byTenant).map(([t, ps]) => (
          <div key={t} className="persona-group">
            <h3>{t}</h3>
            {ps.map((p) => (
              <button key={p.principal_id} className="persona" onClick={() => { setToken(p.token); onDone(); }}>
                <Avatar name={p.display_name} /><span><b>{p.display_name}</b><em>{p.role}</em></span><Icon name="arrow" size={16} />
              </button>
            ))}
          </div>
        ))}
      </div>
    </main>
  );
}

function Shell({ onSignOut }) {
  const [prefs, setPrefs] = useState(loadPrefs);
  const [me, setMe] = useState(null);
  const [cases, setCases] = useState([]);
  const [overview, setOverview] = useState(null);
  const [health, setHealth] = useState(null);
  const [updatedAt, setUpdatedAt] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState('');
  const route = useRoute();
  const toast = useToast();
  const signOut = useRef(onSignOut);

  useEffect(() => { applyPrefs(prefs); savePrefs(prefs); }, [prefs]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const [m, c, o, h] = await Promise.all([api.me(), api.cases(), api.overview(), api.health()]);
      setMe(m); setCases(c); setOverview(o); setHealth(h); setUpdatedAt(Date.now()); setErr('');
    } catch (e) {
      if (e.status === 401) signOut.current(); else setErr(e.message);
    } finally { setRefreshing(false); }
  }, []);

  useEffect(() => {
    refresh();
    if (!prefs.refreshMs) return undefined;
    const t = setInterval(refresh, prefs.refreshMs);
    return () => clearInterval(t);
  }, [refresh, prefs.refreshMs]);

  const exportAudit = async () => {
    try {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(await api.exportAudit());
      a.download = `audit-${me.tenant_id}.ndjson`;
      a.click();
      toast('Audit log exported');
    } catch (e) { toast(e.message, 'err'); }
  };

  return (
    <div className="app">
      <aside className="side">
        <Brand />
        <nav className="menu" aria-label="Main">
          {PAGES.map((p) => (
            <a key={p.id} href={`#/${p.id}`} className={route === p.id ? 'on' : ''} aria-current={route === p.id ? 'page' : undefined}>
              <Icon name={p.icon} size={20} /><span>{p.label}</span>
            </a>
          ))}
        </nav>
        <div className="who">
          <Avatar name={me?.display_name || '?'} />
          <div><b>{me?.display_name?.split(' (')[0] || '…'}</b><span>{me?.role} · {me?.tenant_id}</span></div>
        </div>
      </aside>
      <main className="page" key={route}>
        {err && <div className="notice err" role="alert"><Icon name="alert" size={16} />{err}</div>}
        {route === 'dashboard' && <Dashboard me={me} cases={cases} overview={overview} health={health} updatedAt={updatedAt} refreshing={refreshing} onRefresh={refresh} onChanged={refresh} />}
        {route === 'integrations' && <Integrations />}
        {route === 'settings' && <Settings prefs={prefs} setPrefs={setPrefs} me={me} onExport={exportAudit} onSignOut={onSignOut} />}
      </main>
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(!!getToken());
  useEffect(() => { applyPrefs(loadPrefs()); }, []);          // theme applies on the login screen too
  const signOut = () => { setToken(null); setAuthed(false); };
  return <ToastProvider>{authed ? <Shell onSignOut={signOut} /> : <Login onDone={() => setAuthed(true)} />}</ToastProvider>;
}
