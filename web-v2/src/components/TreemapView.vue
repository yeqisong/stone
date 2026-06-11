<template>
<div>
  <n-space align="center" style="margin-bottom:8px" wrap>
    <n-button-group size="tiny">
  <n-button :type="metric==='mcap'?'primary':'default'" @click="switchMetric('mcap')">📊 市值</n-button>
  <n-button :type="metric==='volume'?'primary':'default'" @click="switchMetric('volume')">📈 成交量</n-button>
  <n-button :type="metric==='amount'?'primary':'default'" @click="switchMetric('amount')">💰 成交额</n-button>
  <n-button :type="metric==='pe'?'primary':'default'" @click="switchMetric('pe')">📉 PE</n-button>
</n-button-group>
    <n-date-picker v-model:formatted-value="selDate" type="date" value-format="yyyy-MM-dd" size="small" style="width:140px" @update:formatted-value="onDateChange" />
    <n-tag v-if="!loading && !noData && levelLabel" size="small" style="margin-left:auto">{{levelLabel}}</n-tag>
    <n-button v-if="drillStack.length>0" size="tiny" @click="goBack">◀ {{drillStack.length>1?drillStack[drillStack.length-2].name:'全部行业'}}</n-button>
  </n-space>

  <n-spin v-if="loading" style="padding:60px" />
  <div v-else-if="noData" style="text-align:center;padding:60px;color:rgba(255,255,255,.45)">
    <div style="font-size:48px;margin-bottom:12px">📭</div>
    <div style="font-size:14px">{{selDate}} 暂无树图数据<br><span style="font-size:11px">请等待数据采集完成后重试，或选择其他日期</span></div>
  </div>
  <div v-else>
    <div :id="'treemap-chart'" style="width:100%;height:calc(100vh - 130px);min-height:400px"></div>
  </div>
</div>
</template>

<script setup>
import { ref, onMounted, nextTick, computed } from 'vue'
import { NSpace, NDatePicker, NTag, NButton, NButtonGroup, NSpin } from 'naive-ui'
import axios from 'axios'
import * as echarts from 'echarts'

const emit = defineEmits(['show-detail'])
const API = window.location.origin
const metric = ref('mcap')
const selDate = ref((window._treemapDate || new Date()).toISOString().slice(0,10))
window._treemapDate = null
const loading = ref(true)

const fmt = v => v != null ? Number(v).toLocaleString() : '0'

function switchMetric(m) {
  metric.value = m
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

function buildTreemapSeries(nodes, depth) {
  return nodes.map(n => {
    const item = {
      name: n.name,
      value: n.value,
      itemStyle: { color: colorForChg(n.chg_pct) },
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
  const chart = echarts.init(el)
  el._echart = chart

  const data = buildTreemapSeries(treeData.value, 2)

  chart.setOption({
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
      itemStyle: { borderColor: '#101014', borderWidth: 1 },
      levels: [
        { label: { show: false }, itemStyle: { borderColor: '#101014', borderWidth: 4 } },
        { label: { show: true, fontSize: 14, fontWeight: 'bold', color: '#fff', position: 'insideTopLeft', padding: [4,0,0,6] },
          upperLabel: { show: true, fontSize: 14, fontWeight: 'bold', color: '#fff', height: 22 },
          itemStyle: { borderColor: '#101014', borderWidth: 2 } },
        { label: { show: true, fontSize: 10, color: '#fff' }, itemStyle: { borderColor: 'rgba(255,255,255,0.06)', borderWidth: 0.5 } },
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

onMounted(() => nextTick(() => loadTree()))
</script>
