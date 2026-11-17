<template>
<div>
  <n-space align="center" style="margin-bottom:8px" wrap>
    <n-button-group size="tiny">
      <n-button size="tiny" :type="!intraday?'primary':'default'" @click="switchMode(false)">日线</n-button>
      <n-button size="tiny" :type="intraday?'primary':'default'" @click="switchMode(true)">分时市值</n-button>
    </n-button-group>
    <n-button-group v-if="!intraday" size="tiny">
  <n-button size="tiny" :type="metric==='mcap'?'primary':'default'" @click="switchMetric('mcap')"><AppIcon name="bar-chart-2" :size="13" />  市值</n-button>
  <n-button size="tiny" :type="metric==='volume'?'primary':'default'" @click="switchMetric('volume')"><AppIcon name="bar-chart-2" :size="13" />  成交量</n-button>
  <n-button size="tiny" :type="metric==='amount'?'primary':'default'" @click="switchMetric('amount')"><AppIcon name="dollar-sign" :size="13" />  成交额</n-button>
  <n-button size="tiny" :type="metric==='pe'?'primary':'default'" @click="switchMetric('pe')"><AppIcon name="bar-chart-2" :size="13" />  PE</n-button>
</n-button-group>
    <n-date-picker v-model:formatted-value="selDate" type="date" value-format="yyyy-MM-dd" size="tiny" style="width:140px" @update:formatted-value="onDateChange" />
    <n-button v-if="!intraday" size="tiny" @click="showGenModal = true"><AppIcon name="zap" :size="13" />  生成</n-button>
    <n-select v-if="intraday" v-model:value="intradaySlot" size="tiny" :options="slotOptions" style="width:110px" @update:value="onSlotChange" />
    <n-button-group v-if="!intraday" size="tiny">
      <n-button size="tiny" :type="colorMode==='chg1d'?'primary':'default'" @click="switchColorMode('chg1d')" title="当日涨跌">日涨跌</n-button>
      <n-button size="tiny" :type="colorMode==='chg20d'?'primary':'default'" @click="switchColorMode('chg20d')" title="20 日涨跌（超跌=深绿，反转视角）">20日</n-button>
      <n-button size="tiny" :type="colorMode==='volr'?'primary':'default'" @click="switchColorMode('volr')" title="量比（今日量/前20日均量）">量比</n-button>
    </n-button-group>
    <n-button-group v-else size="tiny">
      <n-button size="tiny" :type="colorMode==='chg1d'?'primary':'default'" @click="switchColorMode('chg1d')" title="当日涨跌（相对昨收）">日涨跌</n-button>
      <n-button size="tiny" :type="colorMode==='chg10m'?'primary':'default'" @click="switchColorMode('chg10m')" title="近 10 分钟变化（资金刚去哪）">近10分钟</n-button>
    </n-button-group>
    <n-button size="tiny" :type="onlySignal ? 'primary' : 'default'" @click="onlySignal = !onlySignal; renderChart()"><AppIcon name="s" :size="13" />  只看信号</n-button>
    <span v-if="!loading && !noData" style="font-size: var(--text-meta);color:var(--c-text-dim);white-space:nowrap" title="金色=当日信号 / 蓝实线=实盘持仓 / 青虚线=纸面组合">
      <span style="color:var(--c-warn)">━</span>信号 / <span style="color:var(--c-info)">━</span>实盘 / <span style="color:var(--c-text-dim)">╌</span>纸面
    </span>
    <n-tag v-if="!loading && !noData && levelLabel" size="small" style="margin-left:auto">{{levelLabel}}</n-tag>
    <n-button v-if="drillStack.length>0" size="tiny" @click="goBack"><AppIcon name="arrow-left" :size="12" /> {{drillStack.length>1?drillStack[drillStack.length-2].name:'全部行业'}}</n-button>
  </n-space>

  <!-- 市场宽度：分时模式的核心“风向” -->
  <div v-if="intraday && breadth" class="tm-breadth">
    <span>上涨 <b class="up">{{ breadth.up }}</b> / 下跌 <b class="down">{{ breadth.down }}</b> / 平 {{ breadth.flat }}</span>
    <span>中位数 <b :class="breadth.median >= 0 ? 'up' : 'down'">{{ breadth.median > 0 ? '+' : '' }}{{ breadth.median }}%</b></span>
    <span>成交 <b>{{ fmtAmount(breadth.amount) }}</b></span>
    <span>涨停 <b class="up">{{ breadth.limit_up }}</b> / 跌停 <b class="down">{{ breadth.limit_down }}</b></span>
    <span>领涨 <b class="up">{{ breadth.top_l1?.[0]?.name || '—' }} {{ breadth.top_l1?.[0] ? (breadth.top_l1[0].chg_pct > 0 ? '+' : '') + breadth.top_l1[0].chg_pct + '%' : '' }}</b></span>
    <span>领跌 <b class="down">{{ breadth.bottom_l1?.[0]?.name || '—' }} {{ breadth.bottom_l1?.[0] ? (breadth.bottom_l1[0].chg_pct > 0 ? '+' : '') + breadth.bottom_l1[0].chg_pct + '%' : '' }}</b></span>
    <span v-if="colorMode==='chg10m' && !hasPrevSlot" class="tm-b-hint">近10分钟：首档暂无对比</span>
    <span class="tm-b-asof">{{ breadth.asof }} 快照<template v-if="refreshing"> · 正在生成新快照…</template></span>
  </div>

  <n-spin v-if="loading" style="padding:60px" />
  <div v-else-if="intraday && refreshing && !treeData.length" style="text-align:center;padding:60px;color:var(--c-text-dim)">
    <n-spin size="large" style="margin-bottom:14px" />
    <div style="font-size:14px">正在生成首张分时快照…<br><span style="font-size: var(--text-meta)">全市场约 5,500 只，第一次约需 15–25 秒</span></div>
  </div>
  <div v-else-if="noData" style="text-align:center;padding:60px;color:var(--c-text-dim)">
    <div style="font-size:48px;margin-bottom:12px"><AppIcon name="inbox" :size="44" style="color:var(--c-text-faint)" /></div>
    <div v-if="intraday" style="font-size:14px">{{selDate}} 暂无分时快照<br><span style="font-size: var(--text-meta)">分时快照只在交易日 09:30–15:00 每 10 分钟生成，保留最近 5 个交易日</span></div>
    <div v-else style="font-size:14px">{{selDate}} 暂无树图数据<br><span style="font-size: var(--text-meta)">请等待数据采集完成后重试，或选择其他日期</span></div>
  </div>
  <div v-else class="tm-intraday-flex">
    <div class="tm-chart-col">
      <div :id="'treemap-chart'" :class="{ 'tm-chart-intraday': intraday }" style="width:100%;height:calc(100vh - 190px - var(--h5-tabbar-h, 0px));min-height:400px"></div>
    </div>
    <!-- 行业资金流：与树图并排，按日累计/近10分钟切换 -->
    <div v-if="intraday && sectorFlows" class="tm-flow-panel">
      <div class="tm-flow-head">
        <b>行业资金流</b>
        <span class="tm-flow-mode" @click="cycleFlowMode" :title="flowModeTip">
          {{ flowModeLabel }}<span class="tm-flow-cycle">⇄</span>
        </span>
        <span class="tm-flow-trend" @click="openTrend" title="多日资金流趋势">趋势</span>
      </div>
      <div class="tm-flow-cols">
        <span>行业</span>
        <span class="tm-flow-col-sort" @click="cycleSort" :title="sortTip">
          资金流 <b>{{ sortLabel }}</b>
        </span>
      </div>
      <div v-if="flowMode==='m10' && !sectorFlows.has_prev" class="tm-flow-empty">
        首档没有上一档对比；10 分钟后第二档生成后，这里显示这 10 分钟的资金方向。
      </div>
      <div v-if="flowMode==='main' && !sectorFlows.main_date" class="tm-flow-empty">
        该日期没有收盘后的主力资金数据。
      </div>
      <div v-if="!(flowMode==='m10' && !sectorFlows.has_prev) && !(flowMode==='main' && !sectorFlows.main_date)" class="tm-flow-list">
        <div v-for="f in flowRows" :key="f.id" class="tm-flow-row"
          :class="{ active: activeSector === f.id }"
          @click="drillSector(f)"
          :title="`${f.name}：主力净流入 ${fmtFlow(f.main_net)}（${sectorFlows.main_date||'—'}）；压力日 ${fmtFlow(f.flow_day)} / 10分 ${fmtFlow(f.flow_10m)}；成交额 ${fmtFlow(f.amount)}；涨跌 ${f.chg_pct>0?'+':''}${f.chg_pct}%；点击查看该板块二级树图`">
          <span class="tm-flow-name">{{ f.name }}</span>
          <div class="tm-flow-track">
            <span class="tm-flow-zero"></span>
            <i :style="{
              width: flowBarW(f) + '%',
              left: flowKey(f) >= 0 ? '50%' : (50 - flowBarW(f)) + '%',
              background: flowKey(f) >= 0 ? 'var(--c-up)' : 'var(--c-down)'
            }"></i>
          </div>
          <span class="tm-flow-val" :style="{ color: flowKey(f) >= 0 ? 'var(--c-up)' : 'var(--c-down)' }">
            {{ (flowMode==='m10' && !sectorFlows.has_prev) ? '—' : fmtFlow(flowKey(f)) }}
          </span>
        </div>
      </div>
      <div class="tm-flow-note">
        <template v-if="flowMode==='main'">
          主力 = tushare 大单+特大单净额（收盘后，{{ sectorFlows.main_date || '—' }}）；与压力估算不要相加。
        </template>
        <template v-else>
          压力 = 成交额×CLV（日）/ 相邻增量×方向（10分）；绝对值是买卖压力强度，不是净流出金额。
        </template>
      </div>
    </div>
  </div>

  <!-- 生成确认弹窗 -->
  <n-modal v-model:show="showGenModal" preset="card" title="生成树图" style="width:360px;max-width:85vw" :mask-closable="false">
    <div style="text-align:center;padding:10px 0">
      <div style="font-size:14px;color:var(--c-text);margin-bottom:16px">为 {{selDate}} 重新生成树图数据？<br><span style="font-size: var(--text-meta);color:var(--c-text-dim)">已有数据将被更新，无数据将新建</span></div>
      <div style="display:flex;gap:10px;justify-content:center">
        <n-button @click="showGenModal = false">取消</n-button>
        <n-button type="primary" @click="doGenerate" :loading="genLoading">生成</n-button>
      </div>
    </div>
  </n-modal>

  <!-- 行业资金流多日趋势 -->
  <n-modal v-model:show="showTrend" preset="card" title="行业资金流趋势"
    style="width:min(980px,96vw)" :bordered="false">
    <div class="tm-trend-body">
      <div class="tm-trend-head">
        <n-button-group size="tiny">
          <n-button v-for="d in [20,60,120]" :key="d" size="tiny" :type="trendDays===d?'primary':'default'"
            @click="setTrendDays(d)">{{ d }}日</n-button>
        </n-button-group>
        <span class="tm-trend-sub">主力净流入日累计（亿）· tushare 大单+特大单</span>
      </div>
      <n-spin v-if="trendLoading" style="padding:30px" />
      <template v-else-if="trendData && trendData.dates?.length">
        <div ref="trendChartEl" style="width:100%;height:340px"></div>
        <div class="tm-trend-table-wrap">
          <table class="tm-trend-table">
            <thead>
              <tr><th>行业</th><th>今日</th><th>5日</th><th>20日</th><th>连续</th><th>排名</th><th>涨跌(等权)</th></tr>
            </thead>
            <tbody>
              <tr v-for="s in trendRows" :key="s.id">
                <td>{{ s.name }}</td>
                <td :style="{color: s.today>=0?'var(--c-up)':'var(--c-down)'}">{{ fmtYi(s.today) }}</td>
                <td :style="{color: s.sum5>=0?'var(--c-up)':'var(--c-down)'}">{{ fmtYi(s.sum5) }}</td>
                <td :style="{color: s.sum20>=0?'var(--c-up)':'var(--c-down)'}">{{ fmtYi(s.sum20) }}</td>
                <td :style="{color: s.streak_sign>0?'var(--c-up)':s.streak_sign<0?'var(--c-down)':'var(--c-text-dim)'}">
                  {{ s.streak ? (s.streak_sign>0?'+':'-')+s.streak+'天' : '—' }}
                </td>
                <td>
                  {{ s.rank_prev ?? '—' }}→{{ s.rank_today ?? '—' }}
                  <span v-if="s.rank_change" :style="{color: s.rank_change>0?'var(--c-up)':'var(--c-down)'}">{{ s.rank_change>0?'↑':'↓' }}{{ Math.abs(s.rank_change) }}</span>
                </td>
                <td :style="{color: (s.chg_pct||0)>=0?'var(--c-up)':'var(--c-down)'}">{{ s.chg_pct==null?'—':(s.chg_pct>0?'+':'')+s.chg_pct.toFixed(2)+'%' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="tm-trend-note">主力净流入 = tushare 大单+特大单净额（收盘后）；正=净流入、负=净流出。涨跌为行业内个股等权平均。多日累计看趋势，单日看方向；与“压力估算”不要混用。</div>
      </template>
      <div v-else class="tm-trend-empty">
        暂无日频数据；请先运行 <code>python -m scripts.sector_flow --days 120</code> 回补。
      </div>
    </div>
  </n-modal>
</div>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { chartColors as T } from '../utils/chart'
import { NSpace, NDatePicker, NTag, NButton, NButtonGroup, NSpin, NModal, NSelect } from 'naive-ui'
import axios from 'axios'
import * as echarts from 'echarts'
import { useMarketStore } from '../stores/market'
import { useNavStore } from '../stores/nav'
import { useThemeStore } from '../stores/theme'

const store = useMarketStore()
const navStore = useNavStore()
const theme = useThemeStore()
const emit = defineEmits(['show-detail'])
const API = window.location.origin
const metric = ref('mcap')
const selDate = ref(store.selDate)
const loading = ref(true)
const showGenModal = ref(false)
const genLoading = ref(false)

// ── 分时市值模式 ──
const intraday = ref(false)
const intradaySlot = ref('latest')
const intradaySlots = ref([])
const breadth = ref(null)
const refreshing = ref(false)
const sectorFlows = ref(null)
const flowMode = ref('day')   // main | day | m10
const sortMode = ref('abs')   // abs | in | out
const sortLabel = computed(() => sortMode.value === 'in' ? '↓流入' : sortMode.value === 'out' ? '↑流出' : '⇅绝对值')
const sortTip = computed(() => sortMode.value === 'in'
  ? '当前按流入优先排序；点击切换为流出优先'
  : sortMode.value === 'out'
    ? '当前按流出优先排序；点击切换为绝对值'
    : '当前按绝对值排序；点击切换为流入优先')
function cycleSort(){
  sortMode.value = sortMode.value === 'abs' ? 'in' : sortMode.value === 'in' ? 'out' : 'abs'
}
let flowModePinned = false
let intradayTimer = null
const flowModeLabel = computed(() => flowMode.value === 'main' ? '主力'
  : flowMode.value === 'm10' ? '压力·10分' : '压力·日')
const flowModeTip = computed(() => {
  const sf = sectorFlows.value || {}
  if (flowMode.value === 'main') return `主力净流入（tushare 大单+特大单，收盘后，数据 ${sf.main_date || '—'}）；点击切换 → 压力·日`
  if (flowMode.value === 'day') return `压力·日（成交额×CLV，实时估算，快照 ${sf.slot || '—'}）；点击切换 → 压力·10分`
  return `压力·10分（相邻两档成交额增量×价格方向，快照 ${sf.slot || '—'}）；点击切换 → 主力`
})
function cycleFlowMode(){
  flowMode.value = flowMode.value === 'main' ? 'day' : flowMode.value === 'day' ? 'm10' : 'main'
  flowModePinned = true
}
const slotOptions = computed(() => [
  { label: '最新', value: 'latest' },
  ...intradaySlots.value.map(s => ({ label: s, value: s })),
])
const hasPrevSlot = computed(() => {
  if (!intraday.value) return false
  const s = intradaySlot.value
  if (s === 'latest') return intradaySlots.value.length > 1
  return intradaySlots.value.indexOf(s) > 0
})

const fmt = v => v != null ? Number(v).toLocaleString() : '0'
const fmtAmount = v => {
  if (v == null) return '—'
  if (v >= 1e12) return (v / 1e12).toFixed(2) + '万亿'
  if (v >= 1e8) return (v / 1e8).toFixed(0) + '亿'
  return (v / 1e4).toFixed(0) + '万'
}
const fmtFlow = v => {
  if (v == null) return '—'
  const sign = v > 0 ? '+' : v < 0 ? '-' : ''
  const a = Math.abs(v)
  if (a >= 1e8) return sign + (a / 1e8).toFixed(a >= 1e10 ? 0 : 1) + '亿'
  if (a >= 1e4) return sign + (a / 1e4).toFixed(0) + '万'
  return sign + a.toFixed(0)
}
const flowKey = f => flowMode.value === 'main' ? (f.main_net || 0)
  : flowMode.value === 'm10' ? f.flow_10m : f.flow_day
// 按绝对金额排序：最大的流入/流出都在最上面（方向看颜色）
const flowRows = computed(() => {
  const rows = flowMode.value === 'm10'
    ? (sectorFlows.value?.m10 || [])
    : (sectorFlows.value?.day || [])
  const out = [...rows]
  if (sortMode.value === 'in') {
    out.sort((a, b) => flowKey(b) - flowKey(a))
  } else if (sortMode.value === 'out') {
    out.sort((a, b) => flowKey(a) - flowKey(b))
  } else {
    out.sort((a, b) => Math.abs(flowKey(b)) - Math.abs(flowKey(a)))
  }
  return out
})
const flowMax = computed(() => Math.max(1, ...flowRows.value.map(f => Math.abs(flowKey(f)))))
const flowBarW = f => Math.max(1.5, Math.abs(flowKey(f)) / flowMax.value * 50)

// ── 行业资金流多日趋势 ──
const showTrend = ref(false)
const trendDays = ref(60)
const trendData = ref(null)
const trendLoading = ref(false)
const trendChartEl = ref(null)
let trendChart = null
const trendRows = computed(() => trendData.value?.sectors || [])
const fmtYi = v => {
  if (v == null) return '—'
  const sign = v > 0 ? '+' : v < 0 ? '-' : ''
  const a = Math.abs(v) / 1e8
  return sign + (Math.abs(a) >= 100 ? a.toFixed(0) : a.toFixed(1)) + '亿'
}
function openTrend(){ showTrend.value = true; loadTrend() }
async function loadTrend(){
  trendLoading.value = true
  try{
    const r = await axios.get(API + '/api/sector_flow_history', { params: { days: trendDays.value } })
    trendData.value = r.data
  }catch(e){ trendData.value = null }
  trendLoading.value = false
  await nextTick()
  if (trendData.value) setTimeout(renderTrend, 60)
}
function setTrendDays(d){ trendDays.value = d; loadTrend() }
function renderTrend(){
  if (!trendChartEl.value || !trendData.value?.dates?.length) return
  if (!trendChart) trendChart = echarts.init(trendChartEl.value)
  const dates = trendData.value.dates
  const top = new Set(trendData.value.top || [])
  let sectors = (trendData.value.sectors || []).filter(s => top.has(s.id))
  if (!sectors.length) sectors = (trendData.value.sectors || []).slice(0, 8)
  const palette = ['#5b8ff9', '#61d9a9', '#f6bd16', '#e8684a', '#8b6af7', '#4fd1d9', '#c76ee8', '#9aa0a6']
  const textDim = theme.colors.textDim
  const border = theme.colors.border
  trendChart.setOption({
    backgroundColor: theme.colors.bg,
    grid: { left: 58, right: 16, top: 34, bottom: 28 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: theme.colors.cardBg,
      borderColor: border,
      textStyle: { color: theme.colors.text, fontSize: 12 },
      formatter: ps => {
        const d = ps[0]?.axisValue || ''
        return `<b>${d}</b><br/>` + ps.map(p => `${p.marker}${p.seriesName}: ${p.value == null ? '—' : (p.value > 0 ? '+' : '') + Number(p.value).toFixed(1) + '亿'}`).join('<br/>')
      },
    },
    legend: { top: 0, left: 0, itemWidth: 10, itemHeight: 8, textStyle: { color: textDim, fontSize: 11 } },
    xAxis: {
      type: 'category', data: dates, boundaryGap: false,
      axisLine: { lineStyle: { color: border } },
      axisLabel: { color: textDim, fontSize: 10, formatter: v => String(v).slice(5) },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: textDim, fontSize: 10, formatter: v => v + '亿' },
      splitLine: { lineStyle: { color: border, type: 'dashed' } },
    },
    series: sectors.map((s, i) => ({
      name: s.name, type: 'line', showSymbol: false,
      data: s.cumulative.map(v => +(v / 1e8).toFixed(2)),
      lineStyle: { width: 1.8, color: palette[i % palette.length] },
      itemStyle: { color: palette[i % palette.length] },
    })),
  }, true)
  trendChart.resize()
}
function disposeTrend(){ try{ trendChart?.dispose() }catch(e){} trendChart = null }
watch(showTrend, v => {
  if (v) { nextTick(() => setTimeout(renderTrend, 80)) } else { disposeTrend() }
})

const METRICS = ['mcap', 'volume', 'amount', 'pe']
const COLORS = ['chg1d', 'chg20d', 'volr', 'chg10m']

function syncQuery() {
  // metric/color/mode/slot 体现在 URL（分享/刷新可恢复）
  const q = []
  if (!intraday.value && metric.value !== 'mcap') q.push('metric=' + metric.value)
  if (colorMode.value !== 'chg1d') q.push('color=' + colorMode.value)
  if (intraday.value) {
    q.push('mode=intraday')
    if (intradaySlot.value !== 'latest') q.push('slot=' + intradaySlot.value)
  }
  const qs = q.length ? '?' + q.join('&') : ''
  const target = '/market/' + selDate.value + qs
  if (location.hash.slice(1) !== target) history.replaceState(null, '', '#' + target)
}

function applyQuery() {
  const h = location.hash.slice(1)
  const q = h.includes('?') ? new URLSearchParams(h.split('?')[1]) : null
  if (!q) return false
  let changed = false
  const m = q.get('metric'), c = q.get('color')
  const mode = q.get('mode'), slot = q.get('slot')
  if (METRICS.includes(m) && m !== metric.value) { metric.value = m; changed = true }
  if (COLORS.includes(c) && c !== colorMode.value) { colorMode.value = c; changed = true }
  if (mode === 'intraday' && !intraday.value) { intraday.value = true; changed = true }
  if (slot && slot !== intradaySlot.value) { intradaySlot.value = slot; changed = true }
  return changed
}

function switchMode(on) {
  if (intraday.value === on) return
  intraday.value = on
  drillStack.value = []
  store.drillStack = []
  colorMode.value = 'chg1d'
  flowModePinned = false
  if (on) {
    metric.value = 'mcap'
    intradaySlot.value = 'latest'
  } else {
    breadth.value = null
    sectorFlows.value = null
    refreshing.value = false
    clearTimeout(intradayTimer)
  }
  syncQuery()
  loadTree()
}

function onSlotChange(v) {
  intradaySlot.value = v
  drillStack.value = []
  syncQuery()
  loadTree()
}

function switchMetric(m) {
  metric.value = m
  store.drillStack = []
  drillStack.value = []
  syncQuery()
  loadTree()
}
const noData = ref(false)
const treeData = ref([])       // 全量 tree
const drillStack = ref([])     // 下钻路径 [{name,id}]

const levelLabel = computed(() => {
  if (!drillStack.value.length) return ''
  const s = drillStack.value
  const names = s.map(n=>n.name)
  const ns = s.length > 3 ? '...' : names.join(' › ')
  return `${ns} | ${treeData.value.length} 项`
})

function onDateChange() {
  drillStack.value = []
  flowModePinned = false
  if (intraday.value) { intradaySlot.value = 'latest'; refreshing.value = false }
  store.setDate(selDate.value)
  if (selDate.value) syncQuery()
  loadTree()
}

const colorMode = ref('chg1d')   // chg1d | chg20d | volr
const onlySignal = ref(false)
const sigLive = ref({})          // 当日实时信号 {code: {strength, reason, model_version}}

function switchColorMode(m) { colorMode.value = m; syncQuery(); renderChart() }

const holdLive = ref({})   // 实盘持仓 {code: {qty, cost}}
const paperLive = ref({})  // 纸面组合持仓 {code: {shares, buy_price}}

async function loadHoldings() {
  // 实盘持仓 + 纸面组合（均失败容忍——无持仓/未登录场景静默跳过）
  try {
    const r = await axios.get(API + '/api/portfolio')
    const m = {}
    for (const p of (r.data.positions || [])) {
      if (p.stock_code) m[p.stock_code] = { qty: p.quantity, cost: p.cost_price }
    }
    holdLive.value = m
  } catch (e) { holdLive.value = {} }
  try {
    const r = await axios.get(API + '/api/v1/models/paper-portfolio')
    const m = {}
    for (const p of (r.data.positions || [])) {
      if (p.stock_code) m[p.stock_code] = { shares: p.shares, buy_price: p.buy_price }
    }
    paperLive.value = m
  } catch (e) { paperLive.value = {} }
}

async function loadSignals() {
  // 实时叠加当日买点信号（树图缓存的信号快照仅用于历史日期回看）
  try {
    const r = await axios.get(API + '/api/buy_signals', { params: { signal_date: selDate.value, top_n: 100 } })
    const m = {}
    for (const sg of (r.data.signals || [])) {
      if (sg.stock_code) m[sg.stock_code] = { strength: sg.strength ?? sg.score ?? 0, reason: sg.reason || sg.strategy || '', model_version: sg.model_version || '' }
    }
    sigLive.value = m
  } catch (e) { sigLive.value = {} }
}

// 色带：按模式取值 → 颜色
function colorOfNode(n) {
  if (colorMode.value === 'chg10m') {
    const v = n.detail?.d10
    return v == null ? 'var(--c-text-faint)' : colorForChg(v, 1.2)  // 10 分钟幅度小，±1.2% 封顶
  }
  if (colorMode.value === 'chg20d') {
    const v = n.detail?.s20
    return v == null ? 'var(--c-text-faint)' : colorForChg(v / 4)   // 20 日幅度大，±12% 封顶
  }
  if (colorMode.value === 'volr') {
    const v = n.detail?.vr
    if (v == null) return '#555'
    // 量比发散映射：1.0 中性灰白，越高越红（热度），低量淡青
    if (v >= 1) { const p = Math.min((v - 1) / 2, 1); return `rgb(235,${Math.round(200 - p*130)},${Math.round(190 - p*150)})` }
    const p = Math.min((1 - v) / 0.8, 1); return `rgb(${Math.round(200 - p*60)},235,238)`
  }
  return colorForChg(n.chg_pct)
}

function colorForChg(chg, cap = 3) {
  // 固定色相：涨=红、跌=绿；幅度越大颜色越深（默认 ±3% 封顶）
  if (chg > 0) {
    const p = Math.min(chg / cap, 1)
    return `rgb(235,${Math.round(95 - p * 72)},${Math.round(95 - p * 72)})`
  }
  if (chg < 0) {
    const p = Math.min(Math.abs(chg) / cap, 1)
    return `rgb(${Math.round(95 - p * 72)},200,${Math.round(95 - p * 72)})`
  }
  return '#555'
}

async function loadTree() {
  loading.value = true; noData.value = false
  try {
    if (intraday.value) {
      const params = { trade_date: selDate.value, slot: intradaySlot.value }
      if (drillStack.value.length > 0) {
        params.parent = drillStack.value[drillStack.value.length-1].id
      }
      const r = await axios.get(API + '/api/treemap_intraday', { params })
      intradaySlots.value = r.data.slots || []
      breadth.value = r.data.breadth || null
      sectorFlows.value = r.data.sector_flows || null
      // 收盘后默认看主力净流入（真实口径）；盘中默认看实时压力估算
      if (!flowModePinned && sectorFlows.value) {
        flowMode.value = sectorFlows.value.main_date === selDate.value ? 'main' : 'day'
      }
      refreshing.value = !!r.data.refreshing
      treeData.value = r.data.children || []
      noData.value = !treeData.value.length && !refreshing.value
    } else {
      const params = { trade_date: selDate.value, metric: metric.value }
      if (drillStack.value.length > 0) {
        params.parent = drillStack.value[drillStack.value.length-1].id
      }
      const r = await axios.get(API+'/api/treemap_data', { params })
      breadth.value = null
      sectorFlows.value = null
      refreshing.value = false
      treeData.value = r.data.children || []
      noData.value = !treeData.value.length
    }
    await Promise.all([loadSignals(), loadHoldings()])
    loading.value = false; await nextTick()
    if (!noData.value && treeData.value.length) renderChart()
  } catch(e) {
    noData.value = true
    loading.value = false
  } finally {
    // 分时模式：新快照生成中 5s 后重试，否则 60s 轮询一次；
    // 后端按 10 分钟 slot 判断是否需要重新拉全市场，页面轮询本身很轻。
    clearTimeout(intradayTimer)
    if (intraday.value) {
      intradayTimer = setTimeout(loadTree, refreshing.value ? 5000 : 60000)
    }
  }
}

// 行业块统计按当前着色模式计算：20日用 s20，10分钟用 d10，其余用当日涨跌
function indStats(list) {
  const vals = list.map(v => colorMode.value === 'chg20d' ? v.detail?.s20
    : colorMode.value === 'chg10m' ? v.detail?.d10 : v.chg)
    .filter(v => v != null)
  const up = vals.length ? Math.round(vals.filter(v => v > 0).length / vals.length * 1000) / 10 : null
  const vs = [...vals].sort((a, b) => a - b)
  const med = vs.length ? Math.round(((vs.length % 2 ? vs[(vs.length-1)/2] : (vs[vs.length/2-1]+vs[vs.length/2])/2)) * 100) / 100 : null
  return { up, med, n: vals.length }
}

function filterSignal(nodes) {
  // 只看信号：递归丢弃无信号叶子，空行业块剔除
  const out = []
  for (const n of nodes) {
    if (n.type === 'stock') {
      if (sigLive.value[n.id] || n.detail?.signal) out.push(n)
    } else if (n.children) {
      const kids = filterSignal(n.children)
      if (kids.length) out.push({ ...n, children: kids })
    }
  }
  return out
}

function buildTreemapSeries(nodes, depth, borderColor) {
  return nodes.map(n => {
    const sig = n.type === 'stock' ? (sigLive.value[n.id] || n.detail?.signal) : null
    const hold = n.type === 'stock' ? holdLive.value[n.id] : null
    const paper = n.type === 'stock' ? paperLive.value[n.id] : null
    // 描边优先级：实盘持仓(蓝实线) > 纸面组合(青虚线) > 信号(金)；名字前缀兜底标注
    let bColor = borderColor, bWidth = 0.5, bType = 'solid', namePrefix = ''
    if (hold) { bColor = theme.colors.info; bWidth = 3; namePrefix = '◆' }   // canvas 不解析 CSS 变量，取真实色值
    else if (paper) { bColor = T.series[5]; bWidth = 2; bType = 'dashed'; namePrefix = '◇' }
    else if (sig) { bColor = T.warn; bWidth = 2.5 }
    const item = {
      name: namePrefix + n.name,
      value: n.value,
      itemStyle: { color: colorOfNode(n), borderColor: bColor, borderWidth: bWidth, borderType: bType },
      chg_pct: n.chg_pct,
      _type: n.type,
      _id: n.id,
      _detail: n.detail,
      _sig: sig || null,
      _hold: hold || null,
      _paper: paper || null,
    }
    if (n.children && depth > 0) {
      let kids = n.children
      if (onlySignal.value && depth >= 1) kids = filterSignal(kids)
      // 行业块角标：块内信号数（实时优先，回看用缓存 detail.sig）
      const sigCnt = n.type !== 'stock'
        ? n.children.filter(c => sigLive.value[c.id] || c.detail?.signal).length || n.detail?.sig || 0
        : 0
      item.name = sigCnt > 0 ? `${n.name} ✦${sigCnt}` : n.name
      item._up = indStats(n.children)   // 随色带模式重算（renderChart 时重建）
      item.children = buildTreemapSeries(kids, depth - 1)
    }
    return item
  })
}

function renderChart() {
  const el = document.getElementById('treemap-chart')
  if (!el) return
  if (el._echart) el._echart.dispose()
  const chartBg = theme.colors.bg
  const chart = echarts.init(el)
  el._echart = chart

  const rootNodes = onlySignal.value ? filterSignal(treeData.value) : treeData.value
  const data = buildTreemapSeries(rootNodes, 2, chartBg)

  chart.setOption({
    backgroundColor: chartBg,
    tooltip: {
      formatter: p => {
        const d = p.data, detail = d._detail || {}
        const metricLabels = {mcap:'市值',volume:'成交量',amount:'成交额',pe:'PE分位'}
        const mLabel = metricLabels[metric.value] || '指标'
        const val = detail.val !== undefined ? detail.val : d.value
        let html = `<b>${d.name}</b>`
        if (d._type === 'stock') {
          html += `<br/>代码: ${d._id}<br/>股价: ¥${detail.price||'?'}`
          if (metric.value === 'volume') html += `<br/>${mLabel}: ${fmt(val)}股`
          else if (metric.value === 'amount') html += `<br/>${mLabel}: ¥${(val/1e8).toFixed(2)}亿`
          else if (metric.value === 'pe') html += `<br/>${mLabel}: ${(100-(val||0)).toFixed(0)}%`
          else html += `<br/>${mLabel}: ¥${(val/1e8).toFixed(1)}亿`
          html += `<br/>涨跌: ${d.chg_pct>0?'+':''}${(d.chg_pct||0).toFixed(2)}%`
          if (intraday.value && detail.d10 != null) html += `<br/>近10分钟: ${detail.d10>0?'+':''}${detail.d10.toFixed(2)}%`
          if (detail.s20 != null) html += `<br/>20日: ${detail.s20>0?'+':''}${detail.s20.toFixed(1)}%`
          if (detail.vr != null) html += `<br/>量比: ${detail.vr.toFixed(2)}`
          if (detail.tr != null) html += `<br/>换手: ${detail.tr.toFixed(2)}%`
          html += `<br/>趋势: ${detail.trend_up!==false?'↑':'↓'}`
          if (d._sig) html += `<br/><span style="color:${T.warn}">★ 信号强度${d._sig.strength||'?'} ${d._sig.reason||''}</span>`
          if (d._hold) html += `<br/><span style="color:${T.up}">◆ 实盘持仓 ${d._hold.qty} 股 / 成本 ¥${Number(d._hold.cost).toFixed(2)}</span>`
          if (d._paper) html += `<br/><span style="color:${T.series[5]}">◇ 纸面持仓 ${d._paper.shares} 股（模拟口径） / 买价 ¥${Number(d._paper.buy_price).toFixed(2)}</span>`
        } else {
          html += `<br/>${mLabel}: ${metric.value==='volume'?fmt(val)+'股':metric.value==='amount'?'¥'+(val/1e8).toFixed(1)+'亿':metric.value==='pe'?(100-(val||0)/100).toFixed(0)+'%':'¥'+(val/1e8).toFixed(1)+'亿'}`
          html += `<br/>涨跌: ${(d.chg_pct||0)>0?'+':''}${(d.chg_pct||0).toFixed(2)}%`
          if (intraday.value && detail.d10 != null) html += `<br/>近10分钟: ${detail.d10>0?'+':''}${detail.d10.toFixed(2)}%`
          const up = d._up || {}
          const tag = colorMode.value === 'chg20d' ? '20日' : colorMode.value === 'chg10m' ? '10分钟' : '当日'
          if (up.up != null) html += `<br/>上涨家数(${tag}): ${up.up}%`
          else if (detail.up_ratio != null) html += `<br/>上涨家数: ${detail.up_ratio}%`
          if (up.med != null) html += `<br/>中位涨跌(${tag}): ${up.med>0?'+':''}${up.med}%`
          else if (detail.med_chg != null) html += `<br/>中位涨跌: ${detail.med_chg>0?'+':''}${detail.med_chg}%`
          if (detail.avg_tr) html += `<br/>平均换手: ${detail.avg_tr}%`
          if (detail.sig) html += `<br/><span style="color:${T.warn}">★ 信号 ${detail.sig} 只</span>`
        }
        return html
      }
    },
    series: [{
      type: 'treemap',
      roam: false,
      width: '100%', height: '100%',
      breadcrumb: { show: false },
      itemStyle: { borderColor: chartBg, borderWidth: 1 },
      levels: [
        { label: { show: false }, itemStyle: { borderColor: chartBg, borderWidth: 4 } },
        { label: { show: true, fontSize: 14, fontWeight: 'bold', color: theme.isDark ? '#fff' : '#1a1a2e', position: 'insideTopLeft', padding: [4,0,0,6] },
          // 行业标题条：单行统计（两行会溢出 22px 条带，v3.7 审查修复）；家数在 tooltip
          upperLabel: { show: true, fontSize: 13, fontWeight: 'bold', color: theme.isDark ? '#fff' : '#1a1a2e', height: 22,
            formatter: p => {
              const d = p.data
              if (d._type === 'l1' || d._type === 'l2') {
                const up = d._up?.up ?? d._detail?.up_ratio
                return `${d.name.replace(/ ✦\d+$/, '')} ↑${up ?? '?'}%${d._detail?.sig ? ` ✦${d._detail.sig}` : ''}`
              }
              return d.name
            } },
          itemStyle: { borderColor: chartBg, borderWidth: 2 } },
        { label: { show: true, fontSize: 11, color: theme.isDark ? '#fff' : '#1a1a2e' }, itemStyle: { borderColor: chartBg, borderWidth: 0.5 } },
      ],
      data: data,
      emphasis: { itemStyle: { borderColor: '#fff', borderWidth: 2 } }
    }]
  })

  chart.on('click', p => {
    const d = p.data
    if (d._type === 'stock') {
      if (d._id) emit('show-detail', d._id)
    } else if (d._type === 'l1') {
      drillStack.value.push({ name: d.name, id: d._id })
      loadTree()
    } else if (d._type === 'l2') {
      drillStack.value.push({ name: d.name, id: d._id })
      loadTree()
    }
  })
}

const activeSector = computed(() => drillStack.value[0]?.id || '')

/** 从资金流面板点击行业：直接下钻该 l1 的二级树图（与树图点击同一套分类）。 */
function drillSector(f) {
  if (!f?.id) return
  drillStack.value = [{ name: f.name, id: f.id }]
  store.drillStack = drillStack.value
  loadTree()
}

function goBack() {
  if (drillStack.value.length > 0) {
    drillStack.value.pop()
    loadTree()
  }
}

async function doGenerate() {
  genLoading.value = true
  try {
    // include_downstream: false — 树图不产生基础数据，无需跑 stats + daily_completeness
    await axios.post(API + '/api/dag_trigger', { node: 'treemap', date: selDate.value, include_downstream: false })
    showGenModal.value = false
  } catch(e) { noData.value = true; message.error('树图数据加载失败') } finally {
    genLoading.value = false
  }
}

async function loadLatestDate() {
  try {
    const r = await axios.get(API + '/api/treemap_latest_date')
    if (r.data && r.data.latest_date) {
      selDate.value = r.data.latest_date
      store.setDate(selDate.value)
      syncQuery()
    }
  } catch(e) {}
}

const _tm_resize = () => {
  const el = document.getElementById('treemap-chart')
  if (el && el._echart) el._echart.resize()
  trendChart?.resize()
}
const _tm_popstate = () => {
  if (navStore.tab !== 'm') return   // 组件已卸载时守卫
  if (applyQuery()) { drillStack.value = []; loadTree() }
}
window.addEventListener('popstate', _tm_popstate)
window.addEventListener('resize', _tm_resize)
onUnmounted(() => {
  clearTimeout(intradayTimer)
  disposeTrend()
  window.removeEventListener('resize', _tm_resize)
  window.removeEventListener('popstate', _tm_popstate)
  const el = document.getElementById('treemap-chart')
  if (el && el._echart) { el._echart.dispose(); el._echart = null }
})

watch(() => theme.isDark, () => nextTick(() => { renderChart() }))

onMounted(async () => {
  applyQuery()
  await loadLatestDate()
  await nextTick()
  loadTree()
})

// 主题切换时重新渲染图表
watch(() => theme.isDark, () => {
  if (!loading.value && !noData.value) {
    nextTick(() => renderChart())
  }
  if (showTrend.value) nextTick(() => renderTrend())
})
</script>

<style scoped>
/* 市场宽度条：分时模式的“风向”摘要 */
.tm-breadth {
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  margin: 0 0 6px; padding: 6px 10px; font-size: var(--text-meta);
  color: var(--c-text-dim); background: var(--c-card-bg);
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
}
.tm-breadth b { color: var(--c-text); font-variant-numeric: tabular-nums; }
.tm-breadth b.up { color: var(--c-up); }
.tm-breadth b.down { color: var(--c-down); }
.tm-b-asof { margin-left: auto; color: var(--c-text-faint); white-space: nowrap; }
.tm-b-hint { color: var(--c-text-faint); }
/* 分时模式多一条宽度条：图表高度相应收一点 */
.tm-chart-intraday { height: calc(100vh - 238px - var(--h5-tabbar-h, 0px)) !important; }
/* 分时模式：树图 + 行业资金流并排 */
.tm-intraday-flex { display: flex; gap: 8px; align-items: stretch; }
.tm-chart-col { flex: 1; min-width: 0; }
.tm-flow-panel {
  width: 320px; flex-shrink: 0; display: flex; flex-direction: column;
  max-height: calc(100vh - 238px - var(--h5-tabbar-h, 0px));
  padding: 8px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-card-bg);
}
.tm-flow-head { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; margin-bottom: 4px; }
.tm-flow-head b { font-size: 13px; }
.tm-flow-mode { font-size: var(--text-meta); color: var(--c-text-dim); padding: 1px 4px; border-radius: 3px;
  cursor: pointer; user-select: none; white-space: nowrap; }
