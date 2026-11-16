<template>
<n-modal v-model:show="show" :mask-closable="false">
  <n-card class="tr-card" :bordered="false">
    <template #header>
      <div class="tr-head">
        <div class="tr-title">
          <b><AppIcon name="activity" :size="15" />  交易复盘</b>
          <span v-if="review" class="tr-sub">
            {{ review.period.start }} ~ {{ review.period.end }} · {{ review.period.trades }} 笔 / {{ review.period.active_days }} 个交易日
          </span>
        </div>
        <div class="tr-actions">
          <n-button-group size="tiny">
            <n-button v-for="p in periods" :key="p.v" size="tiny" :type="period===p.v?'primary':'default'" @click="setPeriod(p.v)">{{ p.label }}</n-button>
          </n-button-group>
          <n-button size="tiny" @click="showImport=true"><AppIcon name="upload" :size="12" />  导入交割单</n-button>
          <n-button size="tiny" quaternary :loading="loading" @click="loadAll"><AppIcon name="refresh" :size="12" /></n-button>
          <n-button size="tiny" quaternary @click="show=false">关闭</n-button>
        </div>
      </div>
    </template>

    <n-spin v-if="loading && !review" style="padding:60px" />
    <div v-else-if="error" class="tr-error">
      {{ error }} <n-button size="tiny" @click="loadReview">重试</n-button>
    </div>

    <template v-else-if="review">
      <!-- 顶部结论条 -->
      <StatStrip :items="headline" />

      <div v-if="review.warnings?.length" class="tr-warn">
        <span v-for="w in review.warnings" :key="w">· {{ w }}</span>
      </div>

      <n-tabs v-model:value="tab" type="line" size="small" class="tr-tabs" @update:value="onTab">
        <!-- ── 复盘 ── -->
        <n-tab-pane name="review" tab="复盘结论">
          <div class="tr-grid">
            <div class="tr-col">
              <!-- 纪律分 -->
              <div class="tr-panel tr-score-panel">
                <div class="tr-score-main">
                  <div class="tr-score-num" :style="{color: scoreColor}">{{ review.summary.discipline_score }}</div>
                  <div class="tr-score-meta">
                    <span class="tr-grade" :style="{background: scoreColor}">{{ review.summary.discipline_grade }}</span>
                    <span>{{ review.summary.discipline_label }}</span>
                    <span class="tr-muted">纪律分 0-100</span>
                  </div>
                </div>
                <div class="tr-score-parts">
                  <div v-for="p in review.score.parts" :key="p.key" class="tr-part">
                    <span class="tr-part-name">{{ p.key }}</span>
                    <div class="tr-part-bar"><i :style="{width: (p.penalty/p.max*100)+'%', background: p.penalty/p.max>0.6?'var(--c-error)':p.penalty/p.max>0.3?'var(--c-warning)':'var(--c-down)'}"></i></div>
                    <span class="tr-part-val">-{{ p.penalty }}/{{ p.max }}</span>
                  </div>
                </div>
                <div class="tr-score-note">{{ review.score.summary }}</div>
              </div>

              <!-- 结论 -->
              <div class="tr-panel">
                <div class="tr-panel-title">核心结论 <span class="tr-muted">按严重程度排序</span></div>
                <div v-for="f in review.findings" :key="f.key" class="tr-finding" :class="'lv-'+f.level">
                  <div class="tr-finding-head">
                    <span class="tr-lv">{{ levelLabel[f.level] }}</span>
                    <b>{{ f.title }}</b>
                  </div>
                  <div class="tr-finding-ev">{{ f.evidence }}</div>
                  <div class="tr-finding-advice"><span>怎么改</span>{{ f.advice }}</div>
                </div>
              </div>
            </div>

            <div class="tr-col">
              <!-- 资金曲线 -->
              <div class="tr-panel">
                <div class="tr-panel-title">
                  资金曲线
                  <span class="tr-muted">已剔除入金/出金 · 基准 {{ review.summary.benchmark_name || '—' }}</span>
                </div>
                <div ref="chartEl" class="tr-chart"></div>
                <div class="tr-chart-note">
                  净值 {{ pct(review.summary.return_pct) }}（期初 {{ money(review.summary.adjusted_start) }} → 期末 {{ money(review.summary.adjusted_end) }}）
                  · 账户表面 {{ pct((review.summary.account_change||0) / (review.summary.equity_start||1)) }}
                  · 净入金 {{ money(review.summary.net_deposit) }}
                </div>
              </div>

              <!-- 月度 -->
              <div class="tr-panel">
                <div class="tr-panel-title">月度表现 <span class="tr-muted">剔除入金后的净值</span></div>
                <n-data-table :columns="monthCols" :data="review.monthly" size="small" :bordered="false" :max-height="240" />
              </div>
            </div>
          </div>

          <!-- 行为画像 -->
          <div class="tr-panel" style="margin-top:10px">
            <div class="tr-panel-title">行为画像 <span class="tr-muted">这些数字比收益率更能解释为什么亏</span></div>
            <div class="tr-metrics">
              <div v-for="g in metricGroups" :key="g.title" class="tr-metric-group">
                <div class="tr-metric-title">{{ g.title }}</div>
                <div v-for="it in g.items" :key="it.label" class="tr-metric-row">
                  <span class="tr-metric-label">{{ it.label }}</span>
                  <b :style="{color: it.color || 'var(--c-text)'}">{{ it.value }}</b>
                </div>
              </div>
            </div>
          </div>
        </n-tab-pane>

        <!-- ── 交割单明细 ── -->
        <n-tab-pane name="trades" tab="交割单明细">
          <div class="tr-filter">
            <n-date-picker v-model:formatted-value="tFilters.range" type="daterange" value-format="yyyy-MM-dd" size="tiny" clearable style="width:230px" />
            <n-input v-model:value="tFilters.code" size="tiny" placeholder="代码 / 名称" clearable style="width:140px" />
            <n-select v-model:value="tFilters.side" size="tiny" :options="sideOptions" style="width:100px" />
            <n-button size="tiny" type="primary" @click="()=>{tPage=1;loadTrades()}">查询</n-button>
            <n-button size="tiny" quaternary @click="resetTradeFilters">重置</n-button>
            <span class="tr-muted" style="margin-left:auto">
              买入 {{ tradeSummary.count ? money(tradeSummary.buy_amount) : '—' }} / 卖出 {{ tradeSummary.count ? money(tradeSummary.sell_amount) : '—' }} / 费用 {{ money(tradeSummary.fee_total) }}
            </span>
          </div>
          <n-data-table :columns="tradeCols" :data="tradeRows" size="small" :bordered="false"
            :loading="tLoading" :max-height="calcTableH" :row-key="r=>r.id" scroll-x="960" />
          <ListPagination :total="tradeTotal" :page="tPage" :page-size="tPageSize" @change="p=>{tPage=p;loadTrades()}" />
        </n-tab-pane>

        <!-- ── 个股汇总 ── -->
        <n-tab-pane name="stocks" tab="个股盈亏">
          <div class="tr-muted" style="margin-bottom:6px">按已实现盈亏从差到好排序；点击一行查看该股明细。</div>
          <n-data-table :columns="stockCols" :data="review.stocks" size="small" :bordered="false"
            :max-height="calcTableH" scroll-x="820" :row-props="stockRowProps" />
        </n-tab-pane>

        <!-- ── 纪律 ── -->
        <n-tab-pane name="rules" tab="纪律规则">
          <div class="tr-grid">
            <div class="tr-col">
              <div class="tr-panel" :class="gate && !gate.allow_new_buy ? 'tr-gate-bad' : ''">
                <div class="tr-panel-title">
                  今日闸门
                  <span class="tr-muted">{{ gate?.date || '' }}</span>
                </div>
                <div v-if="!gate" class="tr-muted">加载中…</div>
                <template v-else>
                  <div class="tr-gate-sum" :style="{color: gate.violations?.length ? 'var(--c-warning)' : 'var(--c-down)'}">
                    {{ gate.summary }}
                  </div>
                  <div v-if="gate.violations?.length" class="tr-gate-list">
                    <div v-for="v in gate.violations" :key="v.rule+v.message" class="tr-gate-item" :class="'lv-'+v.level">
                      <span class="tr-lv">{{ v.level==='critical'?'禁止':'注意' }}</span>{{ v.message }}
                    </div>
                  </div>
                  <div v-else class="tr-muted">没有触发纪律越界。保持。</div>
                  <div class="tr-gate-foot">
                    连续亏损 {{ gate.consecutive_loss_days }} 天 · 今日 {{ gate.today_buys }} 买 / {{ gate.today_sells }} 卖
                    · {{ gate.allow_new_buy ? '允许开新仓' : '禁止开新仓' }}
                  </div>
                </template>
              </div>

              <div class="tr-panel">
                <div class="tr-panel-title">规则阈值 <span class="tr-muted">保存后立即用于今日闸门</span></div>
                <div v-for="r in review.rules" :key="r.key" class="tr-rule-row">
                  <div class="tr-rule-main">
                    <div class="tr-rule-name">{{ r.name }}</div>
                    <div class="tr-rule-hint">{{ r.hint }}</div>
                  </div>
                  <div class="tr-rule-cur">
                    当前 <b :style="{color: r.status==='pass'?'var(--c-down)':'var(--c-error)'}">{{ r.display }}</b>
                    <span class="tr-rule-state" :style="{color: r.status==='pass'?'var(--c-down)':'var(--c-error)'}">{{ r.status==='pass'?'达标':'越界' }}</span>
                  </div>
                  <div class="tr-rule-input">
                    <n-input-number v-if="ruleEdits[r.key] !== undefined" v-model:value="ruleEdits[r.key]"
                      size="tiny" :step="inputStep(r.key)" :precision="inputPrecision(r.key)" style="width:92px" />
                  </div>
                </div>
                <div class="tr-rule-actions">
                  <n-button size="tiny" type="primary" :loading="savingRules" @click="saveRules">保存阈值</n-button>
                </div>
              </div>
            </div>

            <div class="tr-col">
              <div class="tr-panel">
                <div class="tr-panel-title">导入记录 <span class="tr-muted">撤销会删除该批次成交，需重新同步持仓</span></div>
                <n-data-table :columns="batchCols" :data="batches" size="small" :bordered="false" :max-height="300" />
              </div>
              <div class="tr-panel">
                <div class="tr-panel-title">口径说明</div>
                <div v-for="n in review.notes" :key="n" class="tr-note-line">· {{ n }}</div>
              </div>
            </div>
          </div>
        </n-tab-pane>
      </n-tabs>
    </template>

    <TradeImport v-model:show="showImport" @imported="onImported" />
  </n-card>
