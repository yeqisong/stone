<template>
<div class="mv">
  <!-- 顶栏：指数切换 + 区间 -->
  <div class="mv-toolbar">
    <AppIcon name="activity" :size="15" />
    <span class="mv-title">大盘</span>
    <div class="mv-idx-chips">
      <button v-for="i in IDXES" :key="i.ts" class="mv-chip" :class="{ active: midx === i.ts }"
              @click="switchIdx(i.ts)">{{ i.name }}</button>
    </div>
    <div style="flex:1" />
    <div class="mv-ranges">
      <button v-for="r in RANGES" :key="r.days" class="mv-chip mv-range" :class="{ active: rangeDays === r.days }"
              @click="switchRange(r.days)">{{ r.label }}</button>
    </div>
    <n-button size="tiny" text class="mv-sort-btn" title="调整下方图表顺序（对当前用户全部大盘页生效）"
      @click="openChartOrder"><AppIcon name="arrow-up-down" :size="13" /> 排序</n-button>
  </div>

  <MarketSentiment />

  <div class="mv-grid">
    <div class="mv-left">
      <!-- 指数 K 线（联动组） -->
      <div class="mv-panel" :style="{ order: chartOrderIndex('kline') }">
        <div class="mv-h">
          <span><AppIcon name="candlestick" :size="13" /> {{ idxName }} 日K</span>
          <span v-if="lastQuote" class="mv-quote" :class="(lastQuote.chg ?? 0) >= 0 ? 'up' : 'down'">
            {{ lastQuote.close }}（{{ lastQuote.chg != null ? (lastQuote.chg >= 0 ? '+' : '') + lastQuote.chg + '%' : '—' }}）
          </span>
          <span v-if="dateRange" class="mv-daterange">{{ dateRange }}</span>
        </div>
        <div id="mv-k" class="mv-chart mv-chart-kline"></div>
      </div>
      <div class="mv-panel" :style="{ order: chartOrderIndex('volume') }">
        <div class="mv-h"><span><AppIcon name="monitor" :size="13" /> 成交额</span></div>
        <div id="mv-vol" class="mv-chart"></div>
      </div>
      <div class="mv-panel" :style="{ order: chartOrderIndex('macd') }">
        <div class="mv-h"><span><AppIcon name="zap" :size="13" /> MACD（12,26,9）</span></div>
        <div id="mv-macd" class="mv-chart"></div>
      </div>
      <div class="mv-panel" :style="{ order: chartOrderIndex('rsi') }">
        <div class="mv-h"><span><AppIcon name="percent" :size="13" /> RSI（14）</span></div>
        <div id="mv-rsi" class="mv-chart"></div>
      </div>

      <!-- 指数估值（独立日期域） -->
      <div v-if="valData" class="mv-panel" :style="{ order: chartOrderIndex('pe') }">
        <div class="mv-h">
          <span><AppIcon name="target" :size="13" /> {{ idxName }} PE / PB</span>
          <span v-if="valPct" class="mv-pct">
            PE-TTM {{ valPct.cur_pe_ttm }}（{{ valPct.pe_pct != null ? valPct.pe_pct + '% 分位' : '—' }}）
            · PB {{ valPct.cur_pb }}（{{ valPct.pb_pct != null ? valPct.pb_pct + '% 分位' : '—' }}）
          </span>
        </div>
        <div id="mv-pe" class="mv-chart"></div>
      </div>
      <div v-if="valData" class="mv-panel" :style="{ order: chartOrderIndex('turnover') }">
        <div class="mv-h"><span><AppIcon name="percent" :size="13" /> {{ idxName }} 换手率</span></div>
        <div id="mv-tr" class="mv-chart"></div>
      </div>

      <!-- 比值中枢（风格比价分位）：分子跟随当前指数，分母/窗口可切换 -->
      <div class="mv-panel" :style="{ order: chartOrderIndex('ratio') }">
        <div class="mv-h">
          <span><AppIcon name="percent" :size="13" /> 比值中枢：{{ idxName }} ÷ {{ ratioDenName }}</span>
          <span class="mv-ratio-ctrl">
            <button v-for="d in RATIO_DENS" :key="d.code" class="mv-chip mv-chip-sm"
                    :class="{ active: ratioDen === d.code }" @click="switchDen(d.code)">{{ d.name }}</button>
          </span>
          <span class="mv-ratio-ctrl">
            <button v-for="w in RATIO_WINDOWS" :key="w.value" class="mv-chip mv-chip-sm"
                    :class="{ active: ratioWin === w.value }" @click="switchWin(w.value)">{{ w.label }}</button>
          </span>
          <span v-if="ratioLatest" class="mv-pct" style="margin-left:auto">
            比值 <b>{{ ratioLatest.ratio ?? '—' }}</b> · {{ ratioWindowLabel }}分位
            <b :class="ratioCls(ratioLatest.pct)">{{ ratioLatest.pct == null ? '—' : ratioLatest.pct + '%' }}</b>
          </span>
        </div>
        <div id="mv-ratio" class="mv-chart mv-chart-ratio"></div>
        <div class="mv-note">
          分位 = 当日比值在最近 {{ ratioWin }} 个交易日中的百分位（≥80 偏高区 / ≤20 偏低区），
          虚线为同窗口比值中位数（中枢）。价格指数口径（不含分红），滚动分位只描述相对位置，不构成买卖建议。
        </div>
      </div>

      <!-- 全市场角度 -->
      <div v-if="breadthData.length" class="mv-panel" :style="{ order: chartOrderIndex('breadth') }">
        <div class="mv-h">
          <span><AppIcon name="monitor" :size="13" /> 市场宽度（全市场涨跌家数）</span>
          <span v-if="lastBreadth" class="mv-pct">
            涨 <b class="up">{{ lastBreadth.up }}</b> / 跌 <b class="down">{{ lastBreadth.down }}</b>
            · 涨停 {{ lastBreadth.limit_up }} / 跌停 {{ lastBreadth.limit_down }}
          </span>
        </div>
        <div id="mv-breadth" class="mv-chart"></div>
      </div>
      <div v-if="sentiData.length" class="mv-panel" :style="{ order: chartOrderIndex('senti') }">
        <div class="mv-h">
          <span><AppIcon name="activity" :size="13" /> 市场恐慌指数趋势（自建情绪分，2000 起）</span>
          <span v-if="lastSenti" class="mv-pct">
            最新 <b :class="sentiCls(lastSenti.score)">{{ lastSenti.score }}</b>（{{ sentiLabel(lastSenti.level) }}，{{ lastSenti.date }}）
          </span>
        </div>
        <div id="mv-senti" class="mv-chart"></div>
      </div>
      <div v-if="mfData.length" class="mv-panel" :style="{ order: chartOrderIndex('moneyflow') }">
        <div class="mv-h">
          <span><AppIcon name="download" :size="13" /> 全市场主力资金流（2010 起）</span>
          <span v-if="lastMf" class="mv-pct" :class="lastMf.net_main >= 0 ? 'up' : 'down'">
            最新 {{ fmtYi(lastMf.net_main) }}（{{ lastMf.date }}）
          </span>
        </div>
        <div id="mv-mf" class="mv-chart"></div>
      </div>
      <div v-if="marginData.length" class="mv-panel" :style="{ order: chartOrderIndex('margin') }">
        <div class="mv-h">
          <span><AppIcon name="shield" :size="13" /> 两融余额（2014 起）</span>
          <span v-if="lastMargin" class="mv-pct">融资 {{ fmtYi(lastMargin.fin) }} · 两融合计 {{ fmtYi(lastMargin.total) }}</span>
        </div>
        <div id="mv-margin" class="mv-chart"></div>
      </div>
      <div v-if="sectorData.dates && sectorData.dates.length" class="mv-panel" :style="{ order: chartOrderIndex('sector') }">
        <div class="mv-h"><span><AppIcon name="shuffle" :size="13" /> 行业主力资金流趋势（累计，Top 8）</span></div>
        <div id="mv-sector" class="mv-chart"></div>
      </div>
    </div>

    <!-- 右侧栏 -->
    <div class="mv-side">
      <div class="mv-panel">
        <div class="mv-h"><span><AppIcon name="target" :size="13" /> 指数估值对比</span></div>
        <table class="mv-table">
          <thead><tr><th>指数</th><th>涨跌</th><th>PE-TTM</th><th>分位</th><th>PB</th><th>分位</th></tr></thead>
          <tbody>
            <tr v-for="i in ovIndices" :key="i.ts_code" :class="{ sel: i.ts_code === midx }" @click="switchIdx(i.ts_code)">
              <td>{{ i.name }}</td>
              <td :class="(i.chg_pct ?? 0) >= 0 ? 'up' : 'down'">{{ pctTxt(i.chg_pct) }}</td>
              <td>{{ i.pe_ttm ?? '—' }}</td>
              <td :class="peCls(i.pe_pct)">{{ i.pe_pct != null ? i.pe_pct + '%' : '—' }}</td>
              <td>{{ i.pb ?? '—' }}</td>
              <td :class="peCls(i.pb_pct)">{{ i.pb_pct != null ? i.pb_pct + '%' : '—' }}</td>
            </tr>
          </tbody>
        </table>
        <div class="mv-note">分位为该指数 2005 年（或创立）以来全历史百分位；点击行切换主图。</div>
      </div>

      <div class="mv-panel" v-if="ovMoneyflow || ovMargin">
        <div class="mv-h"><span><AppIcon name="download" :size="13" /> 全市场快照</span></div>
        <table class="mv-table">
          <tbody>
            <tr v-if="ovMoneyflow"><th>主力净流入</th><td :class="ovMoneyflow.net_main >= 0 ? 'up' : 'down'">{{ fmtYi(ovMoneyflow.net_main) }}</td>
                <th>日期</th><td>{{ ovMoneyflow.date }}</td></tr>
            <tr v-if="ovMoneyflow"><th>全单净额</th><td :class="ovMoneyflow.net_total >= 0 ? 'up' : 'down'">{{ fmtYi(ovMoneyflow.net_total) }}</td>
                <th>前一日</th><td :class="(ovMoneyflow.net_main_prev ?? 0) >= 0 ? 'up' : 'down'">{{ fmtYi(ovMoneyflow.net_main_prev) }}</td></tr>
            <tr v-if="ovMargin"><th>融资余额</th><td>{{ fmtYi(ovMargin.fin_amount) }}</td>
                <th>两融合计</th><td>{{ fmtYi(ovMargin.total_amount) }}</td></tr>
          </tbody>
        </table>
      </div>

      <div class="mv-panel" v-if="macroRows.length">
        <div class="mv-h"><span><AppIcon name="calendar" :size="13" /> 宏观指标（同比/利率 %）</span></div>
        <table class="mv-table">
          <tbody>
            <tr v-for="m in macroRows" :key="m.code">
              <th>{{ m.label }}</th>
              <td>{{ m.value }}</td>
              <td class="mv-dim">{{ m.date }}</td>
              <td :class="m.dir > 0 ? 'up' : (m.dir < 0 ? 'down' : '')">{{ m.arrow }}</td>
            </tr>
          </tbody>
        </table>
        <div class="mv-note">CPI/PPI/M0/M1/M2 为同比，SHIBOR 为隔夜利率，GDP 为当季同比。</div>
      </div>
    </div>
  </div>

  <!-- 图表排序：拖动或上下移动，顺序按用户保存到 localStorage，大盘页共用 -->
  <n-modal v-model:show="showChartOrder">
    <n-card style="width:440px;max-width:92vw" title="图表排序" role="dialog" aria-modal="true">
      <div class="chart-order-hint">拖动行或点 ↑↓ 调整；保存后对当前用户大盘页生效。</div>
      <div class="chart-order-list">
        <div v-for="(sid, idx) in chartOrderDraft" :key="sid" class="chart-order-row"
          :class="{ dragging: chartDragIdx === idx }" draggable="true"
          @dragstart="startChartDrag(idx)" @dragend="endChartDrag"
          @dragover.prevent @drop="dropChartOrder(idx)">
          <span class="chart-order-grip" title="拖动排序"><AppIcon name="list" :size="13" /></span>
          <span class="chart-order-no">{{ idx + 1 }}</span>
          <span class="chart-order-label">{{ chartSectionLabel(sid) }}</span>
          <button type="button" class="chart-order-move" :disabled="idx === 0"
            title="上移" @click="moveChartOrder(idx, -1)">↑</button>
          <button type="button" class="chart-order-move" :disabled="idx === chartOrderDraft.length - 1"
            title="下移" @click="moveChartOrder(idx, 1)">↓</button>
        </div>
      </div>
      <template #footer>
        <div style="display:flex;justify-content:space-between;align-items:center;width:100%">
          <n-button size="small" quaternary @click="resetChartOrder">恢复默认</n-button>
          <n-space>
            <n-button size="small" @click="showChartOrder = false">取消</n-button>
            <n-button size="small" type="primary" @click="saveChartOrder">保存</n-button>
          </n-space>
        </div>
      </template>
    </n-card>
  </n-modal>
