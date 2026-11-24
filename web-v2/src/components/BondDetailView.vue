<template>
<div style="padding:12px 4px">
  <n-spin v-if="loading" style="padding:60px" />
  <n-empty v-else-if="notFound" description="未找到该可转债，请确认代码" style="padding:40px" />
  <template v-else-if="item">
    <div class="bd-toolbar">
      <n-button size="tiny" @click="$emit('back')"><AppIcon name="arrow-left" :size="13" /> 返回</n-button>
      <span class="bd-title">{{ item.bond_name || item.bond_code }}</span>
      <span class="bd-code">{{ item.bond_code }}</span>
      <n-tag size="tiny" :bordered="false" :type="labelType(item.label)">{{ item.label }} {{ item.score }}</n-tag>
      <div style="flex:1" />
      <n-button v-if="item.stock_code" size="tiny" @click="openStock">
        <AppIcon name="arrow-left-right" :size="13" /> 正股 {{ item.stock_name || item.stock_code }}
      </n-button>
    </div>

    <StatStrip :items="statItems" />

    <div class="bd-grid">
      <div class="bd-left">
        <div class="bd-panel bd-chart-panel">
          <h4 class="bd-h"><AppIcon name="bar-chart-2" :size="13" /> 价格 / 转股价值 / 溢价率</h4>
          <div ref="chartEl" class="bd-chart"></div>
        </div>
        <div v-if="shareHistory.length" class="bd-panel bd-chart-panel">
          <h4 class="bd-h"><AppIcon name="refresh" :size="13" /> 转股进度 / 剩余规模历史</h4>
          <div ref="shareEl" class="bd-chart"></div>
        </div>

        <div v-if="expect" class="bd-panel bd-expect">
          <h4 class="bd-h">
            <AppIcon name="target" :size="13" /> 买入预期收益测算
            <span class="bd-sub">规则近似 · 不构成投资建议</span>
          </h4>
          <div class="bd-expect-grid">
            <div class="bd-metric">
              <span class="bd-metric-label">到强赎线收益</span>
              <b :class="cls(expect.upside_to_force)">{{ pct(expect.upside_to_force) }}</b>
              <small>正股需涨 {{ pct(expect.stock_gain_to_force) }} 至 {{ expect.force_stock_price?.toFixed(2) }}</small>
            </div>
            <div class="bd-metric">
              <span class="bd-metric-label">债底下行空间</span>
              <b :class="cls(expect.floor_return)">{{ pct(expect.floor_return) }}</b>
              <small>纯债价值 {{ expect.pure_bond_value?.toFixed(2) ?? '—' }} · 到期年化 {{ pct(expect.ytm_approx ?? expect.floor_carry_annual) }}</small>
            </div>
            <div class="bd-metric">
              <span class="bd-metric-label">盈亏比</span>
              <b>{{ expect.risk_reward != null ? expect.risk_reward.toFixed(2) : '—' }}</b>
              <small>上行空间 ÷ 债底下行</small>
            </div>
            <div class="bd-metric">
              <span class="bd-metric-label">溢价安全垫</span>
              <b>{{ pct(expect.premium_cushion) }}</b>
              <small>正股跌到此幅度，转股价值才低于现价</small>
            </div>
          </div>

          <div class="bd-expect-summary">
            现价买入的核心假设是正股向强赎线修复：需上涨 {{ pct(expect.stock_gain_to_force) }}，
            对应转债约 {{ pct(expect.upside_to_force) }}；若正股不涨，历史相似估值持有 60 日的
            中位数为 {{ pct(hist('60')?.median) }}、胜率
            {{ hist('60')?.win_rate != null ? (hist('60').win_rate * 100).toFixed(0) + '%' : '—' }}。
          </div>

          <div class="bd-expect-cols">
            <div>
              <div class="bd-mini-title">正股情景 → 转债估算</div>
              <table class="bd-table">
                <thead><tr><th>正股</th><th>转股价值</th><th>估算转债价</th><th>收益</th></tr></thead>
                <tbody>
                  <tr v-for="s in expect.scenarios" :key="s.stock_move">
                    <td>{{ pct(s.stock_move) }}</td>
                    <td>{{ s.conv_value?.toFixed(2) }}</td>
                    <td>{{ s.est_price?.toFixed(2) }}</td>
                    <td :class="cls(s.return)">{{ pct(s.return) }}</td>
                  </tr>
                </tbody>
              </table>
              <div class="bd-note">假设溢价率不变、转债价不低于纯债价值。</div>
            </div>
            <div>
              <div class="bd-mini-title">历史持有收益（{{ expect.history.start_date }} ~ {{ expect.history.end_date }}）</div>
              <table class="bd-table">
                <thead><tr><th>持有</th><th>中位数</th><th>胜率</th><th>样本</th></tr></thead>
                <tbody>
                  <tr v-for="h in ['20', '60', '120']" :key="h">
                    <td>{{ h }}日</td>
                    <td :class="cls(hist(h)?.median)">{{ pct(hist(h)?.median) }}</td>
                    <td>{{ hist(h)?.win_rate != null ? (hist(h).win_rate * 100).toFixed(0) + '%' : '—' }}</td>
                    <td>{{ hist(h)?.samples ?? 0 }}<span v-if="hist(h)?.similar" class="bd-sub">（相似）</span></td>
                  </tr>
                </tbody>
              </table>
              <div class="bd-note">相似样本 = 当前价格 ±15%、溢价 ±15pp 内的历史时点；不足 30 个时用全样本。</div>
            </div>
          </div>
        </div>
      </div>

      <div class="bd-side">
        <div class="bd-panel">
          <h4 class="bd-h"><AppIcon name="bond" :size="13" /> 基本信息</h4>
          <table class="bd-table">
            <tbody>
              <tr><th>转债代码</th><td>{{ item.bond_code }}</td><th>交易所</th><td>{{ item.exchange || '—' }}</td></tr>
              <tr><th>正股</th><td>
                <a v-if="item.stock_code" class="bd-link" :href="'#/detail/' + item.stock_code"
                   @click.prevent="openStock">{{ item.stock_name || item.stock_code }}</a>
                <span v-else>—</span>
              </td><th>转股价</th><td>{{ item.conv_price != null ? item.conv_price.toFixed(3) : '—' }}</td></tr>
              <tr><th>上市日</th><td>{{ item.list_date || '—' }}</td><th>退市日</th><td>{{ item.delist_date || '—' }}</td></tr>
              <tr><th>到期日</th><td>{{ item.maturity_date || '—' }}<span v-if="item.days_to_maturity!=null">（{{ item.days_to_maturity }}天）</span></td>
                  <th>票面利率</th><td>{{ item.coupon_rate != null ? item.coupon_rate + '%' : '—' }}</td></tr>
              <tr><th>转股起始</th><td>{{ item.conv_start_date || '—' }}</td><th>转股截止</th><td>{{ item.conv_end_date || '—' }}</td></tr>
              <tr><th>停止转股</th><td>{{ item.conv_stop_date || '—' }}</td><th>债券期限</th><td>{{ item.maturity_years != null ? item.maturity_years + '年' : '—' }}</td></tr>
              <tr><th>发行规模</th><td>{{ fmtYi(item.issue_size) }}</td><th>剩余规模</th><td>{{ fmtYi(item.remaining_size) }}</td></tr>
              <tr><th>转股比例</th><td>{{ item.acc_convert_ratio != null ? item.acc_convert_ratio + '%' : '—' }}</td><th>总股本</th><td>{{ item.total_shares != null ? item.total_shares.toLocaleString() : '—' }}</td></tr>
            </tbody>
          </table>
        </div>

        <div class="bd-panel">
          <h4 class="bd-h"><AppIcon name="target" :size="13" /> 策略视角</h4>
          <div v-if="(item.reasons || []).length || (item.risk_flags || []).length" class="bd-reasons">
            <div v-for="r in item.reasons" :key="'r-' + r" class="bd-reason">{{ r }}</div>
            <div v-for="f in item.risk_flags" :key="'f-' + f" class="bd-risk">{{ f }}</div>
          </div>
          <n-empty v-else description="暂无规则理由" style="padding:10px" />
          <div class="bd-note">规则筛选不构成投资建议。</div>
        </div>

        <div class="bd-panel">
          <h4 class="bd-h"><AppIcon name="alert" :size="13" /> 强赎 / 退市观察</h4>
          <table class="bd-table">
            <tbody>
              <tr><th>强赎线</th><td>{{ forceLine }}</td><th>正股距强赎线</th><td>{{ forcePct }}</td></tr>
              <tr><th>近30天≥强赎线</th><td>{{ item.forced_redemption_days != null ? item.forced_redemption_days + ' 天' : '—' }}</td>
                  <th>状态</th><td>{{ forceStatus }}</td></tr>
              <tr><th>距停止转股</th><td>{{ item.days_to_conv_stop != null ? item.days_to_conv_stop + ' 天' : '—' }}</td>
                  <th>距退市</th><td>{{ item.days_to_delist != null ? item.days_to_delist + ' 天' : '—' }}</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </template>