</n-modal>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import StatStrip from './StatStrip.vue'
import ListPagination from './ListPagination.vue'
import TradeImport from './TradeImport.vue'
import { ref, reactive, computed, watch, nextTick, onMounted, onUnmounted, h } from 'vue'
import { NModal, NCard, NButton, NButtonGroup, NTabs, NTabPane, NDataTable, NDatePicker,
  NInput, NSelect, NInputNumber, NSpin, NTag, NPopconfirm, useMessage } from 'naive-ui'
import * as echarts from 'echarts'
import axios from 'axios'
import { fmtMoney } from '../utils/ui'
import { useViewport } from '../utils/viewport'

const props = defineProps({
  show: { type: Boolean, default: false },
  initialTab: { type: String, default: 'review' },
})
const emit = defineEmits(['update:show', 'changed'])
const show = computed({ get: () => props.show, set: v => emit('update:show', v) })
const message = useMessage()
const API = window.location.origin
const { isNarrow } = useViewport()

const periods = [
  { v:'30', label:'近30天' }, { v:'90', label:'近90天' },
  { v:'365', label:'近1年' }, { v:'all', label:'全部' },
]
const period = ref('all')
const loading = ref(false), error = ref('')
const review = ref(null)
const tab = ref('review')
const showImport = ref(false)

