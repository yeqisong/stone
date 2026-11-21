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
      <div class="bd-panel bd-chart-panel">
        <h4 class="bd-h"><AppIcon name="bar-chart-2" :size="13" /> 价格 / 转股价值 / 溢价率</h4>
        <div ref="chartEl" class="bd-chart"></div>
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
              <tr><th>停止转股</th><td>{{ item.conv_stop_date || '—' }}</td><th>剩余期限</th><td>{{ item.maturity_years != null ? item.maturity_years + '年' : '—' }}</td></tr>
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
const chartEl = ref(null)
let chart = null
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
      { name: '转债价', type: 'line', data: prices, symbol: 'none', lineStyle: { color: T.up, width: 1.6 } },
      { name: '转股价值', type: 'line', data: cvs, symbol: 'none', lineStyle: { color: T.series[3], width: 1.2 } },
      { name: '溢价率%', type: 'line', yAxisIndex: 1, data: prems, symbol: 'none', lineStyle: { color: T.warn, width: 1, type: 'dashed' } },
    ],
  }, true)
}

async function load() {
  const c = props.code
  if (!c) return
  loading.value = true
  notFound.value = false
  item.value = null
  // v-if 会移除图表容器：先销毁旧实例，否则换债后用旧 canvas setOption 图表空白
  if (chart) { chart.dispose(); chart = null }
  try {
    const r = await axios.get(API + '/api/cb/' + c)
    if (!alive || props.code !== c) return
    item.value = r.data.item || null
    asof.value = r.data.asof || ''
    loading.value = false
    // 先等 loading=false 让 v-else-if 挂载图表容器，再初始化 ECharts
    await nextTick()
    drawChart()
  } catch (e) {
    if (e.response?.status === 404) notFound.value = true
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  window.addEventListener('resize', drawChart)
})
onUnmounted(() => {
  alive = false
  window.removeEventListener('resize', drawChart)
  if (chart) { chart.dispose(); chart = null }
})
watch(() => props.code, load)
</script>

<style>
.bd-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.bd-title { font-size: 17px; font-weight: 600; color: var(--c-text); }
.bd-code { font-size: var(--text-meta); color: var(--c-text-faint); }
.bd-grid { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 12px; align-items: start; }
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
</style>
