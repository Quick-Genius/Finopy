import { useState, useEffect } from 'react';
import { History, RefreshCw } from 'lucide-react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Scatter, ScatterChart, ZAxis
} from 'recharts';
import { getHistory } from '../api/client';

export default function HistoryPage() {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchHistory = async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await getHistory();
      setRuns(data);
    } catch (err) {
      setRuns([]);
      setError(err.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchHistory(); }, []);

  const chartData = runs.map((r) => ({
    date: new Date(r.started_at).toLocaleDateString(),
    savings: r.total_savings_identified,
    anomalies: r.anomalies_found,
    recs: r.recommendations_generated,
  }));

  return (
    <div className="animate-fade-in">
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h2 style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <History size={22} color="var(--accent)" />
            Agent Run History
          </h2>
          <p>Track all past optimization cycles and their outcomes</p>
        </div>
        <button className="btn btn-outline" onClick={fetchHistory}>
          <RefreshCw size={14} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="card" style={{ borderColor: 'rgba(213, 96, 79, 0.3)', marginBottom: 16 }}>
          <p style={{ color: 'var(--accent-red)' }}>Error: {error}</p>
        </div>
      )}

      {loading && <div className="loading-spinner">Loading history...</div>}

      {!loading && runs.length === 0 && (
        <div className="card">
          <div className="empty-state">
            <History size={48} />
            <p>No agent runs recorded yet. Run an analysis to populate this view.</p>
          </div>
        </div>
      )}

      {chartData.length > 0 && (
        <div className="chart-container">
          <h3>Savings Identified Over Time</h3>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="date" stroke="var(--text-muted)" fontSize={11} />
              <YAxis stroke="var(--text-muted)" fontSize={11} unit="$" />
              <Tooltip
                contentStyle={{
                  background: 'var(--bg-card)',
                  border: '1px solid var(--border-color)',
                  borderRadius: 8,
                  color: 'var(--text-primary)',
                }}
              />
              <Line
                type="monotone"
                dataKey="savings"
                stroke="#34b27b"
                strokeWidth={2}
                dot={{ fill: '#34b27b', r: 4 }}
                activeDot={{ r: 6, fill: '#46c98f' }}
              />
              <Line
                type="monotone"
                dataKey="anomalies"
                stroke="#d5604f"
                strokeWidth={1}
                strokeDasharray="4 4"
                dot={{ fill: '#d5604f', r: 3 }}
              />
            </LineChart>
          </ResponsiveContainer>
          <div style={{ display: 'flex', gap: 20, marginTop: 12, fontSize: 12, color: 'var(--text-secondary)' }}>
            <LegendSwatch color="#34b27b" label="Savings ($)" />
            <LegendSwatch color="#d5604f" label="Anomalies" />
          </div>
        </div>
      )}

      {/* Run Table */}
      {runs.length > 0 && (
        <div className="card">
          <h3 style={{ marginBottom: 16 }}>All Runs</h3>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-color)' }}>
                  <th style={thStyle}>Date</th>
                  <th style={thStyle}>Trigger</th>
                  <th style={thStyle}>Status</th>
                  <th style={thStyle}>Anomalies</th>
                  <th style={thStyle}>Recs</th>
                  <th style={thStyle}>Savings</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id} style={{ borderBottom: '1px solid var(--border-color)' }}>
                    <td style={tdStyle}>
                      {new Date(run.started_at).toLocaleString()}
                    </td>
                    <td style={tdStyle}>
                      <span className="badge badge--medium">{run.trigger}</span>
                    </td>
                    <td style={tdStyle}>
                      <span className={`badge badge--${run.status === 'completed' ? 'low' : 'high'}`}>
                        {run.status}
                      </span>
                    </td>
                    <td style={tdStyle}>{run.anomalies_found}</td>
                    <td style={tdStyle}>{run.recommendations_generated}</td>
                    <td style={{ ...tdStyle, color: 'var(--accent-green)', fontWeight: 600 }}>
                      ${run.total_savings_identified.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

const thStyle = {
  textAlign: 'left',
  padding: '12px 16px',
  color: 'var(--text-muted)',
  fontWeight: 500,
  fontSize: 11,
  textTransform: 'uppercase',
  letterSpacing: '0.5px',
};

const tdStyle = {
  padding: '12px 16px',
  color: 'var(--text-secondary)',
};

function LegendSwatch({ color, label }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span style={{ width: 10, height: 2, background: color, display: 'inline-block' }} />
      {label}
    </span>
  );
}