const levelLabel = { critical:'严重', warn:'注意', ok:'优势', info:'信息' }

// ── 格式化 ──
const money = v => v == null ? '—' : (v < 0 ? '-¥' : '¥') + fmtMoney(Math.abs(Number(v)))
const moneyExact = v => v == null ? '—' : (v < 0 ? '-¥' : '¥') + Number(Math.abs(v)).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
const pct = (v, d = 1) => v == null ? '—' : (v > 0 ? '+' : '') + (Number(v) * 100).toFixed(d) + '%'
const tone = v => v == null ? 'var(--c-text)' : v >= 0 ? 'var(--c-up)' : 'var(--c-down)'
const scoreColor = computed(() => {
  const s = review.value?.summary?.discipline_score ?? 100
  return s < 35 ? 'var(--c-error)' : s < 50 ? 'var(--c-warning)' : 'var(--c-down)'
})

const _pad = n => String(n).padStart(2, '0')
function dateStr(d){ return `${d.getFullYear()}-${_pad(d.getMonth()+1)}-${_pad(d.getDate())}` }
function periodRange(){
  if (period.value === 'all') return { start:null, end:null }
  const n = parseInt(period.value)
  const end = new Date()
  const start = new Date(end.getTime() - n * 86400000)
  return { start: dateStr(start), end: dateStr(end) }
}

async function loadReview(){
  loading.value = true; error.value = ''
  try{
    const r = periodRange()
    const res = await axios.get(API + '/api/portfolio/trades/review', {
      params: { start: r.start, end: r.end }, timeout: 120000,
    })
    review.value = res.data
    syncRuleEdits()
    await nextTick()
    renderChart()
  }catch(e){
    error.value = e.response?.data?.detail || e.message || '复盘加载失败'
    review.value = null
  }finally{ loading.value = false }
}

const headline = computed(() => {
  const s = review.value?.summary || {}
  const acc = s.equity_start ? (s.account_change || 0) / s.equity_start : null
  return [
    { label:'时间加权收益', value: pct(s.return_pct), color: tone(s.return_pct), sub:`账户表面 ${pct(acc)}` },
    { label:'交易盈亏', value: money(s.pnl), color: tone(s.pnl), sub:`净入金 ${money(s.net_deposit)}` },
    { label:'最大回撤', value: pct(-(s.max_drawdown || 0)), color:'var(--c-down)', sub:`${s.peak_date||''} → ${s.trough_date||''}` },
    { label:'胜率', value: s.win_rate == null ? '—' : (s.win_rate*100).toFixed(1)+'%', sub:`盈亏比 ${s.profit_factor ?? '—'}` },
    { label:'单笔期望', value: money(s.expectancy), color: tone(s.expectancy), sub:`${s.closed_episodes || 0} 个回合` },
    { label:'纪律分', value:`${s.discipline_score ?? '—'} ${s.discipline_grade || ''}`, color: scoreColor.value, sub: s.discipline_label || '' },
  ]
})

