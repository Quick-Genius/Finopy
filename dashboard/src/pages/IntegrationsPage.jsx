import { useEffect, useMemo, useState } from 'react';
import {
  Server, Cloud, Layers, MessageCircle, Mail, ClipboardList, Send,
} from 'lucide-react';
import { getIntegrations } from '../api/client';

const ICONS = {
  aws: Server,
  gcp: Cloud,
  azure: Layers,
  slack: MessageCircle,
  email: Mail,
  jira: ClipboardList,
  telegram: Send,
};

const STATUS_META = {
  connected: { label: 'Connected', className: 'badge--low', dot: '#0a7d3b' },
  not_configured: { label: 'Not connected', className: 'badge--medium', dot: '#86868b' },
  coming_soon: { label: 'Coming soon', className: 'badge--medium', dot: '#c7c7cc' },
};

const SECTIONS = [
  { id: 'cloud', title: 'Cloud platforms', hint: 'Where spend data comes from' },
  { id: 'communication', title: 'Communication', hint: 'Where alerts and approvals go' },
];

export default function IntegrationsPage() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const { data } = await getIntegrations();
        setItems(data);
      } catch (err) {
        setError(err.response?.data?.detail || err.message);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const connected = useMemo(
    () => items.filter((i) => i.status === 'connected').length,
    [items]
  );

  return (
    <div className="animate-fade-in">
      <div className="page-header">
        <h2>Integrations</h2>
        <p>
          {items.length > 0
            ? `${connected} of ${items.length} connected`
            : 'Connect your clouds and channels'}
        </p>
      </div>

      {error && (
        <div className="card" style={{ marginBottom: 16 }}>
          <p style={{ color: 'var(--accent-red)' }}>Error: {error}</p>
        </div>
      )}

      {loading && <div className="loading-spinner">Checking connections…</div>}

      {!loading && SECTIONS.map((section) => (
        <div key={section.id} style={{ marginBottom: 28 }}>
          <div className="section-head">
            <h3>{section.title}</h3>
            <span className="muted-note">{section.hint}</span>
          </div>
          <div className="integration-grid">
            {items
              .filter((i) => i.category === section.id)
              .map((item) => {
                const Icon = ICONS[item.id] || Cloud;
                const meta = STATUS_META[item.status] || STATUS_META.not_configured;
                return (
                  <div className="card integration-card" key={item.id}>
                    <div className="integration-top">
                      <span className="integration-icon">
                        <Icon size={19} strokeWidth={1.6} />
                      </span>
                      <span className={`badge ${meta.className}`}>
                        <span className="spike-dot" style={{ background: meta.dot, marginRight: 6 }} />
                        {meta.label}
                      </span>
                    </div>
                    <div className="integration-name">{item.name}</div>
                    <p className="integration-desc">{item.description}</p>
                    {item.capabilities?.length > 0 && (
                      <div className="integration-caps">
                        {item.capabilities.map((c) => (
                          <span className="cap-pill" key={c}>{c}</span>
                        ))}
                      </div>
                    )}
                    <code className="integration-hint">{item.setup_hint}</code>
                  </div>
                );
              })}
          </div>
        </div>
      ))}
    </div>
  );
}
