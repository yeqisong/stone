import { createApp } from 'vue'
import FloatingWatchApp from '../components/FloatingWatchApp.vue'

let pipWindow = null
let app = null

export function canFloatWatch() {
  return typeof window !== 'undefined' && 'documentPictureInPicture' in window
}

/** 打开 Chrome Document Picture-in-Picture 悬浮盯盘窗（始终置顶，浏览器最小化也保留）。 */
export async function openFloatingWatch({ openDetail } = {}) {
  if (pipWindow && !pipWindow.closed) {
    pipWindow.focus()
    return pipWindow
  }
  if (!canFloatWatch()) {
    throw new Error('当前浏览器不支持浮窗盯盘（需要 Chrome/Edge 116+ 的 Document Picture-in-Picture）')
  }
  const width = Math.min(760, Math.max(430, Math.floor((window.screen?.availWidth || 1280) * 0.46)))
  const height = Math.min(680, Math.max(380, Math.floor((window.screen?.availHeight || 900) * 0.62)))
  const pip = await window.documentPictureInPicture.requestWindow({ width, height })
  pip.document.title = '浮窗盯盘'
  // 复用主窗口的主题变量与样式（含 Vue scoped CSS）
  // 注意：主题变量挂在 .app-root 上；只复制 documentElement 会拿不到 --c-bg 等变量
  const appRoot = document.querySelector('.app-root')
  pip.document.documentElement.style.cssText =
    document.documentElement.style.cssText + ';' + (appRoot?.style.cssText || '')
  pip.document.body.className = document.body.className
  pip.document.body.style.margin = '0'
  // Document PiP 的顶层窗口本身是不透明浏览器窗口，CSS 透明无法透出桌面；
  // 用主题背景色填满窗口，避免白底（真正透明需要原生悬浮窗）。
  pip.document.documentElement.style.background = 'var(--c-bg)'
  pip.document.body.style.background = 'var(--c-bg)'
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      const css = Array.from(sheet.cssRules || []).map(r => r.cssText).join('')
      if (!css) continue
      const style = pip.document.createElement('style')
      style.textContent = css
      pip.document.head.appendChild(style)
    } catch (e) {
      if (sheet.href) {
        const link = pip.document.createElement('link')
        link.rel = 'stylesheet'
        link.href = sheet.href
        pip.document.head.appendChild(link)
      }
    }
  }
  const root = pip.document.createElement('div')
  root.id = 'floating-watch-root'
  pip.document.body.appendChild(root)
  app = createApp(FloatingWatchApp, { openDetail: openDetail || null })
  app.mount(root)
  pip.addEventListener('pagehide', () => {
    try { app?.unmount() } catch (e) {}
    app = null
    pipWindow = null
  })
  pipWindow = pip
  return pip
}