const monthCols = [
  { title:'月份', key:'month', width:86 },
  { title:'收益', key:'return_pct', width:82, align:'right', render(r){ return h('span',{style:{color:tone(r.return_pct),fontWeight:500}}, pct(r.return_pct)) } },
  { title:'盈亏', key:'pnl', width:100, align:'right', render(r){ return h('span',{style:{color:tone(r.pnl)}}, money(r.pnl)) } },
  { title:'净入金', key:'net_deposit', width:100, align:'right', render(r){ return r.net_deposit ? money(r.net_deposit) : '—' } },
  { title:'笔数', key:'trades', width:60, align:'right' },
]

const metricGroups = computed(() => {
  const b = review.value?.behavior || {}
  const s = review.value?.summary || {}
  const wd = b.worst_avgdown
  return [
    { title:'交易频率', items:[
      { label:'日均成交', value:`${b.trades_per_day ?? '—'} 笔` },
      { label:'单日最多', value:`${b.max_day_trades ?? '—'} 笔（开仓 ${b.max_day_buys ?? '—'}）` },
      { label:'一年标的', value:`${b.distinct_stocks ?? '—'} 只` },
      { label:'每周标的', value:`${b.stocks_per_week ?? '—'} 只` },
      { label:'换手', value: b.turnover_ratio ? `${b.turnover_ratio}x` : '—' },
    ]},
    { title:'盈亏结构', items:[
      { label:'胜率', value: b.win_rate == null ? '—' : (b.win_rate*100).toFixed(1)+'%' },
      { label:'盈亏比', value: b.profit_factor ?? '—', color: (b.profit_factor??1)<1?'var(--c-error)':'var(--c-down)' },
      { label:'平均盈利', value: money(b.avg_win) },
      { label:'平均亏损', value: b.avg_loss == null ? '—' : money(Math.abs(b.avg_loss)) },
      { label:'单笔期望', value: money(b.expectancy), color: tone(b.expectancy) },
    ]},
    { title:'处置效应', items:[
      { label:'盈利单持有', value: `${b.hold_days_win ?? '—'} 天` },
      { label:'亏损单持有', value: `${b.hold_days_loss ?? '—'} 天` },
      { label:'盈利/亏损比', value: b.disposition_ratio ?? '—' },
      { label:'同日又买又卖', value:`${b.same_day_roundtrips ?? '—'} 天` },
      { label:'卖出后7日回补', value:`${b.rebuy_within_7d ?? '—'} 次` },
    ]},
    { title:'亏损后行为', items:[
      { label:'亏损日次日', value:`${b.post_loss_next_day?.avg_trades ?? '—'} 笔 / ${money(b.post_loss_next_day?.avg_buy_amount)}` },
      { label:'盈利日次日', value:`${b.post_win_next_day?.avg_trades ?? '—'} 笔 / ${money(b.post_win_next_day?.avg_buy_amount)}` },
      { label:'最长连亏', value:`${b.max_consecutive_loss_days ?? '—'} 天` },
      { label:'单日最大亏损', value: pct(b.worst_daily_return), color:'var(--c-error)' },
      { label:'单笔最大亏损', value: pct(b.worst_episode_return), color:'var(--c-error)' },
    ]},
    { title:'加仓行为', items:[
      { label:'亏损时加仓', value:`${b.add_loss_count ?? '—'} 笔 / ${money(b.add_loss_amount)}`, color:'var(--c-error)' },
      { label:'盈利时加仓', value:`${b.add_win_count ?? '—'} 笔 / ${money(b.add_win_amount)}` },
      { label:'最惨案例', value: wd ? `${wd.name} ${wd.count} 笔` : '—' },
      { label:'加仓区间', value: wd ? `${wd.first_price} → ${wd.last_price} (${pct(wd.drop_pct,0)})` : '—' },
      { label:'加仓金额', value: wd ? money(wd.amount) : '—' },
    ]},
    { title:'卖出后走势', items:[
      { label:'5 日', value: pct(b.post_sell_5d), color: tone(b.post_sell_5d) },
      { label:'10 日', value: pct(b.post_sell_10d), color: tone(b.post_sell_10d) },
      { label:'20 日', value: pct(b.post_sell_20d), color: tone(b.post_sell_20d) },
      { label:'结论', value: (b.post_sell_5d ?? 0) < 0 ? '卖出时点不差' : '卖得偏早' },
    ]},
    { title:'成本与集中度', items:[
      { label:'手续费/印花税', value: money(b.fee_total) },
      { label:'费率', value: b.fee_bps ? `${b.fee_bps} bp` : '—' },
      { label:'最大单票', value: b.max_single_weight==null?'—':`${(b.max_single_weight*100).toFixed(0)}%` , color:'var(--c-error)'},
      { label:'发生日', value: b.max_weight_date || '—' },
      { label:'最多持仓', value:`${b.max_positions ?? '—'} 只` },
    ]},
  ]
})

