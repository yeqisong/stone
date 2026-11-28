<template>
<div class="peg" :style="{ order }">
  <div class="peg-head">
    <span class="peg-title"><AppIcon name="target" :size="13" /> PEG：{{ idxName }} 成分股</span>
    <span v-if="coverage" class="peg-sub">
      有效 {{ coverage.with_peg }}/{{ coverage.members }} 只
      <span class="peg-dim">（PE {{ coverage.with_pe }} · 一致预期 {{ coverage.with_consensus }}<template
        v-if="coverage.with_peg_full != null"> · 完整口径 {{ coverage.with_peg_full }}</template>）</span>
    </span>
    <div class="peg-ctrl">
      <n-select v-model:value="industry" size="tiny" :options="industryOptions"
                style="width:132px" :consistent-menu-width="false" />
      <span class="peg-dim">G = G_hist + 0.5×max(G_cons−G_hist, 0)</span>
    </div>
  </div>

  <div v-if="loading" class="peg-state">加载成分与一致预期…</div>
  <div v-else-if="error" class="peg-state peg-err">{{ error }}</div>
  <div v-else-if="!allItems.length" class="peg-state">
    暂无有效 PEG（需 PE_TTM &gt; 0、G &gt; 0；完整口径另需一致预期机构 ≥ 3）。
    <span v-if="coverage">成分 {{ coverage.members }} 只，其中一致预期覆盖 {{ coverage.with_consensus }} 只。</span>
    <span v-if="!metaConsensusDate">一致预期数据尚未回补，请先跑 scripts/backfill_report_rc.py。</span>
  </div>
  <template v-else>
    <div class="peg-selected" v-if="selected">
      <span class="peg-selected-name">{{ selected.name }} <span class="peg-dim">{{ selected.code }}</span></span>
      <span class="peg-chip">{{ selected.industry_l1 }}</span>
      <span>PE_TTM <b>{{ selected.pe_ttm ?? '—' }}</b></span>
      <span>G <b>{{ fmtG(selected.g) }}</b>
        <span class="peg-dim">（历史 {{ fmtG(selected.g_hist) }} / 预期 {{ fmtG(selected.g_cons) }}）</span>
      </span>
      <span>PEG <b class="peg-strong">{{ selected.peg ?? '—' }}</b></span>
      <span class="peg-chip" :class="{ 'peg-chip-warn': selected.peg_basis !== 'full' }">
        {{ selected.peg_basis === 'full' ? '一致预期+历史' : '实际增长口径（占位）' }}
      </span>
      <span class="peg-dim">截面分位 {{ selected.peg_pct ?? '—' }}% · 机构 {{ selected.org_count }}</span>
      <span v-if="flagText(selected)" class="peg-warn">{{ flagText(selected) }}</span>
    </div>

    <div v-if="!filteredItems.length" class="peg-state">该行业当前无有效 PEG。</div>
    <div ref="scatterEl" class="peg-chart peg-scatter"></div>
    <div class="peg-legend">
      <button v-for="ind in legendIndustries" :key="ind.name" type="button"
              class="peg-legend-item" :class="{ active: industry === ind.name }"
              @click="toggleIndustry(ind.name)">
        <i :style="{ background: colorOf(ind.name) }"></i>{{ ind.name }}
        <span class="peg-dim">{{ ind.count }}</span>
      </button>
    </div>
    <div class="peg-note">
      横轴 G（%）= 归母净利增速；纵向虚线为 PEG=0.5 / 1 / 2 等值线（PE = PEG×G）。
      点为当前指数成分中 PEG 有效者，颜色按申万一级行业；点击切换下方个股。
      一致预期历史回补期间（report_rc 限 10 次/天），未覆盖个股以「实际增长口径」
      （仅 G_hist）占位（散点半透明），默认选中 PEG 最低者（完整口径且 PEG≤1 时优先）。
      坐标轴对极端增速/估值做了截断，超出者不显示。
    </div>

    <div class="peg-sub-head">
      <span><AppIcon name="activity" :size="13" /> {{ selected?.name || '—' }} PEG 历史与滚动分位带</span>
      <span v-if="histMeta" class="peg-dim">
        {{ histMeta.display_start }} ~ {{ histMeta.end }} · 窗口 {{ histMeta.window }} 交易日
      </span>
    </div>
    <div v-if="histLoading" class="peg-state">加载 PEG 历史…</div>
    <div v-else-if="!histData.length" class="peg-state">该股暂无 PEG 历史（PE 或一致预期覆盖不足）。</div>
    <template v-else>
      <div ref="bandEl" class="peg-chart peg-band"></div>
      <div v-if="bandClip" class="peg-clip-note">
        纵轴按分位带缩放至 {{ bandClip.yMax }}；PEG 峰值 {{ bandClip.maxPeg }}，超出部分截断显示。
      </div>
      <div ref="pctEl" class="peg-chart peg-pct"></div>
      <div class="peg-note">
        PEG 带为该股自身 PEG 的滚动 20/50/80 分位（窗口 {{ histMeta?.window || 1250 }} 交易日、
        至少 {{ histMeta?.min_periods || 250 }} 个有效值；PEG&gt;10 按 10 计，避免近零增速的
        千倍离群值压平分位带）；分位线为当前 PEG 在自身历史中的位置。
        口径：G 用归母净利，历史 5 年 CAGR 打底、一致预期只向上收缩 50%；亏损/负增长显示 N/A。
        历史分位只描述相对位置，不构成买卖建议。
        <span v-if="histMeta?.fallback_points && histMeta?.first_consensus" class="peg-warn">
          一致预期历史回补中：{{ histMeta.fallback_points }} 个交易日早于首个快照
          （{{ histMeta.first_consensus }}），该段 PEG 仅用实际增长口径（G_hist）占位，
          回补完成后自动切换为完整口径。
        </span>
        <span v-else-if="histMeta?.fallback_points" class="peg-warn">
          该股暂无一致预期快照（券商覆盖不足或尚未回补），整段 PEG 按实际增长口径
          （G_hist）计算，不代表前瞻一致预期。
        </span>
        <span v-if="histMeta?.consensus_points" class="peg-warn">
          一致预期为抽样口径：历史快照每 20 个交易日抽样一次（report_rc 每天仅 10 次配额，
          180 天窗口里只有 6~7 个发布日），机构数中位 {{ histMeta.org_median ?? '—' }}、
          最低 {{ histMeta.org_min ?? '—' }}，低于真实券商覆盖；回补完成后这段口径也不会变。
        </span>
      </div>
    </template>
  </template>
