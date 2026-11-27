import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 一任务一端口对（AGENTS.md 分支策略）：BACKEND_ORIGIN/FRONTEND_PORT 可覆盖，
// 主环境缺省 8000/3000；worktree 任务用 8101/3101 等
const backend = process.env.BACKEND_ORIGIN || 'http://localhost:8000'
const port = Number(process.env.FRONTEND_PORT || 3000)

export default defineConfig({
  plugins: [vue()],
  server: {
    port,
    // WSL/脚本整文件重写下 inotify 偶发漏事件 → dev server 一直吐旧编译结果。
    // 轮询监听开销可接受（本项目文件量小），换来「改完立刻生效」的确定性。
    watch: { usePolling: true, interval: 300 },
    proxy: { '/api': { target: backend, timeout: 120000, ws: true }, '/health': { target: backend, timeout: 120000 } }
  },
  build: { outDir: 'dist' }
})
