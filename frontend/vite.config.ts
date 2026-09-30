import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Backend dev server. The prototype ships no tile server either: the offset-well
// map is a hand-built SVG well location plan, so the app needs no external map
// or chart service in demo/hackathon environments.
const BACKEND_ORIGIN = process.env.PRAVAH_BACKEND_ORIGIN ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // Mirrors the `@/*` paths mapping in tsconfig.app.json.
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: BACKEND_ORIGIN,
        changeOrigin: true,
      },
    },
  },
  // `vite preview` serves the production build; it needs the same /api proxy as
  // the dev server or a built bundle cannot reach the backend.
  preview: {
    port: 5173,
    proxy: {
      '/api': {
        target: BACKEND_ORIGIN,
        changeOrigin: true,
      },
    },
  },
})