</div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, onUnmounted, nextTick } from 'vue'
import { NCard, NButton, NSpace, NModal, useMessage } from 'naive-ui'
import { useNavStore } from '../stores/nav'
import { useThemeStore } from '../stores/theme'
import { useAuthStore } from '../stores/auth'
import axios from 'axios'
import * as echarts from 'echarts'
import { chartColors as T } from '../utils/chart'
import AppIcon from './AppIcon.vue'
import MarketSentiment from './MarketSentiment.vue'

const nav = useNavStore()
const authStore = useAuthStore()
const message = useMessage()
const API = window.location.origin

// 7 大指数（ts=估值表带后缀码 / bare=行情表无后缀码，与后端 MAIN_INDEXES 一致）
const IDXES = [
  { ts: '000001.SH', bare: '000001', name: '上证' },
  { ts: '399001.SZ', bare: '399001', name: '深成' },
  { ts: '399006.SZ', bare: '399006', name: '创业板' },
  { ts: '000300.SH', bare: '000300', name: '沪深300' },
  { ts: '000016.SH', bare: '000016', name: '上证50' },
  { ts: '000905.SH', bare: '000905', name: '中证500' },
  { ts: '000852.SH', bare: '000852', name: '中证1000' },
]
const RANGES = [
  { label: '1月', days: 21 }, { label: '3月', days: 63 }, { label: '6月', days: 126 },
  { label: '1年', days: 250 }, { label: '3年', days: 750 }, { label: '5年', days: 1250 },
  { label: '8年', days: 2000 }, { label: '10年', days: 2500 }, { label: '15年', days: 3750 },
  { label: '20年', days: 5000 }, { label: '全部', days: 10000 },
]

