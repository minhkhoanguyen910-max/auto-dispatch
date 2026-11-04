import { defineConfig, type ProxyOptions } from 'vite';
import react from '@vitejs/plugin-react';

// Same layout as production (nginx.conf): /api/* goes to FastAPI with the prefix stripped
const proxy: Record<string, ProxyOptions> = {
  '/api': {
    target: 'http://localhost:8100',
    rewrite: (path) => path.replace(/^\/api/, ''),
    // Traccar posts positions to the API directly; never expose that endpoint (false = 404)
    bypass: (req) => (req.url?.startsWith('/api/traccar/') ? false : undefined),
  },
};

export default defineConfig({
  plugins: [react()],
  server: { proxy },
  // `npm run preview` (running without Docker) serves the built app in place of nginx
  preview: { proxy, host: true, port: 8090, strictPort: true },
});
