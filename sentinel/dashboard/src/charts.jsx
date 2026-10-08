import { useEffect, useId, useRef, useState } from 'react';
import { usd } from './ui.jsx';

const PL = 52, PR = 14, PT = 14, PB = 28;
const fmtDay = (d) => new Date(`${d}T00:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });

// Smooth line through the points (Catmull-Rom -> cubic Bezier). Control points are clamped to the data range so a spike never dips below zero.
function smooth(pts, lo, hi) {
  if (pts.length < 3) return pts.map((p, i) => `${i ? 'L' : 'M'}${p[0]},${p[1]}`).join(' ');
  const c = (v) => Math.min(Math.max(v, lo), hi);
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i += 1) {
    const [p0, p1, p2, p3] = [pts[i - 1] || pts[i], pts[i], pts[i + 1], pts[i + 2] || pts[i + 1]];
    d += ` C${p1[0] + (p2[0] - p0[0]) / 6},${c(p1[1] + (p2[1] - p0[1]) / 6)} ${p2[0] - (p3[0] - p1[0]) / 6},${c(p2[1] - (p3[1] - p1[1]) / 6)} ${p2[0]},${p2[1]}`;
  }
  return d;
}

// Single-series daily spend. Hover or arrow keys move a crosshair + tooltip; a hidden table carries the same numbers.
export function AreaChart({ points, windowStart, windowEnd, label }) {
  const id = useId();
  const box = useRef(null);
  const [W, setW] = useState(760);                 // real pixel width, so axis text keeps its size on phones
  const [hi, setHi] = useState(null);
  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setW(Math.max(Math.round(e.contentRect.width), 260)));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, [points.length > 0]);
  const H = W < 520 ? 210 : 250;
  if (!points.length) return <div className="empty" ref={box}>No cost data yet.</div>;
  const vals = points.map((p) => Number(p.amount));
  const max = Math.max(...vals) * 1.12 || 1;
  const x = (i) => PL + (i * (W - PL - PR)) / Math.max(points.length - 1, 1);
  const y = (v) => PT + (1 - v / max) * (H - PT - PB);
  const xy = vals.map((v, i) => [+x(i).toFixed(1), +y(v).toFixed(1)]);
  const line = smooth(xy, PT, H - PB);
  const area = `${line} L${xy.at(-1)[0]},${H - PB} L${xy[0][0]},${H - PB} Z`;
  const wi = points.findIndex((p) => p.date >= windowStart);
  const ticks = [0, 0.5, 1].map((f) => f * max / 1.12);
  const move = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const f = ((e.clientX - r.left) / r.width) * W;
    setHi(Math.min(Math.max(Math.round(((f - PL) / (W - PL - PR)) * (points.length - 1)), 0), points.length - 1));
  };
  const key = (e) => {
    if (e.key === 'ArrowLeft') setHi((h) => Math.max((h ?? points.length) - 1, 0));
    if (e.key === 'ArrowRight') setHi((h) => Math.min((h ?? -1) + 1, points.length - 1));
    if (e.key === 'Escape') setHi(null);
  };
  return (
    <div className="chart-wrap" ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} className="area" role="img" aria-label={label} tabIndex={0} onPointerMove={move} onPointerLeave={() => setHi(null)} onKeyDown={key} onBlur={() => setHi(null)}>
        <defs><linearGradient id={id} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--series)" stopOpacity="0.28" /><stop offset="100%" stopColor="var(--series)" stopOpacity="0" /></linearGradient></defs>
        {ticks.map((t) => <g key={t}><line x1={PL} x2={W - PR} y1={y(t)} y2={y(t)} className="gl" /><text x={PL - 10} y={y(t) + 4} textAnchor="end" className="ax">{usd(t, true)}</text></g>)}
        {wi > 0 && <g><rect x={x(wi)} y={PT} width={W - PR - x(wi)} height={H - PT - PB} className="win" /><text x={W - PR - 8} y={PT + 14} textAnchor="end" className="ax win-t">Analysis window</text></g>}
        <path d={area} fill={`url(#${id})`} />
        <path d={line} className="ln" />
        {[0, Math.floor((points.length - 1) / 2), points.length - 1].map((i) => <text key={i} x={x(i)} y={H - 8} textAnchor={i === 0 ? 'start' : i === points.length - 1 ? 'end' : 'middle'} className="ax">{fmtDay(points[i].date)}</text>)}
        {hi != null && <g><line x1={x(hi)} x2={x(hi)} y1={PT} y2={H - PB} className="cross" /><circle cx={x(hi)} cy={y(vals[hi])} r="5" className="dot" /></g>}
      </svg>
      {hi != null && <div className="tip" style={{ left: `${(x(hi) / W) * 100}%`, transform: `translateX(${hi > points.length * 0.7 ? '-105%' : '12px'})` }}><b>{usd(vals[hi])}</b><span>{fmtDay(points[hi].date)}</span></div>}
      <table className="sr"><caption>{label}</caption><tbody>{points.map((p) => <tr key={p.date}><th>{p.date}</th><td>{p.amount}</td></tr>)}</tbody></table>
    </div>
  );
}

// Parts-of-whole as one segmented bar with a 2px surface gap between fills; the legend carries label + count so colour is never the only cue.
export function StackBar({ parts, label }) {
  const total = parts.reduce((s, p) => s + p.value, 0);
  return (
    <div>
      <div className="stack" role="img" aria-label={`${label}: ${parts.map((p) => `${p.label} ${p.value}`).join(', ')}`}>
        {parts.filter((p) => p.value).map((p) => <i key={p.label} className={`fill-${p.tone}`} style={{ flexGrow: p.value }} title={`${p.label}: ${p.value} (${Math.round((p.value / total) * 100)}%)`} />)}
      </div>
      <ul className="legend">{parts.map((p) => <li key={p.label}><span className={`dotk fill-${p.tone}`} />{p.label}<b>{p.value}</b></li>)}</ul>
    </div>
  );
}

export function HBars({ rows, empty = 'Nothing to show yet.' }) {
  if (!rows.length) return <div className="empty">{empty}</div>;
  const max = Math.max(...rows.map((r) => r.value)) || 1;
  return (
    <ul className="hbars">
      {rows.map((r) => (
        <li key={r.label} title={`${r.label}: ${r.text}${r.note ? ` · ${r.note}` : ''}`}>
          <span className="hl">{r.label}</span>
          <span className="track"><i style={{ width: `${Math.max((r.value / max) * 100, 2)}%` }} /></span>
          <b className="hv">{r.text}</b>
        </li>
      ))}
    </ul>
  );
}
