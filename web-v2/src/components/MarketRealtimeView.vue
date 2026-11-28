<template>
<div class="mr">
  <!-- 顶栏：交易时段 + 数据时点 -->
  <div class="mr-toolbar">
    <AppIcon name="activity" :size="15" />
    <span class="mr-title">实时盘</span>
    <span v-if="d" class="mr-session" :class="'ms-' + d.session">{{ d.session_label }}</span>
    <span class="mr-asof">{{ asofText }}</span>
    <span v-if="d && d.stale && d.session !== 'closed'" class="mr-stale">{{ staleText }}</span>
    <div style="flex:1" />
    <button class="mr-chip" @click="load">手动刷新</button>
    <button class="mr-chip" @click="nav.switchTab('k')">← 日K大盘</button>
  </div>

  <MarketSentiment />

  <!-- 实时空仓闸门横幅（design/13 批次三：市场闸门 × regime × 盘面情绪，仅展示） -->
  <div v-if="d && d.gate" class="mr-gate" :class="'gate-' + d.gate.level"
       :title="(d.gate.reasons || []).join('；')">
    <span class="mr-gate-label">开仓闸门</span>
    <b class="mr-gate-state">{{ d.gate.label }}</b>
    <span class="mr-gate-reason">{{ (d.gate.reasons || [])[0] || '' }}</span>
    <span v-if="d.gate.advice_entry" class="mr-gate-advice">{{ d.gate.advice_entry }}</span>
  </div>

  <div v-if="!d" class="mr-empty">等待数据…（后台每 60s 采集一次，开盘后约 1 分钟出数）</div>

  <template v-else>
    <!-- 指数分时组 -->
    <div class="mr-panel">
      <div class="mr-h">
        <span><AppIcon name="activity" :size="13" /> 指数分时</span>
        <span class="mr-meta">价格 + 昨收（虚线）· 腾讯分时 · 点击小图切换大图</span>
      </div>
      <div class="mr-trend-grid">
        <div v-for="ix in idxList" :key="ix.code" class="mr-trend-cell"
             :class="{ active: bigIdx === ix.code }" @click="bigIdx = ix.code">
          <div class="mr-trend-head">
            <span>{{ ix.name }}</span>
            <b :class="chgCls(ix.chg_pct)">{{ signed(ix.chg_pct) }}%</b>
          </div>
          <div :id="'mr-t-' + ix.code" class="mr-chart mr-mini"></div>
        </div>
      </div>
      <div :id="'mr-big'" class="mr-chart" style="height: 250px"></div>
    </div>

    <!-- 宽度 + 情绪轨迹 -->
    <div class="mr-row2">
      <div class="mr-panel">
        <div class="mr-h">
          <span><AppIcon name="list" :size="13" /> 盘面宽度</span>
          <span class="mr-meta">全市场快照口径（含两市成交额预估）</span>
        </div>
        <template v-if="d.breadth">
          <div class="mr-bar" :title="`涨 ${d.breadth.up} / 平 ${d.breadth.flat} / 跌 ${d.breadth.down}`">
            <i class="up" :style="{ width: pctW(d.breadth.up) }"></i>
            <i class="flat" :style="{ width: pctW(d.breadth.flat) }"></i>
            <i class="down" :style="{ width: pctW(d.breadth.down) }"></i>
          </div>
          <div class="mr-bar-legend">
            <span>涨 <b class="up">{{ d.breadth.up }}</b></span>
            <span>平 {{ d.breadth.flat }}</span>
            <span>跌 <b class="down">{{ d.breadth.down }}</b></span>
            <span>共 {{ d.breadth.total }}</span>
          </div>
          <div class="mr-stats">
            <span>涨停 <b class="up">{{ d.breadth.limit_up }}</b></span>
            <span>跌停 <b class="down">{{ d.breadth.limit_down }}</b></span>
            <span>炸板 <b class="warn">{{ ztBroken }}</b></span>
            <span>炸板率 <b class="warn">{{ pctText(d.zt && d.zt.broken_rate) }}</b></span>
            <span>中位 <b :class="chgCls(d.breadth.median)">{{ signed(d.breadth.median) }}%</b></span>
          </div>
          <div class="mr-stats" v-if="d.turnover">
            <span>两市成交 <b>{{ yi(d.turnover.today_cum) }}</b></span>
            <span>昨日同期 <b>{{ yi(d.turnover.yday_same) }}</b></span>
            <span>近4日均 <b>{{ yi(d.turnover.avg_prev_same) }}</b></span>
            <span>全天预估 <b class="hl">{{ yi(d.turnover.est_full) }}</b>
              <i class="mr-basis">{{ d.turnover.est_basis === 'linear' ? '(线性)' : '' }}</i></span>
          </div>
          <div :id="'mr-turnover'" class="mr-chart" style="height: 190px"></div>
          <div class="mr-meta">量能 = 上证综指 + 深证综指 逐分钟累计（亿元）</div>
        </template>
        <div v-else class="mr-empty2">宽度数据待采集</div>
      </div>
      <div class="mr-panel">
        <div class="mr-h">
          <span><AppIcon name="activity" :size="13" /> 当日情绪轨迹</span>
          <span class="mr-meta">三成分口径（宽度/中位/涨跌停）· 与日频恐慌指数序列可比</span>
        </div>
        <div :id="'mr-track'" class="mr-chart" style="height: 268px"></div>
        <div class="mr-meta" v-if="d.senti && d.senti.score != null">
          实时四成分分（含指数 VWAP）：<b>{{ d.senti.score }}</b> {{ d.senti.label }}
        </div>
      </div>
    </div>

    <!-- 涨停梯队 -->
    <div class="mr-panel">
      <div class="mr-h">
        <span><AppIcon name="candlestick" :size="13" /> 涨停梯队</span>
        <span class="mr-meta" v-if="d.zt">
          涨停 <b class="up">{{ d.zt.limit_up }}</b> · 炸板 <b class="warn">{{ d.zt.broken }}</b>
          · 连板高度 <b>{{ d.zt.max_lbc || '—' }} 板</b> · 东财 push2ex 口径<template v-if="ztDateText"> · {{ ztDateText }}</template>
        </span>
        <span class="mr-meta" v-else>涨停池待采集</span>
      </div>
      <div class="mr-row3" v-if="d.zt">
        <div>
          <div class="mr-sub">连板天梯</div>
          <div :id="'mr-ladder'" class="mr-chart" style="height: 210px"></div>
        </div>
        <div>
          <div class="mr-sub">连板高度 / 涨停家数历史（zt_pool_daily）</div>
          <div :id="'mr-zthist'" class="mr-chart" style="height: 210px"></div>
        </div>
      </div>
      <table v-if="poolRows.length" class="mr-table">
        <thead>
          <tr><th>名称</th><th>连板</th><th>几天几板</th><th>封单额</th><th>首封</th><th>炸板次</th><th>行业</th></tr>
        </thead>
        <tbody>
          <tr v-for="p in poolRows" :key="p.code" @click="nav.showDetail(p.code)" class="mr-row-link">
            <td><b>{{ p.name }}</b> <span class="mr-code">{{ p.code }}</span></td>
            <td><b :class="p.lbc >= 3 ? 'up' : ''">{{ p.lbc }}</b></td>
            <td>{{ p.zttj || '—' }}</td>
            <td>{{ yi(p.fund) }}</td>
            <td>{{ p.fbt || '—' }}</td>
            <td :class="p.zbc > 0 ? 'warn' : ''">{{ p.zbc }}</td>
            <td>{{ p.hybk }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="d.zt && d.zt.pool_truncated" class="mr-meta">
        仅展示前 {{ poolRows.length }} 家（按连板/封单排序），全量见日终落库 zt_pool_daily
      </div>
    </div>

    <!-- 板块雷达 + 大盘资金 -->
    <div class="mr-row2">
      <div class="mr-panel">
        <div class="mr-h">
          <span><AppIcon name="filter" :size="13" /> 板块雷达</span>
          <span class="mr-meta">东财行业口径（一级+细分混排，≠树图申万 L1）</span>
          <span style="flex:1"></span>
          <button v-for="b in BOARD_DIMS" :key="b.k" class="mr-chip"
                  :class="{ active: boardDim === b.k }" @click="boardDim = b.k">{{ b.label }}</button>
        </div>
        <div :id="'mr-boards'" class="mr-chart" style="height: 380px"></div>
      </div>
      <div class="mr-panel">
        <div class="mr-h">
          <span><AppIcon name="list" :size="13" /> 大盘主力资金</span>
          <span class="mr-meta">沪深两市当日累计净流入（东财）<template v-if="fflowDateText"> · {{ fflowDateText }}</template></span>
        </div>
        <div :id="'mr-fflow'" class="mr-chart" style="height: 380px"></div>
        <div class="mr-meta" v-if="d.fflow && d.fflow.latest != null">
          当前主力累计 <b :class="chgCls(yiNum(d.fflow.latest))">{{ yi(d.fflow.latest) }}</b>
        </div>
      </div>
    </div>

    <!-- 今日盘面异动（mk% 告警，WS 同步推送 + 此列表回看） -->
    <div class="mr-panel">
      <div class="mr-h">
        <span><AppIcon name="bell" :size="13" /> 今日盘面异动</span>
        <span class="mr-meta">指数急变 / 宽度反转 / 连板高度 / 情绪跨级 · 阈值在 strategy_config.market_alerts</span>
      </div>
      <table v-if="(d.alerts_today || []).length" class="mr-table">
        <thead><tr><th style="width:74px">时间</th><th style="width:130px">类型</th><th>内容</th></tr></thead>
        <tbody>
          <tr v-for="a in d.alerts_today" :key="a.id">
            <td>{{ a.time || '—' }}</td>
            <td><span class="mr-mk" :class="a.level === 'critical' ? 'crit' : ''">{{ mkLabel(a.kind) }}</span></td>
            <td><b>{{ a.title }}</b> <span class="mr-meta">{{ a.body }}</span></td>
          </tr>
        </tbody>
      </table>
      <div v-else class="mr-empty2">今日暂无异动告警</div>
    </div>
  </template>
</div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, onBeforeUnmount, nextTick } from 'vue'
import * as echarts from 'echarts'
import axios from 'axios'
import AppIcon from './AppIcon.vue'
import MarketSentiment from './MarketSentiment.vue'
import { useNavStore } from '../stores/nav'
import { useThemeStore } from '../stores/theme'
import { chartColors as T, axisStyle } from '../utils/chart'