// ── 图表 ──
const chartEl = ref(null)
let chart = null
function cssVar(name, fallback){
  const el = document.querySelector('.app-root') || document.documentElement
  const v = getComputedStyle(el).getPropertyValue(name).trim()
  return v || fallback
}
function renderChart(){
  if (!chartEl.value || !review.value?.equity?.length) return
  if (!chart) chart = echarts.init(chartEl.value)
  const eq = review.value.equity
  const dates = eq.map(p => p.date)
  const adjusted = eq.map(p => p.adjusted)
  const raw = eq.map(p => p.equity)
  const base = review.value.summary.adjusted_start || adjusted[0] || 1
  const bench = eq.map(p => p.benchmark == null ? null : +(p.benchmark * base).toFixed(2))
  const textDim = cssVar('--c-text-dim', '#888')
  const border = cssVar('--c-border', 'rgba(128,128,128,.2)')
  chart.setOption({
    animation: false,
    grid: { left: 56, right: 14, top: 30, bottom: 26 },
    tooltip: {
      trigger:'axis',
      backgroundColor: cssVar('--c-card-bg', '#fff'),
      borderColor: border,
      textStyle: { color: cssVar('--c-text', '#111'), fontSize: 12 },
      formatter: ps => {
        const d = ps[0]?.axisValue || ''
        const lines = ps.map(p => `${p.marker}${p.seriesName}: ${p.value==null?'—':'¥'+Number(p.value).toLocaleString('zh-CN',{maximumFractionDigits:0})}`)
        return `<b>${d}</b><br/>${lines.join('<br/>')}`
      },
    },
    legend: { top: 0, left: 0, itemWidth: 10, itemHeight: 8, textStyle: { color: textDim, fontSize: 11 } },
    xAxis: {
      type:'category', data: dates, boundaryGap: false,
      axisLine: { lineStyle: { color: border } },
      axisLabel: { color: textDim, fontSize: 10, interval: Math.max(0, Math.floor(dates.length / 8)), formatter: v => String(v).slice(5) },
    },
    yAxis: {
      type:'value', scale: true,
      axisLabel: { color: textDim, fontSize: 10, formatter: v => (v/10000).toFixed(0)+'万' },
      splitLine: { lineStyle: { color: border, type:'dashed' } },
    },
    series: [
      { name:'剔除入金净值', type:'line', data: adjusted, showSymbol:false, smooth:false,
        lineStyle:{ width:2, color:'#5b8ff9' }, itemStyle:{ color:'#5b8ff9' },
        areaStyle:{ color:'rgba(91,143,249,.08)' },
        markPoint:{ symbol:'circle', symbolSize:6, itemStyle:{ color:'#5b8ff9' }, label:{ fontSize:10, color:'#5b8ff9' },
          data:[{type:'max', name:'峰值'},{type:'min', name:'谷值'}] } },
      { name:'账户总值', type:'line', data: raw, showSymbol:false,
        lineStyle:{ width:1, type:'dashed', color:'#9aa0a6' }, itemStyle:{ color:'#9aa0a6' } },
      { name:'沪深300', type:'line', data: bench, showSymbol:false,
        lineStyle:{ width:1, color:'#f2b64c' }, itemStyle:{ color:'#f2b64c' } },
    ],
  }, true)
  chart.resize()
}
function resizeChart(){ chart?.resize() }
function disposeChart(){ try{ chart?.dispose() }catch(e){} chart = null }

// ── 明细 ──
const sideOptions = [
  { label:'全部', value:'' }, { label:'买入', value:'buy' }, { label:'卖出', value:'sell' }, { label:'其他', value:'other' },
]
const tFilters = reactive({ range:null, code:'', side:'' })
const tPage = ref(1), tPageSize = 100, tTotal = ref(0), tLoading = ref(false)
const tradeRows = ref([])
const tradeSummary = reactive({ count:0, buy_amount:0, sell_amount:0, fee_total:0 })
const calcTableH = computed(() => isNarrow.value ? 380 : 430)

function tradeParams(page){
  return {
    start: tFilters.range?.[0] || undefined,
    end: tFilters.range?.[1] || undefined,
    code: tFilters.code?.trim() || undefined,
    side: tFilters.side || undefined,
    page: page || tPage.value,
    page_size: tPageSize,
  }
}
async function loadTrades(){
  tLoading.value = true
  try{
    const r = await axios.get(API + '/api/portfolio/trades', { params: tradeParams(), timeout: 30000 })
    tradeRows.value = r.data.items || []
    tTotal.value = r.data.total || 0
    Object.assign(tradeSummary, r.data.summary || {})
  }catch(e){
    message.error(e.response?.data?.detail || '交割单加载失败')
  }finally{ tLoading.value = false }
}
function resetTradeFilters(){ tFilters.range=null; tFilters.code=''; tFilters.side=''; tPage.value=1; loadTrades() }