</div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { NSelect } from 'naive-ui'
import axios from 'axios'
import * as echarts from 'echarts'
import { chartColors as T, axisStyle } from '../utils/chart'
import { useThemeStore } from '../stores/theme'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  idx: { type: String, default: '000300.SH' },
  idxName: { type: String, default: '沪深300' },
  order: { type: Number, default: 99 },
})

const API = window.location.origin
const theme = useThemeStore()

const loading = ref(false)
const error = ref('')
const allItems = ref([])
const industries = ref([])
const coverage = ref(null)
const meta = ref(null)
const industry = ref('')
const selectedCode = ref('')
const histLoading = ref(false)
const histData = ref([])
const histLatest = ref(null)
const histMeta = ref(null)
const bandClip = ref(null)

const scatterEl = ref(null)
const bandEl = ref(null)
const pctEl = ref(null)
let scatterChart = null
let bandChart = null
let pctChart = null
let crossSeq = 0
let histSeq = 0

const PALETTE = [
  '#2f6fb3', '#c22b3f', '#b26a00', '#0a7a5a', '#6b4fa8', '#0e7490',
  '#a34d8c', '#5b6068', '#3b7f4a', '#b8860b', '#4682b4', '#8b4513',
  '#7a5c3e', '#4a6fa5', '#8a4f7d', '#5f7a3a',
]
const colorMap = new Map()
function colorOf(ind) {
  if (!colorMap.has(ind)) colorMap.set(ind, PALETTE[colorMap.size % PALETTE.length])
  return colorMap.get(ind)
}

const industryOptions = computed(() => [
  { label: '全部行业', value: '' },
  ...legendIndustries.value.map(x => ({ label: `${x.name}（${x.count}）`, value: x.name })),
])
const filteredItems = computed(() => {
  const arr = industry.value
    ? allItems.value.filter(x => x.industry_l1 === industry.value)
    : allItems.value
  return [...arr].sort((a, b) => {
    const fa = a.peg_basis === 'full' ? 0 : 1
    const fb = b.peg_basis === 'full' ? 0 : 1
    return fa - fb || a.peg - b.peg
  })
})
const selected = computed(() => allItems.value.find(x => x.code === selectedCode.value) || null)
const legendIndustries = computed(() => {
  const counts = new Map()
  for (const it of allItems.value) {
    counts.set(it.industry_l1, (counts.get(it.industry_l1) || 0) + 1)
  }
  return industries.value.filter(x => counts.has(x.name))
    .map(x => ({ name: x.name, count: counts.get(x.name) }))
})
const metaConsensusDate = computed(() => meta.value?.consensus_date)

