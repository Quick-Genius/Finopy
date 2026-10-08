import { useCallback, useEffect, useState } from 'react';
import { api } from '../api.js';
import { Icon } from '../icons.jsx';
import { Card, Chip, Live, PageHead, Skeleton, useToast } from '../ui.jsx';

const GROUPS = [['cloud', 'Cloud providers', 'Where Sentinel reads cost and inventory from.'], ['notifications', 'Notifications & ticketing', 'Where people hear about a case.'], ['platform', 'Platform', 'The services Sentinel itself runs on.']];
const STATE = { connected: ['green', 'Connected'], degraded: ['amber', 'Degraded'], down: ['red', 'Down'], not_configured: ['grey', 'Not configured'], managed_externally: ['blue', 'Managed in n8n'], planned: ['grey', 'Planned'] };
const MONO = { aws: 'AWS', gcp: 'GCP', n8n: 'n8n', ai: 'AI', pagerduty: 'PD', database: 'PG', runner: 'CC', azure: 'Az', slack: 'Sl', jira: 'Ji', teams: 'Tm' };
const hue = (k) => [...k].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 11);

function Tile({ k }) {
  const h = hue(k);
  return <span className="mono" style={{ background: `linear-gradient(135deg, hsl(${h} 72% 60%), hsl(${(h + 35) % 360} 68% 46%))` }} aria-hidden="true">{MONO[k] || k.slice(0, 2).toUpperCase()}</span>;
}

function Connector({ i, onInterest }) {
  const [tone, label] = STATE[i.state] || ['grey', i.state];
  const planned = i.availability === 'planned';
  return (
    <article className={`conn ${planned ? 'is-planned' : ''}`}>
      <div className="conn-top"><Tile k={i.key} /><Chip tone={tone} label={label} /></div>
      <h3>{i.name}</h3>
      <p className="conn-desc">{i.description}</p>
      <p className="conn-detail">{i.detail}</p>
      {i.facts?.length > 0 && <dl className="facts">{i.facts.map((f) => <div key={f.label}><dt>{f.label}</dt><dd>{String(f.value)}</dd></div>)}</dl>}
      {planned && (
        <button className={`btn ${i.interested ? 'btn-soft' : ''}`} onClick={() => onInterest(i)} aria-pressed={i.interested}>
          <Icon name={i.interested ? 'check' : 'bell'} size={15} />{i.interested ? 'We’ll keep you posted' : 'Notify me'}
        </button>
      )}
    </article>
  );
}

export default function Integrations() {
  const [d, setD] = useState(null);
  const [at, setAt] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const toast = useToast();

  const load = useCallback(async () => {
    setBusy(true);
    try { setD(await api.integrations()); setAt(Date.now()); setErr(''); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);

  const interest = async (i) => {
    const next = !i.interested;
    setD((x) => ({ ...x, integrations: x.integrations.map((n) => (n.key === i.key ? { ...n, interested: next } : n)) }));     // optimistic; rolled back on failure
    try { await api.setInterest(i.key, next); toast(next ? `Noted: you’re interested in ${i.name}` : `Removed interest in ${i.name}`); } catch (e) {
      setD((x) => ({ ...x, integrations: x.integrations.map((n) => (n.key === i.key ? { ...n, interested: !next } : n)) }));
      toast(e.message, 'err');
    }
  };

  const s = d?.summary;
  return (
    <>
      <PageHead title="Integrations" sub="What Sentinel reads from and talks to. Status is checked live; anything the API can’t observe says so.">
        <Live at={at} onRefresh={load} busy={busy} />
      </PageHead>
      {err && <div className="notice err" role="alert"><Icon name="alert" size={16} />{err}</div>}
      {s && (
        <div className="summary">
          <div><b>{s.connected}</b><span>Connected</span></div>
          <div className={s.needs_attention ? 'warn' : ''}><b>{s.needs_attention}</b><span>Need attention</span></div>
          <div><b>{s.externally_managed}</b><span>Managed in n8n</span></div>
          <div><b>{s.planned}</b><span>Planned</span></div>
        </div>
      )}
      {!d ? <div className="conn-grid">{[0, 1, 2].map((n) => <div key={n} className="conn"><Skeleton h={120} r={12} /></div>)}</div> : GROUPS.map(([cat, title, sub]) => (
        <section key={cat} className="group">
          <div className="group-head"><h2>{title}</h2><p>{sub}</p></div>
          <div className="conn-grid">{d.integrations.filter((i) => i.category === cat).map((i) => <Connector key={i.key} i={i} onInterest={interest} />)}</div>
        </section>
      ))}
    </>
  );
}
