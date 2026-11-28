import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { sourceShaFile } from './vite-plugins/sourceSha'

export default defineConfig({
  plugins: [react(), sourceShaFile()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // Proxy API calls to FastAPI mock by default
      '/api': {
        target: process.env.MOCK_API_ORIGIN || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
