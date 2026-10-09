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
            <div className="brand-text">
              <h1>FinOps</h1>
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
