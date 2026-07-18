import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { tanstackRouter } from '@tanstack/router-plugin/vite'
import { fileURLToPath, URL } from 'node:url'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  // In dev the app calls the same-origin `/api` prefix; Vite proxies it to the backend.
  // This mirrors the old Next.js `/api/:path*` rewrite, so the backend needs no extra
  // CORS setup for localhost. In prod, set VITE_API_URL to the backend origin instead.
  const proxyTarget = env.VITE_API_PROXY_TARGET || 'https://178.128.127.5.nip.io'

  return {
    plugins: [
      // Router plugin must come before the React plugin.
      tanstackRouter({ target: 'react', autoCodeSplitting: true }),
      react(),
      tailwindcss(),
    ],
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    server: {
      port: 3001,
      proxy: {
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
          secure: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
  }
})