const midx = computed(() => nav.midx || '000001.SH')
const idxName = computed(() => (IDXES.find(i => i.ts === midx.value) || {}).name || midx.value)
const idxBare = computed(() => (IDXES.find(i => i.ts === midx.value) || {}).bare || '000001')
const rangeDays = ref(250)

// 比值中枢：分母候选 + 滚动窗口（1260 日 = 5年×252，对齐参考口径）
const RATIO_DENS = [
  { code: '000922', name: '中证红利' },
  { code: '000985', name: '中证全指' },
  { code: '000300', name: '沪深300' },
  { code: '000016', name: '上证50' },
]
const RATIO_WINDOWS = [
  { value: 756, label: '3年' }, { value: 1260, label: '5年' }, { value: 2016, label: '8年' },
]
const ratioDen = ref('000922')
const ratioWin = ref(1260)
const ratioData = ref(null)
const ratioLatest = computed(() => ratioData.value?.latest || null)
const ratioDenName = computed(() => (RATIO_DENS.find(d => d.code === ratioDen.value) || {}).name || ratioDen.value)
const ratioWindowLabel = computed(() => (RATIO_WINDOWS.find(w => w.value === ratioWin.value) || {}).label || ratioWin.value + '日')
const ratioCls = p => (p == null ? '' : (p >= 80 ? 'up' : (p <= 20 ? 'down' : '')))
let ratioSeq = 0

// ── 图表顺序（按用户持久化到 localStorage，大盘页共用）──
const CHART_SECTIONS = [
  { id: 'kline', label: '指数日K' },
  { id: 'volume', label: '成交额' },
  { id: 'macd', label: 'MACD' },
  { id: 'rsi', label: 'RSI' },
  { id: 'pe', label: 'PE / PB' },
  { id: 'turnover', label: '换手率' },
  { id: 'ratio', label: '比值中枢（风格比价分位）' },
  { id: 'breadth', label: '市场宽度（全市场涨跌家数）' },
  { id: 'senti', label: '市场恐慌指数趋势' },
  { id: 'moneyflow', label: '全市场主力资金流' },
  { id: 'margin', label: '两融余额' },
  { id: 'sector', label: '行业主力资金流趋势' },
]
const CHART_SECTION_LABEL = Object.fromEntries(CHART_SECTIONS.map(x => [x.id, x.label]))
const DEFAULT_CHART_ORDER = CHART_SECTIONS.map(x => x.id)
const chartOrder = ref([...DEFAULT_CHART_ORDER])
const chartOrderDraft = ref([])
const chartDragIdx = ref(null)
const showChartOrder = ref(false)

function _chartOrderKey() { return 'market_chart_order:' + (authStore.username || 'default') }
function loadChartOrder() {
  let kept = []
  try {
    const raw = JSON.parse(localStorage.getItem(_chartOrderKey()) || 'null')
    if (Array.isArray(raw)) {
      for (const id of raw) {
        if (DEFAULT_CHART_ORDER.includes(id) && !kept.includes(id)) kept.push(id)
      }
    }
  } catch (e) { /* 本地存储损坏 → 回默认顺序 */ }
  chartOrder.value = [...kept, ...DEFAULT_CHART_ORDER.filter(id => !kept.includes(id))]
}
function chartSectionLabel(id) { return CHART_SECTION_LABEL[id] || id }
function chartOrderIndex(id) {
  const i = chartOrder.value.indexOf(id)
  return i < 0 ? DEFAULT_CHART_ORDER.length : i
}
function openChartOrder() {
  chartOrderDraft.value = [...chartOrder.value]
  chartDragIdx.value = null
  showChartOrder.value = true
}
function moveChartOrder(idx, delta) {
  const to = idx + delta
  const arr = [...chartOrderDraft.value]
  if (to < 0 || to >= arr.length) return
  const [moved] = arr.splice(idx, 1)
  arr.splice(to, 0, moved)
  chartOrderDraft.value = arr
}
function startChartDrag(idx) { chartDragIdx.value = idx }
function endChartDrag() { chartDragIdx.value = null }
function dropChartOrder(targetIdx) {
  const from = chartDragIdx.value
  if (from == null || from === targetIdx) return
  const arr = [...chartOrderDraft.value]
  const [moved] = arr.splice(from, 1)
  arr.splice(targetIdx, 0, moved)
  chartOrderDraft.value = arr
  chartDragIdx.value = null
}
function resetChartOrder() { chartOrderDraft.value = [...DEFAULT_CHART_ORDER] }
function saveChartOrder() {
  chartOrder.value = [...chartOrderDraft.value]
  try { localStorage.setItem(_chartOrderKey(), JSON.stringify(chartOrder.value)) } catch (e) {}
  showChartOrder.value = false
  message.success('图表顺序已保存（大盘页生效）')
  nextTick(() => { try { resizeAll() } catch (e) {} })
}
loadChartOrder()
watch(() => authStore.username, loadChartOrder)

// ── 数据 refs（theme 重绘需要全量留存）──
const klineData = ref([])
const valData = ref(null)
const breadthData = ref([])
const sentiData = ref([])
const mfData = ref([])
const marginData = ref([])
const sectorData = ref({})
const ovIndices = ref([])
const ovMoneyflow = ref(null)
const ovMargin = ref(null)
const macroData = ref({})
const dateRange = ref('')
let loadSeq = 0

// ── 派生摘要 ──
const lastQuote = computed(() => {
  const k = klineData.value
  if (!k.length) return null
  const c = k[k.length - 1], p = k[k.length - 2]
  if (!p) return { close: c.close, chg: null }
  return { close: c.close, chg: +(((c.close - p.close) / p.close) * 100).toFixed(2) }
})
const valPct = computed(() => {
  const v = valData.value
  if (!v?.data?.length) return null
  const last = v.data[v.data.length - 1]
  return {
    cur_pe_ttm: last.pe_ttm != null ? Number(last.pe_ttm).toFixed(2) : '—',
    cur_pb: last.pb != null ? Number(last.pb).toFixed(2) : '—',
    pe_pct: v.pct?.pe_pct ?? null, pb_pct: v.pct?.pb_pct ?? null,
  }
})
const lastBreadth = computed(() => breadthData.value[breadthData.value.length - 1] || null)
const lastSenti = computed(() => sentiData.value[sentiData.value.length - 1] || null)
const sentiLabel = lv => ({ panic: '恐慌', weak: '偏弱', neutral: '中性', strong: '偏强' }[lv] || lv || '—')
const sentiCls = sc => (sc == null ? '' : (sc < 25 ? 'down' : (sc >= 70 ? 'up' : '')))
const lastMf = computed(() => mfData.value[mfData.value.length - 1] || null)
const lastMargin = computed(() => marginData.value[marginData.value.length - 1] || null)

