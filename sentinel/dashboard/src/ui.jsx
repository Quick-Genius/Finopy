import { Component, createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { Icon } from './icons.jsx';

export const STATE_TONE = {
  NEW: 'grey', INVESTIGATED: 'blue', DRY_RUN: 'blue', PENDING: 'amber', APPROVED: 'amber', EXECUTED: 'green', VERIFIED: 'green',
  REJECTED: 'red', EXPIRED: 'red', REFUSED: 'red', POLICY_INVALID: 'red', EXECUTION_FAILED: 'red', VERIFICATION_FAILED: 'red', DISMISSED: 'grey',
};
const pretty = (s) => String(s).replace(/_/g, ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase());

// Status is always text + a dot, never colour alone.
export const Chip = ({ state, tone, label }) => <span className={`pill pill-${tone || STATE_TONE[state] || 'grey'}`}><i />{label || pretty(state)}</span>;

export const usd = (v, compact = false) => (v == null ? '—' : Number(v).toLocaleString(undefined, { style: 'currency', currency: 'USD', ...(compact ? { notation: 'compact', maximumFractionDigits: 1 } : { maximumFractionDigits: Math.abs(v) >= 1000 ? 0 : 2 }) }));

export function timeAgo(ts) {
  if (!ts) return '—';
  const s = Math.max(Math.round((Date.now() - new Date(ts).getTime()) / 1000), 0);
  if (s < 10) return 'just now';
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
export function timeLeft(ts) {
  const s = Math.round((new Date(ts).getTime() - Date.now()) / 1000);
  if (s <= 0) return 'expired';
  return s < 3600 ? `${Math.max(Math.round(s / 60), 1)}m left` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m left`;
}

export const Avatar = ({ name = '?', size = 36 }) => {
  const letters = name.split(/[\s(]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('');
  const hue = [...name].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) % 360, 7);
  return <span className="avatar" style={{ width: size, height: size, fontSize: size * 0.38, background: `linear-gradient(135deg, hsl(${hue} 70% 58%), hsl(${(hue + 40) % 360} 70% 48%))` }} aria-hidden="true">{letters}</span>;
};

export const Skeleton = ({ h = 16, w = '100%', r = 8 }) => <span className="skeleton" style={{ height: h, width: w, borderRadius: r }} />;

// Animates a number toward its new value; skipped for people who ask for reduced motion.
export function useCountUp(target, ms = 700) {
  const [v, setV] = useState(target);
  const from = useRef(target);
  useEffect(() => {
    const t = Number(target);
    if (!Number.isFinite(t) || window.matchMedia('(prefers-reduced-motion: reduce)').matches) { setV(t); from.current = t; return undefined; }
    const a = from.current, t0 = performance.now();
    let raf;
    const tick = (now) => {
      const p = Math.min((now - t0) / ms, 1), e = 1 - (1 - p) ** 3;
      setV(a + (t - a) * e);
      if (p < 1) raf = requestAnimationFrame(tick); else from.current = t;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return v;
}

export const Segmented = ({ value, onChange, options, label, disabled }) => (
  <div className="seg" role="radiogroup" aria-label={label}>
    {options.map((o) => (
      <button key={o.value} type="button" role="radio" aria-checked={value === o.value} disabled={disabled} className={value === o.value ? 'on' : ''} onClick={() => onChange(o.value)}>
        {o.icon && <Icon name={o.icon} size={15} />}{o.label}
      </button>
    ))}
  </div>
);

export const Switch = ({ checked, onChange, label, disabled }) => (
  <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} className="switch" onClick={() => onChange(!checked)}><i /></button>
);

const ToastCtx = createContext(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const push = useCallback((text, tone = 'ok') => {
    const id = Math.random().toString(36).slice(2);
    setItems((l) => [...l, { id, text, tone }]);
    setTimeout(() => setItems((l) => l.filter((x) => x.id !== id)), 4200);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">{items.map((t) => <div key={t.id} className={`toast toast-${t.tone}`}><Icon name={t.tone === 'ok' ? 'check' : 'alert'} size={16} />{t.text}</div>)}</div>
    </ToastCtx.Provider>
  );
}

// Right-hand slide-over. Esc and backdrop close it; focus moves in and returns to the opener.
export function Drawer({ open, onClose, title, children }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const prev = document.activeElement;
    ref.current?.focus();
    const key = (e) => e.key === 'Escape' && onClose();
    addEventListener('keydown', key);
    document.body.style.overflow = 'hidden';
    return () => { removeEventListener('keydown', key); document.body.style.overflow = ''; prev?.focus?.(); };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="drawer-wrap">
      <div className="backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref}>
        <button className="icon-btn drawer-x" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        {children}
      </aside>
    </div>
  );
}

export function PageHead({ title, sub, children }) {
  return (
    <header className="pagehead">
      <div><h1>{title}</h1>{sub && <p>{sub}</p>}</div>
      <div className="head-actions">{children}</div>
    </header>
  );
}

// "Updated 4s ago" that keeps itself fresh, plus a manual refresh.
export function Live({ at, onRefresh, busy }) {
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((n) => n + 1), 5000); return () => clearInterval(t); }, []);
  return (
    <button className="live" onClick={onRefresh} disabled={busy} title="Refresh now">
      <span className="livedot" /> {at ? `Updated ${timeAgo(at)}` : 'Connecting…'}
      <Icon name="refresh" size={14} className={busy ? 'spin' : ''} />
    </button>
  );
}

export const Card = ({ title, sub, action, className = '', id, children }) => (
  <section className={`card ${className}`} id={id}>
    {(title || action) && <div className="card-head"><div><h2>{title}</h2>{sub && <p>{sub}</p>}</div>{action}</div>}
    {children}
  </section>
);

// A render error in one panel (e.g. a case with unexpected content) must not blank the whole app.
export class Boundary extends Component {
  state = { err: null };
  static getDerivedStateFromError(err) { return { err }; }
  render() {
    if (!this.state.err) return this.props.children;
    return <div className="notice err" role="alert"><Icon name="alert" size={16} />{this.props.label || 'This section'} couldn’t be displayed ({this.state.err.message}).</div>;
  }
}
