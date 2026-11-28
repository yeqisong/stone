<template>
  <!--
    实时（盘中作战台，design/14）：把依靠盘中实时数据的三块——分时市值、实时大盘、盘中雷达/买点检查——
    按交易纪律顺序放到一屏：决策线（能不能做）→ 盘面（看环境）→ 持仓（先处理风险）→ 机会（后找机会）→ 分析带（验证板块）。
  -->
  <div class="rt-page" :class="{ 'rt-stack': !split, 'rt-zoom': zoomed }">
    <RtDecisionStrip :d="marketD" :cand="cand" :can-radar="canRadar" :loading="marketLoading" :failed="marketFailed"
                     @focus="focusZone" @refresh="refreshAll" />

    <!-- 三区（决策顺序） -->
    <div v-if="hasWork && !zoomed" class="rt-work" :style="workCols">
      <RtMarketZone v-if="canMarket" ref="marketZone" :d="marketD" @open-tab="openTab" />
      <RtHoldingsZone v-if="canRadar" :rows="radarRows" :asof="radarAt" :data-date="radarDataDate"
                      :today="todayStr" :poll-sec="radarPollSec" :alerts="itAlerts" :alerts-on="canAlerts"
                      :loading="radarLoading" :error="radarError" :stacked="!split" @show-detail="showDetail" />
      <RtOpportunityZone v-if="canRadar" :cand="cand" :loading="candLoading" :error="candError"
                         :gate="marketD?.gate"
                         @check="openCheck" @go-watch="nav.switchTab('p')" @go-portfolio="nav.switchTab('p')" />
    </div>

    <RtAnalysisBand v-if="bandTabs.length" v-model:tab="bandTab" :tabs="bandTabs" :d="marketD" :zt-hist="ztHist"
                   :big-idx="bigIdx" :map-date="mapDate" :zoomed="zoomed" :stacked="!split"
                   @update:big-idx="bigIdx = $event" @update:zoom="zoomed = $event"
                   @show-detail="showDetail" @pick="onPick" />

    <!-- 树图叶子快捷菜单：原地「买点检查 / 个股详情」，不跳离实时页 -->
    <n-dropdown trigger="manual" :show="pick.show" :x="pick.x" :y="pick.y" placement="bottom-start"
                :options="pickOptions" @select="onPickSelect" @clickoutside="pick.show = false" />

    <BuyCheck v-model:show="buyShow" :preset="buyPreset" />
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { NDropdown } from 'naive-ui'
import axios from 'axios'
import RtDecisionStrip from './realtime/RtDecisionStrip.vue'
import RtMarketZone from './realtime/RtMarketZone.vue'
import RtHoldingsZone from './realtime/RtHoldingsZone.vue'
import RtOpportunityZone from './realtime/RtOpportunityZone.vue'
import RtAnalysisBand from './realtime/RtAnalysisBand.vue'
import BuyCheck from './BuyCheck.vue'
import { useAuthStore } from '../stores/auth'
import { useNavStore } from '../stores/nav'
import { rememberDetailNavList } from '../utils/detailNav'
import { addWsListener } from '../utils/ws'
import { bjToday, buyPresetOf, latestWeekday, localSession, pollDelay } from '../utils/realtime'
import { useMarketRealtime, useRealtimeLayout } from '../utils/realtimePage'

const emit = defineEmits(['show-detail'])
const nav = useNavStore()
const auth = useAuthStore()
const API = window.location.origin

// 页面入口由容器节点 research.realtime.view 控制；三个分区各自受既有节点二次控制，缺节点不渲染
const canMarket = computed(() => auth.can('research.market.view'))
const canTreemap = computed(() => auth.can('research.treemap.view'))
const canRadar = computed(() => auth.can('research.radar.view'))
// 今日盘中提醒块复用既有 risk.alerts.view（/api/risk/alerts?intraday=true），无新端点
const canAlerts = computed(() => auth.can('risk.alerts.view'))
const hasWork = computed(() => canMarket.value || canRadar.value)

