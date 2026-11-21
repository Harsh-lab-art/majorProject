import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// /api calls are proxied to Khushbu's Express API (npm start in omnitrace-api, port 4000)
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': 'http://localhost:4000' } },
  test: { environment: 'node' },
});
