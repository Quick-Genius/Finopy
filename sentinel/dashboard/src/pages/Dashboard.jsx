import { useMemo, useState } from 'react';
import CaseDetail from '../CaseDetail.jsx';
import { AreaChart, HBars, StackBar } from '../charts.jsx';
import { Icon } from '../icons.jsx';
import { Boundary, Card, Chip, Drawer, Live, PageHead, STATE_TONE, Segmented, Skeleton, timeAgo, timeLeft, usd, useCountUp } from '../ui.jsx';

const NEEDS_ACTION = ['INVESTIGATED', 'PENDING'];
const GROUPS = [['blue', 'Investigated'], ['amber', 'Awaiting / approved'], ['green', 'Executed / verified'], ['red', 'Stopped'], ['grey', 'New / dismissed']];
const pretty = (s) => String(s || '').replace(/[._]/g, ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
const greeting = () => { const h = new Date().getHours(); return h < 5 ? 'Working late' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'; };

function Kpi({ icon, tone, label, value, hint, onClick }) {
  return (
    <button className={`kpi tone-${tone}`} onClick={onClick} disabled={!onClick}>
      <span className="kpi-icon"><Icon name={icon} size={18} /></span>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      <span className="kpi-hint">{hint}</span>
    </button>
  );
}

function Hero({ o }) {
  const monthly = useCountUp(Number(o.totals.identified_monthly));
  return (
    <Card className="hero">
      <div className="eyebrow"><Icon name="sparkle" size={14} /> Savings identified</div>
      <div className="hero-value">{usd(monthly)}<small>/ month</small></div>
      <p className="hero-sub">Across {o.totals.cases} {o.totals.cases === 1 ? 'case' : 'cases'} · about {usd(Number(o.totals.identified_monthly) * 12, true)} a year if every one lands.</p>
      <div className="hero-stats">
        <div><span>In flight</span><b>{usd(o.totals.in_flight_monthly)}</b><em>pending, approved or verified</em></div>
        <div title="Realized savings need billing observations over an agreed window."><span>Realized</span><b>Not measured</b><em>needs billing history</em></div>
      </div>
    </Card>
  );
}

function SpendCard({ o }) {
  const s = o.spend, up = s.change_pct != null && s.change_pct > 0;
  return (
    <Card title="Daily cloud spend" sub={`Last ${s.points.length} days · USD`} className="spend"
      action={s.change_pct != null && <span className={`delta ${up ? 'up' : 'down'}`}><Icon name="trend" size={14} className={up ? '' : 'flip'} />{Math.abs(s.change_pct)}% <em>vs prior 7 days</em></span>}>
      <AreaChart points={s.points} windowStart={s.window_start} windowEnd={s.window_end} label={`Daily cloud spend, last ${s.points.length} days. Last 7 days ${usd(s.last_7d)}, previous 7 days ${usd(s.previous_7d)}.`} />
    </Card>
  );
}

function Attention({ o, cases, open }) {
  const pending = o.pending_approvals.map((a) => ({ key: a.approval_id, id: a.case_id, title: a.workload || a.case_id, meta: `Needs ${a.required_role} · ${timeLeft(a.expires_at)}`, money: a.projected_monthly, tag: ['amber', 'Awaiting approval'] }));
  const ids = new Set(pending.map((p) => p.id));
  const inv = cases.filter((c) => c.state === 'INVESTIGATED' && !ids.has(c.case_id)).sort((a, b) => Number(b.projected_monthly) - Number(a.projected_monthly))
    .map((c) => ({ key: c.case_id, id: c.case_id, title: c.workload, meta: `${pretty(c.kind)} · owner ${c.owner || 'unknown'}`, money: c.projected_monthly, tag: ['blue', 'Ready to review'] }));
  const rows = [...pending, ...inv].slice(0, 6);
  return (
    <Card title="Needs your attention" sub={rows.length ? `${pending.length + inv.length} waiting` : 'You’re all caught up'}>
      {rows.length === 0 ? <div className="empty"><Icon name="check" size={22} />Nothing is waiting on you.</div> : (
        <ul className="queue">
          {rows.map((r) => (
            <li key={r.key}>
              <button onClick={() => open(r.id)}>
                <span className="q-main"><b>{r.title}</b><em>{r.meta}</em></span>
                <span className="q-side"><b>{usd(r.money)}<small>/mo</small></b><Chip tone={r.tag[0]} label={r.tag[1]} /></span>
                <Icon name="arrow" size={16} className="q-go" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Activity({ o, open }) {
  return (
    <Card title="Recent activity" sub="Latest audit events for your workspace">
      {o.recent_activity.length === 0 ? <div className="empty">No activity yet.</div> : (
        <ol className="feed">
          {o.recent_activity.slice(0, 5).map((e) => (
            <li key={e.audit_id}>
              <span className={`feed-dot fill-${STATE_TONE[e.to_state] || 'grey'}`} />
              <div>
                <p>{e.to_state ? <><b>{e.workload || 'A case'}</b> moved to <b>{pretty(e.to_state)}</b></> : <b>{pretty(e.event_type)}</b>}</p>
                <span>{e.actor} · {timeAgo(e.created_at)}{e.case_id && <> · <button className="link" onClick={() => open(e.case_id)}>Open</button></>}</span>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function Savings({ o }) {
  const [by, setBy] = useState('workload');
  const list = by === 'workload' ? o.savings_by_workload : o.savings_by_owner;
  const key = by === 'workload' ? 'workload' : 'owner';
  return (
    <Card title="Where the savings are" sub="Projected per month"
      action={<Segmented label="Group by" value={by} onChange={setBy} options={[{ value: 'workload', label: 'Workload' }, { value: 'owner', label: 'Owner' }]} />}>
      <HBars rows={list.map((r) => ({ label: r[key], value: Number(r.monthly), text: usd(r.monthly), note: `${r.cases} ${r.cases === 1 ? 'case' : 'cases'}` }))} />
    </Card>
  );
}

function Pipeline({ cases }) {
  const n = Object.fromEntries(GROUPS.map(([t]) => [t, 0]));
  cases.forEach((c) => { n[STATE_TONE[c.state] || 'grey'] += 1; });
  return (
    <Card title="Case pipeline" sub={`${cases.length} total`}>
      {cases.length === 0 ? <div className="empty">No cases yet.</div> : <StackBar label="Cases by stage" parts={GROUPS.map(([tone, label]) => ({ tone, label, value: n[tone] }))} />}
    </Card>
  );
}

function CaseTable({ cases, filter, setFilter, q, setQ, open }) {
  const counts = { all: cases.length, action: cases.filter((c) => NEEDS_ACTION.includes(c.state)).length };
  counts.closed = counts.all - counts.action;
  const shown = useMemo(() => cases.filter((c) => (filter === 'all' || (filter === 'action') === NEEDS_ACTION.includes(c.state))
    && `${c.workload} ${c.owner} ${c.kind} ${c.state}`.toLowerCase().includes(q.trim().toLowerCase())), [cases, filter, q]);
  return (
    <Card title="All cases" className="cases" id="cases">
      <div className="toolbar">
        <label className="search"><Icon name="search" size={16} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search workload, owner or state" aria-label="Search cases" /></label>
        <Segmented label="Filter cases" value={filter} onChange={setFilter}
          options={[{ value: 'all', label: `All ${counts.all}` }, { value: 'action', label: `Needs action ${counts.action}` }, { value: 'closed', label: `Closed ${counts.closed}` }]} />
      </div>
      {cases.length === 0 ? <div className="empty">No cases yet. Run the “Sentinel scheduled analysis” workflow in n8n, then this list fills in.</div> : shown.length === 0 ? <div className="empty">No cases match.</div> : (
        <div className="tablewrap">
          <table className="grid-table">
            <thead><tr><th>Workload</th><th>Stage</th><th>Assessment</th><th>Owner</th><th className="r">Projected / mo</th><th className="r">Updated</th></tr></thead>
            <tbody>
              {shown.map((c) => (
                <tr key={c.case_id} onClick={() => open(c.case_id)}>
                  <td><button className="rowbtn" onClick={(e) => { e.stopPropagation(); open(c.case_id); }}><b>{c.workload}</b><em>{pretty(c.kind)}</em></button></td>
                  <td><Chip state={c.state} /></td>
                  <td>{pretty(c.classification)}</td>
                  <td>{c.owner || <span className="muted">Unknown</span>}</td>
                  <td className="r num">{usd(c.projected_monthly)}</td>
                  <td className="r muted">{timeAgo(c.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export default function Dashboard({ me, cases, overview: o, health, updatedAt, refreshing, onRefresh, onChanged }) {
  const [sel, setSel] = useState(null);
  const [filter, setFilter] = useState('all');
  const [q, setQ] = useState('');
  const first = (me?.display_name || '').split(/[\s(]/)[0];
  const sys = !health ? ['red', 'API unreachable'] : (health.database === 'ok' && health.runner === 'ok' && Object.values(health.workers || {}).every((v) => v === 'alive')) ? ['green', 'All systems normal'] : ['amber', 'Degraded'];
  const jump = (f) => { setFilter(f); document.getElementById('cases')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); };

  return (
    <>
      <PageHead title={first ? `${greeting()}, ${first}` : 'Overview'} sub={`Cost anomalies and waste for ${me?.tenant_id || 'your workspace'}, and where each one is in the approval pipeline.`}>
        <Chip tone={sys[0]} label={sys[1]} />
        <Live at={updatedAt} onRefresh={onRefresh} busy={refreshing} />
      </PageHead>

      {!o ? (
        <div className="grid"><div className="skel-card span-5"><Skeleton h={150} r={16} /></div><div className="skel-card span-7"><Skeleton h={190} r={16} /></div></div>
      ) : (
        <div className="grid">
          <div className="span-5 eq"><Hero o={o} /></div>
          <div className="span-7 eq"><SpendCard o={o} /></div>
          <div className="kpis span-12">
            <Kpi icon="bell" tone="blue" label="Needs action" value={o.totals.needs_action} hint="Investigated or pending" onClick={() => jump('action')} />
            <Kpi icon="clock" tone="amber" label="Awaiting approval" value={o.totals.pending_approvals} hint="Expire if undecided" onClick={() => jump('action')} />
            <Kpi icon="shield" tone="green" label="Verified" value={o.totals.verified} hint="Change confirmed" onClick={() => jump('closed')} />
            <Kpi icon="alert" tone="red" label="Refused" value={o.totals.refused} hint="Blocked by safety checks" onClick={() => jump('closed')} />
          </div>
          <div className="span-7"><Attention o={o} cases={cases} open={setSel} /></div>
          <div className="span-5"><Pipeline cases={cases} /></div>
          <div className="span-6"><Savings o={o} /></div>
          <div className="span-6"><Activity o={o} open={setSel} /></div>
          <div className="span-12"><CaseTable cases={cases} filter={filter} setFilter={setFilter} q={q} setQ={setQ} open={setSel} /></div>
        </div>
      )}

      <Drawer open={!!sel} onClose={() => setSel(null)} title="Case details">
        {sel && <Boundary key={sel} label="This case"><CaseDetail id={sel} me={me} onChanged={onChanged} /></Boundary>}
      </Drawer>
    </>
  );
}