function fmtG(v) {
  if (v == null || !Number.isFinite(Number(v))) return '—'
  return (Number(v) * 100).toFixed(1) + '%'
}
function niceMax(raw, floor, cap, halfStep = false) {
  const v = Math.min(Math.max(Number(raw) || 0, floor), cap)
  const step = halfStep ? (v <= 3 ? 0.5 : v <= 12 ? 2 : 5)
    : (v <= 12 ? 2 : v <= 30 ? 5 : v <= 60 ? 10 : 20)
  return Math.min(Math.ceil(v / step) * step, cap)
}
function axisNum(v) {
  const n = Number(v)
  return Number.isFinite(n) ? String(Math.round(n * 10) / 10) : ''
}
// 与后端 _default_code 一致：完整口径且 PEG≤1 才优先，否则全表最低
function defaultOf(items) {
  const full = items.filter(x => x.peg_basis === 'full')
  if (full.length && full[0].peg <= 1) return full[0].code
  let best = null
  for (const it of items) if (!best || it.peg < best.peg) best = it
  return best?.code || ''
}
function flagText(it) {
  const f = it.flags || 0
  const out = []
  if (f & 32) out.push('缺一致预期，仅历史 G')
  else if (f & 16) out.push('预期回退 2/1 年')
  if (f & 8) out.push('缺 5 年历史锚，仅预期 G')
  if (f & 2) out.push('预测不完整')
  return out.join(' · ')
}

async function loadCross() {
  const seq = ++crossSeq
  disposeCharts('all')
  loading.value = true
  error.value = ''
  try {
    const { data } = await axios.get(`${API}/api/market/peg`, {
      params: { index_code: props.idx, lam: 0.5 },
    })
    if (seq !== crossSeq) return
    meta.value = data
    allItems.value = data.items || []
    industries.value = data.industries || []
    coverage.value = data.coverage || null
    colorMap.clear()
    for (const it of allItems.value) colorOf(it.industry_l1)
    // 默认选中：API 已按 PEG 升序，取有效最低；行业过滤变化时同理
    const pool = filteredItems.value
    if (!pool.some(x => x.code === selectedCode.value)) {
      selectedCode.value = defaultOf(pool)
    }
    // 先结束 loading，v-else 里的图表容器才会挂载；否则 drawScatter 拿到 null
    loading.value = false
    if (selectedCode.value) await loadHistory(selectedCode.value)
    else { histData.value = []; histLatest.value = null; histMeta.value = null; bandClip.value = null }
    await nextTick()
    drawScatter()
  } catch (e) {
    if (seq !== crossSeq) return
    error.value = e?.response?.data?.detail || 'PEG 成分数据加载失败'
    allItems.value = []
  } finally {
    if (seq === crossSeq) loading.value = false
  }
}

async function loadHistory(code) {
  const seq = ++histSeq
  disposeCharts('hist')
  histLoading.value = true
  bandClip.value = null
  try {
    const { data } = await axios.get(`${API}/api/stock/${code}/peg_history`, {
      params: { years: 5, lam: 0.5 },
    })
    if (seq !== histSeq) return
    histData.value = data.data || []
    histLatest.value = data.latest || null
    histMeta.value = data.meta || null
    // 同上：先结束 histLoading，band/pct 容器才存在
    histLoading.value = false
    await nextTick()
    drawBand()
    drawPct()
  } catch (e) {
    if (seq !== histSeq) return
    histData.value = []
    histLatest.value = null
    histMeta.value = null
  } finally {
    if (seq === histSeq) histLoading.value = false
  }
}

function selectStock(code) {
  if (!code || code === selectedCode.value) return
  selectedCode.value = code
  loadHistory(code)
}

function toggleIndustry(name) {
  industry.value = industry.value === name ? '' : name
}

