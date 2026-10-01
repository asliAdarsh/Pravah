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
  // `vite preview` serves the production build and normally needs the same /api
  // proxy as the dev server. A static build is the exception: there is no
  // backend, and `/api` holds the exported snapshot the console reads directly.
  // Proxying it turns every read into a connection error, so the proxy is
  // omitted when VITE_STATIC_DATA is set.
  ...(process.env.VITE_STATIC_DATA === '1'
    ? {}
    : {
        preview: {
          port: 5173,
          proxy: {
            '/api': {
              target: BACKEND_ORIGIN,
              changeOrigin: true,
            },
          },
        },
      }),
})
