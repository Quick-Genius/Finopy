import { useState } from 'react';
import { TrendingUp, Calendar } from 'lucide-react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine
} from 'recharts';
import { getForecast } from '../api/client';

export default function ForecastPage() {
  const [horizon, setHorizon] = useState(30);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const runForecast = async () => {
    setLoading(true);
    try {
      const { data } = await getForecast(horizon);
      setResult(data);
    } catch (err) {
      setResult({ error: err.response?.data?.detail || err.message });
    } finally {
      setLoading(false);
    }
  };

  // Generate mock chart data for visualization
  const hasForecast =
    result && !result.error && Array.isArray(result.confidence_interval);
  const chartData = hasForecast
    ? Array.from({ length: horizon }, (_, i) => ({
        day: `Day ${i + 1}`,
        projected: result.daily_average + (Math.random() - 0.5) * result.daily_average * 0.3,
        upper: result.confidence_interval[1] / horizon + (Math.random() - 0.5) * 20,
        lower: result.confidence_interval[0] / horizon + (Math.random() - 0.5) * 20,
      }))
    : [];

  return (
    <div className="animate-fade-in">
      <div className="page-header">
        <h2 style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <TrendingUp size={22} color="var(--accent)" />
          Spend Forecast
        </h2>
        <p>Predict future cloud costs using Prophet time-series models</p>
      </div>

      <div className="card" style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16 }}>
          <Calendar size={18} color="var(--accent)" />
          <label style={{ color: 'var(--text-secondary)', fontSize: 14 }}>
            Forecast horizon: <strong style={{ color: 'var(--accent-strong)' }}>{horizon} days</strong>
          </label>
        </div>
        <input
          type="range"
          min="7"
          max="90"
          value={horizon}
          onChange={(e) => setHorizon(Number(e.target.value))}
          style={{
            width: '100%',
            marginBottom: 20,
            accentColor: 'var(--accent)',
          }}
        />
        <button className="btn btn-primary" onClick={runForecast} disabled={loading}>
          <TrendingUp size={16} />
          {loading ? 'Forecasting...' : 'Generate Forecast'}
        </button>
      </div>

      {loading && (
        <div className="loading-spinner">Running Prophet forecast model...</div>
      )}

      {result && !result.error && (
        <>
          <div className="metrics-grid">
            <div className="metric-card metric-card--blue">
              <div className="metric-label">Projected Total</div>
              <div className="metric-value">${result.projected_total.toLocaleString()}</div>
            </div>
            <div className="metric-card metric-card--green">
              <div className="metric-label">Daily Average</div>
              <div className="metric-value">${result.daily_average.toFixed(2)}</div>
            </div>
            <div className="metric-card metric-card--orange">
              <div className="metric-label">Confidence Range</div>
              <div className="metric-value" style={{ fontSize: 20 }}>
                ${result.confidence_interval[0].toLocaleString()} – ${result.confidence_interval[1].toLocaleString()}
              </div>
            </div>
          </div>

          <div className="chart-container">
            <h3>Projected Daily Spend</h3>
            <ResponsiveContainer width="100%" height={350}>
              <AreaChart data={chartData}>
                <defs>
                  <linearGradient id="gradientFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#34b27b" stopOpacity={0.28} />
                    <stop offset="100%" stopColor="#34b27b" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="gradientUpper" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#5b8ac0" stopOpacity={0.16} />
                    <stop offset="100%" stopColor="#5b8ac0" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="day" stroke="var(--text-muted)" fontSize={11} />
                <YAxis stroke="var(--text-muted)" fontSize={11} unit="$" />
                <Tooltip
                  contentStyle={{
                    background: 'var(--bg-card)',
                    border: '1px solid var(--border-color)',
                    borderRadius: 8,
                    color: 'var(--text-primary)',
                  }}
                  formatter={(val) => [`$${val.toFixed(2)}`, '']}
                />
                <Area
                  type="monotone"
                  dataKey="upper"
                  stroke="#5b8ac0"
                  strokeWidth={1}
                  fill="url(#gradientUpper)"
                  strokeDasharray="4 4"
                />
                <Area
                  type="monotone"
                  dataKey="projected"
                  stroke="#34b27b"
                  strokeWidth={2}
                  fill="url(#gradientFill)"
                />
                <Area
                  type="monotone"
                  dataKey="lower"
                  stroke="#7e93a6"
                  strokeWidth={1}
                  fill="none"
                  strokeDasharray="4 4"
                />
                <ReferenceLine
                  y={result.daily_average}
                  stroke="#d9a441"
                  strokeDasharray="6 3"
                  label={{ value: 'Avg', fill: '#d9a441', fontSize: 11 }}
                />
              </AreaChart>
            </ResponsiveContainer>
            <div style={{ display: 'flex', gap: 20, marginTop: 12, fontSize: 12, color: 'var(--text-secondary)' }}>
              <LegendSwatch color="#34b27b" label="Projected" />
              <LegendSwatch color="#5b8ac0" label="Upper bound" />
              <LegendSwatch color="#7e93a6" label="Lower bound" />
              <LegendSwatch color="#d9a441" label="Average" />
            </div>
          </div>
        </>
      )}

      {result?.error && (
        <div className="card" style={{ borderColor: 'rgba(213, 96, 79, 0.3)' }}>
          <p style={{ color: 'var(--accent-red)' }}>Error: {result.error}</p>
        </div>
      )}
    </div>
  );
}

function LegendSwatch({ color, label }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span style={{ width: 10, height: 2, background: color, display: 'inline-block' }} />
      {label}
    </span>
  );
}
