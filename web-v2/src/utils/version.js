/**
 * 前端版本自检——解决「改了代码但页面还是上一版」。
 *
 * 原理：构建产物 dist/index.html 里 JS 哈希每次 `vite build` 都会变，
 * 后端 /api/version 暴露这个哈希；页面加载时记住基线，之后每 20 秒比对：
 * - 变了且页面在后台（hidden）或用户没在输入 → 自动刷新；
 * - 否则显示右下角提示条，点击刷新（不打断正在输入的内容）。
 * dev(:3000) 由 vite HMR 负责，这里也不会误伤（哈希不变即无动作）。
 */
export function startVersionWatch({ intervalMs = 20000, onUpdate } = {}) {
  let base = null
  let stopped = false

  async function tick() {
    if (stopped) return
    try {
      const r = await fetch('/api/version', { cache: 'no-store' })
      if (!r.ok) return
      const j = await r.json()
      const build = j && j.build
      if (!build || build === 'dev') return
      if (base === null) { base = build; return }
      if (build !== base) {
        base = build
        onUpdate && onUpdate(build)
      }
    } catch (e) { /* 后端未起/网络抖动：下次再试 */ }
  }

  tick()
  const timer = setInterval(tick, intervalMs)
  return () => { stopped = true; clearInterval(timer) }
}

/** 当前是否适合直接刷新（不在输入、页面在后台） */
export function canAutoReload() {
  if (typeof document === 'undefined') return false
  if (document.visibilityState === 'hidden') return true
  const el = document.activeElement
  const typing = el && (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT')
  return !typing
}