</div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useNavStore } from '../stores/nav'
import { NButton, NTag, NSpin, NEmpty } from 'naive-ui'
import axios from 'axios'
import * as echarts from 'echarts'
import { chartColors as T } from '../utils/chart'
import AppIcon from './AppIcon.vue'
import StatStrip from './StatStrip.vue'

const props = defineProps({ code: String })
const emit = defineEmits(['back'])
const nav = useNavStore()
const API = window.location.origin
const loading = ref(false)
const notFound = ref(false)
const item = ref(null)
const asof = ref('')
const expect = ref(null)
const chartEl = ref(null)
const shareEl = ref(null)
const shareHistory = ref([])
let chart = null
let shareChart = null
let alive = true

function labelType(label) {
  if (label === '进攻') return 'success'
  if (label === '均衡') return 'info'
  if (label === '防守') return 'warning'
  return 'error'
}

function fmtYi(v) {
  if (v == null) return '—'
  const y = v / 1e8
  return (y >= 10 ? y.toFixed(1) : y.toFixed(2)) + '亿'
}

function pct(v) {
  if (v == null) return '—'
  return (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%'
}

function cls(v) {
  if (v == null) return ''
  return v >= 0 ? 'bd-up' : 'bd-down'
}

function hist(h) {
  const w = expect.value?.history?.windows?.[h]
  if (!w) return null
  const sim = w.similar
  if (sim && sim.samples >= 30) return { ...sim, similar: true }
  return { ...(w.all || {}), similar: false }
}

const statItems = computed(() => {
  const x = item.value || {}
  const pct = x.pct_chg
  return [
    { label: '转债价', value: x.price != null ? x.price.toFixed(3) : '—', color: pct != null ? (pct >= 0 ? 'var(--c-up)' : 'var(--c-down)') : undefined },
    { label: '涨跌', value: pct != null ? (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%' : '—', color: pct != null ? (pct >= 0 ? 'var(--c-up)' : 'var(--c-down)') : undefined },
    { label: '溢价率', value: x.premium_rate != null ? (x.premium_rate * 100).toFixed(1) + '%' : '—' },
    { label: '双低', value: x.double_low != null ? x.double_low.toFixed(1) : '—' },
    { label: '转股价值', value: x.conv_value != null ? x.conv_value.toFixed(2) : '—' },
    { label: '纯债溢价', value: x.pure_bond_premium != null ? (x.pure_bond_premium * 100).toFixed(1) + '%' : '—' },
    { label: '剩余规模', value: fmtYi(x.remaining_size) },
    { label: '规则评分', value: `${x.score ?? '—'} ${x.label || ''}`, color: x.score >= 70 ? 'var(--c-up)' : (x.score >= 55 ? 'var(--c-text)' : (x.score >= 40 ? 'var(--c-warn)' : 'var(--c-down)')) },
  ]
})

const forceLine = computed(() => {
  const cp = item.value?.conv_price
  return cp ? (cp * 1.3).toFixed(2) : '—'
})
const forcePct = computed(() => {
  const v = item.value?.stock_to_force_pct
  return v != null ? ((v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%') : '—'
})
const forceStatus = computed(() => {
  const x = item.value || {}
  const days = x.forced_redemption_days
  if (x.days_to_delist != null && x.days_to_delist <= 30) return '退市临近'
  if (x.days_to_conv_stop != null && x.days_to_conv_stop <= 30) return '停止转股临近'
  if (days != null && days >= 15) return '接近/满足强赎条件'
  if (days != null && days > 0) return '强赎观察'
  return '暂无强赎迹象'
})

function openStock() {
  const c = item.value?.stock_code
  if (c) nav.showDetail(c)
}

function drawChart() {
  const el = chartEl.value
  const hist = item.value?.history || []
  if (!el || !hist.length) return
  if (!chart) { chart = echarts.init(el); el._echart = chart }
  const dates = hist.map(x => x.date)
  const prices = hist.map(x => x.price)
  const cvs = hist.map(x => x.conv_value)
  const prems = hist.map(x => x.premium_rate != null ? +(x.premium_rate * 100).toFixed(2) : null)
  chart.setOption({
    tooltip: { trigger: 'axis', valueFormatter: v => v == null ? '—' : v },
    legend: { top: 0, right: 4, itemWidth: 12, itemHeight: 6, textStyle: { fontSize: 10, color: T.dim } },
    grid: { left: 56, right: 58, top: 28, bottom: 28 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10, color: T.faint, hideOverlap: true }, axisLine: { lineStyle: { color: T.line } } },
    yAxis: [
      { type: 'value', scale: true, axisLabel: { fontSize: 10, color: T.faint }, splitLine: { lineStyle: { color: T.line } } },
      { type: 'value', axisLabel: { fontSize: 10, color: T.faint, formatter: v => v + '%' }, splitLine: { show: false } },
    ],
    series: [
      { name: '转债价', type: 'line', data: prices, symbol: 'none', lineStyle: { color: T.up, width: 1.6 }, itemStyle: { color: T.up } },
      { name: '转股价值', type: 'line', data: cvs, symbol: 'none', lineStyle: { color: T.series[3], width: 1.2 }, itemStyle: { color: T.series[3] } },
      { name: '溢价率%', type: 'line', yAxisIndex: 1, data: prems, symbol: 'none', lineStyle: { color: T.warn, width: 1, type: 'dashed' }, itemStyle: { color: T.warn } },
    ],
  }, true)
}

function drawShareChart() {
  const el = shareEl.value
  const hist = shareHistory.value || []
  if (!el || !hist.length) return
  if (!shareChart) { shareChart = echarts.init(el); el._echart = shareChart }
  const dates = hist.map(x => x.date)
  const remain = hist.map(x => x.remain_size != null ? +(x.remain_size / 1e8).toFixed(3) : null)
  const acc = hist.map(x => x.acc_convert_ratio != null ? +x.acc_convert_ratio.toFixed(3) : null)
  const convPx = hist.map(x => x.convert_price != null ? +x.convert_price.toFixed(3) : null)
  shareChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { top: 0, right: 4, itemWidth: 12, itemHeight: 6, textStyle: { fontSize: 10, color: T.dim } },
    grid: { left: 56, right: 96, top: 28, bottom: 28 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10, color: T.faint, hideOverlap: true }, axisLine: { lineStyle: { color: T.line } } },
    yAxis: [
      { type: 'value', scale: true, axisLabel: { fontSize: 10, color: T.faint, formatter: v => v + '亿' }, splitLine: { lineStyle: { color: T.line } } },
      { type: 'value', axisLabel: { fontSize: 10, color: T.faint, formatter: v => v + '%' }, splitLine: { show: false } },
      { type: 'value', position: 'right', offset: 48, scale: true, axisLabel: { fontSize: 10, color: T.faint, formatter: v => v.toFixed(1) }, splitLine: { show: false } },
    ],
    series: [
      { name: '剩余规模(亿)', type: 'line', data: remain, symbol: 'none', lineStyle: { color: T.series[3], width: 1.6 }, itemStyle: { color: T.series[3] }, areaStyle: { color: T.series[3], opacity: .08 } },
      { name: '累计转股比例%', type: 'line', yAxisIndex: 1, data: acc, symbol: 'none', lineStyle: { color: T.warn, width: 1.4 }, itemStyle: { color: T.warn } },
      { name: '转股价', type: 'line', yAxisIndex: 2, data: convPx, symbol: 'none', lineStyle: { color: T.faint, width: 1, type: 'dashed' }, itemStyle: { color: T.faint } },
    ],
  }, true)
}

async function load() {
  const c = props.code
  if (!c) return
  loading.value = true
  notFound.value = false
  item.value = null
  expect.value = null
  shareHistory.value = []
  // v-if 会移除图表容器：先销毁旧实例，否则换债后用旧 canvas setOption 图表空白
  if (chart) { chart.dispose(); chart = null }
  if (shareChart) { shareChart.dispose(); shareChart = null }
  try {
    const [rd, rs, re] = await Promise.allSettled([
      axios.get(API + '/api/cb/' + c),
      axios.get(API + '/api/cb/' + c + '/share-history?days=5000'),
      axios.get(API + '/api/cb/' + c + '/expected-return?lookback=1500'),
    ])
    if (!alive || props.code !== c) return
    if (rd.status !== 'fulfilled') {
      if (rd.reason?.response?.status === 404) notFound.value = true
      return
    }
    item.value = rd.value.data.item || null
    asof.value = rd.value.data.asof || ''
    shareHistory.value = rs.status === 'fulfilled' ? (rs.value.data.data || []) : []
    expect.value = re.status === 'fulfilled' ? re.value.data : null
    loading.value = false
    // 先等 loading=false 让 v-else-if 挂载图表容器，再初始化 ECharts
    await nextTick()
    drawChart()
    drawShareChart()
  } finally {
    loading.value = false
  }
}

function resizeCharts() {
  drawChart()
  drawShareChart()
}
onMounted(() => {
  load()
  window.addEventListener('resize', resizeCharts)
})
onUnmounted(() => {
  alive = false
  window.removeEventListener('resize', resizeCharts)
  if (chart) { chart.dispose(); chart = null }
  if (shareChart) { shareChart.dispose(); shareChart = null }
})
watch(() => props.code, load)
</script>

<style>
.bd-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.bd-title { font-size: 17px; font-weight: 600; color: var(--c-text); }
.bd-code { font-size: var(--text-meta); color: var(--c-text-faint); }
.bd-grid { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 12px; align-items: start; }
.bd-left { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
@media (max-width: 900px) { .bd-grid { grid-template-columns: 1fr; } }
.bd-panel { background: var(--c-card-bg); border: 1px solid var(--c-border); border-radius: var(--radius); padding: 10px 12px; }
.bd-chart-panel { min-height: 320px; display: flex; flex-direction: column; }
.bd-h { margin: 0 0 8px; font-size: 14px; color: var(--c-text); display: flex; align-items: center; gap: 6px; }
.bd-chart { flex: 1; min-height: 300px; width: 100%; }
.bd-side { display: flex; flex-direction: column; gap: 12px; }
.bd-table { width: 100%; border-collapse: collapse; font-size: var(--text-meta); }
.bd-table th { width: 76px; text-align: left; color: var(--c-text-faint); font-weight: 500; padding: 4px 6px; border-bottom: 1px solid var(--c-border-light); white-space: nowrap; }
.bd-table td { color: var(--c-text); padding: 4px 6px; border-bottom: 1px solid var(--c-border-light); white-space: nowrap; }
.bd-link { color: var(--c-accent); text-decoration: none; cursor: pointer; }
.bd-link:hover { text-decoration: underline; }
.bd-reasons { display: flex; flex-direction: column; gap: 4px; }
.bd-reason { font-size: var(--text-meta); color: var(--c-text-dim); }
.bd-risk { font-size: var(--text-meta); color: var(--c-warn); }
.bd-note { margin-top: 8px; font-size: var(--text-meta); color: var(--c-text-faint); }
.bd-sub { font-size: var(--text-meta); color: var(--c-text-faint); font-weight: 400; }
.bd-up { color: var(--c-up); }
.bd-down { color: var(--c-down); }
.bd-expect-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-bottom: 12px; }
.bd-metric { background: var(--c-bg-soft, rgba(127,127,127,.06)); border-radius: var(--radius); padding: 8px 10px; }
.bd-metric-label { display: block; font-size: var(--text-meta); color: var(--c-text-faint); margin-bottom: 2px; }
.bd-metric b { font-size: 18px; line-height: 1.2; }
.bd-metric small { display: block; margin-top: 3px; font-size: 11px; color: var(--c-text-faint); }
.bd-expect-summary { margin-bottom: 10px; padding: 7px 10px; border-radius: var(--radius); background: var(--c-bg-soft, rgba(127,127,127,.06)); font-size: var(--text-meta); color: var(--c-text-dim); line-height: 1.6; }
.bd-expect-cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; }
.bd-mini-title { font-size: var(--text-meta); font-weight: 600; color: var(--c-text-dim); margin-bottom: 4px; }
@media (max-width: 900px) {
  .bd-expect-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .bd-expect-cols { grid-template-columns: 1fr; }
}
</style>
