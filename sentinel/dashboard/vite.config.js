import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// API_TARGET lets the dev server point at any running API (default: the compose-published port).
const target = process.env.API_TARGET || 'http://localhost:8000';
export default defineConfig({
  plugins: [react()],
  server: { port: 3000, proxy: { '/api': { target, rewrite: (p) => p.replace(/^\/api/, '') } } },
});
