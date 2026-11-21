<template>
  <n-drawer v-model:show="visible" placement="right" :width="isNarrow ? '96%' : '60vw'">
    <n-drawer-content closable>
      <template #header>
        <div class="cl-head">
          <div>
            <div class="cl-title">正股 × 转债联动</div>
            <div class="cl-sub">
              {{ stockName || stockCode }} · {{ data?.date_range?.[0] || '—' }} ~ {{ data?.date_range?.[1] || '—' }} ·
              {{ data?.days || 0 }} 个交易日 · {{ data?.bonds?.length || 0 }} 只 · 打开时实时计算
            </div>
          </div>
        </div>
      </template>

      <div class="cl-body">
        <n-spin v-if="loading" style="padding:40px" />
        <n-empty v-else-if="!data || !data.bonds?.length" description="关联转债样本不足，暂无法分析" style="padding:40px" />
        <template v-else>
          <div class="cl-controls">
            <n-select v-model:value="selectedCode" size="small" :options="bondOptions" style="flex:1" />
            <n-select v-model:value="rangeDays" size="small" style="width:112px"
              :options="[{label:'近1年',value:250},{label:'近2年',value:500},{label:'近4年',value:1000},{label:'全部',value:5000}]" />
            <n-select v-model:value="chartView" size="small" style="width:112px"
              :options="[{label:'联动走势',value:'trend'},{label:'滚动相关',value:'corr'},{label:'收益散点',value:'scatter'}]" />
          </div>

          <div class="cl-panel">
            <div class="cl-panel-head">
              <span>联动解读</span>
              <span class="cl-note">转股价按当前值近似历史溢价</span>
            </div>
            <div class="cl-interpret">
              {{ selectedBond?.label }}
              <span class="cl-note"> · 样本 {{ m?.days || 0 }} 日（{{ m?.start_date || '—' }} ~ {{ m?.end_date || '—' }}）</span>
            </div>
            <div class="cl-metrics">
              <div><span>全样本相关</span><b>{{ fmtNum(m?.corr_full) }}</b></div>
              <div><span>20日相关</span><b>{{ fmtNum(m?.corr_20) }}</b></div>
              <div><span>60日相关</span><b>{{ fmtNum(m?.corr_60) }}</b></div>
              <div><span>β（正股→转债）</span><b>{{ fmtNum(m?.beta) }}</b></div>
            </div>
            <div class="cl-metrics">
              <div><span>5日</span>
                <b :style="{color: retColor(m?.returns?.['5']?.excess)}">{{ pct(m?.returns?.['5']?.stock) }} / {{ pct(m?.returns?.['5']?.cb) }}</b>
              </div>
              <div><span>20日</span>
                <b :style="{color: retColor(m?.returns?.['20']?.excess)}">{{ pct(m?.returns?.['20']?.stock) }} / {{ pct(m?.returns?.['20']?.cb) }}</b>
              </div>
              <div><span>60日</span>
                <b :style="{color: retColor(m?.returns?.['60']?.excess)}">{{ pct(m?.returns?.['60']?.stock) }} / {{ pct(m?.returns?.['60']?.cb) }}</b>
              </div>
              <div><span>当前溢价</span><b>{{ pct(m?.premium?.current) }}</b></div>
            </div>
          </div>

          <div class="cl-panel">
            <div class="cl-panel-head"><span>图表</span><span class="cl-note">可滚轮缩放</span></div>
            <div v-show="chartView==='trend'" ref="trendEl" class="cl-chart tall"></div>
            <div v-show="chartView==='corr'" ref="corrEl" class="cl-chart"></div>
            <div v-show="chartView==='scatter'" ref="scatterEl" class="cl-chart tall"></div>
          </div>

          <div class="cl-disclaimer">
            统计口径基于日线重叠样本；“联动”不代表因果，也不构成投资建议。
          </div>
        </template>
      </div>
    </n-drawer-content>
  </n-drawer>
</template>

<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { NDrawer, NDrawerContent, NSelect, NSpin, NEmpty } from 'naive-ui'
import axios from 'axios'
import * as echarts from 'echarts'
import { chartColors as T } from '../utils/chart'
import { useViewport } from '../utils/viewport'

const props = defineProps({
  show: Boolean,
  stockCode: String,
  stockName: String,
  defaultBondCode: String,
})
const emit = defineEmits(['update:show'])
const { isNarrow } = useViewport()
const API = window.location.origin