const MACRO_LABELS = { cpi: 'CPI', ppi: 'PPI', m0: 'M0', m1: 'M1', m2: 'M2', shibor_on: 'SHIBOR隔夜', gdp: 'GDP' }
const macroRows = computed(() => {
  const rows = []
  for (const [code, label] of Object.entries(MACRO_LABELS)) {
    const s = macroData.value[code] || []
    if (!s.length) continue
    const last = s[s.length - 1]
    const prev = s.length > 1 ? s[s.length - 2] : null
    const dir = prev ? Math.sign((last.value ?? 0) - (prev.value ?? 0)) : 0
    rows.push({
      code, label, value: last.value != null ? last.value : '—', date: last.date,
      dir, arrow: dir > 0 ? '↑' : (dir < 0 ? '↓' : '—'),
    })
  }
  return rows
})

function fmtYi(v) {
  if (v == null) return '—'
  const y = v / 1e8
  return (Math.abs(y) >= 100 ? y.toFixed(0) : y.toFixed(1)) + '亿'
}
// 序列数据已经换算成“亿”时用这个（资金流/两融/行业累计）。
// fmtYi 只接受原始“元”，混用会再除 1e8，轴刻度会全变成 0.0亿。
function fmtYiValue(v) {
  if (v == null) return '—'
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return (Math.abs(n) >= 100 ? n.toFixed(0) : n.toFixed(1)) + '亿'
}
function pctTxt(v) { return v == null ? '—' : ((v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%') }
function peCls(p) { return p == null ? '' : (p >= 70 ? 'up' : (p <= 30 ? 'down' : '')) }

function switchIdx(ts) {
  if (nav.midx === ts) return
  nav.midx = ts
}
function switchRange(d) {
  rangeDays.value = d
  loadIdxCharts(++loadSeq)  // 递增序号使上一个在途请求失效（连点区间防竞态）
}

// ── ECharts 约定（对齐 Detail.vue）：实例复用 / 低调滑块 / 统一 grid ──
function make(id, opt) {
  const el = document.getElementById(id)
  if (!el) return null
  let c = el._echart
  if (!c) { c = echarts.init(el); el._echart = c }
  c.setOption(opt, { notMerge: true })
  return c
}
const dzSlider = () => ({
  height: 16, bottom: 4, handleSize: 6,
  borderColor: 'transparent',
  backgroundColor: T.line,
  fillerColor: T.line,
  dataBackground: { lineStyle: { color: 'transparent' }, areaStyle: { color: T.line } },
  selectedDataBackground: { lineStyle: { color: T.series[3] }, areaStyle: { color: T.line } },
  handleStyle: { color: T.faint, borderColor: T.line },
  moveHandleStyle: { color: T.line },
  textStyle: { color: T.dim, fontSize: 11 },
  labelStyle: { color: 'transparent' },
})
const GRID_STD = { left: 64, right: 48, bottom: 34 }
const gl = { lineStyle: { color: T.line } }

function drawIdxCharts() {
  // K线 / 成交额 / MACD / RSI 四图同组联动（echarts.connect）
  const k = klineData.value
  if (!k.length) return
  const dates = k.map(d => d.trade_date)
  const ohlc = k.map(d => [d.open, d.close, d.low, d.high])
  const closes = k.map(d => d.close)
  const amts = k.map(d => d.amount)
  const bc = ohlc.map(d => d[1] >= d[0] ? T.up : T.down)
  const MA = (n) => closes.map((_, i) => { if (i < n - 1) return null; let s = 0; for (let j = 0; j < n; j++) s += closes[i - j]; return +(s / n).toFixed(2) })
  const ma5 = MA(5), ma10 = MA(10), ma20 = MA(20), ma60 = MA(60), ma120 = MA(120)
  const bmid = k.map(d => d.boll_mid), bup = k.map(d => d.boll_upper), blo = k.map(d => d.boll_lower)
  const di = k.map(d => d.dif), de = k.map(d => d.dea), ba = k.map(d => d.macd_bar)
  const rs = k.map(d => d.rsi)
  dateRange.value = dates[0] + ' ~ ' + dates[dates.length - 1]

  const dz = [
    { type: 'inside', xAxisIndex: 0, zoomOnMouseWheel: true, moveOnMouseWheel: 'shift' },
    { type: 'slider', xAxisIndex: 0, start: 0, end: 100, ...dzSlider() },
  ]
  const xA = { type: 'category', data: dates, axisLabel: { show: false }, axisLine: { lineStyle: { color: T.line } }, axisTick: { show: false } }
  const tt = { trigger: 'axis', axisPointer: { type: 'cross' } }
  const chg = i => (i > 0 && ohlc[i - 1][1] ? +((ohlc[i][1] - ohlc[i - 1][1]) / ohlc[i - 1][1] * 100).toFixed(2) : null)

  const cK = make('mv-k', {
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, formatter: p => {
      const i = p[0]?.dataIndex
      if (i == null || !ohlc[i]) return ''
      const c = chg(i)
      return `<div style="font-size:12px"><b>${dates[i]}</b><br/>` +
        `开: ${ohlc[i][0].toFixed(2)}  收: <span style="color:${c != null && c >= 0 ? T.up : T.down}">${ohlc[i][1].toFixed(2)}（${c != null ? c + '%' : '—'}）</span><br/>` +
        `高: ${ohlc[i][3].toFixed(2)}  低: ${ohlc[i][2].toFixed(2)}  额: ${fmtYi(amts[i])}</div>`
    } },
    grid: { ...GRID_STD, top: 18 },
    xAxis: xA, yAxis: { scale: true, splitLine: gl },
    dataZoom: dz,
    series: [
      { name: 'K线', type: 'candlestick', data: ohlc, itemStyle: { color: T.up, color0: T.down, borderColor: T.up, borderColor0: T.down } },
      { name: 'MA5', type: 'line', data: ma5, lineStyle: { color: T.series[0], width: 1 }, itemStyle: { color: T.series[0] }, symbol: 'none' },
      { name: 'MA10', type: 'line', data: ma10, lineStyle: { color: T.series[1], width: 1 }, itemStyle: { color: T.series[1] }, symbol: 'none' },
      { name: 'MA20', type: 'line', data: ma20, lineStyle: { color: T.series[2], width: 1 }, itemStyle: { color: T.series[2] }, symbol: 'none' },
      { name: 'MA60', type: 'line', data: ma60, lineStyle: { color: T.warn, width: 1 }, itemStyle: { color: T.warn }, symbol: 'none' },
      { name: 'MA120', type: 'line', data: ma120, lineStyle: { color: T.series[4], width: 1 }, itemStyle: { color: T.series[4] }, symbol: 'none' },
      { name: 'BOLL上', type: 'line', data: bup, lineStyle: { color: T.faint, width: 1, type: 'dashed' }, itemStyle: { color: T.faint }, symbol: 'none' },
      { name: 'BOLL下', type: 'line', data: blo, lineStyle: { color: T.faint, width: 1, type: 'dashed' }, itemStyle: { color: T.faint }, symbol: 'none' },
    ],
  })
  const cV = make('mv-vol', {
    tooltip: tt, grid: { ...GRID_STD, top: 8 },
    xAxis: xA, yAxis: { axisLabel: { fontSize: 11, formatter: v => fmtYi(v) }, splitLine: gl },
    dataZoom: dz,
    series: [{ name: '成交额', type: 'bar', data: amts, itemStyle: { color: p => bc[p.dataIndex] } }],
  })
  const cM = make('mv-macd', {
    tooltip: tt, grid: { ...GRID_STD, top: 8 },
    xAxis: xA, yAxis: { splitLine: gl },
    dataZoom: dz,
    series: [
      { name: '柱', type: 'bar', data: ba, itemStyle: { color: p => (ba[p.dataIndex] >= 0 ? T.up : T.down) } },
      { name: 'DIF', type: 'line', data: di, lineStyle: { color: T.warn, width: 1 }, itemStyle: { color: T.warn }, symbol: 'none' },
      { name: 'DEA', type: 'line', data: de, lineStyle: { color: '#06b6d4', width: 1 }, itemStyle: { color: '#06b6d4' }, symbol: 'none' },
    ],
  })
  const cR = make('mv-rsi', {
    tooltip: tt, grid: { ...GRID_STD, top: 8 },
    xAxis: xA, yAxis: { min: 0, max: 100, splitLine: gl },
    dataZoom: dz,
    series: [{
      name: 'RSI', type: 'line', data: rs, lineStyle: { color: T.series[4], width: 1.5 }, itemStyle: { color: T.series[4] },
      symbol: 'none', areaStyle: { color: T.series[4], opacity: .12 },
      markLine: { silent: true, symbol: 'none', data: [
        { yAxis: 70, label: { formatter: '超买' }, lineStyle: { color: T.up, type: 'dashed' } },
        { yAxis: 30, label: { formatter: '超卖' }, lineStyle: { color: T.down, type: 'dashed' } },
      ] },
    }],
  })
  // 四图联动：connect 只连声明了同 group 的实例（对齐 Detail.vue 的 c.group='s'）
  for (const c of [cK, cV, cM, cR]) { if (c) c.group = 'mk' }
  echarts.connect('mk')
}

function drawValCharts() {
  const v = valData.value
  if (!v?.data?.length) return
  const d = v.data
  const dates = d.map(x => x.date)
  const pe = d.map(x => x.pe_ttm)
  const pb = d.map(x => x.pb)
  const tr = d.map(x => x.turnover_rate)
  const gl2 = { lineStyle: { color: T.line } }
  const pct = v.pct || {}
  const mkPe = { silent: true, symbol: 'none', data: [
    ...(pct.p30_pe != null ? [{ yAxis: pct.p30_pe, label: { formatter: '30分位 ' + pct.p30_pe, position: 'insideEndTop', fontSize: 10, color: T.faint }, lineStyle: { color: T.faint, type: 'dashed', width: 1 } }] : []),
    ...(pct.p70_pe != null ? [{ yAxis: pct.p70_pe, label: { formatter: '70分位 ' + pct.p70_pe, position: 'insideEndTop', fontSize: 10, color: T.faint }, lineStyle: { color: T.faint, type: 'dashed', width: 1 } }] : []),
  ] }
  const mkPb = { silent: true, symbol: 'none', data: [
    ...(pct.p30_pb != null ? [{ yAxis: pct.p30_pb, label: { formatter: String(pct.p30_pb), position: 'insideEndTop', fontSize: 10, color: T.faint }, lineStyle: { color: T.faint, type: 'dashed', width: 1 } }] : []),
    ...(pct.p70_pb != null ? [{ yAxis: pct.p70_pb, label: { formatter: String(pct.p70_pb), position: 'insideEndTop', fontSize: 10, color: T.faint }, lineStyle: { color: T.faint, type: 'dashed', width: 1 } }] : []),
  ] }
  make('mv-pe', {
    tooltip: { trigger: 'axis' },
    legend: { top: 0, right: 4, itemWidth: 12, itemHeight: 6, textStyle: { fontSize: 10, color: T.dim } },
    grid: GRID_STD,
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10, color: T.faint, hideOverlap: true }, axisLine: { lineStyle: { color: T.line } } },
    yAxis: [
      { type: 'value', scale: true, name: 'PE-TTM', nameTextStyle: { fontSize: 10, color: T.faint }, axisLabel: { fontSize: 10, color: T.faint }, splitLine: gl2 },
      { type: 'value', scale: true, name: 'PB', nameTextStyle: { fontSize: 10, color: T.faint }, axisLabel: { fontSize: 10, color: T.faint }, splitLine: { show: false } },
    ],
    dataZoom: [{ type: 'inside' }, { type: 'slider', ...dzSlider() }],
    series: [
      { name: 'PE-TTM', type: 'line', data: pe, symbol: 'none', lineStyle: { color: T.up, width: 1.4 }, itemStyle: { color: T.up }, markLine: mkPe },
      { name: 'PB', type: 'line', yAxisIndex: 1, data: pb, symbol: 'none', lineStyle: { color: T.series[3], width: 1.2 }, itemStyle: { color: T.series[3] }, markLine: mkPb },
    ],
  })
  make('mv-tr', {
    tooltip: { trigger: 'axis' }, grid: { ...GRID_STD, top: 8 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10, color: T.faint, hideOverlap: true }, axisLine: { lineStyle: { color: T.line } } },
    yAxis: { axisLabel: { fontSize: 10, color: T.faint, formatter: v => v + '%' }, splitLine: gl2 },
    dataZoom: [{ type: 'inside' }, { type: 'slider', ...dzSlider() }],
    series: [{ name: '换手率', type: 'line', data: tr, symbol: 'none', lineStyle: { color: T.series[2], width: 1.2 }, itemStyle: { color: T.series[2] }, areaStyle: { color: T.series[2], opacity: .08 } }],
  })
}

