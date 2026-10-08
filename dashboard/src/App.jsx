import { useState } from 'react';
import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  MessageSquare,
  BarChart3,
  TrendingUp,
  CheckCircle2,
  History,
  Zap,
  DollarSign,
} from 'lucide-react';
import AskPage from './pages/AskPage';
import AnalysisPage from './pages/AnalysisPage';
import ForecastPage from './pages/ForecastPage';
import ApprovalsPage from './pages/ApprovalsPage';
import HistoryPage from './pages/HistoryPage';
import './styles/global.css';
import './styles/layout.css';

const navItems = [
  { path: '/', icon: MessageSquare, label: 'Ask AI' },
  { path: '/analysis', icon: BarChart3, label: 'Analysis' },
  { path: '/forecast', icon: TrendingUp, label: 'Forecast' },
  { path: '/approvals', icon: CheckCircle2, label: 'Approvals' },
  { path: '/history', icon: History, label: 'History' },
];

export default function App() {
  return (
    <BrowserRouter>
      <div className="app-layout">
        <aside className="sidebar">
          <div className="sidebar-brand">
            <div className="brand-icon">
              <DollarSign size={24} />
            </div>
            <div className="brand-text">
              <h1>FinOps</h1>
              <span>AI Optimizer</span>
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
                <Icon size={20} />
                <span>{label}</span>
              </NavLink>
            ))}
          </nav>

          <div className="sidebar-footer">
            <div className="status-badge">
              <Zap size={14} />
              <span>Agents Ready</span>
            </div>
          </div>
        </aside>

        <main className="main-content">
          <Routes>
            <Route path="/" element={<AskPage />} />
            <Route path="/analysis" element={<AnalysisPage />} />
            <Route path="/forecast" element={<ForecastPage />} />
            <Route path="/approvals" element={<ApprovalsPage />} />
            <Route path="/history" element={<HistoryPage />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