const visible = computed({
  get: () => props.show,
  set: v => emit('update:show', v),
})

const loading = ref(false)
const data = ref(null)
const selectedCode = ref('')
const chartView = ref('trend')
const rangeDays = ref(500)
const trendEl = ref(null)
const corrEl = ref(null)
const scatterEl = ref(null)
let trendChart = null
let corrChart = null
let scatterChart = null
let alive = true

const bondOptions = computed(() => (data.value?.bonds || []).map(b => ({
  label: `${b.bond_name || ''} ${b.bond_code}`,
  value: b.bond_code,
})))

const selectedBond = computed(() => (data.value?.bonds || [])
  .find(b => b.bond_code === selectedCode.value) || null)
const m = computed(() => selectedBond.value?.metrics || null)

function fmtNum(v) {
  return v == null ? '—' : Number(v).toFixed(2)
}
function pct(v) {
  return v == null ? '—' : (v >= 0 ? '+' : '') + (v * 100).toFixed(2) + '%'
}
function retColor(v) {
  if (v == null) return undefined
  return v >= 0 ? 'var(--c-up)' : 'var(--c-down)'
}

function destroyCharts() {
  for (const c of [trendChart, corrChart, scatterChart]) {
    if (c) c.dispose()
  }
  trendChart = corrChart = scatterChart = null
}

function drawCharts() {
  const b = selectedBond.value
  if (!b || !b.series) return
  const s = b.series

  if (chartView.value === 'trend' && trendEl.value) {
    if (!trendChart) { trendChart = echarts.init(trendEl.value); trendEl.value._echart = trendChart }
    trendChart.setOption({
      tooltip: { trigger: 'axis', valueFormatter: v => v == null ? '—' : Number(v).toFixed(2) },
      legend: { top: 0, right: 4, itemWidth: 12, itemHeight: 6, textStyle: { fontSize: 10, color: T.dim } },
      grid: { left: 52, right: 58, top: 28, bottom: 34 },
      xAxis: { type: 'category', data: s.dates, axisLabel: { fontSize: 10, color: T.faint, hideOverlap: true }, axisLine: { lineStyle: { color: T.line } } },
      yAxis: [
        { type: 'value', scale: true, axisLabel: { fontSize: 10, color: T.faint }, splitLine: { lineStyle: { color: T.line } } },
        { type: 'value', axisLabel: { fontSize: 10, color: T.faint, formatter: v => v + '%' }, splitLine: { show: false } },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: 0 },
        { type: 'slider', xAxisIndex: 0, height: 14, bottom: 2, borderColor: 'transparent', backgroundColor: T.line, fillerColor: T.line },
      ],
      series: [
        { name: '正股(归一)', type: 'line', data: s.stock_idx, symbol: 'none', lineStyle: { color: T.series[3], width: 1.6 } },
        { name: '转债(归一)', type: 'line', data: s.cb_idx, symbol: 'none', lineStyle: { color: T.up, width: 1.4 } },
        { name: '溢价率%', type: 'line', yAxisIndex: 1, data: s.premium_rate.map(x => x == null ? null : +(x * 100).toFixed(2)), symbol: 'none', lineStyle: { color: T.warn, width: 1, type: 'dashed' } },
      ],
    }, true)
    trendChart.resize()
  }

  if (chartView.value === 'corr' && corrEl.value) {
    if (!corrChart) { corrChart = echarts.init(corrEl.value); corrEl.value._echart = corrChart }
    corrChart.setOption({
      tooltip: { trigger: 'axis', valueFormatter: v => v == null ? '—' : Number(v).toFixed(2) },
      grid: { left: 48, right: 20, top: 18, bottom: 34 },
      xAxis: { type: 'category', data: s.dates, axisLabel: { fontSize: 10, color: T.faint, hideOverlap: true }, axisLine: { lineStyle: { color: T.line } } },
      yAxis: { type: 'value', min: -1, max: 1, axisLabel: { fontSize: 10, color: T.faint }, splitLine: { lineStyle: { color: T.line, type: 'dashed' } } },
      dataZoom: [{ type: 'inside', xAxisIndex: 0 }],
      series: [
        { name: '20日滚动相关', type: 'line', data: s.rolling_corr.map(x => x == null ? null : +x.toFixed(3)), symbol: 'none', lineStyle: { color: T.series[4], width: 1.5 }, areaStyle: { color: T.series[4], opacity: .10 } },
      ],
    }, true)
    corrChart.resize()
  }

  if (chartView.value === 'scatter' && scatterEl.value) {
    if (!scatterChart) { scatterChart = echarts.init(scatterEl.value); scatterEl.value._echart = scatterChart }
    const pts = []
    for (let i = 0; i < s.stock_ret.length; i++) {
      const x = s.stock_ret[i], y = s.cb_ret[i]
      if (x == null || y == null) continue
      pts.push([+(x * 100).toFixed(2), +(y * 100).toFixed(2)])
    }
    scatterChart.setOption({
      tooltip: { formatter: p => `正股 ${p.data[0]}%<br/>转债 ${p.data[1]}%` },
      grid: { left: 52, right: 22, top: 20, bottom: 40 },
      xAxis: { name: '正股日收益%', type: 'value', scale: true, axisLabel: { fontSize: 10, color: T.faint, formatter: v => v + '%' }, splitLine: { lineStyle: { color: T.line } } },
      yAxis: { name: '转债日收益%', type: 'value', scale: true, axisLabel: { fontSize: 10, color: T.faint, formatter: v => v + '%' }, splitLine: { lineStyle: { color: T.line, type: 'dashed' } } },
      series: [{ type: 'scatter', data: pts, symbolSize: 6, itemStyle: { color: T.series[3], opacity: .65 } }],
    }, true)
    scatterChart.resize()
  }
}

