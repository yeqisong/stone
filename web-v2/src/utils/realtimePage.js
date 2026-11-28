import { ref, computed, onMounted, onUnmounted } from 'vue'
import axios from 'axios'
import { useThemeStore } from '../stores/theme'
import { pollDelay } from './realtime'

const API = window.location.origin

/**
 * 实时页布局判定：驾驶舱（一屏放下）还是堆叠（整页滚动）。
 * 可用高度 ≈ 视口高 − 顶栏 56 − 导航 46 − 标题栏 61 − 内边距 46 ≈ 视口高 − 209；
 * 决策线 + 三区 + 分析带至少要 ~790px 才不憋屈，所以视口高 ≥ 1000 才走一屏模式。
 * 三区并排需要 ≥1100px 宽，否则单列。
 */
export function useRealtimeLayout() {
  const split = ref(false)
  const multiCol = ref(false)
  function update() {
    split.value = window.innerHeight >= 1000 && window.innerWidth >= 1100
    multiCol.value = window.innerWidth >= 1100
  }
  onMounted(() => { update(); window.addEventListener('resize', update) })
  onUnmounted(() => window.removeEventListener('resize', update))
  return { split, multiCol }
}

/**
 * 实时大盘聚合轮询（/api/market/realtime）：交易时段 15s、其余 60s（design/13 §4 口径）。
 * enabled 为 false（无 research.market.view）时不发请求；失败保留旧数据静默重试；
 * seq 守卫丢弃过期响应（手动刷新与定时器并发时只有最后一次生效）。
 */
export function useMarketRealtime(enabled) {
  const d = ref(null)
  const ztHist = ref([])
  const loading = ref(false)
  const failed = ref(false)   // 最近一次拉取失败（保留旧数据时界面要标出来，不能静默）
  let timer = null
  let histTimer = null
  let seq = 0
  const active = () => (typeof enabled === 'function' ? enabled() : enabled.value)

  async function load() {
    clearTimeout(timer)
    if (!active()) return
    const my = ++seq
    loading.value = !d.value
    try {
      const r = await axios.get(API + '/api/market/realtime', { timeout: 20000 })
      if (my !== seq) return
      d.value = r.data
      failed.value = false
    } catch (e) {
      if (my === seq) failed.value = true   // 保留旧数据，静默重试
    } finally {
      loading.value = false
    }
    timer = setTimeout(load, pollDelay('market', d.value?.session))
  }

  async function loadHist() {
    try {
      const r = await axios.get(API + '/api/market/realtime/zt-history', { params: { days: 120 }, timeout: 20000 })
      ztHist.value = r.data.days || []
    } catch (e) { /* 静默 */ }
  }

  onMounted(() => {
    load()
    histTimer = setInterval(() => { if (active()) loadHist() }, 300000)
  })
  onUnmounted(() => { seq++; clearTimeout(timer); clearInterval(histTimer) })
  return { d, ztHist, loading, failed, refresh: load }
}

/**
 * 图表 option 构造器：主题切换后强制重算。
 * chartColors 每次取色都读 getComputedStyle，本身不响应式——这里显式碰一下 isDark 建立依赖。
 */
export function useChartOption(builder) {
  const theme = useThemeStore()
  return computed(() => { void theme.isDark; return builder() })
}
