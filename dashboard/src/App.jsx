import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom';
import {
  ChartColumn,
  TrendingUp,
  Check,
  Clock3,
  Blocks,
} from 'lucide-react';
import AnalysisPage from './pages/AnalysisPage';
import ForecastPage from './pages/ForecastPage';
import ApprovalsPage from './pages/ApprovalsPage';
import HistoryPage from './pages/HistoryPage';
import IntegrationsPage from './pages/IntegrationsPage';
import './styles/global.css';
import './styles/layout.css';

const navItems = [
  { path: '/analysis', icon: ChartColumn, label: 'Analytics' },
  { path: '/forecast', icon: TrendingUp, label: 'Forecast' },
  { path: '/approvals', icon: Check, label: 'Approvals' },
  { path: '/history', icon: Clock3, label: 'History' },
  { path: '/integrations', icon: Blocks, label: 'Integrations' },
];

const ICON_PROPS = { size: 19, strokeWidth: 1.6 };

export default function App() {
  return (
    <BrowserRouter>
      <div className="app-layout">
        <aside className="sidebar">
          <div className="sidebar-brand sidebar-brand--no-icon">
            <div className="brand-mark" aria-hidden="true">
              <svg viewBox="-8 -8 226 368" fill="none" role="img" aria-label="Finopy logo">
                <g fill="#2B4BC3">
                  <rect x="52" y="0" width="158" height="64" rx="14" />
                  <rect x="0" y="96" width="64" height="64" rx="14" />
                  <rect x="50" y="32" width="64" height="96" />
                  <rect x="52" y="192" width="158" height="64" rx="14" />
                  <rect x="0" y="288" width="64" height="64" rx="14" />
                  <rect x="50" y="224" width="64" height="128" />
                </g>
              </svg>
            </div>
            <div className="brand-text">
              <h1>Finopy</h1>
              <span>Cost optimizer</span>
            </div>
          </div>

          <nav className="sidebar-nav">
            {navItems.map(({ path, icon: Icon, label }) => (
              <NavLink
                key={path}
                to={path}
                className={({ isActive }) =>
                  `nav-item ${isActive ? 'nav-item--active' : ''}`
                }
              >
                <Icon {...ICON_PROPS} />
                <span>{label}</span>
              </NavLink>
            ))}
          </nav>

          <div className="sidebar-footer">
            <div className="status-badge">
              <span className="status-dot" />
              <span>Agents ready</span>
            </div>
          </div>
        </aside>

        <main className="main-content">
          <Routes>
            <Route path="/" element={<Navigate to="/analysis" replace />} />
            <Route path="/analysis" element={<AnalysisPage />} />
            <Route path="/forecast" element={<ForecastPage />} />
            <Route path="/approvals" element={<ApprovalsPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/integrations" element={<IntegrationsPage />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
