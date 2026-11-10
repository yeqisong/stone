import { defineStore } from 'pinia'
import { ref, computed, onUnmounted } from 'vue'
import { darkTheme } from 'naive-ui'

const STORAGE_KEY = 'theme-mode'

export const useThemeStore = defineStore('theme', () => {
  const mode = ref(localStorage.getItem(STORAGE_KEY) || 'light')
  const _now = ref(new Date())  // 每分钟更新，驱动 auto 模式的时间检测
  let _timer = null
let _sysListener = null

  // auto 模式：优先跟随系统 prefers-color-scheme；无偏好时按时间 8:00-19:00 → light
  function _isAutoDark() {
    try {
      const mq = window.matchMedia('(prefers-color-scheme: dark)')
      if (mq.media !== '(prefers-color-scheme: dark)') {
        // 浏览器不支持该媒体查询，回退时间方案
      } else {
        return mq.matches
      }
    } catch (e) { /* SSR/隐私模式兜底 */ }
    const h = _now.value.getHours()
    return h < 8 || h >= 19
  }

  const isDark = computed(() => {
    if (mode.value === 'dark') return true
    if (mode.value === 'light') return false
    return _isAutoDark()  // auto
  })

  const naiveTheme = computed(() => isDark.value ? darkTheme : null)

  function toggle() {
    if (mode.value === 'light') mode.value = 'dark'
    else if (mode.value === 'dark') mode.value = 'auto'
    else mode.value = 'light'
    localStorage.setItem(STORAGE_KEY, mode.value)
  }

  function startAutoTimer() {
    if (_timer) return
    _timer = setInterval(() => {
      const prev = isDark.value
      _now.value = new Date()
      // 结果翻转时才触发响应式更新（避免每分钟无谓重算 colors 链）
      if (isDark.value !== prev) _now.value = new Date(_now.value.getTime())
    }, 60000)
    // auto 模式监听系统主题变化即时切换
    try {
      const mq = window.matchMedia('(prefers-color-scheme: dark)')
      _sysListener = (e) => { if (mode.value === 'auto') _now.value = new Date() }
      mq.addEventListener?.('change', _sysListener)
    } catch (e) {}
  }

  function stopAutoTimer() {
    if (_timer) { clearInterval(_timer); _timer = null }
    try {
      const mq = window.matchMedia('(prefers-color-scheme: dark)')
      mq.removeEventListener?.('change', _sysListener)
    } catch (e) {}
  }

  // 使用此 store 的组件卸载时清理
  try { onUnmounted(stopAutoTimer) } catch(e) {}

  const modeLabel = computed(() => {
    return { light: 'sun', dark: 'moon', auto: 'monitor' }[mode.value] || 'monitor'
  })

  const modeHint = computed(() => {
    return { light: '浅色', dark: '深色', auto: '自动' }[mode.value] || ''
  })

  // ── 主题色常量映射 ──
  const colors = computed(() => isDark.value ? {
    bg: '#121417',
    bgHeader: '#1a1d21',
    text: '#f2f3f5',
    textDim: '#a6abb3',
    textDimmer: '#8b9199',
    textFaint: '#6d737b',
    textFainter: '#3a4047',
    border: 'rgba(255,255,255,.10)',
    borderLight: 'rgba(255,255,255,.06)',
    cardBg: '#1a1d21',
    cardBgHover: '#22262b',
    success: '#a6abb3',
    error: '#ff7a85',
    warning: '#f2b64c',
    info: '#ff7a85',
    accent: '#ff7a85',
    accentWeak: '#3a2026',
    up: '#ff7a85',
    down: '#3ddc97',
    chartBg: '#121417',
    inputBg: '#22262b',
  } : {
    bg: '#f6f5f1',
    bgHeader: '#ffffff',
    text: '#14161a',
    textDim: '#5b6068',
    textDimmer: '#666b73',
    textFaint: '#767b83',
    textFainter: '#c9c8bf',
    border: 'rgba(20,22,26,.12)',
    borderLight: 'rgba(20,22,26,.07)',
    cardBg: '#ffffff',
    cardBgHover: '#f0efe9',
    success: '#5b6068',
    error: '#c22b3f',
    warning: '#8a5a00',
    info: '#c22b3f',
    accent: '#c22b3f',
    accentWeak: '#fbeaec',
    up: '#c22b3f',
    down: '#0a7a5a',
    chartBg: '#ffffff',
    inputBg: '#f0efe9',
  })

  return { mode, isDark, naiveTheme, colors, toggle, modeLabel, modeHint, startAutoTimer, stopAutoTimer }
})