async function load() {
  if (!props.stockCode) return
  const reqCode = props.stockCode
  const prevCode = selectedCode.value
  loading.value = true
  // 换股/重开时先清旧数据并销毁旧实例，避免 v-if 重建 DOM 后
  // 仍向已脱离文档的 canvas setOption，导致图表空白。
  data.value = null
  destroyCharts()
  try {
    const r = await axios.get(`${API}/api/stock/${reqCode}/cb-linkage`, { params: { days: rangeDays.value } })
    if (!alive || reqCode !== props.stockCode) return
    data.value = r.data
    const codes = (r.data.bonds || []).map(x => x.bond_code)
    // 切换时间范围时保留当前选中的债；换股/重开才回落到入口指定的债
    if (prevCode && codes.includes(prevCode)) {
      selectedCode.value = prevCode
    } else if (props.defaultBondCode && codes.includes(props.defaultBondCode)) {
      selectedCode.value = props.defaultBondCode
    } else {
      selectedCode.value = codes[0] || ''
    }
    await nextTick()
    drawCharts()
  } catch (e) {
    data.value = null
  } finally {
    loading.value = false
    await nextTick()
    drawCharts()
  }
}

watch(() => props.show, v => {
  if (v) {
    load()
  } else {
    destroyCharts()
  }
}, { immediate: true })
watch(() => props.stockCode, v => { if (props.show) load() })
watch(() => props.defaultBondCode, v => {
  if (props.show && v && (data.value?.bonds || []).some(b => b.bond_code === v)) {
    selectedCode.value = v
  }
})
watch(selectedCode, () => nextTick(drawCharts))
watch(chartView, () => nextTick(drawCharts))
watch(rangeDays, () => { if (props.show) load() })

onUnmounted(() => {
  alive = false
  destroyCharts()
})
</script>

<style>
.cl-head { display: flex; align-items: center; gap: 8px; }
.cl-title { font-weight: 600; color: var(--c-text); }
.cl-sub { font-size: var(--text-meta); color: var(--c-text-faint); }
.cl-body { display: flex; flex-direction: column; gap: 12px; }
.cl-controls { display: flex; gap: 8px; align-items: center; }
.cl-panel { background: var(--c-card-bg); border: 1px solid var(--c-border); border-radius: var(--radius); padding: 10px 12px; }
.cl-panel-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-weight: 600; color: var(--c-text); }
.cl-note { font-size: var(--text-meta); color: var(--c-text-faint); font-weight: 500; }
.cl-interpret { font-size: var(--text-meta); line-height: 1.7; color: var(--c-text); margin-bottom: 8px; }
.cl-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 6px 10px; margin-bottom: 6px; font-size: var(--text-meta); color: var(--c-text-dim); }
.cl-metrics b { color: var(--c-text); font-variant-numeric: tabular-nums; }
.cl-chart { width: 100%; height: 280px; }
.cl-chart.tall { height: 330px; }
.cl-disclaimer { font-size: var(--text-meta); color: var(--c-text-faint); line-height: 1.6; }
</style>
