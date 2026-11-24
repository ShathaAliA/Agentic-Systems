import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The FastAPI backend binds 127.0.0.1:8000 (Backend/docs/authentication.md).
// Proxying keeps the browser on one origin, so no CORS and no cookies are involved.
const backend = { target: 'http://127.0.0.1:8000', changeOrigin: false }

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/auth': backend,
      '/api': backend,
      '/me': backend,
      '/admin': backend,
    },
  },
})