function drawRatioChart() {
  const d = ratioData.value?.data
  const el = document.getElementById('mv-ratio')
  if (!el) return
  if (!d || !d.length) {
    if (el._echart) el._echart.clear()
    return
  }
  const dates = d.map(x => x.date)
  const ratio = d.map(x => x.ratio)
  const mid = d.map(x => x.mid)
  const pct = d.map(x => x.pct)
  const lbl = ratioWindowLabel.value
  const gl2 = { lineStyle: { color: T.line } }
  make('mv-ratio', {
    tooltip: { trigger: 'axis', formatter: ps => {
      const i = ps?.[0]?.dataIndex
      const x = i == null ? null : d[i]
      if (!x) return ''
      return `<div style="font-size:12px"><b>${x.date}</b><br/>` +
        `比值: <b>${x.ratio ?? '—'}</b>　滚动中枢: ${x.mid ?? '—'}<br/>` +
        `${lbl}分位: <b>${x.pct == null ? '—' : x.pct + '%'}</b>` +
        (x.p20 != null && x.p80 != null ? `　20-80带: ${x.p20} ~ ${x.p80}` : '') + `</div>`
    } },
    legend: { top: 0, right: 4, itemWidth: 12, itemHeight: 6, textStyle: { fontSize: 10, color: T.dim } },
    grid: [
      { left: 64, right: 48, top: 26, height: '38%' },
      { left: 64, right: 48, top: '58%', bottom: 34 },
    ],
    xAxis: [
      { type: 'category', gridIndex: 0, data: dates, axisLabel: { show: false },
        axisLine: { lineStyle: { color: T.line } }, axisTick: { show: false } },
      { type: 'category', gridIndex: 1, data: dates,
        axisLabel: { fontSize: 10, color: T.faint, hideOverlap: true },
        axisLine: { lineStyle: { color: T.line } }, axisTick: { show: false } },
    ],
    yAxis: [
      { gridIndex: 0, type: 'value', scale: true, name: '比值',
        nameTextStyle: { fontSize: 10, color: T.faint },
        axisLabel: { fontSize: 10, color: T.faint, formatter: v => Number(v).toFixed(2) }, splitLine: gl2 },
      { gridIndex: 1, type: 'value', min: 0, max: 100, name: '分位%',
        nameTextStyle: { fontSize: 10, color: T.faint },
        axisLabel: { fontSize: 10, color: T.faint }, splitLine: gl2 },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1] },
      { type: 'slider', xAxisIndex: [0, 1], ...dzSlider() },
    ],
    series: [
      { name: '比值', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ratio, symbol: 'none',
        lineStyle: { color: T.series[0], width: 1.6 }, itemStyle: { color: T.series[0] } },
      { name: `${lbl}中枢`, type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: mid, symbol: 'none',
        lineStyle: { color: T.warn, width: 1.1, type: 'dashed' }, itemStyle: { color: T.warn } },
      { name: `${lbl}滚动分位`, type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: pct, symbol: 'none',
        lineStyle: { color: T.series[3], width: 1.3 }, itemStyle: { color: T.series[3] },
        areaStyle: { color: T.series[3], opacity: .10 },
        markLine: { silent: true, symbol: 'none', data: [
          { yAxis: 80, label: { formatter: '80 偏高', fontSize: 10, color: T.faint }, lineStyle: { color: T.up, type: 'dashed', width: 1 } },
          { yAxis: 20, label: { formatter: '20 偏低', fontSize: 10, color: T.faint }, lineStyle: { color: T.down, type: 'dashed', width: 1 } },
        ] },
        markArea: { silent: true, data: [
          [{ yAxis: 80, itemStyle: { color: T.up, opacity: .08 } }, { yAxis: 100 }],
          [{ yAxis: 20, itemStyle: { color: T.down, opacity: .08 } }, { yAxis: 0 }],
        ] } },
    ],
  })
}

