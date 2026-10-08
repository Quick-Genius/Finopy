import { useState } from 'react';
import { Play, AlertTriangle, Target, DollarSign, BarChart3 } from 'lucide-react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell, PieChart, Pie, Legend
} from 'recharts';
import { analyzeCoasts } from '../api/client';

const SEVERITY_COLORS = {
  critical: '#d5604f',
  high: '#d9a441',
  medium: '#5b8ac0',
  low: '#34b27b',
};

export default function AnalysisPage() {
  const [query, setQuery] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const runAnalysis = async () => {
    setLoading(true);
    try {
      const { data } = await analyzeCoasts(query || 'Full cost optimization scan');
      setResult(data);
    } catch (err) {
      setResult({ error: err.response?.data?.detail || err.message });
    } finally {
      setLoading(false);
    }
  };

  const anomalyChartData = result?.anomalies?.map((a) => ({
    name: a.service.replace('Amazon ', ''),
    deviation: Math.abs(a.deviation_pct),
    severity: a.severity,
    actual: a.actual_cost,
    expected: a.expected_cost,
  })) || [];

  const savingsByType = result?.recommendations?.reduce((acc, r) => {
    const existing = acc.find((x) => x.name === r.action_type);
    if (existing) existing.value += r.estimated_savings_monthly;
    else acc.push({ name: r.action_type, value: r.estimated_savings_monthly });
    return acc;
  }, []) || [];

  const PIE_COLORS = ['#34b27b', '#5b8ac0', '#d9a441', '#d5604f', '#7e93a6', '#46c98f'];

  return (
    <div className="animate-fade-in">
      <div className="page-header">
        <h2 style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <BarChart3 size={22} color="var(--accent)" />
          Cost Analysis
        </h2>
        <p>Run the full Observe → Diagnose → Recommend agent pipeline</p>
      </div>

      <div className="card" style={{ marginBottom: 24 }}>
        <div className="input-group">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Focus area (e.g., 'Focus on EC2 and RDS')..."
          />
        </div>
        <button className="btn btn-primary" onClick={runAnalysis} disabled={loading}>
          <Play size={16} />
          {loading ? 'Running Pipeline...' : 'Run Analysis'}
        </button>
      </div>

      {loading && (
        <div className="loading-spinner">
          Running Observe → Diagnose → Recommend pipeline...
        </div>
      )}

      {result && !result.error && (
        <>
          {/* Metrics */}
          <div className="metrics-grid">
            <div className="metric-card metric-card--red">
              <div className="metric-label">Anomalies Detected</div>
              <div className="metric-value">{result.anomalies_count}</div>
            </div>
            <div className="metric-card metric-card--blue">
              <div className="metric-label">Recommendations</div>
              <div className="metric-value">{result.recommendations_count}</div>
            </div>
            <div className="metric-card metric-card--green">
              <div className="metric-label">Potential Savings</div>
              <div className="metric-value">
                ${result.total_potential_savings?.toFixed(0)}/mo
              </div>
            </div>
          </div>

          {/* Anomaly Chart */}
          {anomalyChartData.length > 0 && (
            <div className="chart-container">
              <h3>
                <AlertTriangle size={16} style={{ marginRight: 8, color: 'var(--accent-orange)' }} />
                Cost Anomalies by Service
              </h3>
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={anomalyChartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                  <XAxis dataKey="name" stroke="var(--text-muted)" fontSize={12} />
                  <YAxis stroke="var(--text-muted)" fontSize={12} unit="%" />
                  <Tooltip
                    contentStyle={{
                      background: 'var(--bg-card)',
                      border: '1px solid var(--border-color)',
                      borderRadius: 8,
                      color: 'var(--text-primary)',
                    }}
                  />
                  <Bar dataKey="deviation" radius={[6, 6, 0, 0]}>
                    {anomalyChartData.map((entry, i) => (
                      <Cell key={i} fill={SEVERITY_COLORS[entry.severity]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Savings Pie */}
          {savingsByType.length > 0 && (
            <div className="chart-container">
              <h3>
                <DollarSign size={16} style={{ marginRight: 8, color: 'var(--accent-green)' }} />
                Savings by Action Type
              </h3>
              <ResponsiveContainer width="100%" height={280}>
                <PieChart>
                  <Pie
                    data={savingsByType}
                    cx="50%"
                    cy="50%"
                    outerRadius={100}
                    dataKey="value"
                    label={({ name, value }) => `${name}: $${value.toFixed(0)}`}
                    labelLine={false}
                  >
                    {savingsByType.map((_, i) => (
                      <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Legend />
                  <Tooltip />
                </PieChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Recommendations List */}
          {result.recommendations?.length > 0 && (
            <div className="card">
              <h3 style={{ marginBottom: 16 }}>
                <Target size={16} style={{ marginRight: 8 }} />
                Recommendations
              </h3>
              {result.recommendations.map((rec) => (
                <div className="rec-card" key={rec.id}>
                  <span className={`badge badge--${rec.risk_level}`}>
                    {rec.risk_level}
                  </span>
                  <div className="rec-info">
                    <div className="rec-title">{rec.title}</div>
                    <div className="rec-desc">{rec.description}</div>
                  </div>
                  <div className="rec-savings">
                    ${rec.estimated_savings_monthly.toFixed(0)}/mo
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Report */}
          {result.report && (
            <div className="card" style={{ marginTop: 24 }}>
              <h3 style={{ marginBottom: 12 }}>Full Report</h3>
              <div className="report-content">{result.report}</div>
            </div>
          )}
        </>
      )}

      {result?.error && (
        <div className="card" style={{ borderColor: 'rgba(239, 68, 68, 0.3)' }}>
          <p style={{ color: 'var(--accent-red)' }}>Error: {result.error}</p>
        </div>
      )}
    </div>
  );
}
