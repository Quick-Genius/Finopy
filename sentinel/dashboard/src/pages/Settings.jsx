import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api.js';
import { Icon } from '../icons.jsx';
import { Avatar, Card, PageHead, Segmented, Skeleton, Switch, useToast } from '../ui.jsx';
import { DEFAULT_PREFS } from '../prefs.js';

const NOTIFY = [
  ['REFUSED', 'Refused by safety checks', 'A proposed action was blocked.'],
  ['APPROVED', 'Approved', 'A named person approved a case.'],
  ['REJECTED', 'Rejected', 'A person said no.'],
  ['EXPIRED', 'Approval expired', 'Nobody decided in time. Nothing was approved.'],
  ['VERIFIED', 'Change verified', 'A change was observed in live state (tag mode only).'],
];
const LOCKED = [['POLICY_INVALID', 'Invalid policy'], ['EXECUTION_FAILED', 'Execution failed'], ['VERIFICATION_FAILED', 'Verification failed']];
const MODE = {
  alert_only: ['Alert only', 'Sentinel investigates and alerts. It never changes your cloud; people decide and fix.'],
  tag_after_approval: ['Tag after approval', 'After a named person approves, the isolated runner may apply one reversible tag, then verify it.'],
};

const Row = ({ title, hint, children, stack }) => (
  <div className={`srow ${stack ? 'stack' : ''}`}><div className="stext"><b>{title}</b>{hint && <span>{hint}</span>}</div><div className="sctl">{children}</div></div>
);

function Notifications({ s, canEdit }) {
  const toast = useToast();
  const [draft, setDraft] = useState(null);
  useEffect(() => { setDraft(s.settings); }, [s.settings]);
  const dirty = useMemo(() => draft && JSON.stringify(draft) !== JSON.stringify(s.settings), [draft, s.settings]);
  const [saving, setSaving] = useState(false);
  const toggle = (k, on) => setDraft((d) => ({ ...d, notify_states: NOTIFY.map((n) => n[0]).filter((x) => (x === k ? on : d.notify_states.includes(x))) }));
  const save = async () => {
    setSaving(true);
    try { s.apply(await api.saveSettings(draft)); toast('Notification settings saved'); } catch (e) { toast(e.message, 'err'); } finally { setSaving(false); }
  };
  if (!draft) return null;
  return (
    <Card title="Notifications" sub="Which outcomes this workspace hears about in Slack. Saved for everyone in the workspace.">
      {!canEdit && <div className="notice"><Icon name="lock" size={15} />Only platform reviewers can change workspace settings. You’re viewing them read-only.</div>}
      <div className="rows">
        {NOTIFY.map(([k, t, h]) => <Row key={k} title={t} hint={h}><Switch label={t} checked={draft.notify_states.includes(k)} disabled={!canEdit} onChange={(on) => toggle(k, on)} /></Row>)}
        {LOCKED.map(([k, t]) => <Row key={k} title={t} hint="Always sent. Failures can’t be muted."><Switch label={`${t} (always on)`} checked disabled onChange={() => {}} /></Row>)}
        <Row title="Minimum projected saving" hint="Skip the alerts above for cases projected below this amount per month. Failures are still sent.">
          <label className="money"><span>$</span><input type="number" min="0" max="1000000" step="10" inputMode="decimal" disabled={!canEdit} value={draft.min_notify_savings_usd}
            onChange={(e) => setDraft((d) => ({ ...d, min_notify_savings_usd: e.target.value === '' ? '' : Number(e.target.value) }))} aria-label="Minimum projected saving per month in dollars" /></label>
        </Row>
      </div>
      {canEdit && (
        <div className={`savebar ${dirty ? 'show' : ''}`}>
          <span>{dirty ? 'You have unsaved changes' : 'All changes saved'}</span>
          <div><button className="btn" disabled={!dirty || saving} onClick={() => setDraft(s.settings)}>Discard</button>
            <button className="btn btn-primary" disabled={!dirty || saving || draft.min_notify_savings_usd === ''} onClick={save}>{saving ? 'Saving…' : 'Save changes'}</button></div>
        </div>
      )}
    </Card>
  );
}

