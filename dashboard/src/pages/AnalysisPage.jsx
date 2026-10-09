import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, Server, Gauge, Wallet, Activity } from 'lucide-react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell,
} from 'recharts';
import { analyzeCosts } from '../api/client';

const SEVERITY_COLORS = {
  critical: '#d70015',
  high: '#b25e09',
  medium: '#0071e3',
  low: '#0a7d3b',
};

const RANGES = [
  { id: 'today', label: 'Today' },
  { id: '7d', label: 'Past 7 days' },
  { id: '30d', label: 'Past 30 days' },
];

/* Recency mapped onto the demo anomaly set so the range toggle filters for real.
   EC2 spike is from today, RDS 3 days ago, S3 12 days ago, Lambda 21 days ago. */
const RECENCY_BY_SERVICE = {
  'Amazon EC2': 0,
  'Amazon RDS': 3,
  'Amazon S3': 12,
  'AWS Lambda': 21,
};

const RANGE_DAYS = { today: 1, '7d': 7, '30d': 30 };

/* Fleet model mirrors the backend narrative:
   ec2_advisor (idle <5% CPU, oversized max <40%) + demo recommendations. */
const FALLBACK_FLEET = [
  { id: 'i-0a1b2c3d4', name: 'web-batch-01', type: 'm5.xlarge', service: 'Amazon EC2', avgCpu: 2.1, maxCpu: 4.0, uptimePct: 100, state: 'idle' },
  { id: 'i-3d4e5f6g7', name: 'web-batch-02', type: 'm5.xlarge', service: 'Amazon EC2', avgCpu: 2.8, maxCpu: 5.1, uptimePct: 100, state: 'idle' },
  { id: 'i-6g7h8i9j0', name: 'loadtest-leftover', type: 'm5.xlarge', service: 'Amazon EC2', avgCpu: 1.9, maxCpu: 3.4, uptimePct: 99.98, state: 'idle' },
  { id: 'db-prod-analytics-01', name: 'analytics primary', type: 'db.r5.2xlarge', service: 'Amazon RDS', avgCpu: 22.0, maxCpu: 35.0, uptimePct: 99.95, state: 'oversized' },
  { id: 'i-prod-web-01', name: 'prod web 01', type: 'c6i.large', service: 'Amazon EC2', avgCpu: 62.0, maxCpu: 84.0, uptimePct: 99.99, state: 'healthy' },
  { id: 'i-prod-web-02', name: 'prod web 02', type: 'c6i.large', service: 'Amazon EC2', avgCpu: 58.0, maxCpu: 79.0, uptimePct: 99.99, state: 'healthy' },
  { id: 'i-prod-api-01', name: 'prod api 01', type: 'c6i.large', service: 'Amazon EC2', avgCpu: 55.0, maxCpu: 76.0, uptimePct: 99.97, state: 'healthy' },
  { id: 'i-dev-01', name: 'dev box 01', type: 't3.medium', service: 'Amazon EC2', avgCpu: 8.0, maxCpu: 31.0, uptimePct: 100, state: 'schedulable' },
  { id: 'i-staging-01', name: 'staging', type: 't3.medium', service: 'Amazon EC2', avgCpu: 11.0, maxCpu: 38.0, uptimePct: 99.9, state: 'schedulable' },
];

const STATE_META = {
  healthy: { label: 'Healthy', dot: '#0a7d3b' },
  idle: { label: 'Idle', dot: '#d70015' },
  oversized: { label: 'Oversized', dot: '#b25e09' },
  schedulable: { label: 'Part-time', dot: '#0071e3' },
};

function shortService(name) {
  return (name || '').replace('Amazon ', '').replace('AWS ', '');
}

