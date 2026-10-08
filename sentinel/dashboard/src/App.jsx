import { useCallback, useEffect, useState } from 'react';
import { api, getToken, setToken } from './api.js';
import CaseDetail from './CaseDetail.jsx';
import { Chip, usd } from './ui.jsx';

function Login({ onDone }) {
  const [people, setPeople] = useState(null);
  const [err, setErr] = useState('');
  useEffect(() => { api.personas().then(setPeople).catch((e) => setErr(e.message)); }, []);
  const byTenant = (people || []).reduce((m, p) => ((m[p.tenant_id] ||= []).push(p), m), {});
  return (
    <div className="login">
      <h1>FinOps Sentinel</h1>
      <p className="muted">Local demo sign-in. Each persona is a seeded principal; the server derives tenant and role from its token.</p>
      {err && <p className="err">{err} — is the stack running with DEMO_MODE=true?</p>}
      <div className="grid2">
        {Object.entries(byTenant).map(([t, ps]) => (
          <div key={t} className="card">
            <h3>{t}</h3>
            {ps.map((p) => (
              <button key={p.principal_id} className="persona" onClick={() => { setToken(p.token); onDone(); }}>
                <b>{p.display_name}</b><span className="muted"> · {p.role}</span>
              </button>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function Tiles({ s }) {
  const st = s.cases_by_state || {};
  return (
    <div className="tiles">
      {['INVESTIGATED', 'PENDING', 'VERIFIED', 'REFUSED', 'DISMISSED'].map((k) => (
        <div key={k} className="tile"><div className="muted">{k}</div><div className="big">{st[k] || 0}</div></div>
      ))}
      <div className="tile"><div className="muted">Projected / month in flight</div><div className="big">{usd(s.projected_monthly_in_flight)}</div></div>
      <div className="tile" title={s.realized_savings.note}><div className="muted">Realized savings</div><div className="big">not measured</div></div>
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(!!getToken());
  const [me, setMe] = useState(null);
  const [cases, setCases] = useState([]);
  const [summary, setSummary] = useState(null);
  const [sel, setSel] = useState(null);
  const [err, setErr] = useState('');

  const refresh = useCallback(async () => {
    try {
      const [m, c, s] = await Promise.all([api.me(), api.cases(), api.summary()]);
      setMe(m); setCases(c); setSummary(s); setErr('');
    } catch (e) {
      if (e.status === 401) { setToken(null); setAuthed(false); } else setErr(e.message);
    }
  }, []);

  useEffect(() => {
    if (!authed) return undefined;
    refresh();
    const t = setInterval(refresh, 4000);
    return () => clearInterval(t);
  }, [authed, refresh]);

  if (!authed) return <Login onDone={() => setAuthed(true)} />;

  const exportAudit = async () => {
    const blob = await api.exportAudit();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `audit-${me.tenant_id}.ndjson`;
    a.click();
  };

  return (
    <div className="shell">
      <header>
        <div><b>FinOps Sentinel</b> <span className="tenant">{me?.tenant_id}</span></div>
        <div className="right">
          <span className="muted">{me?.display_name} · {me?.role}</span>
          <button onClick={exportAudit}>Export audit</button>
          <button onClick={() => { setToken(null); setAuthed(false); setSel(null); }}>Switch persona</button>
        </div>
      </header>
      {err && <div className="err banner">{err}</div>}
      {summary && <Tiles s={summary} />}
      <div className="split">
        <nav>
          {cases.length === 0 && <p className="muted pad">No cases yet. Run the “Sentinel scheduled analysis” workflow in n8n (see README), then this list fills in.</p>}
          {cases.map((c) => (
            <button key={c.case_id} className={`row ${sel === c.case_id ? 'on' : ''}`} onClick={() => setSel(c.case_id)}>
              <div className="rowtop"><b>{c.workload}</b><Chip state={c.state} /></div>
              <div className="muted small">{c.kind} · {c.classification}</div>
              <div className="small">{usd(c.projected_monthly)}/mo projected · owner {c.owner || 'unknown'}</div>
            </button>
          ))}
        </nav>
        <main>{sel ? <CaseDetail id={sel} me={me} onChanged={refresh} /> : <p className="muted pad">Select a case.</p>}</main>
      </div>
    </div>
  );
}