const sideTag = s => s === 'buy' ? { t:'买入', c:'var(--c-up)' } : s === 'sell' ? { t:'卖出', c:'var(--c-down)' } : { t:'其他', c:'var(--c-text-dim)' }
const tradeCols = [
  { title:'日期', key:'trade_date', width:92 },
  { title:'时间', key:'trade_time', width:72, render(r){ return r.trade_time || '—' } },
  { title:'代码', key:'stock_code', width:76, render(r){ return h('span',{style:{color:'var(--c-text-dim)'}},r.stock_code) } },
  { title:'名称', key:'stock_name', width:96 },
  { title:'方向', key:'side', width:62, render(r){ const x=sideTag(r.side); return h('span',{style:{color:x.c,fontWeight:500}},x.t) } },
  { title:'数量', key:'quantity', width:78, align:'right', render(r){ return r.quantity ? Number(r.quantity).toLocaleString() : '—' } },
  { title:'价格', key:'price', width:82, align:'right', render(r){ return r.price ? '¥'+Number(r.price).toFixed(2) : '—' } },
  { title:'金额', key:'amount', width:104, align:'right', render(r){ return moneyExact(r.amount) } },
  { title:'费用', key:'fee', width:80, align:'right', render(r){ return moneyExact((r.commission||0)+(r.stamp_tax||0)+(r.transfer_fee||0)+(r.extra_fee||0)) } },
  { title:'股份余额', key:'balance_qty', width:82, align:'right', render(r){ return r.balance_qty==null?'—':Number(r.balance_qty).toLocaleString() } },
  { title:'业务', key:'business_name', width:112, ellipsis:{tooltip:true} },
]
const stockCols = [
  { title:'代码', key:'stock_code', width:76 },
  { title:'名称', key:'stock_name', width:96 },
  { title:'买入', key:'buy_count', width:56, align:'right' },
  { title:'卖出', key:'sell_count', width:56, align:'right' },
  { title:'买入额', key:'buy_amount', width:100, align:'right', render(r){ return moneyExact(r.buy_amount) } },
  { title:'卖出额', key:'sell_amount', width:100, align:'right', render(r){ return moneyExact(r.sell_amount) } },
  { title:'已实现', key:'realized', width:104, align:'right', render(r){ return h('span',{style:{color:tone(r.realized),fontWeight:600}}, moneyExact(r.realized)) } },
  { title:'当前持仓', key:'current_qty', width:82, align:'right', render(r){ return r.current_qty ? Number(r.current_qty).toLocaleString() : '—' } },
  { title:'最后价', key:'last_price', width:80, align:'right', render(r){ return r.last_price ? '¥'+Number(r.last_price).toFixed(2) : '—' } },
  { title:'操作', width:80, render(r){ return h(NButton,{size:'tiny',quaternary:true,onClick:()=>filterByStock(r)},()=>'看明细') } },
]
function stockRowProps(row){ return { style:'cursor:pointer', onClick:()=>filterByStock(row) } }
function filterByStock(row){
  tFilters.code = row.stock_code
  tFilters.range = null
  tPage.value = 1
  tab.value = 'trades'
  loadTrades()
}

// ── 纪律规则 ──
const rulesCfg = ref({})
const ruleEdits = reactive({})
const savingRules = ref(false)
function syncRuleEdits(){
  axios.get(API + '/api/portfolio/trades/discipline/rules').then(r => {
    rulesCfg.value = r.data.rules || {}
    const c = rulesCfg.value
    ruleEdits.max_new_buys_per_day = c.max_new_buys_per_day
    ruleEdits.max_positions = c.max_positions
    ruleEdits.max_single_weight = +(c.max_single_weight * 100).toFixed(1)
    ruleEdits.stop_loss_pct = +(c.stop_loss_pct * 100).toFixed(1)
    ruleEdits.no_rebuy_days = c.no_rebuy_days
    ruleEdits.max_daily_loss_pct = +(c.max_daily_loss_pct * 100).toFixed(1)
    ruleEdits.consecutive_loss_trigger = c.consecutive_loss_trigger
  }).catch(()=>{})
}
function inputStep(k){ return ['max_single_weight','stop_loss_pct','max_daily_loss_pct'].includes(k) ? 1 : 1 }
function inputPrecision(k){ return ['max_single_weight','stop_loss_pct','max_daily_loss_pct'].includes(k) ? 1 : 0 }
async function saveRules(){
  savingRules.value = true
  try{
    const body = {
      max_new_buys_per_day: ruleEdits.max_new_buys_per_day,
      max_positions: ruleEdits.max_positions,
      max_single_weight: ruleEdits.max_single_weight / 100,
      stop_loss_pct: ruleEdits.stop_loss_pct / 100,
      no_rebuy_days: ruleEdits.no_rebuy_days,
      max_daily_loss_pct: ruleEdits.max_daily_loss_pct / 100,
      consecutive_loss_trigger: ruleEdits.consecutive_loss_trigger,
    }
    await axios.put(API + '/api/portfolio/trades/discipline/rules', body)
    message.success('纪律阈值已保存')
    rulesCfg.value = { ...rulesCfg.value, ...body }
    await Promise.all([loadReview(), loadGate()])
  }catch(e){ message.error(e.response?.data?.detail || '保存失败') }
  finally{ savingRules.value = false }
}