export default function AnalysisPage() {
  const [query, setQuery] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [range, setRange] = useState('30d');
  const [severity, setSeverity] = useState('all');
  const [idleOnly, setIdleOnly] = useState(false);
  const [autoRan, setAutoRan] = useState(false);

  const runAnalysis = async () => {
    setLoading(true);
    try {
      const { data } = await analyzeCosts(query || 'Full cost optimization scan');
      setResult(data);
    } catch (err) {
      setResult({ error: err.response?.data?.detail || err.message });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!autoRan) {
      setAutoRan(true);
      runAnalysis();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const days = RANGE_DAYS[range];

  const spikes = useMemo(() => {
    const list = (result?.anomalies || []).map((a) => ({
      ...a,
      short: shortService(a.service),
      ageDays: RECENCY_BY_SERVICE[a.service] ?? 30,
    }));
    return list
      .filter((a) => a.ageDays < days)
      .filter((a) => (severity === 'all' ? true : a.severity === severity))
      .sort((x, y) => y.deviation_pct - x.deviation_pct);
  }, [result, days, severity]);

  const spikeChartData = spikes.map((a) => ({
    name: a.short,
    deviation: Math.abs(a.deviation_pct),
    severity: a.severity,
  }));

  const efficiency = useMemo(() => {
    const list = (result?.anomalies || []).map((a) => {
      const score = a.actual_cost > 0 ? Math.max(0, Math.min(100, (a.expected_cost / a.actual_cost) * 100)) : 100;
      return { service: shortService(a.service), score, actual: a.actual_cost, expected: a.expected_cost, severity: a.severity };
    });
    const totActual = list.reduce((s, x) => s + x.actual, 0);
    const totExpected = list.reduce((s, x) => s + x.expected, 0);
    const overall = totActual > 0 ? Math.round((totExpected / totActual) * 100) : 100;
    return { byService: list.sort((a, b) => a.score - b.score), overall };
  }, [result]);

  const fleet = useMemo(() => {
    const rows = FALLBACK_FLEET;
    const filtered = idleOnly ? rows.filter((r) => r.state === 'idle' || r.state === 'oversized') : rows;
    const healthy = rows.filter((r) => r.state === 'healthy').length;
    const availability = rows.length ? Math.round((healthy / rows.length) * 1000) / 10 : 100;
    const avgUptime = rows.length
      ? Math.round((rows.reduce((s, r) => s + r.uptimePct, 0) / rows.length) * 100) / 100
      : 100;
    const idleCount = rows.filter((r) => r.state === 'idle').length;
    return { rows: filtered, availability, avgUptime, idleCount, total: rows.length };
  }, [idleOnly]);

  const waste = useMemo(() => {
    const recs = result?.recommendations || [];
    const sum = (fn) => recs.filter(fn).reduce((s, r) => s + (r.estimated_savings_monthly || 0), 0);
    return [
      { label: 'Idle compute', value: sum((r) => r.action_type === 'terminate'), hint: `${fleet.idleCount} instances < 5% CPU` },
      { label: 'Rightsizing', value: sum((r) => r.action_type === 'rightsize'), hint: 'Peak under 40% CPU' },
      { label: 'Commitments', value: sum((r) => r.action_type === 'reserve'), hint: 'Steady-state 24/7' },
      { label: 'Scheduling', value: sum((r) => r.action_type === 'schedule'), hint: 'Nights + weekends off' },
    ];
  }, [result, fleet.idleCount]);

  const rangeSpend = useMemo(() => {
    // Anchored on the demo baseline: ~$614/day, $18,420.50 / 30d.
    const daily = 614;
    if (range === 'today') return daily * 1.32;
    if (range === '7d') return daily * 7 * 1.12;
    return 18420.5;
  }, [range]);

  const kpis = [
    { icon: Wallet, label: `Spend · ${RANGES.find((r) => r.id === range).label.toLowerCase()}`, value: `$${Math.round(rangeSpend).toLocaleString()}`, tone: '' },
    { icon: Activity, label: 'Cost spikes', value: String(spikes.length), tone: spikes.length > 0 ? 'is-bad' : 'is-good' },
    { icon: Gauge, label: 'Cost efficiency', value: `${efficiency.overall}%`, tone: efficiency.overall >= 75 ? 'is-good' : '' },
    { icon: Server, label: 'Fleet available', value: `${fleet.availability}%`, tone: '' },
  ];

  return (
    <div className="animate-fade-in">
      <div className="page-header analytics-head">
        <div>
          <h2>Analytics</h2>
          <p>Availability, efficiency and spikes across your fleet</p>
        </div>
        <div className="segmented" role="tablist" aria-label="Time range">
          {RANGES.map((r) => (
            <button
              key={r.id}
              role="tab"
              aria-selected={range === r.id}
              className={`segmented-btn ${range === r.id ? 'is-active' : ''}`}
              onClick={() => setRange(r.id)}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      <div className="card toolbar-card">
        <div className="input-group" style={{ marginBottom: 0, flex: 1 }}>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Focus area (e.g. EC2 and RDS)…"
          />
        </div>
        <button className="btn btn-primary" onClick={runAnalysis} disabled={loading}>
          <ArrowRight size={16} strokeWidth={2} />
          {loading ? 'Running…' : 'Run analysis'}
        </button>
      </div>

      <div className="toggle-row">
        <div className="chip-group" aria-label="Severity filter">
          {['all', 'critical', 'high', 'medium', 'low'].map((s) => (
            <button
              key={s}
              className={`chip ${severity === s ? 'is-active' : ''}`}
              onClick={() => setSeverity(s)}
            >
              {s === 'all' ? 'All severities' : s}
            </button>
          ))}
        </div>
        <button
          className={`switch ${idleOnly ? 'is-on' : ''}`}
          onClick={() => setIdleOnly((v) => !v)}
          aria-pressed={idleOnly}
        >
          <span className="switch-thumb" />
          <span className="switch-label">Waste only</span>
        </button>
      </div>

      {loading && <div className="loading-spinner">Running Observe → Diagnose → Recommend…</div>}

      {result && !result.error && (
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

          <div className="analytics-grid">
            <div className="chart-container spike-card">
              <div className="card-title-row">
                <h3>Cost spikes · {RANGES.find((r) => r.id === range).label.toLowerCase()}</h3>
                <span className="count-pill">{spikes.length}</span>
              </div>
              {spikeChartData.length > 0 && (
                <ResponsiveContainer width="100%" height={180}>
                  <BarChart data={spikeChartData} layout="vertical" margin={{ left: 8, right: 16 }}>
                    <CartesianGrid stroke="#ececf1" horizontal={false} />
                    <XAxis type="number" hide />
                    <YAxis type="category" dataKey="name" width={92} tick={{ fontSize: 12, fill: '#515154' }} tickLine={false} axisLine={false} />
                    <Tooltip
                      cursor={{ fill: '#f5f5f7' }}
                      contentStyle={{
                        background: '#fff', border: '1px solid #e8e8ed', borderRadius: 12,
                        color: '#1d1d1f', boxShadow: '0 4px 24px rgba(0,0,0,0.08)', fontSize: 13,
                      }}
                      formatter={(v) => [`+${v}% over expected`, 'Spike']}
                    />
                    <Bar dataKey="deviation" radius={[4, 8, 8, 4]} maxBarSize={18} background={{ fill: '#f5f5f7', radius: 8 }}>
                      {spikeChartData.map((entry, i) => (
                        <Cell key={i} fill={SEVERITY_COLORS[entry.severity]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
              <div className="spike-list">
                {spikes.map((a) => (
                  <div className="spike-row" key={`${a.service}-${a.region}`}>
                    <span className="spike-dot" style={{ background: SEVERITY_COLORS[a.severity] }} />
                    <div className="spike-main">
                      <div className="spike-title">{a.short} · {a.region}</div>
                      <div className="spike-sub">
                        ${a.actual_cost.toLocaleString()} vs ${a.expected_cost.toLocaleString()} expected
                        {' · '}{a.ageDays === 0 ? 'today' : `${a.ageDays}d ago`}
                      </div>
                    </div>
                    <span className={`badge badge--${a.severity}`}>+{Math.round(a.deviation_pct)}%</span>
                  </div>
                ))}
                {spikes.length === 0 && (
                  <p className="muted-note">No spikes in this window. Try a wider range.</p>
                )}
              </div>
            </div>

            <div className="chart-container efficiency-card">
              <h3>Cost efficiency</h3>
              <div className="efficiency-score">
                <span className="efficiency-number">{efficiency.overall}%</span>
                <span className="efficiency-caption">of expected spend</span>
              </div>
              <div className="efficiency-list">
                {efficiency.byService.map((s) => (
                  <div className="efficiency-row" key={s.service}>
                    <div className="efficiency-top">
                      <span>{s.service}</span>
                      <span className={s.score < 70 ? 'is-bad' : ''}>{Math.round(s.score)}%</span>
                    </div>
                    <div className="efficiency-bar">
                      <div
                        className="efficiency-fill"
                        style={{
                          width: `${s.score}%`,
                          background: s.score < 60 ? '#d70015' : s.score < 80 ? '#b25e09' : '#0a7d3b',
                        }}
                      />
                    </div>
                  </div>
                ))}
              </div>
              <p className="muted-note">Efficiency = expected ÷ actual spend. Under 70% is worth a look.</p>
            </div>
          </div>

          <div className="card fleet-card">
            <div className="card-title-row">
              <h3 className="card-title">Instance availability</h3>
              <span className="muted-note">{fleet.total} tracked · {fleet.avgUptime}% avg uptime</span>
            </div>
            <div className="fleet-head">
              <span>Instance</span><span>Status</span><span>CPU avg</span><span>Uptime 30d</span>
            </div>
            {fleet.rows.map((r) => (
              <div className="fleet-row" key={r.id}>
                <div className="fleet-id">
                  <strong>{r.name}</strong>
                  <span>{r.id} · {r.type}</span>
                </div>
                <span className="fleet-status">
                  <span className="spike-dot" style={{ background: STATE_META[r.state].dot }} />
                  {STATE_META[r.state].label}
                </span>
                <span className="fleet-cpu">
                  <span className="efficiency-bar slim">
                    <span
                      className="efficiency-fill"
                      style={{ width: `${Math.min(100, r.avgCpu)}%`, background: r.avgCpu < 5 ? '#d70015' : r.avgCpu < 40 ? '#b25e09' : '#0a7d3b' }}
                    />
                  </span>
                  {r.avgCpu}%
                </span>
                <span className="fleet-uptime">{r.uptimePct}%</span>
              </div>
            ))}
          </div>

          <div className="waste-grid">
            {waste.map((w) => (
              <div className="metric-card kpi-card" key={w.label}>
                <div className="metric-label">{w.label}</div>
                <div className="metric-value">${Math.round(w.value).toLocaleString()}<span className="per-mo">/mo</span></div>
                <div className="waste-hint">{w.hint}</div>
              </div>
            ))}
          </div>

          {result.recommendations?.length > 0 && (
            <div className="card">
              <h3 style={{ marginBottom: 14, fontSize: 15, fontWeight: 600 }}>Recommendations</h3>
              {result.recommendations.map((rec) => (
                <div className="rec-card" key={rec.id}>
                  <span className={`badge badge--${rec.risk_level}`}>{rec.risk_level}</span>
                  <div className="rec-info">
                    <div className="rec-title">{rec.title}</div>
                    <div className="rec-desc">{rec.description}</div>
                  </div>
                  <div className="rec-savings">${rec.estimated_savings_monthly.toFixed(0)}/mo</div>
                </div>
              ))}
            </div>
          )}
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
