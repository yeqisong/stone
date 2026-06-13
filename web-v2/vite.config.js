import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: { 
    port: 3000,
    proxy: { '/api': { target: 'http://localhost:8000', timeout: 120000, ws: true }, '/health': { target: 'http://localhost:8000', timeout: 120000 } }
  },
  build: { outDir: 'dist' }
})