// ── 今日闸门 / 批次 ──
const gate = ref(null)
async function loadGate(){
  try{ gate.value = (await axios.get(API + '/api/portfolio/trades/discipline/today')).data }catch(e){ gate.value = null }
}
const batches = ref([])
async function loadBatches(){
  try{ batches.value = (await axios.get(API + '/api/portfolio/trades/batches')).data.items || [] }catch(e){ batches.value = [] }
}
async function removeBatch(id){
  try{
    await axios.delete(API + '/api/portfolio/trades/batches/' + id)
    message.success('批次已删除；请确认持仓是否需要重新同步')
    await Promise.all([loadBatches(), loadReview()])
    emit('changed')
  }catch(e){ message.error(e.response?.data?.detail || '删除失败') }
}
const batchCols = [
  { title:'文件', key:'filename', width:120, ellipsis:{tooltip:true} },
  { title:'新增/重复', width:90, render(r){ return `${r.rows_inserted}/${r.rows_duplicate}` } },
  { title:'区间', width:170, render(r){ return `${r.date_min||''} ~ ${r.date_max||''}` } },
  { title:'同步', key:'positions_synced', width:54, render(r){ return r.positions_synced?'已':'未' } },
  { title:'', width:52, render(r){ return h(NPopconfirm,{onPositiveClick:()=>removeBatch(r.id)},{trigger:()=>h(NButton,{size:'tiny',quaternary:true,type:'error'},()=>'撤销'),default:()=>'删除该批次成交？持仓需重新同步。'}) } },
]

// ── 生命周期 ──
function setPeriod(v){ period.value = v; loadReview() }
function onTab(v){
  if (v === 'review') nextTick(renderChart)
  if (v === 'rules') { loadGate(); loadBatches() }
}
async function loadAll(){
  await Promise.all([loadReview(), loadGate(), loadBatches()])
  if (tab.value === 'trades' && !tradeRows.value.length) loadTrades()
}
function onImported(res){
  showImport.value = false
  emit('changed')
  loadAll()
  if (res?.synced) message.success('持仓已按交割单更新')
  else message.info('交割单已入库；持仓未改动')
}
watch(() => props.show, v => {
  if (v) {
    tab.value = props.initialTab || 'review'
    loadAll(); loadTrades()
    nextTick(() => setTimeout(renderChart, 120))
  } else {
    disposeChart()
  }
})
onMounted(() => {
  if (props.show) { loadAll(); loadTrades() }
  window.addEventListener('resize', resizeChart)
})
onUnmounted(() => { window.removeEventListener('resize', resizeChart); disposeChart() })
</script>

