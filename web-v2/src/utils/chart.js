/**
 * 图表主题：ECharts 画在 canvas 上，读不到 CSS 变量。
 * App.vue 把主题色写到 <html> 的 --c-* 上，这里取真实色值。
 * 每次属性访问都读一次，主题切换后重绘即可拿到新值。
 */
function cv(name, fallback) {
  if (typeof document === 'undefined') return fallback
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

export function chartTheme() {
  return {
    up: cv('--c-up', '#c22b3f'),
    down: cv('--c-down', '#0a7a5a'),
    warn: cv('--c-warning', '#8a5a00'),
    info: cv('--c-info', '#c22b3f'),
    text: cv('--c-text', '#14161a'),
    dim: cv('--c-text-dim', '#5b6068'),
    faint: cv('--c-text-faint', '#6b7078'),
    line: cv('--c-border', 'rgba(20,22,26,.12)'),
    surface: cv('--c-card-bg', '#ffffff'),
    bg: cv('--c-bg', '#f6f5f1'),
    series: [
      cv('--c-series-1', '#c22b3f'),
      cv('--c-series-2', '#b26a00'),
      cv('--c-series-3', '#0a7a5a'),
      cv('--c-series-4', '#2f6fb3'),
      cv('--c-series-5', '#6b4fa8'),
      cv('--c-series-6', '#0e7490'),
    ],
  }
}

/** 取色器：T.up / T.series[3]，始终读当前主题 */
export const chartColors = new Proxy({}, {
  get: (_t, k) => chartTheme()[k],
})

/** 统一坐标轴：轴文本 11px，网格线用边框色 */
export function axisStyle(T, extra = {}) {
  return {
    axisLine: { lineStyle: { color: T.line } },
    axisTick: { show: false },
    axisLabel: { color: T.dim, fontSize: 11 },
    splitLine: { lineStyle: { color: T.line, type: 'dashed' } },
    ...extra,
  }
}
