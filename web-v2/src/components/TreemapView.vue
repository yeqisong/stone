<template>
<div>
  <n-space align="center" style="margin-bottom:8px" wrap>
    <n-button-group size="tiny">
  <n-button :type="metric==='mcap'?'primary':'default'" @click="switchMetric('mcap')">📊 市值</n-button>
  <n-button :type="metric==='volume'?'primary':'default'" @click="switchMetric('volume')">📈 成交量</n-button>
  <n-button :type="metric==='amount'?'primary':'default'" @click="switchMetric('amount')">💰 成交额</n-button>
  <n-button :type="metric==='pe'?'primary':'default'" @click="switchMetric('pe')">📉 PE</n-button>
</n-button-group>
    <n-date-picker v-model:formatted-value="selDate" type="date" value-format="yyyy-MM-dd" size="tiny" style="width:140px" @update:formatted-value="onDateChange" />
    <n-button size="tiny" @click="showGenModal = true">⚡ 生成</n-button>
    <n-tag v-if="!loading && !noData && levelLabel" size="small" style="margin-left:auto">{{levelLabel}}</n-tag>
    <n-button v-if="drillStack.length>0" size="tiny" @click="goBack">◀ {{drillStack.length>1?drillStack[drillStack.length-2].name:'全部行业'}}</n-button>
  </n-space>

  <n-spin v-if="loading" style="padding:60px" />
  <div v-else-if="noData" style="text-align:center;padding:60px;color:var(--c-text-dim)">
    <div style="font-size:48px;margin-bottom:12px">📭</div>
    <div style="font-size:14px">{{selDate}} 暂无树图数据<br><span style="font-size:11px">请等待数据采集完成后重试，或选择其他日期</span></div>
  </div>
  <div v-else>
    <div :id="'treemap-chart'" style="width:100%;height:calc(100vh - 130px);min-height:400px"></div>
  </div>

  <!-- 生成确认弹窗 -->
  <n-modal v-model:show="showGenModal" preset="card" title="⚡ 生成树图" style="width:360px;max-width:85vw" :mask-closable="false">
    <div style="text-align:center;padding:10px 0">
      <div style="font-size:14px;color:var(--c-text);margin-bottom:16px">为 {{selDate}} 重新生成树图数据？<br><span style="font-size:11px;color:var(--c-text-dim)">已有数据将被更新，无数据将新建</span></div>
      <div style="display:flex;gap:10px;justify-content:center">
        <n-button @click="showGenModal = false">取消</n-button>
        <n-button type="primary" @click="doGenerate" :loading="genLoading">生成</n-button>
      </div>
    </div>
  </n-modal>
</div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick, watch } from 'vue'
import { NSpace, NDatePicker, NTag, NButton, NButtonGroup, NSpin, NModal } from 'naive-ui'
import axios from 'axios'
import * as echarts from 'echarts'
import { useMarketStore } from '../stores/market'
import { useThemeStore } from '../stores/theme'

const store = useMarketStore()
const theme = useThemeStore()
const emit = defineEmits(['show-detail'])
const API = window.location.origin
const metric = ref('mcap')
const selDate = ref(store.selDate)
const loading = ref(true)
const showGenModal = ref(false)
const genLoading = ref(false)

const fmt = v => v != null ? Number(v).toLocaleString() : '0'

function switchMetric(m) {
  metric.value = m
  store.drillStack = []
  drillStack.value = []
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
  store.setDate(selDate.value)
  if (selDate.value) history.replaceState(null, '', '#/market/' + selDate.value)
  loadTree()
}

function colorForChg(chg) {
  if (chg > 0) {
    const p = Math.min(chg / 3, 1)
    return `rgb(${Math.round(239-p*80)},${Math.round(68+p*40)},${Math.round(68-p*40)})`
  }
  if (chg < 0) {
    const p = Math.min(Math.abs(chg)/3, 1)
    return `rgb(${Math.round(16+p*60)},${Math.round(185-p*60)},${Math.round(129-p*40)})`
  }
  return '#555'
}

async function loadTree() {
  loading.value = true; noData.value = false
  try {
    const params = { trade_date: selDate.value, metric: metric.value }
    if (drillStack.value.length > 0) {
      params.parent = drillStack.value[drillStack.value.length-1].id
    }
    const r = await axios.get(API+'/api/treemap_data', { params })
    treeData.value = r.data.children || []
    noData.value = !treeData.value.length
    loading.value = false; await nextTick()
    if (!noData.value) renderChart()
  } catch(e) {
    noData.value = true
    loading.value = false
  }
}

function buildTreemapSeries(nodes, depth, borderColor) {
  return nodes.map(n => {
    const item = {
      name: n.name,
      value: n.value,
      itemStyle: { color: colorForChg(n.chg_pct), borderColor: borderColor, borderWidth: 0.5 },
      chg_pct: n.chg_pct,
      _type: n.type,
      _id: n.id,
      _detail: n.detail,
    }
    if (n.children && depth > 0) {
      item.children = buildTreemapSeries(n.children, depth - 1)
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

  const data = buildTreemapSeries(treeData.value, 2, chartBg)

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
          html += `<br/>趋势: ${detail.trend_up!==false?'↑':'↓'}`
        } else {
          html += `<br/>${mLabel}: ${metric.value==='volume'?fmt(val)+'股':metric.value==='amount'?'¥'+(val/1e8).toFixed(1)+'亿':metric.value==='pe'?(100-(val||0)/100).toFixed(0)+'%':'¥'+(val/1e8).toFixed(1)+'亿'}`
          html += `<br/>涨跌: ${(d.chg_pct||0)>0?'+':''}${(d.chg_pct||0).toFixed(2)}%`
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
          upperLabel: { show: true, fontSize: 14, fontWeight: 'bold', color: theme.isDark ? '#fff' : '#1a1a2e', height: 22 },
          itemStyle: { borderColor: chartBg, borderWidth: 2 } },
        { label: { show: true, fontSize: 10, color: theme.isDark ? '#fff' : '#1a1a2e' }, itemStyle: { borderColor: chartBg, borderWidth: 0.5 } },
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
  } catch(e) {} finally {
    genLoading.value = false
  }
}

async function loadLatestDate() {
  try {
    const r = await axios.get(API + '/api/treemap_latest_date')
    if (r.data && r.data.latest_date) {
      selDate.value = r.data.latest_date
      store.setDate(selDate.value)
      history.replaceState(null, '', '#/market/' + selDate.value)
    }
  } catch(e) {}
}

onMounted(async () => {
  await loadLatestDate()
  await nextTick()
  loadTree()
})

// 主题切换时重新渲染图表
watch(() => theme.isDark, () => {
  if (!loading.value && !noData.value) {
    nextTick(() => renderChart())
  }
})
</script>