watch(industry, async () => {
  const pool = filteredItems.value
  if (!pool.some(x => x.code === selectedCode.value)) {
    selectedCode.value = defaultOf(pool)
    if (selectedCode.value) loadHistory(selectedCode.value)
    else { histData.value = []; histLatest.value = null; histMeta.value = null; bandClip.value = null }
  }
  await nextTick()
  drawScatter()
})

watch(() => props.idx, () => {
  selectedCode.value = ''
  histData.value = []
  histLatest.value = null
  histMeta.value = null
  bandClip.value = null
  industry.value = ''
  loadCross()
})

watch(selectedCode, () => nextTick(drawScatter))

watch(() => theme.isDark, () => nextTick(drawAll))

function initChart(el, group) {
  let c = el?._echart
  if (!c) { c = echarts.init(el); el._echart = c }
  if (group) { c.group = group; echarts.connect(group) }
  return c
}

function disposeCharts(which = 'all') {
  const list = which === 'hist'
    ? [[bandChart, bandEl], [pctChart, pctEl]]
    : [[scatterChart, scatterEl], [bandChart, bandEl], [pctChart, pctEl]]
  for (const [c, el] of list) {
    try { c?.dispose() } catch (e) {}
    if (el?.value?._echart) delete el.value._echart
  }
  if (which === 'all') scatterChart = null
  bandChart = pctChart = null
}

function baseTooltip() {
  return {
    backgroundColor: T.surface, borderColor: T.line,
    textStyle: { color: T.text, fontSize: 12 },
  }
}

function drawScatter() {
  if (!scatterEl.value) return
  if (!filteredItems.value.length) {
    scatterChart?.clear()
    return
  }
  scatterChart = initChart(scatterEl.value)
  const items = filteredItems.value
  const gVals = items.map(x => Math.max(0, (x.g || 0) * 100))
  const peVals = items.map(x => x.pe_ttm || 0)
  const xMax = niceMax(Math.max(...gVals) * 1.06, 8, 80)
  const yMax = niceMax(Math.max(...peVals) * 1.08, 10, 150)

  // 按行业分组着色（同行业共享颜色，图例在 HTML 中）
  const groups = new Map()
  for (const it of items) {
    if (!groups.has(it.industry_l1)) groups.set(it.industry_l1, [])
    groups.get(it.industry_l1).push(it)
  }
  const series = []
  for (const [ind, arr] of groups) {
    series.push({
      name: ind, type: 'scatter', z: 3,
      symbolSize: items.length > 1200 ? 5 : (items.length > 500 ? 6 : 8),
      itemStyle: { color: colorOf(ind), opacity: items.length > 500 ? 0.6 : 0.78 },
      emphasis: { itemStyle: { opacity: 1, borderColor: T.text, borderWidth: 1 } },
      data: arr.map(it => ({
        value: [Math.max(0, it.g * 100), it.pe_ttm], code: it.code, item: it,
        itemStyle: { opacity: it.peg_basis === 'full' ? 1 : 0.35 },
      })),
    })
  }
  // PEG 等值线：PE = PEG × G（截断到坐标轴内）
  for (const p of [0.5, 1, 2]) {
    const xEnd = Math.min(xMax, yMax / p)
    series.push({
      name: `PEG=${p}`, type: 'line', z: 1, silent: true,
      showSymbol: false, symbol: 'none',
      lineStyle: { type: 'dashed', width: 1, color: T.faint, opacity: 0.7 },
      endLabel: { show: true, formatter: `PEG=${p}`, color: T.faint, fontSize: 10 },
      tooltip: { show: false },
      data: [[0, 0], [xEnd, p * xEnd]],
    })
  }
  const sel = selected.value
  if (sel) {
    series.push({
      name: '选中', type: 'scatter', z: 5, symbolSize: 15,
      itemStyle: { color: T.text, borderColor: T.surface, borderWidth: 2 },
      label: {
        show: true, position: 'top', color: T.text, fontSize: 11, fontWeight: 600,
        formatter: sel.name,
      },
      data: [{ value: [Math.max(0, sel.g * 100), sel.pe_ttm], code: sel.code, item: sel }],
    })
  }

  scatterChart.setOption({
    backgroundColor: 'transparent',
    animation: false,
    grid: { left: 58, right: 46, top: 28, bottom: 38 },
    tooltip: {
      ...baseTooltip(), trigger: 'item',
      formatter: p => {
        const d = p.data?.item
        if (!d) return ''
        return `<b>${d.name}</b> <span style="opacity:.65">${d.code}</span><br/>`
          + `${d.industry_l1}<br/>`
          + `PE_TTM <b>${d.pe_ttm ?? '—'}</b><br/>`
          + `G <b>${fmtG(d.g)}</b>（历史 ${fmtG(d.g_hist)} / 预期 ${fmtG(d.g_cons)}）<br/>`
          + `PEG <b>${d.peg ?? '—'}</b> · 截面分位 ${d.peg_pct ?? '—'}%<br/>`
          + `${d.peg_basis === 'full' ? '一致预期+历史口径' : '实际增长口径（占位，一致预期未覆盖）'}<br/>`
          + `机构 ${d.org_count} · 市值 ${fmtMv(d.market_cap)}`
      },
    },
    xAxis: {
      type: 'value', min: 0, max: xMax,
      name: 'G（%）', nameTextStyle: { color: T.dim, fontSize: 11 },
      ...axisStyle(T),
      axisLabel: { color: T.dim, fontSize: 11, formatter: axisNum },
    },
    yAxis: {
      type: 'value', min: 0, max: yMax,
      name: 'PE_TTM', nameTextStyle: { color: T.dim, fontSize: 11 },
      ...axisStyle(T),
      axisLabel: { color: T.dim, fontSize: 11, formatter: axisNum },
    },
    series,
  }, true)
  scatterChart.off('click')
  scatterChart.on('click', p => {
    const code = p.data?.code || p.data?.item?.code
    if (code) selectStock(code)
  })
}

