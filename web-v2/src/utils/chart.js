import * as echarts from 'echarts'

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

// ── 触控板双指手势定向（个股详情页 / 大盘页）──────────────────────────────
// 触控板手势在浏览器统一映射为 wheel 事件：横滑=deltaX、竖滑=deltaY、
// 捏合=ctrlKey+deltaY。ECharts inside dataZoom 默认把任何 wheel 都当缩放，
// 所以在 document 捕获阶段（先于 zrender 收到）按手势分类：
//   双指横滑（|deltaX|>|deltaY| 且无 ctrl）→ dataZoom 窗口平移；全宽（0~100）原地不动
//   双指竖滑 → 只截停不 preventDefault，交还浏览器滚动页面（.main-content）
//   双指捏合（ctrlKey）→ 完全放行，ECharts 保持原有「捏合缩放数据范围」行为
// document 级委托的好处：不逐图绑定，v-if 重建图表容器后依然生效。
function chartFromEvent(e) {
  let n = e.target
  while (n && n.nodeType === 1 && n !== document) {
    // echarts.init 会在容器 el 上登记 _echarts_instance_；dispose 后查注册表返回 falsy
    if (n.hasAttribute && n.hasAttribute('_echarts_instance_')) {
      return echarts.getInstanceByDom(n) || null
    }
    n = n.parentElement
  }
  return null
}

function panChartWindow(chart, e) {
  const dz = chart.getOption()?.dataZoom?.[0]
  if (!dz) return
  const start = dz.start ?? 0
  const end = dz.end ?? 100
  const span = end - start
  if (start <= 0 && end >= 100) return          // 全宽无处可滚
  const w = chart.getWidth() || 1
  // deltaMode 1=行 / 2=页（Firefox 鼠标滚轮）归一成像素
  const dx = e.deltaX * (e.deltaMode === 1 ? 33 : e.deltaMode === 2 ? w : 1)
  // 内容跟手：横移一个图宽 = 窗口滚一个窗宽
  const ns = Math.min(Math.max(start + (dx / w) * span, 0), 100 - span)
  chart.dispatchAction({ type: 'dataZoom', start: +ns.toFixed(2), end: +(ns + span).toFixed(2) })
}

/** 挂载触控板手势定向，返回解绑函数（页面 onBeforeUnmount 调用） */
export function attachChartWheelGesture() {
  const onWheel = (e) => {
    if (e.ctrlKey) return                                    // 捏合 → ECharts 原生缩放
    const chart = chartFromEvent(e)
    if (!chart) return                                       // 不在图表上 → 页面默认行为
    e.stopPropagation()                                      // 截停，别让 inside dataZoom 当缩放
    if (Math.abs(e.deltaX) <= Math.abs(e.deltaY)) return     // 竖滑 → 页面滚动（不拦默认）
    e.preventDefault()                                       // 横滑 → 滚动 dataZoom 窗口
    panChartWindow(chart, e)
  }
  document.addEventListener('wheel', onWheel, { capture: true, passive: false })
  return () => document.removeEventListener('wheel', onWheel, { capture: true })
}
