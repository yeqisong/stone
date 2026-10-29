import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 3000,
    // WSL/脚本整文件重写下 inotify 偶发漏事件 → dev server 一直吐旧编译结果。
    // 轮询监听开销可接受（本项目文件量小），换来「改完立刻生效」的确定性。
    watch: { usePolling: true, interval: 300 },
    proxy: { '/api': { target: 'http://localhost:8000', timeout: 120000, ws: true }, '/health': { target: 'http://localhost:8000', timeout: 120000 } }
  },
  build: { outDir: 'dist' }
})