function drawBand() {
  if (!bandEl.value || !histData.value.length) return
  bandChart = initChart(bandEl.value)
  const rows = histData.value
  const dates = rows.map(r => r.date)
  const bandData = rows.map((r, i) => [i, r.band20, r.band80])
  const bandColor = T.series[3] || T.info

  const latestIdx = (() => {
    for (let i = rows.length - 1; i >= 0; i--) if (rows[i].peg != null) return i
    return -1
  })()
  // 纵轴按 20-80 分位带的量级缩放：近零增速会把 PEG 推到几十上百倍，
  // 全区间会把分位带压成一条线；原始值仍保留在 tooltip/选中摘要中。
  const bandHi = Math.max(0, ...rows.map(r => r.band80 ?? 0))
  const bandTop = niceMax(Math.max(2, bandHi * 1.5), 2, 10, true)
  const pegMax = Math.max(0, ...rows.map(r => r.peg ?? 0))
  bandClip.value = pegMax > bandTop * 1.01
    ? { yMax: bandTop, maxPeg: Number(pegMax.toFixed(2)) }
    : null
  const latestY = latestIdx >= 0
    ? Math.min(rows[latestIdx].peg, bandTop) : null

  bandChart.setOption({
    backgroundColor: 'transparent',
    animation: false,
    grid: { left: 52, right: 52, top: 24, bottom: 40 },
    tooltip: {
      ...baseTooltip(), trigger: 'axis',
      formatter: ps => {
        const i = ps[0]?.dataIndex
        if (i == null) return ''
        const r = rows[i]
        return `<b>${r.date}</b><br/>PEG <b>${r.peg ?? '—'}</b><br/>`
          + `PE_TTM ${r.pe_ttm ?? '—'}<br/>`
          + `G ${fmtG(r.g)}（历史 ${fmtG(r.g_hist)} / 预期 ${fmtG(r.g_cons)}）<br/>`
          + `分位带 ${r.band20 ?? '—'} / ${r.band50 ?? '—'} / ${r.band80 ?? '—'}<br/>`
          + `历史分位 ${r.peg_pct ?? '—'}%`
      },
    },
    xAxis: {
      type: 'category', data: dates, boundaryGap: false,
      ...axisStyle(T), axisLabel: { color: T.dim, fontSize: 10, hideOverlap: true },
    },
    yAxis: {
      type: 'value', min: 0, max: bandTop,
      ...axisStyle(T),
      axisLabel: { color: T.dim, fontSize: 10, formatter: axisNum },
    },
    dataZoom: [
      { type: 'inside', xAxisIndex: 0 },
      {
        type: 'slider', xAxisIndex: 0, height: 14, bottom: 4, start: 0, end: 100,
        borderColor: 'transparent', backgroundColor: T.line, fillerColor: T.line,
        handleStyle: { color: T.faint }, moveHandleStyle: { color: T.line },
        textStyle: { color: T.dim, fontSize: 10 }, labelStyle: { color: 'transparent' },
        dataBackground: { lineStyle: { color: 'transparent' }, areaStyle: { color: T.line } },
        selectedDataBackground: { lineStyle: { color: T.series[3] }, areaStyle: { color: T.line } },
      },
    ],
    series: [
      {
        name: '20-80分位带', type: 'custom', z: 1, silent: true,
        encode: { x: 0, y: [1, 2] },
        data: bandData,
        renderItem: (params, api) => {
          const i = api.value(0)
          const lo = api.value(1), hi = api.value(2)
          const lo2 = rows[i + 1]?.band20, hi2 = rows[i + 1]?.band80
          if (lo == null || hi == null || lo2 == null || hi2 == null
              || i >= rows.length - 1) return null
          const p0 = api.coord([i, lo])
          const p1 = api.coord([i, hi])
          const p2 = api.coord([i + 1, hi2])
          const p3 = api.coord([i + 1, lo2])
          return {
            type: 'polygon',
            shape: { points: [p0, p1, p2, p3] },
            style: { fill: bandColor, opacity: 0.14 },
          }
        },
      },
      {
        name: 'P20', type: 'line', data: rows.map(r => r.band20), z: 2,
        showSymbol: false, lineStyle: { width: 1, type: 'dashed', color: T.faint, opacity: 0.7 },
      },
      {
        name: 'P80', type: 'line', data: rows.map(r => r.band80), z: 2,
        showSymbol: false, lineStyle: { width: 1, type: 'dashed', color: T.faint, opacity: 0.7 },
      },
      {
        name: 'P50', type: 'line', data: rows.map(r => r.band50), z: 2,
        showSymbol: false, lineStyle: { width: 1, type: 'dotted', color: T.series[5] || T.info },
      },
      {
        name: 'PEG', type: 'line', data: rows.map(r => r.peg), z: 4,
        showSymbol: false, connectNulls: false,
        lineStyle: { width: 2, color: T.info },
        itemStyle: { color: T.info },
        markPoint: latestIdx >= 0 ? {
          symbol: 'circle', symbolSize: 7,
          data: [{ coord: [latestIdx, latestY] }],
          itemStyle: { color: T.info, borderColor: T.surface, borderWidth: 1 },
          label: {
            show: true, position: 'top', color: T.info, fontSize: 11, fontWeight: 600,
            formatter: `PEG ${rows[latestIdx].peg}`,
          },
        } : undefined,
      },
    ],
  }, true)
}