function drawMarketCharts() {
  const gl2 = { lineStyle: { color: T.line } }
  const xOpt = dates => ({ type: 'category', data: dates, axisLabel: { fontSize: 10, color: T.faint, hideOverlap: true }, axisLine: { lineStyle: { color: T.line } }, axisTick: { show: false } })

  // 市场宽度：上涨（红，向上）+ 下跌（绿，向下）对零轴堆叠，涨跌停双线右轴
  if (breadthData.value.length) {
    const b = breadthData.value
    const dates = b.map(x => x.date)
    make('mv-breadth', {
      tooltip: { trigger: 'axis' },
      legend: { top: 0, right: 4, itemWidth: 12, itemHeight: 6, textStyle: { fontSize: 10, color: T.dim } },
      grid: GRID_STD,
      xAxis: xOpt(dates),
      yAxis: [
        { type: 'value', axisLabel: { fontSize: 10, color: T.faint }, splitLine: gl2 },
        { type: 'value', axisLabel: { fontSize: 10, color: T.faint }, splitLine: { show: false } },
      ],
      dataZoom: [{ type: 'inside' }, { type: 'slider', ...dzSlider() }],
      series: [
        { name: '上涨家数', type: 'bar', stack: 'w', data: b.map(x => x.up), itemStyle: { color: T.up, opacity: .85 } },
        { name: '下跌家数', type: 'bar', stack: 'w', data: b.map(x => -x.down), itemStyle: { color: T.down, opacity: .85 } },
        { name: '涨停', type: 'line', yAxisIndex: 1, data: b.map(x => x.limit_up), symbol: 'none', lineStyle: { color: T.up, width: 1, type: 'dashed' }, itemStyle: { color: T.up } },
        { name: '跌停', type: 'line', yAxisIndex: 1, data: b.map(x => -x.limit_down), symbol: 'none', lineStyle: { color: T.down, width: 1, type: 'dashed' }, itemStyle: { color: T.down } },
      ],
    })
  }
  // 全市场主力资金流：净流入柱 + 累计线右轴
  if (mfData.value.length) {
    const m = mfData.value
    const dates = m.map(x => x.date)
    const nm = m.map(x => x.net_main == null ? null : +(x.net_main / 1e8).toFixed(2))
    const cum = m.map(x => x.cum_main == null ? null : +(x.cum_main / 1e8).toFixed(0))
    make('mv-mf', {
      tooltip: { trigger: 'axis', valueFormatter: v => fmtYiValue(v) },
      legend: { top: 0, right: 4, itemWidth: 12, itemHeight: 6, textStyle: { fontSize: 10, color: T.dim } },
      grid: GRID_STD,
      xAxis: xOpt(dates),
      yAxis: [
        { type: 'value', name: '亿/日', nameTextStyle: { fontSize: 10, color: T.faint }, axisLabel: { fontSize: 10, color: T.faint, formatter: v => fmtYiValue(v) }, splitLine: gl2 },
        { type: 'value', name: '累计亿', nameTextStyle: { fontSize: 10, color: T.faint }, axisLabel: { fontSize: 10, color: T.faint, formatter: v => fmtYiValue(v) }, splitLine: { show: false } },
      ],
      dataZoom: [{ type: 'inside' }, { type: 'slider', ...dzSlider() }],
      series: [
        { name: '主力净流入', type: 'bar', data: nm, itemStyle: { color: p => ((nm[p.dataIndex] ?? 0) >= 0 ? T.up : T.down) } },
        { name: '累计', type: 'line', yAxisIndex: 1, data: cum, symbol: 'none', lineStyle: { color: T.series[4], width: 1.2 }, itemStyle: { color: T.series[4] } },
      ],
    })
  }
  // 两融余额：融资余额 + 两融合计（亿）
  if (marginData.value.length) {
    const g = marginData.value
    const dates = g.map(x => x.date)
    const fin = g.map(x => x.fin == null ? null : +(x.fin / 1e8).toFixed(0))
    const tot = g.map(x => x.total == null ? null : +(x.total / 1e8).toFixed(0))
    make('mv-margin', {
      tooltip: { trigger: 'axis', valueFormatter: v => fmtYiValue(v) },
      legend: { top: 0, right: 4, itemWidth: 12, itemHeight: 6, textStyle: { fontSize: 10, color: T.dim } },
      grid: GRID_STD,
      xAxis: xOpt(dates),
      yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 10, color: T.faint, formatter: v => fmtYiValue(v) }, splitLine: gl2 },
      dataZoom: [{ type: 'inside' }, { type: 'slider', ...dzSlider() }],
      series: [
        { name: '融资余额', type: 'line', data: fin, symbol: 'none', lineStyle: { color: T.up, width: 1.4 }, itemStyle: { color: T.up } },
        { name: '两融合计', type: 'line', data: tot, symbol: 'none', lineStyle: { color: T.series[3], width: 1 }, itemStyle: { color: T.series[3] } },
      ],
    })
  }
  // 行业主力资金流累计（Top 8）：服务端预计算 cumulative（与 dates 对齐的
  // 逐日累计数组）与 sum20（近 20 日累计），直接取用，勿按 data dict 取数
  const sd = sectorData.value
  if (sd.dates && sd.dates.length && sd.sectors && sd.sectors.length) {
    const dates = sd.dates
    const totals = [...sd.sectors].sort((a, b2) => (b2.sum20 || 0) - (a.sum20 || 0)).slice(0, 8)
    make('mv-sector', {
      tooltip: { trigger: 'axis', valueFormatter: v => fmtYiValue(v) },
      legend: { top: 0, type: 'scroll', itemWidth: 12, itemHeight: 6, textStyle: { fontSize: 10, color: T.dim }, pageTextStyle: { color: T.dim } },
      grid: GRID_STD,
      xAxis: xOpt(dates),
      yAxis: { type: 'value', axisLabel: { fontSize: 10, color: T.faint, formatter: v => fmtYiValue(v) }, splitLine: gl2 },
      dataZoom: [{ type: 'inside' }, { type: 'slider', ...dzSlider() }],
      series: totals.map((t, i) => ({
        name: t.name || t.id, type: 'line',
        data: (t.cumulative || []).map(v => v == null ? null : +(v / 1e8).toFixed(1)),
        symbol: 'none', lineStyle: { color: T.series[i % T.series.length], width: 1.2 }, itemStyle: { color: T.series[i % T.series.length] },
      })),
    })
  }
  // 恐慌指数趋势：情绪分折线按档位着色（visualMap 按值分段，单值数据 dimension=0）
  if (sentiData.value.length) {
    const s = sentiData.value
    const dates = s.map(x => x.date)
    const scores = s.map(x => x.score)
    make('mv-senti', {
      tooltip: { trigger: 'axis', formatter: p => {
        const i = p[0]?.dataIndex
        if (i == null || !s[i]) return ''
        const x = s[i]
        return `<div style="font-size:12px"><b>${x.date}</b> ${sentiLabel(x.level)} <b>${x.score}</b> 分` +
          (x.score_rt != null ? `<span style="color:${T.faint}">（实时口径 ${x.score_rt}）</span>` : '') + `<br/>` +
          `涨 ${x.up} / 跌 ${x.down} · 涨停 ${x.limit_up} / 跌停 ${x.limit_down} · 中位 ${x.median == null ? '—' : x.median + '%'}</div>`
      } },
      grid: GRID_STD,
      xAxis: xOpt(dates),
      yAxis: { min: 0, max: 100, splitLine: gl2 },
      dataZoom: [{ type: 'inside' }, { type: 'slider', ...dzSlider() }],
      visualMap: { show: false, dimension: 0, pieces: [
        { lt: 25, color: T.down }, { gte: 25, lt: 45, color: T.warn },
        { gte: 45, lt: 70, color: T.series[3] }, { gte: 70, color: T.up } ] },
      series: [{ name: '情绪分', type: 'line', data: scores, symbol: 'none', lineStyle: { width: 1.4 },
        markLine: { silent: true, symbol: 'none', data: [
          { yAxis: 25, label: { formatter: '恐慌线 25', fontSize: 10, color: T.faint }, lineStyle: { color: T.down, type: 'dashed', width: 1 } },
          { yAxis: 45, label: { formatter: '偏弱线 45', fontSize: 10, color: T.faint }, lineStyle: { color: T.warn, type: 'dashed', width: 1 } },
          { yAxis: 70, label: { formatter: '强势线 70', fontSize: 10, color: T.faint }, lineStyle: { color: T.up, type: 'dashed', width: 1 } } ] } }],
    })
  }
}

