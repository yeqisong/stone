import { defineStore } from 'pinia'
import { ref, computed, onUnmounted } from 'vue'
import { darkTheme } from 'naive-ui'

const STORAGE_KEY = 'theme-mode'

export const useThemeStore = defineStore('theme', () => {
  const mode = ref(localStorage.getItem(STORAGE_KEY) || 'light')
  const _now = ref(new Date())  // 每分钟更新，驱动 auto 模式的时间检测
  let _timer = null

  // auto 模式：8:00-19:00 → light，否则 dark
  function _isAutoDark() {
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
    _timer = setInterval(() => { _now.value = new Date() }, 60000)
  }

  function stopAutoTimer() {
    if (_timer) { clearInterval(_timer); _timer = null }
  }

  // 使用此 store 的组件卸载时清理
  try { onUnmounted(stopAutoTimer) } catch(e) {}

  const modeLabel = computed(() => {
    return { light: '☀️', dark: '🌙', auto: '🌓' }[mode.value] || '🌓'
  })

  const modeHint = computed(() => {
    return { light: '浅色', dark: '深色', auto: '自动' }[mode.value] || ''
  })

  // ── 主题色常量映射 ──
  const colors = computed(() => isDark.value ? {
    bg: '#101014',
    bgHeader: '#1a1a1e',
    text: '#fff',
    textDim: 'rgba(255,255,255,.45)',
    textDimmer: 'rgba(255,255,255,.38)',
    textFaint: 'rgba(255,255,255,.25)',
    textFainter: 'rgba(255,255,255,.15)',
    border: 'rgba(255,255,255,.09)',
    borderLight: 'rgba(255,255,255,.06)',
    cardBg: 'rgba(255,255,255,.03)',
    cardBgHover: 'rgba(255,255,255,.05)',
    success: '#10b981',
    error: '#ef4444',
    warning: '#f59e0b',
    info: '#2080f0',
    chartBg: '#101014',
    inputBg: 'rgba(255,255,255,.08)',
  } : {
    bg: '#f5f6f8',
    bgHeader: '#fff',
    text: '#1a1a2e',
    textDim: 'rgba(0,0,0,.55)',
    textDimmer: 'rgba(0,0,0,.4)',
    textFaint: 'rgba(0,0,0,.25)',
    textFainter: 'rgba(0,0,0,.1)',
    border: 'rgba(0,0,0,.1)',
    borderLight: 'rgba(0,0,0,.06)',
    cardBg: 'rgba(0,0,0,.02)',
    cardBgHover: 'rgba(0,0,0,.04)',
    success: '#10b981',
    error: '#ef4444',
    warning: '#d97706',
    info: '#2563eb',
    chartBg: '#fff',
    inputBg: 'rgba(0,0,0,.04)',
  })

  return { mode, isDark, naiveTheme, colors, toggle, modeLabel, modeHint, startAutoTimer, stopAutoTimer }
})