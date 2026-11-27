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

// 横滑的派发节奏必须对齐 ECharts 原生 dataZoom 交互（拖滑块/滚轮）：原生先经 throttle
// 节流才派发。逢 wheel 事件必派发会让联动组每帧走一遍完整 update（全部系列重渲染），
// 反而比拖滑块卡得多——实测详情页 2 年区间帧率：拖滑块 56，逢事件必派发 43，本节奏 59。
//   周期自适应 = 2.5 × 上次派发耗时，夹在 [16, 100]ms：轻页面仍每帧更新，重页面自动降频
//   （100ms 即 ECharts 动画开启时的 throttle 默认值），让更新约占主线程四成
//   周期内的位移先累积、到点合并成一次派发——总位移守恒，内容跟手
//   animation.duration=0 是 ECharts 为 dataZoom 派发预留的动画覆盖口（update payload 的
//   animation 字段）：过渡动画期间联动组每帧都要重绘，实测去掉更流畅
const PAN_PERIOD_MIN = 16
const PAN_PERIOD_MAX = 100
const PAN_PERIOD_FACTOR = 2.5
const PAN_ANIM_OFF = { duration: 0 }

/** 挂载触控板手势定向，返回解绑函数（页面 onBeforeUnmount 调用） */
export function attachChartWheelGesture() {
  let chart = null        // 当前手势作用的图表
  let acc = 0             // 本周期内累积的横向位移（px）
  let timer = null        // 周期末尾的合并派发
  let nextAt = 0          // 下一次允许派发的时间戳
  let lastCost = 0        // 上次派发耗时，决定下一个周期的长短

  const flush = () => {
    timer = null
    const dx = acc
    acc = 0
    if (!chart || chart.isDisposed()) return
    const dz = chart.getOption()?.dataZoom?.[0]
    const start = dz?.start ?? 0, end = dz?.end ?? 100
    const span = end - start
    const t0 = performance.now()
    let period = PAN_PERIOD_MIN
    if (dz && span > 0 && !(start <= 0 && end >= 100)) {       // 全宽无处可滚
      // 内容跟手：横移一个图宽 = 窗口滚一个窗宽
      const ns = Math.min(Math.max(start + dx / (chart.getWidth() || 1) * span, 0), 100 - span)
      chart.dispatchAction({ type: 'dataZoom', start: +ns.toFixed(2), end: +(ns + span).toFixed(2), animation: PAN_ANIM_OFF })
      lastCost = performance.now() - t0
      period = Math.min(Math.max(lastCost * PAN_PERIOD_FACTOR, PAN_PERIOD_MIN), PAN_PERIOD_MAX)
    }
    nextAt = t0 + period
  }
  const dropPending = () => { acc = 0; if (timer) { clearTimeout(timer); timer = null } }

  const onWheel = (e) => {
    if (e.ctrlKey) { dropPending(); nextAt = 0; return }     // 捏合 → ECharts 原生缩放
    const c = chartFromEvent(e)
    if (!c) return                                         // 不在图表上 → 页面默认行为
    e.stopPropagation()                                    // 截停，别让 inside dataZoom 当缩放
    if (Math.abs(e.deltaX) <= Math.abs(e.deltaY)) return   // 竖滑 → 页面滚动（不拦默认）
    e.preventDefault()                                     // 横滑 → 滚动 dataZoom 窗口
    if (c !== chart) { chart = c; dropPending(); nextAt = 0 }
    // deltaMode 1=行 / 2=页（Firefox 鼠标滚轮）归一成像素
    acc += e.deltaX * (e.deltaMode === 1 ? 33 : e.deltaMode === 2 ? c.getWidth() : 1)
    const wait = nextAt - performance.now()
    if (wait <= 0) { if (timer) clearTimeout(timer); flush() }
    else if (!timer) timer = setTimeout(flush, wait)
  }
  document.addEventListener('wheel', onWheel, { capture: true, passive: false })
  return () => {
    dropPending()
    document.removeEventListener('wheel', onWheel, { capture: true })
  }
}