function redrawAll() {
  drawIdxCharts()
  drawValCharts()
  drawRatioChart()
  drawMarketCharts()
}

// ── 加载 ──
async function loadIdxCharts(seq) {
  // 指数相关：K线组 + 估值（切指数/切区间时重取，全市场图不动）
  const d = rangeDays.value
  const [rk, rv] = await Promise.allSettled([
    axios.get(`${API}/api/stock/${idxBare.value}/kline?days=${d}&type=index`),
    axios.get(`${API}/api/market/valuation/${midx.value}?days=6000`),
  ])
  if (seq !== loadSeq) return
  klineData.value = rk.status === 'fulfilled' ? (rk.value.data.kline || []) : []
  valData.value = rv.status === 'fulfilled' ? rv.value.data : null
  await nextTick()
  if (seq !== loadSeq) return
  drawIdxCharts()
  drawValCharts()
}

async function loadRatio() {
  // 比值中枢：分子跟随当前指数，分母/窗口切换时单独重取（有独立序号防竞态）
  const seq = ++ratioSeq
  try {
    const r = await axios.get(`${API}/api/market/ratio-percentile`, {
      params: { num: idxBare.value, den: ratioDen.value, window: ratioWin.value },
    })
    if (seq !== ratioSeq) return
    ratioData.value = r.data
  } catch (e) {
    if (seq !== ratioSeq) return
    ratioData.value = null
  }
  await nextTick()
  if (seq !== ratioSeq) return
  drawRatioChart()
}

function switchDen(code) {
  if (ratioDen.value === code) return
  ratioDen.value = code
  loadRatio()
}

function switchWin(v) {
  if (ratioWin.value === v) return
  ratioWin.value = v
  loadRatio()
}