const API = window.location.origin
const nav = useNavStore()
const theme = useThemeStore()

const d = ref(null)
const ztHist = ref([])
const bigIdx = ref('000001.SH')
const boardDim = ref('chg')
let timer = null
let histTimer = null
let loadSeq = 0

const BOARD_DIMS = [
  { k: 'chg', label: '涨幅' },
  { k: 'net', label: '主力净流入' },
]

// ── 工具 ──
const signed = v => v == null ? '—' : ((Number(v) >= 0 ? '+' : '') + Number(v).toFixed(2))
const yi = v => v == null ? '—' : (Number(v) / 1e8).toFixed(1) + '亿'
const yiNum = v => v == null ? 0 : Number(v) / 1e8
const chgCls = v => Number(v) >= 0 ? 'up' : 'down'
const pctText = v => v == null ? '—' : (Number(v) * 100).toFixed(1) + '%'
function pctW(n) {
  const t = Number(d.value?.breadth?.total) || 1
  return (Number(n) / t * 100).toFixed(2) + '%'
}
const sentiLabel = lv => ({ panic: '恐慌', weak: '偏弱', neutral: '中性', strong: '强势' }[lv] || lv || '—')
// mk% 告警类型中文标签（后端 title 已含详情，此处仅分类徽标）
const MK_LABELS = { mk_idx_surge: '指数急涨', mk_idx_drop: '指数急跌', mk_breadth_up: '宽度转强',
                    mk_breadth_dn: '宽度转弱', mk_height: '连板新高', mk_panic: '盘面恐慌', mk_strong: '盘面亢奋' }
