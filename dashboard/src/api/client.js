import axios from 'axios';

// In dev, requests go to `/api/*` so Vite proxies them to the backend
// (see vite.config.js `proxy`). Set VITE_API_URL for production builds
// that serve the frontend separately from the API.
const API_BASE = import.meta.env.VITE_API_URL || '/api';

const api = axios.create({
  baseURL: API_BASE,
  timeout: 120000,
  headers: { 'Content-Type': 'application/json' },
});

export const analyzeCosts = (query) =>
  api.post('/analyze', { query });

export const askQuestion = (query) =>
  api.post('/ask', { query });

export const approveRecommendation = (id, approved) =>
  api.post('/approve', { recommendation_id: id, approved });

export const getRecommendations = (pendingOnly = false) =>
  api.get('/recommendations', { params: { pending_only: pendingOnly } });

export const getForecast = (horizonDays = 30) =>
  api.get('/forecast', { params: { horizon_days: horizonDays } });

export const getHealth = () =>
  api.get('/health');

export const getHistory = (limit = 20) =>
  api.get('/history/runs', { params: { limit } });

export const getIntegrations = () =>
  api.get('/integrations');

export default api;