const { split, multiCol } = useRealtimeLayout()
const { d: marketD, ztHist, loading: marketLoading, failed: marketFailed, refresh: refreshMarket } = useMarketRealtime(canMarket)

// 交易时段：盘面接口给了就用它的（含节假日），否则按客户端时钟粗判——只用来决定轮询快慢
const session = computed(() => marketD.value?.session || localSession())
const todayStr = computed(() => bjToday())

// ── 分析带：tab 按权限过滤；URL 里的 tab 无权时回落到第一个可见 tab ──
const BAND_TABS = [
  { k: 'map', can: canTreemap },
  { k: 'idx', can: canMarket }, { k: 'boards', can: canMarket },
  { k: 'zt', can: canMarket }, { k: 'turn', can: canMarket },
]
const bandTabs = computed(() => BAND_TABS.filter(t => t.can.value).map(t => t.k))
const bandTab = computed({
  get: () => (bandTabs.value.includes(nav.rtTab) ? nav.rtTab : (bandTabs.value[0] || '')),
  set: v => { if (v && v !== nav.rtTab) nav.rtTab = v },
})
// 指数分时大图选中项与分析带放大态都在 nav（URL ?tab=idx&idx=…&zoom=1）：刷新/分享不丢
const bigIdx = computed({
  get: () => nav.rtIdx,
  set: v => { nav.rtIdx = v || '' },
})
const zoomed = computed({
  get: () => nav.rtZoom,
  set: v => { nav.rtZoom = !!v },
})
// 资金地图日期 = 盘面数据的真实所属交易日：休市日/盘前各源都回退成最近交易日，地图要跟着落在同一天，
// 否则周六打开是一片「今日暂无快照」。盘面未到（后端刚重启、缓存为空）或无权限时兜底「最近工作日」
// （周末回退到周五，不含节假日）——比直接用「今天」好，缓存变热后 watch 会自动切准。
const mapDate = computed(() => marketD.value?.data_date || latestWeekday())