const mkLabel = k => MK_LABELS[k] || k

const idxList = computed(() => d.value?.indices_trend || [])
const bigData = computed(() => idxList.value.find(x => x.code === bigIdx.value) || idxList.value[0] || null)
const poolRows = computed(() => (d.value?.zt?.pool || []).slice(0, 60))
const ztBroken = computed(() => d.value?.zt?.broken ?? '—')
const asofText = computed(() => {
  if (!d.value) return ''
  const parts = [d.value.date]
  // data_date=数据真实所属交易日。盘前/休市日腾讯、东财都回退成上一交易日的整段数据，
  // 缓存刷新时间（asof）不等于数据时间，必须分开标
  if (prevDataDate.value) parts.push(`显示 ${prevDataDate.value}（最近交易日）数据`)
  if (d.value.asof) parts.push('更新 ' + d.value.asof)
  return parts.join(' · ')
})
// 数据不是今天的（仅当能判定出数据日期时）
const prevDataDate = computed(() => {
  const dd = d.value?.data_date
  return dd && dd !== d.value?.date ? dd : ''
})
const staleText = computed(() => prevDataDate.value
  ? `当前为 ${prevDataDate.value} 的数据，开盘后自动更新`
  : '数据延迟，后台采集中')
// 梯队/资金流各自的数据日期（与今天不同才标）
const iso8 = s => /^\d{8}$/.test(String(s || '')) ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6)}` : ''
const ztDateText = computed(() => {
  const t = iso8(d.value?.zt?.date)
  return t && t !== d.value?.date ? `${t} 数据` : ''
})
const fflowDateText = computed(() => {
  const t = d.value?.fflow?.date
  return t && t !== d.value?.date ? `${t} 数据` : ''
})

// ── ECharts 约定：实例缓存 DOM 节点，onBeforeUnmount（DOM 还在）时 dispose ──
function make(id, opt) {
  const el = document.getElementById(id)
  if (!el) return null
  let c = el._echart
  if (!c) { c = echarts.init(el); el._echart = c }
  c.setOption(opt, { notMerge: true })
  return c
}

function miniTrend(data, id) {
  if (!data || !data.trend || !data.trend.length) return
  const prices = data.trend.map(x => x.price)
  const ts = data.trend.map(x => x.t)
  make(id, {
    animation: false,
    grid: { left: 46, right: 6, top: 6, bottom: 16 },
    xAxis: { type: 'category', data: ts, axisLabel: { show: false }, axisLine: { lineStyle: { color: T.line } }, axisTick: { show: false } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: T.dim, fontSize: 10 }, splitLine: { lineStyle: { color: T.line } }, axisLine: { show: false } },
    tooltip: { trigger: 'axis', formatter: p => `${ts[p[0]?.dataIndex]} ${p[0]?.value}` },
    series: [
      { type: 'line', data: prices, symbol: 'none', lineStyle: { width: 1.2, color: prices[prices.length - 1] >= (data.preClose || prices[0]) ? T.up : T.down },
        markLine: { silent: true, symbol: 'none', data: [{ yAxis: data.preClose, lineStyle: { color: T.faint, type: 'dashed', width: 1 } }], label: { show: false } } },
    ],
  })
}

function drawBig() {
  const ix = bigData.value
  if (!ix || !ix.trend || !ix.trend.length) return
  const ts = ix.trend.map(x => x.t)
  const prices = ix.trend.map(x => x.price)
  const last = prices[prices.length - 1]
  const color = ix.preClose && last >= ix.preClose ? T.up : T.down
  make('mr-big', {
    animation: false,
    grid: { left: 60, right: 46, top: 24, bottom: 28 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, formatter: p => {
      const i = p[0]?.dataIndex
      return `<b>${ix.name}</b> ${ts[i]}<br/>价 ${prices[i]}<br/>较昨收 ${signed(ix.preClose ? (prices[i] / ix.preClose - 1) * 100 : null)}%`
    } },
    xAxis: { type: 'category', data: ts, boundaryGap: false, axisLabel: { color: T.dim, fontSize: 10, interval: 29 }, axisLine: { lineStyle: { color: T.line } }, axisTick: { show: false } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: T.dim, fontSize: 10, formatter: v => v.toFixed(0) }, splitLine: { lineStyle: { color: T.line } } },
    series: [
      { name: '价格', type: 'line', data: prices, symbol: 'none', lineStyle: { width: 1.6, color },
        areaStyle: { opacity: 0.06, color },
        markLine: { silent: true, symbol: 'none', data: [{ yAxis: ix.preClose, label: { formatter: '昨收 ' + ix.preClose, fontSize: 10, color: T.faint, position: 'insideEndTop' }, lineStyle: { color: T.faint, type: 'dashed', width: 1 } }] } },
    ],
  })
}

function drawTurnover() {
  const tv = d.value?.turnover
  if (!tv || !tv.series || !tv.series.length) return
  const ts = tv.series.map(x => x.t)
  const y2v = v => v == null ? null : Number(v) / 1e8
  make('mr-turnover', {
    animation: false,
    grid: { left: 52, right: 10, top: 26, bottom: 20 },
    legend: { top: 2, textStyle: { color: T.dim, fontSize: 10 }, itemWidth: 12, data: ['今日', '昨日', '近4日均'] },
    tooltip: { trigger: 'axis', formatter: p => `${ts[p[0]?.dataIndex]}<br/>` + p.map(x => `${x.marker}${x.seriesName} ${Number(x.value).toFixed(1)}亿`).join('<br/>') },
    xAxis: { type: 'category', data: ts, boundaryGap: false, axisLabel: { color: T.dim, fontSize: 10, interval: 29 }, axisLine: { lineStyle: { color: T.line } }, axisTick: { show: false } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: T.dim, fontSize: 10, formatter: v => v.toFixed(0) }, splitLine: { lineStyle: { color: T.line } }, name: '亿', nameTextStyle: { color: T.faint } },
    series: [
      { name: '今日', type: 'line', data: tv.series.map(x => y2v(x.today)), symbol: 'none', lineStyle: { width: 1.5, color: T.up } },
      { name: '昨日', type: 'line', data: tv.series.map(x => y2v(x.yday)), symbol: 'none', lineStyle: { width: 1, color: T.dim, type: 'dashed' } },
      { name: '近4日均', type: 'line', data: tv.series.map(x => y2v(x.avg_prev)), symbol: 'none', lineStyle: { width: 1, color: T.series[3] } },
    ],
  })
}

function drawTrack() {
  const s = d.value?.senti_track || []
  if (!s.length) return
  const slots = s.map(x => x.slot)
  make('mr-track', {
    animation: false,
    grid: { left: 40, right: 14, top: 24, bottom: 22 },
    tooltip: { trigger: 'axis', formatter: p => {
      const i = p[0]?.dataIndex
      const x = s[i]
      if (!x) return ''
      return `<b>${x.slot}</b> ${sentiLabel(x.level)} <b>${x.score}</b> 分<br/>涨占比 ${(x.up_ratio * 100).toFixed(1)}% · 中位 ${x.median == null ? '—' : x.median + '%'}<br/>涨停 ${x.limit_up} / 跌停 ${x.limit_down}`
    } },
    xAxis: { type: 'category', data: slots, axisLabel: { color: T.dim, fontSize: 10, interval: 3 }, axisLine: { lineStyle: { color: T.line } }, axisTick: { show: false } },
    yAxis: { min: 0, max: 100, axisLabel: { color: T.dim, fontSize: 10 }, splitLine: { lineStyle: { color: T.line } } },
    visualMap: { show: false, dimension: 0, pieces: [
      { lt: 25, color: T.down }, { gte: 25, lt: 45, color: T.warn },
      { gte: 45, lt: 70, color: T.series[3] }, { gte: 70, color: T.up }] },
    series: [{ type: 'line', data: s.map(x => x.score), symbol: 'circle', symbolSize: 4, lineStyle: { width: 1.6 },
      markLine: { silent: true, symbol: 'none', data: [
        { yAxis: 45, lineStyle: { color: T.line, type: 'dashed', width: 1 }, label: { show: false } },
        ...(d.value?.senti?.score != null ? [{ yAxis: d.value.senti.score, lineStyle: { color: T.faint, type: 'dotted', width: 1 }, label: { formatter: `实时 ${d.value.senti.score}`, fontSize: 10, color: T.faint, position: 'insideEndTop' } }] : []),
      ] } }],
  })
}

function drawLadder() {
  const lad = d.value?.zt?.ladder || []
  if (!lad.length) return
  const rows = [...lad].reverse()  // 高板在上
  make('mr-ladder', {
    animation: false,
    grid: { left: 60, right: 60, top: 8, bottom: 20 },
    tooltip: { formatter: p => {
      const x = rows[p[0]?.dataIndex]
      return `<b>${x.lbc} 连板</b> ${x.n} 家<br/>${(x.names || []).join('、')}`
    } },
    xAxis: { type: 'value', axisLabel: { color: T.dim, fontSize: 10 }, splitLine: { lineStyle: { color: T.line } } },
    yAxis: { type: 'category', data: rows.map(x => x.lbc + '板'), axisLabel: { color: T.dim, fontSize: 11 }, axisLine: { lineStyle: { color: T.line } }, axisTick: { show: false } },
    series: [{ type: 'bar', data: rows.map(x => x.n), barWidth: 16, itemStyle: { color: T.up },
      label: { show: true, position: 'right', color: T.dim, fontSize: 10, formatter: p => {
        const x = rows[p.dataIndex]
        return (x.names || []).slice(0, 3).join('、') + (x.n > 3 ? ' …' : '')
      } } }],
  })
}

function drawZtHist() {
  const rows = ztHist.value || []
  if (!rows.length) return
  const dates = rows.map(x => x.date.slice(5))
  make('mr-zthist', {
    animation: false,
    grid: { left: 44, right: 44, top: 26, bottom: 20 },
    legend: { top: 2, textStyle: { color: T.dim, fontSize: 10 }, itemWidth: 12, data: ['连板高度', '涨停家数', '炸板率'] },
    tooltip: { trigger: 'axis', formatter: p => {
      const i = p[0]?.dataIndex
      const x = rows[i]
      return `<b>${x.date}</b><br/>` + p.map(s => `${s.marker}${s.seriesName} ${s.seriesName === '炸板率' ? pctText(x.broken_rate) : s.seriesName === '连板高度' ? (x.max_lbc || 0) + ' 板' : x.limit_up + ' 家'}`).join('<br/>')
    } },
    xAxis: { type: 'category', data: dates, axisLabel: { color: T.dim, fontSize: 10, interval: Math.max(0, Math.floor(dates.length / 8)) }, axisLine: { lineStyle: { color: T.line } }, axisTick: { show: false } },
    yAxis: [
      { type: 'value', min: 0, axisLabel: { color: T.dim, fontSize: 10 }, splitLine: { lineStyle: { color: T.line } } },
      { type: 'value', min: 0, max: 100, axisLabel: { color: T.dim, fontSize: 10, formatter: '{value}%' }, splitLine: { show: false } },
    ],
    series: [
      { name: '涨停家数', type: 'bar', data: rows.map(x => x.limit_up), itemStyle: { color: T.up, opacity: 0.55 }, barWidth: '55%' },
      { name: '连板高度', type: 'line', data: rows.map(x => x.max_lbc), symbol: 'circle', symbolSize: 3, lineStyle: { width: 1.4, color: T.warn }, yAxisIndex: 0 },
      { name: '炸板率', type: 'line', data: rows.map(x => x.broken_rate == null ? null : Number(x.broken_rate) * 100), symbol: 'none', lineStyle: { width: 1, color: T.series[3], type: 'dashed' }, yAxisIndex: 1 },
    ],
  })
}

function drawBoards() {
  const bs = d.value?.boards || []
  if (!bs.length) return
  const byDim = boardDim.value === 'net'
    ? [...bs].sort((a, b) => (b.main_net || 0) - (a.main_net || 0))
    : [...bs].sort((a, b) => (b.chg_pct || -999) - (a.chg_pct || -999))
  const top = byDim.slice(0, 12)
  const bottom = byDim.slice(-6)
  // ECharts category y 轴 index 0 渲染在底部：最差在最下、最好在最上
  const rows = [...bottom, ...top]
  const names = rows.map(x => x.name)
  const isNet = boardDim.value === 'net'
  const vals = rows.map(x => isNet ? yiNum(x.main_net) : (x.chg_pct || 0))
  make('mr-boards', {
    animation: false,
    grid: { left: 76, right: 46, top: 8, bottom: 18 },
    tooltip: { formatter: p => {
      const x = rows[p[0]?.dataIndex]
      return `<b>${x.name}</b> ${isNet ? yi(x.main_net) : signed(x.chg_pct) + '%'}<br/>涨 ${x.up} / 跌 ${x.down}` + (x.leader ? `<br/>领涨 ${x.leader}` : '')
    } },
    xAxis: { type: 'value', axisLabel: { color: T.dim, fontSize: 10 }, splitLine: { lineStyle: { color: T.line } } },
    yAxis: { type: 'category', data: names, axisLabel: { color: T.dim, fontSize: 11 }, axisLine: { lineStyle: { color: T.line } }, axisTick: { show: false } },
    series: [{ type: 'bar', data: vals.map(v => ({ value: v, itemStyle: { color: v >= 0 ? T.up : T.down } })), barWidth: 12,
      label: { show: true, position: 'right', fontSize: 10, color: T.dim, formatter: p => isNet ? yi(rows[p.dataIndex].main_net) : signed(rows[p.dataIndex].chg_pct) + '%' } }],
  })
}

function drawFflow() {
  const s = d.value?.fflow?.series || []
  if (!s.length) return
  const ts = s.map(x => x.t)
  make('mr-fflow', {
    animation: false,
    grid: { left: 60, right: 16, top: 30, bottom: 22 },
    legend: { top: 2, textStyle: { color: T.dim, fontSize: 10 }, itemWidth: 12, data: ['主力', '超大单', '大单'] },
    tooltip: { trigger: 'axis', formatter: p => `${ts[p[0]?.dataIndex]}<br/>` + p.map(x => `${x.marker}${x.seriesName} ${Number(x.value).toFixed(1)}亿`).join('<br/>') },
    xAxis: { type: 'category', data: ts, boundaryGap: false, axisLabel: { color: T.dim, fontSize: 10, interval: 29 }, axisLine: { lineStyle: { color: T.line } }, axisTick: { show: false } },
    yAxis: { type: 'value', axisLabel: { color: T.dim, fontSize: 10, formatter: v => v.toFixed(0) }, splitLine: { lineStyle: { color: T.line } }, name: '亿', nameTextStyle: { color: T.faint } },
    series: [
      { name: '主力', type: 'line', data: s.map(x => yiNum(x.main)), symbol: 'none', lineStyle: { width: 1.6, color: T.up },
        areaStyle: { opacity: 0.07, color: T.up }, markLine: { silent: true, symbol: 'none', data: [{ yAxis: 0, lineStyle: { color: T.line, width: 1 } }], label: { show: false } } },
      { name: '超大单', type: 'line', data: s.map(x => yiNum(x.huge)), symbol: 'none', lineStyle: { width: 1, color: T.series[1] } },
      { name: '大单', type: 'line', data: s.map(x => yiNum(x.big)), symbol: 'none', lineStyle: { width: 1, color: T.series[3] } },
    ],
  })
}

function drawAll() {
  if (!d.value) return
  for (const ix of idxList.value) miniTrend(ix, 'mr-t-' + ix.code)
  drawBig()
  drawTurnover()
  drawTrack()
  drawLadder()
  drawZtHist()
  drawBoards()
  drawFflow()
}

function resizeAll() {
  document.querySelectorAll('.mr-chart').forEach(el => el._echart && el._echart.resize())
}

// ── 数据加载 ──
async function load() {
  const seq = ++loadSeq
  try {
    const r = await axios.get(API + '/api/market/realtime', { timeout: 20000 })
    if (seq !== loadSeq) return
    d.value = r.data
    await nextTick()
    drawAll()
    resizeAll()
  } catch (e) { /* 保留旧数据，静默重试 */ }
  clearTimeout(timer)
  const sess = d.value?.session
  timer = setTimeout(load, sess === 'open' || sess === 'pre' ? 15000 : 60000)
}

async function loadHist() {
  try {
    const r = await axios.get(API + '/api/market/realtime/zt-history', { params: { days: 120 }, timeout: 20000 })
    ztHist.value = r.data.days || []
    drawZtHist()
  } catch (e) { /* 静默 */ }
}

onMounted(() => { load(); loadHist(); histTimer = setInterval(loadHist, 300000); window.addEventListener('resize', resizeAll) })
onUnmounted(() => { clearTimeout(timer); clearInterval(histTimer); window.removeEventListener('resize', resizeAll) })
onBeforeUnmount(() => {
  // 必须在 onBeforeUnmount（DOM 还在）dispose，onUnmounted 时已查不到节点
  loadSeq++
  document.querySelectorAll('.mr-chart').forEach(el => { if (el._echart) { el._echart.dispose(); delete el._echart } })
})

watch(bigIdx, () => nextTick(drawBig))
watch(boardDim, () => nextTick(drawBoards))
watch(() => theme.isDark, () => nextTick(drawAll))
</script>

<style scoped>
.mr { display: flex; flex-direction: column; gap: 10px; padding-bottom: 20px; }
.mr-toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.mr-title { font-size: 16px; font-weight: 600; color: var(--c-text); }
.mr-session { font-size: var(--text-meta); padding: 1px 8px; border-radius: 4px; border: 1px solid var(--c-border); color: var(--c-text-dim); }
.mr-session.ms-open { color: var(--c-up); border-color: var(--c-up); }
.mr-session.ms-lunch, .mr-session.ms-pre { color: var(--c-warning, #8a5a00); border-color: var(--c-warning, #8a5a00); }
.mr-asof { font-size: var(--text-meta); color: var(--c-text-faint); }
.mr-stale { font-size: var(--text-meta); color: var(--c-warning, #8a5a00); }
.mr-chip { border: 1px solid var(--c-border); background: var(--c-card-bg); color: var(--c-text-dim); border-radius: 4px; padding: 3px 10px; font-size: var(--text-meta); cursor: pointer; }
.mr-chip:hover { color: var(--c-text); border-color: var(--c-text-faint); }
.mr-chip.active { background: var(--c-accent); border-color: var(--c-accent); color: #fff; }
.mr-empty { padding: 40px; text-align: center; color: var(--c-text-faint); font-size: 13px; background: var(--c-card-bg); border: 1px dashed var(--c-border); border-radius: var(--radius, 6px); }
.mr-empty2 { padding: 24px; text-align: center; color: var(--c-text-faint); font-size: var(--text-meta); }

.mr-panel { background: var(--c-card-bg); border: 1px solid var(--c-border); border-radius: var(--radius, 6px); padding: 10px 12px; }
.mr-h { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; color: var(--c-text); }
.mr-meta { font-size: var(--text-meta); color: var(--c-text-faint); }
.mr-sub { font-size: var(--text-meta); color: var(--c-text-dim); margin-bottom: 2px; }

.mr-trend-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-bottom: 8px; }
.mr-trend-cell { border: 1px solid var(--c-border); border-radius: 4px; padding: 4px 6px 2px; cursor: pointer; }
.mr-trend-cell.active { border-color: var(--c-accent); }
.mr-mini { height: 120px; }
.mr-trend-head { display: flex; justify-content: space-between; font-size: var(--text-meta); color: var(--c-text-dim); padding: 0 2px; }
.mr-trend-head b.up { color: var(--c-up); }
.mr-trend-head b.down { color: var(--c-down); }
@media (max-width: 1000px) { .mr-trend-grid { grid-template-columns: repeat(2, 1fr); } }

.mr-row2 { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 10px; }
.mr-row3 { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 10px; margin-bottom: 8px; }
@media (max-width: 1000px) { .mr-row2, .mr-row3 { grid-template-columns: 1fr; } }

.mr-bar { display: flex; height: 14px; border-radius: 3px; overflow: hidden; margin: 4px 0; }
.mr-bar i { display: block; height: 100%; }
.mr-bar i.up { background: var(--c-up); }
.mr-bar i.down { background: var(--c-down); }
.mr-bar i.flat { background: var(--c-text-faint); opacity: 0.4; }
.mr-bar-legend { display: flex; gap: 12px; font-size: var(--text-meta); color: var(--c-text-dim); margin-bottom: 6px; }
.mr-stats { display: flex; gap: 14px; flex-wrap: wrap; font-size: var(--text-meta); color: var(--c-text-dim); margin: 4px 0; }
.mr-stats b { color: var(--c-text); }
.mr-stats b.up { color: var(--c-up); }
.mr-stats b.down { color: var(--c-down); }
.mr-stats b.warn { color: var(--c-warning, #8a5a00); }
.mr-stats b.hl { color: var(--c-up); }
.mr-basis { font-style: normal; color: var(--c-text-faint); }

.mr-table { width: 100%; border-collapse: collapse; font-size: var(--text-meta); }
.mr-table th { text-align: left; color: var(--c-text-faint); font-weight: 400; padding: 4px 8px; border-bottom: 1px solid var(--c-border); }
.mr-table td { padding: 4px 8px; border-bottom: 1px solid var(--c-border); color: var(--c-text-dim); }
.mr-table td b { color: var(--c-text); }
.mr-table td b.up { color: var(--c-up); }
.mr-table td.warn { color: var(--c-warning, #8a5a00); }
.mr-row-link { cursor: pointer; }
.mr-row-link:hover td { background: var(--c-bg); }
.mr-code { color: var(--c-text-faint); font-size: 11px; }
.up { color: var(--c-up); }
.down { color: var(--c-down); }
.warn { color: var(--c-warning, #8a5a00); }

.mr-gate { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 8px 12px; background: var(--c-card-bg); border: 1px solid var(--c-border); border-left: 3px solid var(--c-text-dim); border-radius: var(--radius, 6px); font-size: 13px; }
.mr-gate-label { color: var(--c-text-dim); }
.mr-gate-reason { color: var(--c-text-dim); }
.mr-gate-advice { color: var(--c-text-faint); font-size: var(--text-meta); }
.mr-gate.gate-open { border-left-color: var(--c-down); }
.mr-gate.gate-open .mr-gate-state { color: var(--c-down); }
.mr-gate.gate-caution { border-left-color: var(--c-warning, #8a5a00); }
.mr-gate.gate-caution .mr-gate-state { color: var(--c-warning, #8a5a00); }
.mr-gate.gate-closed { border-left-color: var(--c-up); }
.mr-gate.gate-closed .mr-gate-state { color: var(--c-up); }
.mr-mk { font-size: var(--text-meta); padding: 1px 6px; border-radius: 3px; border: 1px solid var(--c-border); color: var(--c-text-dim); }
.mr-mk.crit { color: var(--c-up); border-color: var(--c-up); }
</style>
