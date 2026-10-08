import { useState, useEffect } from 'react';
import { CheckCircle2, XCircle, Clock, Shield } from 'lucide-react';
import { getRecommendations, approveRecommendation } from '../api/client';

export default function ApprovalsPage() {
  const [recs, setRecs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(null);
  const [error, setError] = useState(null);

  const fetchPending = async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await getRecommendations(true);
      setRecs(data);
    } catch (err) {
      setRecs([]);
      setError(err.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchPending(); }, []);

  const handleAction = async (id, approved) => {
    setActionLoading(id);
    setError(null);
    try {
      await approveRecommendation(id, approved);
      setRecs((prev) => prev.filter((r) => r.id !== id));
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div className="animate-fade-in">
      <div className="page-header">
        <h2 style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <CheckCircle2 size={22} color="var(--accent)" />
          Approval Queue
        </h2>
        <p>Review and approve/reject optimization actions before execution</p>
      </div>

      {error && (
        <div className="card" style={{ borderColor: 'rgba(213, 96, 79, 0.3)', marginBottom: 16 }}>
          <p style={{ color: 'var(--accent-red)' }}>Error: {error}</p>
        </div>
      )}

      {loading && <div className="loading-spinner">Loading pending approvals...</div>}

      {!loading && recs.length === 0 && (
        <div className="card">
          <div className="empty-state">
            <Shield size={48} />
            <p>No pending approvals. The queue is clear.</p>
          </div>
        </div>
      )}

      {recs.map((rec) => (
        <div className="rec-card animate-slide-in" key={rec.id} style={{ padding: 24 }}>
          <div style={{ flex: '0 0 auto' }}>
            <span className={`badge badge--${rec.risk_level}`}>
              {rec.risk_level}
            </span>
          </div>

          <div className="rec-info" style={{ flex: 1 }}>
            <div className="rec-title" style={{ fontSize: 16, marginBottom: 6 }}>
              {rec.title}
            </div>
            <div className="rec-desc" style={{ marginBottom: 8 }}>
              {rec.description}
            </div>
            <div style={{ display: 'flex', gap: 16, fontSize: 12, color: 'var(--text-muted)' }}>
              <span>Type: <strong>{rec.action_type}</strong></span>
              <span>Resources: {rec.resource_ids?.length || 0}</span>
            </div>
          </div>

          <div className="rec-savings" style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
              Monthly savings
            </div>
            ${rec.estimated_savings_monthly.toFixed(0)}
          </div>

          <div className="rec-actions" style={{ flexDirection: 'column' }}>
            <button
              className="btn btn-success"
              onClick={() => handleAction(rec.id, true)}
              disabled={actionLoading === rec.id}
              style={{ fontSize: 12, padding: '8px 16px' }}
            >
              <CheckCircle2 size={14} />
              Approve
            </button>
            <button
              className="btn btn-danger"
              onClick={() => handleAction(rec.id, false)}
              disabled={actionLoading === rec.id}
              style={{ fontSize: 12, padding: '8px 16px' }}
            >
              <XCircle size={14} />
              Reject
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
