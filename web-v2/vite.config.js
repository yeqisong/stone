import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 一任务一端口对（AGENTS.md 分支策略）：BACKEND_ORIGIN/FRONTEND_PORT 可覆盖，
// 主环境缺省 8000/3000；worktree 任务用 8101/3101 等
const backend = process.env.BACKEND_ORIGIN || 'http://localhost:8000'
const port = Number(process.env.FRONTEND_PORT || 3000)

export default defineConfig({
  plugins: [vue()],
  // worktree 与主仓库共享 node_modules 软链时，依赖预构建缓存（.vite）不能共用，
  // 否则两个 dev server 互相踩 _metadata.json（v3.28 教训④）；旁路实例用 VITE_CACHE_DIR 隔离
  cacheDir: process.env.VITE_CACHE_DIR || 'node_modules/.vite',
  server: {
    port,
    // WSL/脚本整文件重写下 inotify 偶发漏事件 → dev server 一直吐旧编译结果。
    // 轮询监听开销可接受（本项目文件量小），换来「改完立刻生效」的确定性。
    watch: { usePolling: true, interval: 300 },
    proxy: { '/api': { target: backend, timeout: 120000, ws: true }, '/health': { target: backend, timeout: 120000 } }
  },
  build: { outDir: 'dist' }
})