async function loadMarketCharts(seq) {
  // 全市场角度：宽度/情绪趋势/资金流/两融/行业流（不随指数切换变化）
  const [rb, rm, rg, rs, rv] = await Promise.allSettled([
    axios.get(`${API}/api/market/breadth?days=500`),
    axios.get(`${API}/api/market/moneyflow?days=4500`),
    axios.get(`${API}/api/market/margin?days=4000`),
    axios.get(`${API}/api/sector_flow_history?days=60`),
    axios.get(`${API}/api/market/sentiment-history?days=3000`),
  ])
  if (seq !== loadSeq) return
  breadthData.value = rb.status === 'fulfilled' ? (rb.value.data.data || []) : []
  sentiData.value = rv.status === 'fulfilled' ? (rv.value.data.data || []) : []
  mfData.value = rm.status === 'fulfilled' ? (rm.value.data.data || []) : []
  marginData.value = rg.status === 'fulfilled' ? (rg.value.data.data || []) : []
  sectorData.value = rs.status === 'fulfilled' ? rs.value.data : {}
  await nextTick()
  if (seq !== loadSeq) return
  drawMarketCharts()
}

async function loadOverview(seq) {
  try {
    const r = await axios.get(`${API}/api/market/overview`)
    if (seq !== loadSeq) return
    ovIndices.value = r.data.indices || []
    ovMoneyflow.value = r.data.moneyflow || null
    ovMargin.value = r.data.margin || null
  } catch (e) { /* 侧栏 best-effort */ }
}

async function loadMacro(seq) {
  try {
    const r = await axios.get(`${API}/api/market/macro`)
    if (seq !== loadSeq) return
    macroData.value = r.data.series || {}
  } catch (e) { /* 宏观卡 best-effort */ }
}

async function load() {
  const seq = ++loadSeq
  loadOverview(seq)
  loadMacro(seq)
  loadIdxCharts(seq)
  loadRatio()
  await loadMarketCharts(seq)
}

// 指数切换：URL 同步交给 nav store watch，只重取指数相关图（含比值分子）
watch(midx, () => { loadIdxCharts(++loadSeq); loadRatio() })

// 主题切换：全量重绘（chartColors Proxy 每次取值都读当前主题）
const theme = useThemeStore()
watch(() => theme.isDark, () => nextTick(redrawAll))

function resizeAll() {
  for (const el of document.querySelectorAll('.mv-chart')) {
    if (el._echart) el._echart.resize()
  }
}

onMounted(() => {
  load()
  window.addEventListener('resize', resizeAll)
})
onBeforeUnmount(() => {
  // 注意必须在 onBeforeUnmount（DOM 还在）dispose，onUnmounted 时已查不到节点
  loadSeq++
  for (const el of document.querySelectorAll('.mv-chart')) {
    if (el._echart) { el._echart.dispose(); delete el._echart }
  }
})
onUnmounted(() => {
  window.removeEventListener('resize', resizeAll)
})
</script>

<style scoped>
.mv { padding: 4px; }
.mv-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }
.mv-title { font-size: 16px; font-weight: 600; color: var(--c-text); }
.mv-sort-btn { color: var(--c-text-dim); }
.mv-sort-btn:hover { color: var(--c-text); }
.mv-idx-chips, .mv-ranges { display: flex; gap: 4px; flex-wrap: wrap; }
.mv-chip {
  border: 1px solid var(--c-border); background: var(--c-card-bg); color: var(--c-text-dim);
  border-radius: 4px; padding: 3px 10px; font-size: var(--text-meta); cursor: pointer;
}
.mv-chip:hover { color: var(--c-text); border-color: var(--c-text-faint); }
.mv-chip.active { background: var(--c-accent); border-color: var(--c-accent); color: #fff; }
.mv-grid { display: grid; grid-template-columns: minmax(0, 1fr) 330px; gap: 10px; align-items: start; }
.mv-left { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.mv-side { display: flex; flex-direction: column; gap: 10px; position: sticky; top: 8px; }
@media (max-width: 1000px) { .mv-grid { grid-template-columns: 1fr; } .mv-side { position: static; } }
.mv-panel { background: var(--c-card-bg); border: 1px solid var(--c-border); border-radius: var(--radius, 6px); padding: 10px 12px; }
.mv-h { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; font-size: 13px; color: var(--c-text); }
.mv-quote { font-size: 13px; font-weight: 600; }
.mv-quote.up { color: var(--c-up); }
.mv-quote.down { color: var(--c-down); }
.mv-daterange { font-size: var(--text-meta); color: var(--c-text-faint); margin-left: auto; }
.mv-pct { font-size: var(--text-meta); color: var(--c-text-dim); }
.mv-pct b.up, .up { color: var(--c-up); }
.mv-pct b.down, .down { color: var(--c-down); }
.mv-chart { height: 240px; width: 100%; }
.mv-chart-kline { height: 320px; }
.mv-chart-ratio { height: 380px; }
.mv-ratio-ctrl { display: inline-flex; gap: 4px; align-items: center; }
.mv-chip-sm { padding: 1px 6px; font-size: 11px; }
.mv-table { width: 100%; border-collapse: collapse; font-size: var(--text-meta); }
.mv-table th { color: var(--c-text-faint); font-weight: 500; text-align: left; padding: 4px 5px; border-bottom: 1px solid var(--c-border-light); white-space: nowrap; }
.mv-table td { color: var(--c-text); padding: 4px 5px; border-bottom: 1px solid var(--c-border-light); white-space: nowrap; }
.mv-table tbody tr { cursor: pointer; }
.mv-table tbody tr.sel td { background: color-mix(in srgb, var(--c-accent) 10%, transparent); }
.mv-note { margin-top: 6px; font-size: var(--text-meta); color: var(--c-text-faint); }
.mv-dim { color: var(--c-text-faint); }

/* 图表排序弹窗（与个股详情页同款交互） */
.chart-order-hint { font-size: var(--text-meta); color: var(--c-text-dim); margin-bottom: 8px; }
.chart-order-list { display: flex; flex-direction: column; gap: 4px; max-height: 52vh; overflow-y: auto; padding-right: 2px; }
.chart-order-row {
  display: flex; align-items: center; gap: 8px; padding: 6px 8px;
  border: 1px solid var(--c-border); border-radius: var(--radius);
  background: var(--c-card-bg); cursor: grab;
}
.chart-order-row.dragging { opacity: .55; border-color: var(--c-primary, #2080f0); }
.chart-order-grip { color: var(--c-text-faint); display: flex; align-items: center; }
.chart-order-no { width: 20px; text-align: right; font-variant-numeric: tabular-nums; font-size: var(--text-meta); color: var(--c-text-faint); }
.chart-order-label { flex: 1; font-size: 13px; color: var(--c-text); }
.chart-order-move {
  width: 24px; height: 22px; border: 1px solid var(--c-border); border-radius: 4px;
  background: transparent; color: var(--c-text-dim); cursor: pointer; line-height: 1;
}
.chart-order-move:hover:not(:disabled) { color: var(--c-text); border-color: var(--c-text-dim); }
.chart-order-move:disabled { opacity: .3; cursor: default; }
</style>
