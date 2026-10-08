import { useCallback, useEffect, useState } from 'react';
import { api } from './api.js';
import { Chip, usd } from './ui.jsx';

// Real cost facts only: the line is the tenant's stored daily cost, the dashed marks are the deterministic expected values.
function CostChart({ s }) {
  if (!s || !s.points.length) return <p className="muted">No cost series for this case.</p>;
  const pts = s.points.map((p) => ({ d: p.date, v: Number(p.amount) }));
  const W = 640, H = 170, P = 28;
  const max = Math.max(...pts.map((p) => p.v)) * 1.1 || 1;
  const x = (i) => P + (i * (W - 2 * P)) / Math.max(pts.length - 1, 1);
  const y = (v) => H - P - (v / max) * (H - 2 * P);
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ');
  const wi = pts.findIndex((p) => p.d >= s.window_start);
  const exp = Object.fromEntries((s.window_expected || []).map((e) => [e.date, Number(e.expected)]));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Daily cost with analysis window">
      {wi >= 0 && <rect x={x(wi)} y={P / 2} width={W - P - x(wi)} height={H - P * 1.5} className="win" />}
      <path d={path} className="line" />
      {pts.map((p, i) => exp[p.d] != null && <line key={p.d} x1={x(i) - 6} x2={x(i) + 6} y1={y(exp[p.d])} y2={y(exp[p.d])} className="exp" />)}
      <text x={P} y={H - 6} className="axis">{pts[0].d}</text>
      <text x={W - P} y={H - 6} className="axis" textAnchor="end">{pts[pts.length - 1].d}</text>
      <text x={P - 4} y={y(max / 1.1) + 4} className="axis" textAnchor="end">{usd(max / 1.1)}</text>
    </svg>
  );
}

const Sec = ({ title, children }) => <section><h3>{title}</h3>{children}</section>;
const Hash = ({ label, v }) => <div className="hash"><span className="muted">{label}</span><code>{v}</code></div>;