<style scoped>
.tr-card { width: min(1400px, 96vw); height: 92vh; display:flex; flex-direction:column; }
.tr-card :deep(.n-card__content) { flex:1; min-height:0; overflow-y:auto; padding-top:4px; }
.tr-head { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }
.tr-title { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }
.tr-title b { font-size:15px; }
.tr-sub { font-size: var(--text-meta); color: var(--c-text-dim); font-weight:400; }
.tr-actions { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.tr-error { padding:40px; text-align:center; color: var(--c-error); font-size:13px; }
.tr-warn { margin:6px 0 2px; font-size: var(--text-meta); color: var(--c-warning); line-height:1.7; }
.tr-tabs { margin-top:6px; }
.tr-muted { font-size: var(--text-meta); color: var(--c-text-faint); font-weight:400; margin-left:6px; }
.tr-grid { display:grid; grid-template-columns: minmax(0,1.05fr) minmax(0,1fr); gap:10px; }
.tr-col { display:flex; flex-direction:column; gap:10px; min-width:0; }
@media (max-width: 900px) { .tr-grid { grid-template-columns: 1fr; } }
.tr-panel { background: var(--c-card-bg); border:1px solid var(--c-border); border-radius: var(--radius); padding:10px 12px; min-width:0; }
.tr-panel-title { display:flex; align-items:baseline; gap:6px; font-size:13px; font-weight:600; margin-bottom:8px; flex-wrap:wrap; }

/* 纪律分 */
.tr-score-panel { border-left:3px solid var(--c-accent); }
.tr-score-main { display:flex; align-items:center; gap:16px; }
.tr-score-num { font-size:44px; line-height:1; font-weight:700; font-variant-numeric: tabular-nums; }
.tr-score-meta { display:flex; flex-direction:column; gap:3px; font-size: var(--text-meta); color: var(--c-text-dim); }
.tr-grade { display:inline-block; width:22px; text-align:center; color:#fff; border-radius:4px; font-weight:700; font-size:12px; padding:1px 0; }
.tr-score-parts { display:flex; flex-direction:column; gap:5px; margin-top:10px; }
.tr-part { display:flex; align-items:center; gap:8px; font-size: var(--text-meta); }
.tr-part-name { width:62px; color: var(--c-text-dim); flex-shrink:0; }
.tr-part-bar { flex:1; height:6px; border-radius:3px; background: var(--c-border); overflow:hidden; }
.tr-part-bar i { display:block; height:100%; border-radius:3px; }
.tr-part-val { width:66px; text-align:right; color: var(--c-text-faint); font-variant-numeric: tabular-nums; }
.tr-score-note { margin-top:8px; font-size: var(--text-meta); color: var(--c-text-dim); }

/* 结论卡 */
.tr-finding { border:1px solid var(--c-border); border-left:3px solid var(--c-text-faint); border-radius: var(--radius-sm); padding:8px 10px; margin-bottom:8px; }
.tr-finding.lv-critical { border-left-color: var(--c-error); }
.tr-finding.lv-warn { border-left-color: var(--c-warning); }
.tr-finding.lv-ok { border-left-color: var(--c-down); }
.tr-finding.lv-info { border-left-color: var(--c-text-faint); }
.tr-finding-head { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; font-size:13px; }
.tr-finding-head b { font-weight:600; }
.tr-lv { flex-shrink:0; font-size:10px; padding:1px 5px; border-radius:3px; color:#fff; background: var(--c-text-faint); }
.lv-critical .tr-lv { background: var(--c-error); }
.lv-warn .tr-lv { background: var(--c-warning); }
.lv-ok .tr-lv { background: var(--c-down); }
.tr-finding-ev { margin-top:5px; font-size: var(--text-meta); color: var(--c-text-dim); line-height:1.65; }
.tr-finding-advice { margin-top:6px; font-size: var(--text-meta); line-height:1.6; padding:6px 8px; background: var(--c-card-bg-hover); border-radius: var(--radius-sm); color: var(--c-text); }
.tr-finding-advice span { color: var(--c-accent); font-weight:600; margin-right:6px; }

/* 图表 */
.tr-chart { width:100%; height:280px; }
.tr-chart-note { margin-top:4px; font-size: var(--text-meta); color: var(--c-text-faint); line-height:1.6; }

/* 行为画像 */
.tr-metrics { display:grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap:8px; }
.tr-metric-group { border:1px solid var(--c-border); border-radius: var(--radius-sm); padding:8px 10px; background: var(--c-card-bg); }
.tr-metric-title { font-size:12px; font-weight:600; margin-bottom:5px; color: var(--c-text); }
.tr-metric-row { display:flex; justify-content:space-between; gap:8px; font-size: var(--text-meta); line-height:1.9; }
.tr-metric-label { color: var(--c-text-dim); white-space:nowrap; }
.tr-metric-row b { font-weight:500; text-align:right; font-variant-numeric: tabular-nums; }

/* 明细筛选 */
.tr-filter { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:8px; }

/* 纪律 */
.tr-gate-sum { font-size:14px; font-weight:600; margin-bottom:6px; }
.tr-gate-list { display:flex; flex-direction:column; gap:4px; margin-bottom:6px; }
.tr-gate-item { font-size: var(--text-meta); color: var(--c-text-dim); line-height:1.7; }
.tr-gate-item .tr-lv { margin-right:6px; }
.tr-gate-foot { font-size: var(--text-meta); color: var(--c-text-faint); border-top:1px solid var(--c-border); padding-top:6px; margin-top:6px; }
.tr-rule-row { display:flex; align-items:center; gap:10px; padding:6px 0; border-bottom:1px dashed var(--c-border); }
.tr-rule-main { flex:1; min-width:0; }
.tr-rule-name { font-size:12px; color: var(--c-text); }
.tr-rule-hint { font-size: var(--text-meta); color: var(--c-text-faint); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.tr-rule-cur { font-size: var(--text-meta); color: var(--c-text-dim); white-space:nowrap; }
.tr-rule-state { margin-left:5px; font-size:10px; }
.tr-rule-input { flex-shrink:0; }
.tr-rule-actions { display:flex; justify-content:flex-end; margin-top:8px; }
.tr-note-line { font-size: var(--text-meta); color: var(--c-text-dim); line-height:1.8; }
.tr-gate-bad { border-left:3px solid var(--c-error); }
</style>