function Guardrails({ v }) {
  const [mode, note] = MODE[v.action_mode] || [v.action_mode, ''];
  const stats = [
    ['Approval window', `${v.approval_ttl_minutes} min`, 'Undecided approvals expire; they never auto-approve.'],
    ['Targets per action', v.max_targets_per_action, 'Larger blast radius is refused.'],
    ['Owner confidence', v.min_attribution_confidence, 'Minimum to assign an owner.'],
    ['Inventory freshness', `${v.snapshot_max_age_minutes} min`, 'Older snapshots are not acted on.'],
    ['Analysis window', `${v.analysis_window_days} days`, `Against a ${v.history_days}-day baseline.`],
    ['AI summaries', v.ai_summaries.enabled ? v.ai_summaries.provider : 'Off', 'Always fact-checked before display.'],
  ];
  return (
    <Card title="Safety guardrails" sub="Set by the server environment. Shown here so everyone can see the limits; they can’t be changed from the browser.">
      <div className="mode"><Icon name="shield" size={20} /><div><b>{mode}</b><span>{note}</span></div></div>
      <div className="statgrid">{stats.map(([l, val, h]) => <div key={l}><span>{l}</span><b>{val}</b><em>{h}</em></div>)}</div>
    </Card>
  );
}

export default function Settings({ prefs, setPrefs, me, onExport, onSignOut }) {
  const [s, setS] = useState(null);
  const [err, setErr] = useState('');
  const load = useCallback(async () => { try { setS(await api.settings()); setErr(''); } catch (e) { setErr(e.message); } }, []);
  useEffect(() => { load(); }, [load]);
  const set = (k) => (v) => setPrefs({ ...prefs, [k]: v });
  const canExport = ['analyst', 'reviewer', 'finance'].includes(me?.role);
  const server = s && { ...s, apply: (r) => setS({ ...s, settings: r.settings, updated_by: r.updated_by, updated_at: new Date().toISOString() }) };

  return (
    <>
      <PageHead title="Settings" sub="Make Sentinel yours. Appearance is saved in this browser; notifications are saved for the workspace." />
      {err && <div className="notice err" role="alert"><Icon name="alert" size={16} />{err}</div>}

      <div className="stack-cards">
        <Card title="Appearance" sub="Stored in this browser only.">
          <div className="rows">
            <Row title="Theme" hint="Match your device, or pick one."><Segmented label="Theme" value={prefs.theme} onChange={set('theme')} options={[{ value: 'system', label: 'System', icon: 'monitor' }, { value: 'light', label: 'Light', icon: 'sun' }, { value: 'dark', label: 'Dark', icon: 'moon' }]} /></Row>
            <Row title="Density" hint="Compact fits more rows on screen."><Segmented label="Density" value={prefs.density} onChange={set('density')} options={[{ value: 'comfortable', label: 'Comfortable' }, { value: 'compact', label: 'Compact' }]} /></Row>
            <Row title="Auto-refresh" hint="How often data is re-read."><Segmented label="Auto-refresh" value={prefs.refreshMs} onChange={set('refreshMs')} options={[{ value: 0, label: 'Off' }, { value: 4000, label: '4s' }, { value: 15000, label: '15s' }, { value: 60000, label: '1m' }]} /></Row>
          </div>
          <button className="link" onClick={() => setPrefs({ ...DEFAULT_PREFS })}>Reset to defaults</button>
        </Card>

        {!s && !err && <Card title="Notifications"><Skeleton h={160} r={12} /></Card>}
        {server && <Notifications s={server} canEdit={s.can_edit} />}
        {server && <Guardrails v={s.server} />}

        <Card title="Account" sub="Who you’re signed in as.">
          <div className="account">
            <Avatar name={me?.display_name || '?'} size={48} />
            <div><b>{me?.display_name}</b><span>{me?.role} · workspace {me?.tenant_id}</span></div>
          </div>
          <div className="actions">
            <button className="btn" onClick={onExport} disabled={!canExport} title={canExport ? '' : 'Your role cannot export the audit log'}><Icon name="download" size={15} />Export audit log</button>
            <button className="btn" onClick={onSignOut}><Icon name="logout" size={15} />Switch persona</button>
          </div>
        </Card>
      </div>
    </>
  );
}