const workCols = computed(() => {
  if (!multiCol.value) return { gridTemplateColumns: 'minmax(0, 1fr)' }
  if (canMarket.value && !canRadar.value) return { gridTemplateColumns: 'minmax(0, 1fr)' }
  if (!canMarket.value && canRadar.value) return { gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' }
  // 盘面（四张指数小图）和持仓（雷达表要列宽）各多给一点；机会是列表，窄一些也够
  return { gridTemplateColumns: 'minmax(0, 1.15fr) minmax(0, 1.15fr) minmax(0, 1fr)' }
})

// ── 盘中雷达：关注池实时排序（后端另有 10s 用户级缓存）──
const radarRows = ref([])
const radarAt = ref('')
const radarDataDate = ref('')
const radarLoading = ref(false)
const radarError = ref(false)
let radarTimer = null
let radarSeq = 0
async function loadRadar() {
  clearTimeout(radarTimer)
  if (!canRadar.value) return
  const my = ++radarSeq          // seq 守卫：手动刷新与轮询并发时旧响应不得覆盖新响应
  radarLoading.value = true
  try {
    const r = await axios.get(API + '/api/stock/radar')
    if (my !== radarSeq) return
    radarRows.value = r.data?.items || []
    radarAt.value = r.data?.asof || ''
    radarDataDate.value = r.data?.data_date || ''
    radarError.value = false
  } catch (e) {
    // 保留上一帧并标出来：数据缺失是检查结果，不能静默当成「一切正常」
    if (my === radarSeq) radarError.value = true
  } finally {
    if (my === radarSeq) radarLoading.value = false
  }
  radarTimer = setTimeout(loadRadar, pollDelay('radar', session.value))
}
// 表头标注的刷新间隔随时段变化（盘中 15s / 收盘 60s），不能写死「15s 刷新」
const radarPollSec = computed(() => Math.round(pollDelay('radar', session.value) / 1000))

// ── 买点候选：关注池四道闸门扫描 ──
const cand = ref(null)
const candLoading = ref(false)
const candError = ref(false)
let candTimer = null
let candSeq = 0
async function loadCandidates() {
  clearTimeout(candTimer)
  if (!canRadar.value) return
  const my = ++candSeq
  candLoading.value = true
  try {
    const r = await axios.get(API + '/api/buy/candidates', { timeout: 30000 })
    if (my !== candSeq) return
    cand.value = r.data || { allowed: false, items: [], watch: [] }
    candError.value = false
  } catch (e) {
    if (my === candSeq) candError.value = true   // 同上：保留上一帧结论
  } finally {
    if (my === candSeq) candLoading.value = false
  }
  candTimer = setTimeout(loadCandidates, pollDelay('cand', session.value))
}

// ── 今日盘中提醒（个人 it%）：复用 /api/risk/alerts?intraday=true。
// Chrome 通知是瞬时的，错过即不可见；盘中提醒要能在持仓区回看（按权限渲染）。
const itAlerts = ref([])
let alertsTimer = null
let alertsSeq = 0
async function loadAlerts() {
  clearTimeout(alertsTimer)
  if (!canAlerts.value) return
  const my = ++alertsSeq
  try {
    const r = await axios.get(API + '/api/risk/alerts', { params: { intraday: true, limit: 30 } })
    if (my !== alertsSeq) return
    const today = todayStr.value
    itAlerts.value = (r.data || []).filter(a => String(a.created_at || '').slice(0, 10) === today)
  } catch (e) { /* 保留上一帧；提醒列表失败不打扰主链路 */ }
  alertsTimer = setTimeout(loadAlerts, pollDelay('alerts', session.value))
}

function refreshAll() {
  refreshMarket()
  loadRadar()
  loadCandidates()
  loadAlerts()
}

// ── 联动 ──
const marketZone = ref(null)
function focusZone(zone) {
  // 决策线「今日异动」→ 把盘面区的异动流滚进视野
  if (zone === 'market') marketZone.value?.revealAlerts?.()
}
function openTab(tab, payload) {
  if (tab === 'idx' && payload) bigIdx.value = payload
  bandTab.value = tab
  zoomed.value = false
}
function showDetail(code, list) {
  if (!code) return
  rememberDetailNavList(list)
  emit('show-detail', code)
}

// 树图叶子 → 点击处的快捷菜单
const pick = reactive({ show: false, x: 0, y: 0, code: '', name: '' })
const pickOptions = computed(() => [
  { key: 'title', label: `${pick.name || '—'}  ${pick.code}`, disabled: true },
  { key: 'check', label: '买点检查（四道闸门）' },
  { key: 'detail', label: '个股详情' },
])
function onPick(p) {
  if (!p || !p.code) return
  Object.assign(pick, p, { show: true })
}
function onPickSelect(key) {
  pick.show = false
  if (key === 'check') openCheck(buyPresetOf(pick))
  else if (key === 'detail') showDetail(pick.code, radarRows.value.map(r => r.code))
}

// 买点检查弹窗（候选行 / 树图叶子 / 手动输入共用）
const buyShow = ref(false)
const buyPreset = ref(null)
function openCheck(preset) {
  buyPreset.value = preset
  buyShow.value = true
}

// 盘中提醒（it%）由 watcher 10s 一轮驱动，比雷达轮询更早看到异动 → 收到即补刷一次（5s 节流）
let lastWsRefresh = 0
const offWs = addWsListener(data => {
  if (!data || data.type !== 'risk_alert') return
  const kinds = (data.alerts || []).map(a => String(a.kind || ''))
  if (!kinds.some(k => k.startsWith('it'))) return
  const now = Date.now()
  if (now - lastWsRefresh < 5000) return
  lastWsRefresh = now
  loadRadar()
  loadAlerts()   // WS 刚推过的提醒立即进「今日盘中提醒」列表，不等下一轮轮询
})

// mk% Chrome 通知点击 → 进实时页并滚到「今日盘面异动」（nav.rtFocus 一次性提示）
watch(() => nav.rtFocus, (v) => {
  if (!v) return
  nav.rtFocus = ''
  if (v === 'market' && canMarket.value) nextTick(() => focusZone('market'))
})

onMounted(() => {
  // 通知点击发生在其它页面时 rtFocus 在挂载前就已写入，watch 不会触发——挂载时消费一次
  if (nav.rtFocus === 'market' && canMarket.value) nextTick(() => focusZone('market'))
  nav.rtFocus = ''
  loadRadar(); loadCandidates(); loadAlerts()
})
onUnmounted(() => {
  offWs()
  clearTimeout(radarTimer)
  clearTimeout(candTimer)
  clearTimeout(alertsTimer)
})
</script>

<style>
/* 三区共用的分区外壳：分区内容在子组件里，外壳样式统一在这里定义 */
.rt-page .rt-zone {
  display: flex; flex-direction: column; min-width: 0; min-height: 0; overflow: hidden;
  background: var(--c-card-bg); border: 1px solid var(--c-border); border-radius: var(--radius);
}
.rt-page .rt-zh {
  display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-shrink: 0;
  min-height: 44px; padding: 0 10px 0 12px; border-bottom: 1px solid var(--c-border);
}
.rt-page .rt-zt { display: flex; align-items: baseline; gap: 8px; min-width: 0; white-space: nowrap; }
.rt-page .rt-zt b { font-size: var(--text-title); font-weight: 600; color: var(--c-text); }
.rt-page .rt-zt span { font-size: var(--text-micro); color: var(--c-text-faint); overflow: hidden; text-overflow: ellipsis; }
.rt-page .rt-zr { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.rt-page .rz-chip {
  padding: 1px 7px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  font-size: var(--text-micro); color: var(--c-text-dim); white-space: nowrap; font-variant-numeric: tabular-nums;
}
.rt-page .rz-chip b { color: var(--c-text); font-weight: 600; }
.rt-page .rz-chip.rz-hot { border-color: var(--c-text-faint); }
.rt-page .rz-chip.rz-hot.rz-stop, .rt-page .rz-chip.rz-hot.rz-reduce { color: var(--c-error); border-color: var(--c-error); }
.rt-page .rz-chip.rz-hot.rz-stop b, .rt-page .rz-chip.rz-hot.rz-reduce b { color: var(--c-error); }
.rt-page .rz-chip.rz-hot.rz-watch { color: var(--c-warning); border-color: var(--c-warning); }
.rt-page .rz-chip.rz-hot.rz-watch b { color: var(--c-warning); }
.rt-page .rz-chip.rz-hot.rz-buy { color: var(--c-accent); border-color: var(--c-accent); }
.rt-page .rz-chip.rz-hot.rz-buy b { color: var(--c-accent); }
.rt-page .rz-chip.rz-err { color: var(--c-error); border-color: var(--c-error); }
.rt-page .rt-zb { flex: 1; min-height: 0; overflow-y: auto; }

/* 堆叠布局（一屏放不下 / 窄屏）：整页滚动，分区给定高度、内部不再各自滚 */
.rt-page.rt-stack .rt-zone { min-height: 460px; }
.rt-page.rt-stack .rt-zb { overflow: visible; }
.rt-page.rt-stack .rb-body { flex: none; height: 500px; }
</style>

<style scoped>
.rt-page { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 10px; }
.rt-work { flex: 11 1 0; min-height: 0; display: grid; gap: 10px; align-items: stretch; }
.rt-band { flex: 9 1 0; }
/* 分析带放大：三区让位（v-if 卸载，图表随挂载重建，不留 0 尺寸实例） */
.rt-zoom .rt-band { flex: 1; }

.rt-stack { flex: none; min-height: auto; }
.rt-stack .rt-work { flex: none; }
.rt-stack .rt-band { flex: none; }
</style>