.tm-flow-mode:hover { color: var(--c-text); background: var(--c-card-bg-hover); }
.tm-flow-cycle { font-size: 10px; margin-left: 2px; opacity: .55; }
.tm-flow-trend { font-size: var(--text-meta); color: var(--c-text-faint); padding: 1px 3px; border-radius: 3px;
  cursor: pointer; user-select: none; white-space: nowrap; }
.tm-flow-trend:hover { color: var(--c-text); background: var(--c-card-bg-hover); }
.tm-trend-body { max-height: 76vh; overflow-y: auto; }
.tm-trend-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
.tm-trend-sub { font-size: var(--text-meta); color: var(--c-text-faint); }
.tm-trend-table-wrap { max-height: 260px; overflow-y: auto; border: 1px solid var(--c-border); border-radius: var(--radius-sm); }
.tm-trend-table { width: 100%; border-collapse: collapse; font-size: var(--text-meta); }
.tm-trend-table th, .tm-trend-table td {
  padding: 4px 8px; border-bottom: 1px solid var(--c-border);
  text-align: right; white-space: nowrap;
}
.tm-trend-table th:first-child, .tm-trend-table td:first-child { text-align: left; }
.tm-trend-table thead th {
  position: sticky; top: 0; z-index: 1; background: var(--c-card-bg);
  color: var(--c-text-dim); font-weight: 500;
}
.tm-trend-note { margin-top: 8px; font-size: 10px; color: var(--c-text-faint); line-height: 1.6; }
.tm-trend-empty { padding: 30px; text-align: center; color: var(--c-text-faint); font-size: 13px; }
.tm-flow-empty { margin-bottom: 6px; font-size: var(--text-meta); color: var(--c-text-faint); line-height: 1.5; }
.tm-flow-cols {
  display: grid; grid-template-columns: 64px 1fr; gap: 6px; align-items: center;
  padding: 2px 0 3px; margin-bottom: 2px; font-size: 10px; color: var(--c-text-faint);
  border-bottom: 1px solid var(--c-border);
}
.tm-flow-col-sort { text-align: right; cursor: pointer; user-select: none; white-space: nowrap; }
.tm-flow-col-sort:hover { color: var(--c-text-dim); }
.tm-flow-col-sort b { font-weight: 500; color: var(--c-text-dim); }
.tm-flow-list { flex: 1; min-height: 0; overflow-y: auto; }
.tm-flow-row {
  display: grid; grid-template-columns: 64px minmax(0, 1fr) 72px; gap: 6px; align-items: center;
  padding: 2px 4px; margin: 0 -4px; font-size: var(--text-meta);
  cursor: pointer; border-radius: 3px;
}
.tm-flow-row:hover { background: var(--c-card-bg-hover); }
.tm-flow-row.active { background: color-mix(in srgb, var(--c-accent) 10%, transparent); }
.tm-flow-name { color: var(--c-text-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tm-flow-track { position: relative; height: 8px; border-radius: 4px; background: var(--c-border); }
.tm-flow-track i { position: absolute; top: 0; height: 100%; border-radius: 3px; }
.tm-flow-zero { position: absolute; left: 50%; top: -2px; width: 1px; height: 12px; background: var(--c-text-faint); opacity: .55; }
.tm-flow-val { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
.tm-flow-note { margin-top: 6px; font-size: 10px; color: var(--c-text-faint); line-height: 1.5; }
@media (max-width: 900px) {
  .tm-intraday-flex { flex-direction: column; }
  .tm-flow-panel { width: 100%; max-height: 280px; }
}
@media (max-width: 768px) {
  .tm-breadth { gap: 9px; }
  .tm-chart-intraday { height: calc(100vh - 300px - var(--h5-tabbar-h, 0px)) !important; }
}
</style>
