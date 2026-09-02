<template>
<div>
  <n-spin v-if="loading" style="padding:60px" />
  <div v-else-if="loadError" style="padding:40px;text-align:center;color:var(--c-error);font-size:13px">
    统计加载失败 <n-button size="tiny" @click="load" style="margin-left:8px">重试</n-button>
  </div>
  <template v-else-if="stats">
    <!-- Overview -->
    <StatStrip :items="overviews" />

    <!-- Charts Row -->
    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px">
      <div style="flex:1;min-width:300px">
        <h4 style="font-size:13px;color:var(--c-text);margin:0 0 6px"><AppIcon name="trending-up" :size="13" />  每日信号 & 胜率趋势</h4>
        <div id="st-chart-trend" style="width:100%;height:280px"></div>
      </div>
      <div style="flex:1;min-width:260px">
        <h4 style="font-size:13px;color:var(--c-text);margin:0 0 6px"><AppIcon name="bar-chart-2" :size="13" />  收益分布 (已了结)</h4>
        <div id="st-chart-dist" style="width:100%;height:280px"></div>
      </div>
    </div>

    <!-- Tables Row -->
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <div style="flex:1;min-width:280px">
        <h4 style="font-size:13px;color:var(--c-text);margin:0 0 6px"> 行业胜率 (Top 15)</h4>
        <n-data-table :columns="indCols" :data="stats.by_industry" size="small" :max-height="400" />
      </div>
      <div style="flex:1;min-width:280px">
        <h4 style="font-size:13px;color:var(--c-text);margin:0 0 6px"> 个股信号 (Top 20)</h4>
        <n-data-table :columns="stkCols" :data="stats.top_stocks" size="small" :max-height="400" />
      </div>
    </div>
  </template>
</div>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import { ref, onMounted, nextTick } from 'vue'
import { NSpin, NDataTable, NButton } from 'naive-ui'
import axios from 'axios'
import * as echarts from 'echarts'
import StatStrip from './StatStrip.vue'

const API = window.location.origin
const loading = ref(true)
const stats = ref(null)
const loadError = ref(false)

const overviews = ref([])
const indCols = [
  { title:'行业', key:'industry', width:120, ellipsis:{tooltip:true} },
  { title:'信号', key:'signals', width:55 },
  { title:'胜率', width:65, render(r){ const v=r.win_rate; const c=v>=0.6?'#10b981':v>=0.45?'#f59e0b':'#ef4444'; return h('span',{style:{color:c,fontWeight:600}},(v*100).toFixed(1)+'%') }},
  { title:'均收益', width:75, render(r){ const v=r.avg_return; return h('span',{style:{color:v>=0?'#ef4444':'#10b981'}},(v>=0?'+':'')+(v*100).toFixed(2)+'%') }},
]
const stkCols = [
  { title:'代码', key:'stock_code', width:65 },
  { title:'名称', key:'stock_name', width:72, ellipsis:{tooltip:true} },
  { title:'信号', key:'signals', width:45 },
  { title:'胜率', width:60, render(r){ const v=r.win_rate; const c=v>=0.6?'#10b981':v>=0.45?'#f59e0b':'#ef4444'; return h('span',{style:{color:c,fontWeight:600}},(v*100).toFixed(0)+'%') }},
  { title:'均收益', width:70, render(r){ const v=r.avg_return; return h('span',{style:{color:v>=0?'#ef4444':'#10b981'}},(v>=0?'+':'')+(v*100).toFixed(2)+'%') }},
]

import { h } from 'vue'

async function load() {
  loading.value = true
  try {
    const r = await axios.get(API + '/api/signal/stats?days=90')
    stats.value = r.data
    const o = r.data.overview
    overviews.value = [
      { label:'累计信号', value: o.total, color: 'var(--c-text)' },
      { label:'已了结', value: o.closed, color: 'var(--c-text)' },
      { label:'胜率', value: (o.win_rate*100).toFixed(1)+'%', color: o.win_rate>=0.5?'#10b981':'#ef4444' },
      { label:'均收益', value: (o.avg_return>=0?'+':'')+(o.avg_return*100).toFixed(2)+'%', color: o.avg_return>=0?'#ef4444':'#10b981' },
      { label:'前5日均', value: (o.avg_forward_5d>=0?'+':'')+(o.avg_forward_5d*100).toFixed(2)+'%', color: o.avg_forward_5d>=0?'#ef4444':'#10b981' },
      { label:'前10日均', value: (o.avg_forward_10d>=0?'+':'')+(o.avg_forward_10d*100).toFixed(2)+'%', color: o.avg_forward_10d>=0?'#ef4444':'#10b981' },
    ]
    await nextTick()
    drawTrend()
    drawDist()
  } catch(e) { loadError.value = true } finally { loading.value = false }
}

function makeChart(id, opt) {
  const el = document.getElementById(id)
  if (!el) return null
  if (el._echart) el._echart.dispose()
  const c = echarts.init(el)
  el._echart = c
  c.setOption(opt)
  return c
}

function drawTrend() {
  const dates = stats.value.daily_trend.map(d => d.date)
  const signals = stats.value.daily_trend.map(d => d.signals)
  const winRates = stats.value.daily_trend.map(d => (d.win_rate * 100).toFixed(1))
  makeChart('st-chart-trend', {
    tooltip: { trigger: 'axis' },
    legend: { data: ['信号数', '胜率%'], bottom: 0, textStyle: { fontSize: 10, color: 'var(--c-text-dim)' } },
    grid: { left: '8%', right: '8%', top: 10, bottom: 30 },
    xAxis: { type: 'category', data: dates, axisLabel: { show: false } },
    yAxis: [
      { type: 'value', name: '信号数', splitLine: { lineStyle: { color: 'rgba(128,128,128,0.1)' } } },
      { type: 'value', name: '胜率%', min: 0, max: 100, splitLine: { show: false } }
    ],
    series: [
      { name: '信号数', type: 'bar', data: signals, itemStyle: { color: 'rgba(32,128,240,0.3)' }, barWidth: '60%' },
      { name: '胜率%', type: 'line', yAxisIndex: 1, data: winRates, lineStyle: { color: '#10b981', width: 2 }, symbol: 'none', smooth: true }
    ]
  })
}

function drawDist() {
  const d = stats.value.return_distribution
  const total = d.counts.reduce((a,b) => a+b, 0)
  const data = d.buckets.map((b, i) => ({ label: b, count: d.counts[i], pct: total ? (d.counts[i] / total * 100).toFixed(1) : 0 }))
  makeChart('st-chart-dist', {
    tooltip: { trigger: 'axis', formatter: p => p[0] ? p[0].name + '<br/>' + p[0].value + ' 个 (' + data[p[0].dataIndex].pct + '%)' : '' },
    grid: { left: '8%', right: '3%', top: 10, bottom: 20 },
    xAxis: { type: 'category', data: d.buckets, axisLabel: { fontSize: 9 } },
    yAxis: { splitLine: { lineStyle: { color: 'rgba(128,128,128,0.1)' } } },
    series: [{
      type: 'bar', data: d.counts,
      itemStyle: { color: p => p.value > d.counts[5] ? '#ef4444' : '#10b981' },
      barWidth: '80%'
    }]
  })
}

onMounted(load)
</script>