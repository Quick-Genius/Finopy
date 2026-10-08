import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const api = axios.create({
  baseURL: API_BASE,
  timeout: 120000,
  headers: { 'Content-Type': 'application/json' },
});

export const analyzeCoasts = (query) =>
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

export default api;
