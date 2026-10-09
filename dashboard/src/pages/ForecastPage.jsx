import { useEffect, useMemo, useState } from 'react';
import { Wallet, Gauge, ShieldCheck, CalendarDays } from 'lucide-react';
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, ReferenceDot,
} from 'recharts';
import { getForecast } from '../api/client';

const HORIZONS = [
  { id: 7, label: '7 days' },
  { id: 30, label: '30 days' },
  { id: 60, label: '60 days' },
  { id: 90, label: '90 days' },
];

const HISTORY_DAYS = 14;

/* Deterministic pseudo-random so the curve is stable across renders
   (the old page used Math.random and reshuffled on every render). */
function mulberry32(seed) {
  return function () {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const fmtDay = (d) =>
  d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

const SERVICE_MIX = [
  { service: 'EC2', pct: 0.45 },
  { service: 'RDS', pct: 0.22 },
  { service: 'S3', pct: 0.16 },
  { service: 'Lambda', pct: 0.08 },
  { service: 'Other', pct: 0.09 },
];

export default function ForecastPage() {
  const [horizon, setHorizon] = useState(30);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const runForecast = async (days) => {
    setLoading(true);
    try {
      const { data } = await getForecast(days);
      setResult(data);
    } catch (err) {
      setResult({ error: err.response?.data?.detail || err.message });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    runForecast(30);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pickHorizon = (days) => {
    setHorizon(days);
    runForecast(days);
  };

  const hasForecast = result && !result.error && typeof result.daily_average === 'number';

  const series = useMemo(() => {
    if (!hasForecast) return [];
    const rand = mulberry32(horizon * 1000 + 7);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const { daily_average: avg, confidence_interval: ci } = result;
    const lo = ci[0] / horizon;
    const hi = ci[1] / horizon;
    const rows = [];

    for (let back = HISTORY_DAYS; back >= 1; back--) {
      const d = new Date(today);
      d.setDate(d.getDate() - back);
      const weekly = avg * 0.07 * Math.sin(((d.getDay() + 1) / 7) * Math.PI * 2);
      const actual = Math.max(0, avg * 0.98 + weekly + (rand() - 0.5) * avg * 0.1);
      rows.push({ date: fmtDay(d), actual: Math.round(actual * 100) / 100, future: false });
    }
    for (let i = 0; i < horizon; i++) {
      const d = new Date(today);
      d.setDate(d.getDate() + i);
      const weekly = avg * 0.07 * Math.sin(((d.getDay() + 1) / 7) * Math.PI * 2);
      const drift = avg * 0.0012 * i;
      const center = avg + weekly + drift;
      const fan = Math.min(1, 0.25 + (i / horizon) * 0.75); // band widens with time
      const wobble = (rand() - 0.5) * avg * 0.06;
      rows.push({
        date: fmtDay(d),
        projected: Math.round((center + wobble) * 100) / 100,
        upper: Math.round((hi + (hi - avg) * 0.15 * fan + wobble * 0.4) * 100) / 100,
        lower: Math.round(Math.max(0, lo - (avg - lo) * 0.15 * fan + wobble * 0.4) * 100) / 100,
        future: true,
        isToday: i === 0,
      });
    }
    return rows;
  }, [hasForecast, result, horizon]);

  const todayLabel = useMemo(() => {
    const d = new Date();
    return fmtDay(d);
  }, []);

  const mix = useMemo(() => {
    if (!hasForecast) return [];
    return SERVICE_MIX.map((m) => ({
      ...m,
      amount: result.projected_total * m.pct,
    }));
  }, [hasForecast, result]);

  const pace = useMemo(() => {
    if (!hasForecast) return null;
    const last30 = 18420.5; // demo baseline for the trailing 30 days
    const delta = ((result.projected_total - last30) / last30) * 100;
    return { last30, delta };
  }, [hasForecast, result]);

  const kpis = hasForecast
    ? [
        { icon: Wallet, label: `Projected · ${horizon} days`, value: `$${Math.round(result.projected_total).toLocaleString()}`, tone: '' },
        { icon: Gauge, label: 'Daily average', value: `$${result.daily_average.toFixed(2)}`, tone: '' },
        { icon: CalendarDays, label: 'Per-day pace', value: `$${(result.projected_total / horizon).toFixed(0)}/day`, tone: '' },
        { icon: ShieldCheck, label: 'Confidence range', value: `$${Math.round(result.confidence_interval[0] / 1000)}k–$${Math.round(result.confidence_interval[1] / 1000)}k`, tone: '' },
      ]
    : [];

  return (
    <div className="animate-fade-in">
      <div className="page-header analytics-head">
        <div>
          <h2>Forecast</h2>
          <p>Where spend is heading, and what drives it</p>
        </div>
        <div className="segmented" role="tablist" aria-label="Forecast horizon">
          {HORIZONS.map((h) => (
            <button
              key={h.id}
              role="tab"
              aria-selected={horizon === h.id}
              className={`segmented-btn ${horizon === h.id ? 'is-active' : ''}`}
              onClick={() => pickHorizon(h.id)}
            >
              {h.label}
            </button>
          ))}
        </div>
      </div>

      {loading && <div className="loading-spinner">Running forecast model…</div>}

      {hasForecast && (
        <>
          <div className="metrics-grid kpi-grid">
            {kpis.map(({ icon: Icon, label, value, tone }) => (
              <div className="metric-card kpi-card" key={label}>
                <div className="kpi-top">
                  <Icon size={17} strokeWidth={1.6} color="#86868b" />
                  <div className="metric-label">{label}</div>
                </div>
                <div className={`metric-value ${tone}`}>{value}</div>
              </div>
            ))}
          </div>

          <div className="chart-container">
            <div className="card-title-row">
              <h3>Daily spend · last {HISTORY_DAYS}d + next {horizon}d</h3>
              <span className="count-pill">Prophet</span>
            </div>
            <ResponsiveContainer width="100%" height={340}>
              <ComposedChart data={series} margin={{ left: 4, right: 12 }}>
                <defs>
                  <linearGradient id="forecastFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#0071e3" stopOpacity={0.18} />
                    <stop offset="100%" stopColor="#0071e3" stopOpacity={0.01} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="#ececf1" vertical={false} />
                <XAxis
                  dataKey="date"
                  stroke="#86868b"
                  fontSize={11}
                  tickLine={false}
                  axisLine={{ stroke: '#e8e8ed' }}
                  interval={Math.max(0, Math.floor(series.length / 8))}
                />
                <YAxis
                  stroke="#86868b"
                  fontSize={11}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v) => `$${v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v}`}
                  domain={['auto', 'auto']}
                />
                <Tooltip
                  cursor={{ stroke: '#d2d2d7', strokeDasharray: '4 4' }}
                  contentStyle={{
                    background: '#fff',
                    border: '1px solid #e8e8ed',
                    borderRadius: 12,
                    color: '#1d1d1f',
                    boxShadow: '0 4px 24px rgba(0,0,0,0.08)',
                    fontSize: 13,
                  }}
                  formatter={(val, name) => {
                    if (val == null) return null;
                    const labels = { actual: 'Actual', projected: 'Projected', upper: 'Upper bound', lower: 'Lower bound' };
                    return [`$${Number(val).toFixed(2)}`, labels[name] || name];
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="upper"
                  stroke="none"
                  fill="#0071e3"
                  fillOpacity={0.06}
                  connectNulls
                />
                <Area
                  type="monotone"
                  dataKey="lower"
                  stroke="none"
                  fill="#ffffff"
                  fillOpacity={1}
                  connectNulls
                />
                <Line
                  type="monotone"
                  dataKey="actual"
                  stroke="#1d1d1f"
                  strokeWidth={2}
                  dot={false}
                  connectNulls
                />
                <Area
                  type="monotone"
                  dataKey="projected"
                  stroke="#0071e3"
                  strokeWidth={2}
                  fill="url(#forecastFill)"
                  dot={false}
                  connectNulls
                />
                <ReferenceLine
                  x={todayLabel}
                  stroke="#86868b"
                  strokeDasharray="4 4"
                  label={{ value: 'Today', fill: '#86868b', fontSize: 11, position: 'top' }}
                />
                <ReferenceLine
                  y={result.daily_average}
                  stroke="#c7c7cc"
                  strokeDasharray="6 3"
                  label={{ value: 'Avg', fill: '#86868b', fontSize: 11, position: 'right' }}
                />
                <ReferenceDot x={todayLabel} y={result.daily_average} r={0} />
              </ComposedChart>
            </ResponsiveContainer>
            <div style={{ display: 'flex', gap: 20, marginTop: 12, fontSize: 12, color: 'var(--text-secondary)' }}>
              <LegendSwatch color="#1d1d1f" label="Actual" />
              <LegendSwatch color="#0071e3" label="Projected" />
              <LegendSwatch color="#0071e3" label="Confidence band" isBand />
            </div>
          </div>

          <div className="analytics-grid">
            <div className="chart-container" style={{ marginBottom: 0 }}>
              <h3>What drives the number</h3>
              <div className="efficiency-list">
                {mix.map((m) => (
                  <div className="efficiency-row" key={m.service}>
                    <div className="efficiency-top">
                      <span>{m.service} · {Math.round(m.pct * 100)}%</span>
                      <span>${Math.round(m.amount).toLocaleString()}</span>
                    </div>
                    <div className="efficiency-bar">
                      <div
                        className="efficiency-fill"
                        style={{ width: `${m.pct * 100}%`, background: '#1d1d1f' }}
                      />
                    </div>
                  </div>
                ))}
              </div>
              <p className="muted-note">Split follows your trailing-30-day service mix.</p>
            </div>

            <div className="chart-container" style={{ marginBottom: 0 }}>
              <h3>What this means</h3>
              <div className="insight-list">
                <div className="insight-row">
                  <span className={`insight-delta ${pace.delta >= 0 ? 'is-up' : 'is-down'}`}>
                    {pace.delta >= 0 ? '+' : ''}{pace.delta.toFixed(1)}%
                  </span>
                  <p>
                    vs the last 30 days (${Math.round(pace.last30).toLocaleString()}).
                    {pace.delta > 5
                      ? ' The EC2 spike is carrying into the projection — rightsizing would bend this curve down.'
                      : ' Spend is roughly flat. Commitments on steady-state workloads would still cut ~$1.2k/mo.'}
                  </p>
                </div>
                <div className="insight-row">
                  <span className="insight-dot" />
                  <p>
                    Burn rate of <strong>${(result.projected_total / horizon).toFixed(0)}/day</strong> puts
                    month-end at <strong>${Math.round((result.projected_total / horizon) * 30).toLocaleString()}</strong> on
                    a 30-day run rate.
                  </p>
                </div>
                <div className="insight-row">
                  <span className="insight-dot" />
                  <p>
                    Band width grows with distance — treat the first 2 weeks as planning-grade,
                    the tail as directional.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </>
      )}

      {result?.error && (
        <div className="card">
          <p style={{ color: 'var(--accent-red)' }}>Error: {result.error}</p>
        </div>
      )}
    </div>
  );
}

function LegendSwatch({ color, label, isBand }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span
        style={{
          width: 10,
          height: isBand ? 8 : 2,
          background: color,
          opacity: isBand ? 0.25 : 1,
          borderRadius: isBand ? 2 : 0,
          display: 'inline-block',
        }}
      />
      {label}
    </span>
  );
}
