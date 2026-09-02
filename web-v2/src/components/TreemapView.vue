<template>
<div>
  <n-space align="center" style="margin-bottom:8px" wrap>
    <n-button-group size="tiny">
  <n-button size="tiny" :type="metric==='mcap'?'primary':'default'" @click="switchMetric('mcap')"><AppIcon name="bar-chart-2" :size="13" />  市值</n-button>
  <n-button size="tiny" :type="metric==='volume'?'primary':'default'" @click="switchMetric('volume')"><AppIcon name="trending-up" :size="13" />  成交量</n-button>
  <n-button size="tiny" :type="metric==='amount'?'primary':'default'" @click="switchMetric('amount')"><AppIcon name="dollar-sign" :size="13" />  成交额</n-button>
  <n-button size="tiny" :type="metric==='pe'?'primary':'default'" @click="switchMetric('pe')"><AppIcon name="trending-down" :size="13" />  PE</n-button>
</n-button-group>
    <n-date-picker v-model:formatted-value="selDate" type="date" value-format="yyyy-MM-dd" size="tiny" style="width:140px" @update:formatted-value="onDateChange" />
    <n-button size="tiny" @click="showGenModal = true"><AppIcon name="zap" :size="13" />  生成</n-button>
    <n-button-group size="tiny">
      <n-button size="tiny" :type="colorMode==='chg1d'?'primary':'default'" @click="switchColorMode('chg1d')" title="当日涨跌">日涨跌</n-button>
      <n-button size="tiny" :type="colorMode==='chg20d'?'primary':'default'" @click="switchColorMode('chg20d')" title="20 日涨跌（超跌=深绿，反转视角）">20日</n-button>
      <n-button size="tiny" :type="colorMode==='volr'?'primary':'default'" @click="switchColorMode('volr')" title="量比（今日量/前20日均量）">量比</n-button>
    </n-button-group>
    <n-button size="tiny" :type="onlySignal ? 'primary' : 'default'" @click="onlySignal = !onlySignal; renderChart()"><AppIcon name="zap" :size="13" />  只看信号</n-button>
    <span v-if="!loading && !noData" style="font-size:10px;color:var(--c-text-dim);white-space:nowrap" title="金色=当日信号 · 蓝实线=实盘持仓 · 青虚线=纸面组合">
      <span :style="{color:SIG_COLOR}">━</span>信号 · <span style="color:var(--c-info)">━</span>实盘 · <span style="color:#06b6d4">╌</span>纸面
    </span>
    <n-tag v-if="!loading && !noData && levelLabel" size="small" style="margin-left:auto">{{levelLabel}}</n-tag>
    <n-button v-if="drillStack.length>0" size="tiny" @click="goBack"><AppIcon name="arrow-left" :size="12" /> {{drillStack.length>1?drillStack[drillStack.length-2].name:'全部行业'}}</n-button>
  </n-space>

  <n-spin v-if="loading" style="padding:60px" />
  <div v-else-if="noData" style="text-align:center;padding:60px;color:var(--c-text-dim)">
    <div style="font-size:48px;margin-bottom:12px"><AppIcon name="inbox" :size="44" style="color:var(--c-text-faint)" /></div>
    <div style="font-size:14px">{{selDate}} 暂无树图数据<br><span style="font-size:11px">请等待数据采集完成后重试，或选择其他日期</span></div>
  </div>
  <div v-else>
    <div :id="'treemap-chart'" style="width:100%;height:calc(100vh - 130px - var(--h5-tabbar-h, 0px));min-height:400px"></div>
  </div>

  <!-- 生成确认弹窗 -->
  <n-modal v-model:show="showGenModal" preset="card" title="生成树图" style="width:360px;max-width:85vw" :mask-closable="false">
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
import AppIcon from './AppIcon.vue'
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { NSpace, NDatePicker, NTag, NButton, NButtonGroup, NSpin, NModal } from 'naive-ui'
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

const fmt = v => v != null ? Number(v).toLocaleString() : '0'

const METRICS = ['mcap', 'volume', 'amount', 'pe']
const COLORS = ['chg1d', 'chg20d', 'volr']

function syncQuery() {
  // metric/color 体现在 URL：#/market/:date?metric=volume&color=chg20d（分享/刷新可恢复）
  const q = []
  if (metric.value !== 'mcap') q.push('metric=' + metric.value)
  if (colorMode.value !== 'chg1d') q.push('color=' + colorMode.value)
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
  if (METRICS.includes(m) && m !== metric.value) { metric.value = m; changed = true }
  if (COLORS.includes(c) && c !== colorMode.value) { colorMode.value = c; changed = true }
  return changed
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
  store.setDate(selDate.value)
  if (selDate.value) syncQuery()
  loadTree()
}