function drawPct() {
  if (!pctEl.value || !histData.value.length) return
  pctChart = initChart(pctEl.value)
  const rows = histData.value
  const dates = rows.map(r => r.date)
  const pcts = rows.map(r => r.peg_pct)
  const latestIdx = (() => {
    for (let i = rows.length - 1; i >= 0; i--) if (rows[i].peg_pct != null) return i
    return -1
  })()

  pctChart.setOption({
    backgroundColor: 'transparent',
    animation: false,
    grid: { left: 46, right: 22, top: 20, bottom: 34 },
    tooltip: {
      ...baseTooltip(), trigger: 'axis',
      formatter: ps => {
        const i = ps[0]?.dataIndex
        if (i == null) return ''
        const r = rows[i]
        return `<b>${r.date}</b><br/>PEG ${r.peg ?? '—'}<br/>历史分位 <b>${r.peg_pct ?? '—'}%</b>`
      },
    },
    xAxis: {
      type: 'category', data: dates, boundaryGap: false,
      ...axisStyle(T), axisLabel: { color: T.dim, fontSize: 10, hideOverlap: true },
    },
    yAxis: {
      type: 'value', min: 0, max: 100,
      ...axisStyle(T), axisLabel: { color: T.dim, fontSize: 10, formatter: '{value}%' },
    },
    series: [{
      name: 'PEG历史分位', type: 'line', data: pcts, z: 3,
      showSymbol: false, connectNulls: false,
      lineStyle: { width: 2, color: T.series[2] || T.info },
      itemStyle: { color: T.series[2] || T.info },
      areaStyle: { color: T.series[2] || T.info, opacity: 0.08 },
      markLine: {
        silent: true, symbol: 'none',
        lineStyle: { type: 'dashed', color: T.faint, opacity: 0.7 },
        label: { color: T.dim, fontSize: 10, formatter: '{b}' },
        data: [{ yAxis: 20, name: '20%' }, { yAxis: 50, name: '50%' },
               { yAxis: 80, name: '80%' }],
      },
      markArea: {
        silent: true,
        itemStyle: { color: T.up, opacity: 0.05 },
        data: [[{ yAxis: 80 }, { yAxis: 100 }]],
      },
      markPoint: latestIdx >= 0 ? {
        symbol: 'circle', symbolSize: 7,
        data: [{ coord: [latestIdx, pcts[latestIdx]] }],
        itemStyle: { color: T.series[2] || T.info, borderColor: T.surface, borderWidth: 1 },
        label: {
          show: true, position: 'top', color: T.series[2] || T.info,
          fontSize: 11, fontWeight: 600, formatter: `${pcts[latestIdx]}%`,
        },
      } : undefined,
    }],
  }, true)
}