export default function CaseDetail({ id, me, onChanged }) {
  const [c, setC] = useState(null);
  const [s, setS] = useState(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api.caseDetail(id);
      setC(d); setErr('');
      if (d.content.signal.features) api.series(d.content.workload, d.content.service).then(setS).catch(() => setS(null));
    } catch (e) { setErr(e.message); }
  }, [id]);
  useEffect(() => { setC(null); setS(null); load(); const t = setInterval(load, 4000); return () => clearInterval(t); }, [load]);

  if (err) return <p className="err pad">{err}</p>;
  if (!c) return <p className="muted pad">Loading…</p>;

  const k = c.content, rec = k.recommendation, attr = k.attribution, pol = c.policy, apr = c.approval, ex = c.execution;
  const canDryRun = c.state === 'INVESTIGATED' && rec.route === 'dry_run' && ['analyst', 'reviewer'].includes(me?.role);
  const dryRun = async () => {
    setBusy(true);
    try { await api.dryRun(id); await load(); onChanged(); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  return (
    <div className="detail">
      <div className="dhead">
        <div>
          <h2>{k.workload} <span className="muted">· {k.kind}</span></h2>
          <div className="muted small">{c.case_id} · version {c.version} · tenant {c.tenant_id}</div>
        </div>
        <div className="chips"><Chip state={c.state} /><Chip state={k.assessment.classification} tone={{ probable_waste: 'amber', legitimate: 'green', needs_review: 'blue' }[k.assessment.classification]} /></div>
      </div>

      <Sec title="What changed">
        {k.signal.status === 'anomaly' ? (
          <p>Spend on <b>{k.service}</b> was <b>{usd(k.signal.actual)}</b> over the last 3 days against an expected <b>{usd(k.signal.expected)}</b> (anomaly score {k.signal.anomaly_score}, deterministic baseline with weekday seasonality).</p>
        ) : (
          <p className="muted">No material spend deviation on {k.service} (status: {k.signal.status}). This case comes from a waste indicator.</p>
        )}
        <CostChart s={s} />
        <div className="muted small">Line: stored daily cost. Shaded: analysis window. Dashes: expected value. Currency {k.signal.currency}.</div>
      </Sec>

      <Sec title="Why — assessment">
        <ul>{k.assessment.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
        {k.waste_indicators.map((w) => <div key={w.resource_id} className="small muted">indicator: {w.summary}</div>)}
      </Sec>

      <Sec title="Who owns it">
        <div className="conf"><div className="bar"><i style={{ width: `${attr.confidence * 100}%` }} /></div>
          <b>{attr.owner || 'needs_review'}</b> · confidence {attr.confidence.toFixed(2)} · {attr.reason}</div>
        <table><thead><tr><th>Rank</th><th>Source</th><th>Owner</th><th>Evidence</th></tr></thead>
          <tbody>{attr.evidence.map((e, i) => <tr key={i}><td>{e.rank}</td><td>{e.source}</td><td>{e.owner}</td><td>{e.ref}</td></tr>)}</tbody></table>
      </Sec>

      <Sec title="What exactly will happen">
        {!pol && <p className="muted">No policy yet. {canDryRun ? 'Run a Custodian dry-run to freeze the exact targets.' : rec.route === 'dry_run' ? 'The n8n intake workflow starts the dry-run automatically.' : `Route is “${rec.route}”: this case has no executable action.`}</p>}
        {canDryRun && <button className="primary" disabled={busy} onClick={dryRun}>Run Custodian dry-run</button>}
        {pol && (<>
          <p>Template <b>{pol.template}</b> v{pol.template_version} · status <Chip state={pol.status.toUpperCase()} tone={pol.status === 'valid' ? 'green' : 'red'} /> · risk <b>{rec.risk}</b></p>
          {pol.refusal_reasons.length > 0 && <div className="refusal"><b>Refused:</b><ul>{pol.refusal_reasons.map((r, i) => <li key={i}>{r}</li>)}</ul></div>}
          <table><thead><tr><th>Exact target</th><th>Decision</th></tr></thead><tbody>
            {pol.target_ids.map((t) => <tr key={t}><td><code>{t}</code></td><td className="ok">will be acted on</td></tr>)}
            {pol.excluded.map((t) => <tr key={t.resource_id}><td><code>{t.resource_id}</code></td><td className="bad">excluded — {t.reason}</td></tr>)}
          </tbody></table>
          <Hash label="policy sha256" v={pol.policy_sha256} />
          <Hash label="target-set sha256" v={pol.target_set_sha256} />
          <p className="small"><b>Rollback:</b> {rec.rollback}</p>
          <details><summary>Rendered Custodian policy (deterministic template, not model-authored)</summary><pre>{pol.policy_yaml}</pre></details>
          <details><summary>Dry-run log (exit {pol.dryrun_exit_code}, custodian {pol.runner_versions?.custodian})</summary><pre>{pol.validate_log}{'\n'}{pol.dryrun_log}</pre></details>
        </>)}
      </Sec>

      <Sec title="Savings">
        <div className="two">
          <div><div className="muted">Projected (per month)</div><div className="big">{usd(pol?.guards?.savings?.monthly ?? rec.projected_savings.monthly)}</div>
            <ul className="small muted">{rec.projected_savings.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul></div>
          <div><div className="muted">Realized</div><div className="big">not measured</div><div className="small muted">{rec.realized_savings.note}</div></div>
        </div>
      </Sec>

      <Sec title="Approval">
        {!apr ? <p className="muted">No approval requested.</p> : (<>
          <p>Status <Chip state={apr.status.toUpperCase()} tone={{ requested: 'amber', approved: 'green', rejected: 'red', expired: 'red', invalidated: 'grey' }[apr.status]} /> · required role {apr.required_role} · expires {new Date(apr.expires_at).toLocaleString()}</p>
          {apr.status === 'requested' && (apr.form_url
            ? <p><a className="btn primary" href={apr.form_url} target="_blank" rel="noreferrer">Open approval form (n8n)</a> <span className="muted small">identity and PIN are checked by the API, not by the form</span></p>
            : <p className="muted">Waiting for n8n to publish the approval form…</p>)}
          {apr.decided_at && <p className="small">Decided by <b>{apr.actor_id}</b> ({apr.actor_role}) at {new Date(apr.decided_at).toLocaleString()} {apr.note && `— ${apr.note}`}</p>}
        </>)}
      </Sec>

      <Sec title="Execution & verification">
        {!ex ? <p className="muted">Nothing has run. Nothing is queued unless an approval exists.</p> : (<>
          <p>Execution <b>{ex.status}</b> · exit code {ex.exit_code ?? '—'} · matched {(ex.matched_ids || []).join(', ') || '—'}</p>
          {ex.result?.reasons?.length > 0 && <div className="refusal"><ul>{ex.result.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul></div>}
          {ex.verification && (<table><thead><tr><th>Resource</th><th>Expected tag</th><th>Observed after</th><th>Changed</th></tr></thead><tbody>
            {(ex.verification.resources || []).map((r) => <tr key={r.resource_id}><td><code>{r.resource_id}</code></td><td>{r.expected_tag} ∋ {r.expected_contains}</td><td>{r.observed ?? '—'}</td><td className={r.changed ? 'ok' : 'bad'}>{String(r.changed)}</td></tr>)}
          </tbody></table>)}
          {ex.logs && <details><summary>Runner log</summary><pre>{ex.logs}</pre></details>}
        </>)}
      </Sec>

      <Sec title="Audit timeline (this tenant only)">
        <ol className="timeline">{c.audit.map((a) => (
          <li key={a.audit_id}><span className="muted small">{new Date(a.created_at).toLocaleTimeString()}</span> <b>{a.event_type}</b>
            {a.from_state && <> <span className="muted">{a.from_state} → {a.to_state}</span></>} <span className="muted small">by {a.actor}</span>
            {a.detail?.reason && <div className="small">{a.detail.reason}</div>}
            {a.detail?.reasons?.length > 0 && <div className="small">{a.detail.reasons.join('; ')}</div>}</li>))}</ol>
      </Sec>
    </div>
  );
}