const colorMode = ref('chg1d')   // chg1d | chg20d | volr
const onlySignal = ref(false)
const sigLive = ref({})          // 当日实时信号 {code: {strength, reason, model_version}}
const SIG_COLOR = '#f59e0b'

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

function colorForChg(chg) {
  // 固定色相：涨=红、跌=绿；幅度越大颜色越深（±3% 封顶）
  if (chg > 0) {
    const p = Math.min(chg / 3, 1)
    return `rgb(235,${Math.round(95 - p * 72)},${Math.round(95 - p * 72)})`
  }
  if (chg < 0) {
    const p = Math.min(Math.abs(chg) / 3, 1)
    return `rgb(${Math.round(95 - p * 72)},200,${Math.round(95 - p * 72)})`
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
    await Promise.all([loadSignals(), loadHoldings()])
    noData.value = !treeData.value.length
    loading.value = false; await nextTick()
    if (!noData.value) renderChart()
  } catch(e) {
    noData.value = true
    loading.value = false
  }
}

// 行业块统计按当前着色模式计算：chg20d 模式用 20 日涨跌（s20），其余用当日涨跌
function indStats(list) {
  const vals = list.map(v => colorMode.value === 'chg20d' ? v.detail?.s20 : v.chg)
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
    else if (paper) { bColor = '#06b6d4'; bWidth = 2; bType = 'dashed'; namePrefix = '◇' }
    else if (sig) { bColor = SIG_COLOR; bWidth = 2.5 }
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
          if (detail.s20 != null) html += `<br/>20日: ${detail.s20>0?'+':''}${detail.s20.toFixed(1)}%`
          if (detail.vr != null) html += `<br/>量比: ${detail.vr.toFixed(2)}`
          if (detail.tr != null) html += `<br/>换手: ${detail.tr.toFixed(2)}%`
          html += `<br/>趋势: ${detail.trend_up!==false?'↑':'↓'}`
          if (d._sig) html += `<br/><span style="color:#f59e0b">★ 信号强度${d._sig.strength||'?'} ${d._sig.reason||''}</span>`
          if (d._hold) html += `<br/><span style="color:#2080f0">◆ 实盘持仓 ${d._hold.qty} 股 / 成本 ¥${Number(d._hold.cost).toFixed(2)}</span>`
          if (d._paper) html += `<br/><span style="color:#06b6d4">◇ 纸面持仓 ${d._paper.shares} 股 / 买价 ¥${Number(d._paper.buy_price).toFixed(2)}</span>`
        } else {
          html += `<br/>${mLabel}: ${metric.value==='volume'?fmt(val)+'股':metric.value==='amount'?'¥'+(val/1e8).toFixed(1)+'亿':metric.value==='pe'?(100-(val||0)/100).toFixed(0)+'%':'¥'+(val/1e8).toFixed(1)+'亿'}`
          html += `<br/>涨跌: ${(d.chg_pct||0)>0?'+':''}${(d.chg_pct||0).toFixed(2)}%`
          const up = d._up || {}
          const tag = colorMode.value === 'chg20d' ? '20日' : '当日'
          if (up.up != null) html += `<br/>上涨家数(${tag}): ${up.up}%`
          else if (detail.up_ratio != null) html += `<br/>上涨家数: ${detail.up_ratio}%`
          if (up.med != null) html += `<br/>中位涨跌(${tag}): ${up.med>0?'+':''}${up.med}%`
          else if (detail.med_chg != null) html += `<br/>中位涨跌: ${detail.med_chg>0?'+':''}${detail.med_chg}%`
          if (detail.avg_tr) html += `<br/>平均换手: ${detail.avg_tr}%`
          if (detail.sig) html += `<br/><span style="color:#f59e0b">★ 信号 ${detail.sig} 只</span>`
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
}
const _tm_popstate = () => {
  if (navStore.tab !== 'm') return   // 组件已卸载时守卫
  if (applyQuery()) { drillStack.value = []; loadTree() }
}
window.addEventListener('popstate', _tm_popstate)
window.addEventListener('resize', _tm_resize)
onUnmounted(() => {
  window.removeEventListener('resize', _tm_resize)
  window.removeEventListener('popstate', _tm_popstate)
  const el = document.getElementById('treemap-chart')
  if (el && el._echart) { el._echart.dispose(); el._echart = null }
})

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
})
</script>