function fmtMv(v) {
  if (v == null || !Number.isFinite(Number(v))) return '—'
  return (Number(v) / 1e8).toFixed(0) + '亿'
}

function drawAll() {
  drawScatter()
  drawBand()
  drawPct()
}

function resizeAll() {
  scatterChart?.resize()
  bandChart?.resize()
  pctChart?.resize()
}

onMounted(() => {
  loadCross()
  window.addEventListener('resize', resizeAll)
})
onBeforeUnmount(() => {
  crossSeq++
  histSeq++
  window.removeEventListener('resize', resizeAll)
  // DOM 还在时 dispose（onUnmounted 时节点已移除）
  disposeCharts('all')
})
</script>

<style scoped>
.peg {
  background: var(--c-card-bg);
  border: 1px solid var(--c-border);
  border-radius: var(--radius, 6px);
  padding: 10px 12px;
  min-width: 0;
}
.peg-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 6px; }
.peg-title { font-size: 13px; color: var(--c-text); display: inline-flex; align-items: center; gap: 6px; }
.peg-sub { font-size: var(--text-meta); color: var(--c-text-dim); }
.peg-ctrl { margin-left: auto; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.peg-dim { color: var(--c-text-faint); font-size: var(--text-meta); }
.peg-state {
  padding: 18px 8px; text-align: center; color: var(--c-text-faint);
  font-size: var(--text-meta); line-height: 1.7;
}
.peg-err { color: var(--c-down); }
.peg-selected {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 6px 8px; margin-bottom: 4px; border-radius: 4px;
  background: color-mix(in srgb, var(--c-accent) 7%, transparent);
  font-size: var(--text-meta); color: var(--c-text-dim);
}
.peg-selected b { color: var(--c-text); }
.peg-selected-name { color: var(--c-text); font-weight: 600; font-size: 13px; }
.peg-strong { color: var(--c-accent); font-size: 13px; }
.peg-chip {
  border: 1px solid var(--c-border); border-radius: 3px; padding: 0 5px;
  color: var(--c-text-dim);
}
.peg-chip-warn { border-color: var(--c-warning, #8a5a00); color: var(--c-warning, #8a5a00); }
.peg-warn { color: var(--c-warning, #8a5a00); }
.peg-chart { width: 100%; }
.peg-scatter { height: 360px; }
.peg-band { height: 300px; }
.peg-pct { height: 190px; }
.peg-clip-note {
  margin-top: -2px; font-size: 10px; color: var(--c-text-faint);
  line-height: 1.5;
}
.peg-legend {
  display: flex; gap: 4px; flex-wrap: wrap; max-height: 76px; overflow-y: auto;
  padding: 2px 0 4px;
}
.peg-legend-item {
  display: inline-flex; align-items: center; gap: 4px; max-width: 150px;
  border: 1px solid transparent; background: transparent; color: var(--c-text-dim);
  font-size: 11px; padding: 1px 5px; border-radius: 3px; cursor: pointer;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.peg-legend-item:hover { border-color: var(--c-border); color: var(--c-text); }
.peg-legend-item.active { border-color: var(--c-accent); color: var(--c-text); }
.peg-legend-item i { width: 7px; height: 7px; border-radius: 50%; flex: none; }
.peg-note { margin-top: 6px; font-size: var(--text-meta); color: var(--c-text-faint); line-height: 1.65; }
.peg-sub-head {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin: 12px 0 4px; font-size: 13px; color: var(--c-text);
}
.peg-sub-head .peg-dim { margin-left: auto; }
</style>
