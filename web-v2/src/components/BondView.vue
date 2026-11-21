<template>
<div class="page-fill">
  <n-space align="center" style="margin-bottom:8px;flex-shrink:0" wrap>
    <n-button-group size="tiny">
      <n-button size="tiny" :type="viewTab==='list'?'primary':'default'" @click="switchView('list')">列表</n-button>
      <n-button size="tiny" :type="viewTab==='strategy'?'primary':'default'" @click="switchView('strategy')">策略</n-button>
      <n-button size="tiny" :type="viewTab==='analysis'?'primary':'default'" @click="switchView('analysis')">分析</n-button>
    </n-button-group>
    <n-input v-model:value="kw" size="tiny" placeholder="转债代码/名称/正股..." clearable style="width:190px" @keyup.enter="doSearch" />
    <n-button class="cta" type="primary" size="tiny" :loading="loading" @click="doSearch">搜索</n-button>
    <n-select v-model:value="sortField" size="tiny" style="width:130px" :options="sortOptions" @update:value="doSearch" />
    <n-button size="tiny" :type="onlyActive?'primary':'default'" @click="toggleActive">
      {{ onlyActive ? '仅活跃' : '含退市' }}
    </n-button>
    <span class="bond-asof">数据 {{ asof || '—' }} · 共 {{ total }} 只</span>
  </n-space>

  <template v-if="viewTab === 'list'">
    <n-data-table class="fill-table" flex-height :columns="columns" :data="rows" size="small"
      :row-props="rowProps" :loading="loading" :bordered="false" scroll-x="1350" />
    <ListPagination :total="total" :page="page" :page-size="pageSize" @change="goPage" />
  </template>

  <template v-else-if="viewTab === 'strategy'">
    <div class="bond-pools">
      <div v-for="(p, key) in pools" :key="key" class="bond-pool">
        <div class="bp-head">
          <span class="bp-title">{{ STRATEGY_LABELS[key] || key }}</span>
          <span class="bp-count">{{ p.length }} 只</span>
        </div>
        <div v-if="p.length" class="bp-rows">
          <button v-for="x in p.slice(0, 8)" :key="x.bond_code" type="button" class="bp-row"
            :title="(x.reasons||[]).join('；')" @click="openBond(x)">
            <span class="bp-name">{{ x.bond_name }}</span>
            <span class="bp-code">{{ x.bond_code }}</span>
            <b class="bp-price">{{ x.price != null ? x.price.toFixed(2) : '—' }}</b>
            <span class="bp-prem" :class="premClass(x.premium_rate)">{{ premText(x.premium_rate) }}</span>
            <span class="bp-dl">双低 {{ x.double_low != null ? x.double_low.toFixed(1) : '—' }}</span>
            <span class="bp-score">{{ x.score }} · {{ x.label }}</span>
          </button>
        </div>
        <n-empty v-else description="当前池为空" style="padding:10px" />
      </div>
    </div>
  </template>

  <template v-else>
    <div class="bond-analysis">
      <div class="ba-head">
        <span>价格 × 溢价率 分布 · 半径=剩余规模 · 颜色=规则评分</span>
        <span class="ba-note">左上高债底 / 右上高弹性 / 右下高溢价风险；点击圆点进转债详情</span>
      </div>
      <div ref="scatterEl" class="bond-scatter"></div>
    </div>
  </template>

  <BondCoach />
</div>
</template>

<script setup>
import ListPagination from './ListPagination.vue'
import BondCoach from './BondCoach.vue'
import { ref, computed, h, onMounted, onUnmounted, nextTick } from 'vue'
import { useNavStore } from '../stores/nav'
import { NButton, NButtonGroup, NInput, NSelect, NDataTable, NEmpty, NSpace, useMessage } from 'naive-ui'
import axios from 'axios'
import * as echarts from 'echarts'
import { chartColors as T } from '../utils/chart'

const nav = useNavStore()
const message = useMessage()
const API = window.location.origin
const STRATEGY_LABELS = {
  low_premium: '低溢价进攻',
  double_low: '双低防守',
  small_elastic: '小盘弹性',
  defensive: '防守债性',
  risk: '风险回避',
}

const viewTab = ref('list')
const kw = ref('')
const sortField = ref('score')
const order = ref('desc')
const strategy = ref('')
const onlyActive = ref(true)
const page = ref(1)
const pageSize = ref(50)
const rows = ref([])
const total = ref(0)
const asof = ref('')
const loading = ref(false)
const pools = ref({})
const scatterEl = ref(null)
let scatterChart = null
let alive = true

const sortOptions = [
  { label: '规则评分', value: 'score' },
  { label: '转债价', value: 'price' },
  { label: '溢价率', value: 'premium' },
  { label: '双低', value: 'double_low' },
  { label: '正股涨跌', value: 'stock_pct_chg' },
  { label: '剩余规模', value: 'remaining_size' },
  { label: '换手率', value: 'turnover' },
  { label: '价格分位', value: 'price_percentile' },
]

// URL 状态（#/bonds?tab=&kw=&page=&sf=&od=）：返回/直达还原筛选与页码
function parseHash() {
  try {
    const q = new URLSearchParams((location.hash.split('?')[1] || ''))
    const t = q.get('tab'); if (t) viewTab.value = t
    const k = q.get('kw'); if (k) kw.value = k
    const p = parseInt(q.get('page')); if (p && p > 0) page.value = p
    const sf = q.get('sf'); if (sf) sortField.value = sf
    const od = q.get('od'); if (od) order.value = od
    const st = q.get('strategy'); if (st) strategy.value = st
    const oa = q.get('oa'); if (oa) onlyActive.value = oa !== '0'
  } catch (e) {}
}
function syncHash() {
  const q = new URLSearchParams()
  q.set('tab', viewTab.value); q.set('kw', kw.value); q.set('page', page.value)
  q.set('sf', sortField.value); q.set('od', order.value); q.set('oa', onlyActive.value ? '1' : '0')
  if (strategy.value) q.set('strategy', strategy.value)
  history.replaceState(null, '', location.pathname + '#/bonds?' + q.toString())
}

const columns = computed(() => [
  { title: '代码', key: 'bond_code', width: 82, fixed: 'left', render(r) {
    return h('span', { style: { color: 'var(--c-text-dim)', cursor: 'pointer' }, onClick: () => openBond(r) }, r.bond_code)
  }},
  { title: '名称', key: 'bond_name', width: 110, fixed: 'left', render(r) {
    return h('span', { style: { color: 'var(--c-text)', cursor: 'pointer' }, title: r.bond_full_name || r.bond_name, onClick: () => openBond(r) }, r.bond_name || '—')
  }},
  { title: '转债价', key: 'price', width: 92, align: 'right', render(r) {
    return h('span', { style: { fontWeight: 600, whiteSpace: 'nowrap' } }, r.price != null ? r.price.toFixed(3) : '—')
  }},
  { title: '涨跌', key: 'pct_chg', width: 82, align: 'right', render(r) {
    if (r.pct_chg == null) return '—'
    const c = r.pct_chg >= 0 ? 'var(--c-up)' : 'var(--c-down)'
    return h('span', { style: { color: c, whiteSpace: 'nowrap' } }, (r.pct_chg >= 0 ? '+' : '') + r.pct_chg.toFixed(2) + '%')
  }},
  { title: '溢价率', key: 'premium_rate', width: 88, align: 'right', render(r) {
    if (r.premium_rate == null) return '—'
    const p = r.premium_rate * 100
    const c = p <= 20 ? 'var(--c-up)' : (p >= 80 ? 'var(--c-down)' : 'var(--c-text)')
    return h('span', { style: { color: c, whiteSpace: 'nowrap' } }, p.toFixed(1) + '%')
  }},
  { title: '双低', key: 'double_low', width: 84, align: 'right', render(r) {
    return h('span', { style: { whiteSpace: 'nowrap' } }, r.double_low != null ? r.double_low.toFixed(1) : '—')
  }},
  { title: '正股', key: 'stock_name', width: 110, render(r) {
    return h('span', {
      style: { color: 'var(--c-text-dim)', cursor: 'pointer', textDecoration: 'underline', textDecorationColor: 'var(--c-border)' },
      title: r.stock_code || '', onClick: () => openStock(r),
    }, r.stock_name || r.stock_code || '—')
  }},
  { title: '正股涨跌', key: 'stock_pct_chg', width: 90, align: 'right', render(r) {
    if (r.stock_pct_chg == null) return '—'
    const c = r.stock_pct_chg >= 0 ? 'var(--c-up)' : 'var(--c-down)'
    return h('span', { style: { color: c, whiteSpace: 'nowrap' } }, (r.stock_pct_chg >= 0 ? '+' : '') + r.stock_pct_chg.toFixed(2) + '%')
  }},
  { title: '剩余规模', key: 'remaining_size', width: 96, align: 'right', render(r) {
    if (r.remaining_size == null) return '—'
    const y = r.remaining_size / 1e8
    return h('span', { style: { whiteSpace: 'nowrap' } }, (y >= 10 ? y.toFixed(1) : y.toFixed(2)) + '亿')
  }},
  { title: '换手', key: 'turnover_pct', width: 76, align: 'right', render(r) {
    return r.turnover_pct != null ? r.turnover_pct.toFixed(2) + '%' : '—'
  }},
  { title: '评分', key: 'score', width: 118, align: 'center', render(r) {
    const c = r.score >= 70 ? 'var(--c-up)' : (r.score >= 55 ? 'var(--c-text)' : (r.score >= 40 ? 'var(--c-warn)' : 'var(--c-down)'))
    return h('span', { style: { color: c, fontWeight: 600, whiteSpace: 'nowrap' } }, `${r.score} ${r.label}`)
  }},
  { title: '风险/理由', key: 'reasons', width: 230, render(r) {
    const text = (r.risk_flags || []).join(' / ') || (r.reasons || []).slice(0, 2).join('；')
    return h('span', { style: { color: 'var(--c-text-dim)' }, title: (r.risk_flags || []).concat(r.reasons || []).join('；') }, text || '—')
  }},
])

function rowProps(row) {
  return { style: 'cursor:pointer', onClick: () => openBond(row) }
}

function openBond(x) {
  if (x?.bond_code) nav.showBondDetail(x.bond_code)
}
function openStock(x) {
  if (x?.stock_code) nav.showDetail(x.stock_code)
}
function premClass(v) {
  if (v == null) return ''
  const p = v * 100
  if (p <= 20) return 'up'
  if (p >= 80) return 'down'
  return ''
}
function premText(v) {
  return v == null ? '—' : (v * 100).toFixed(1) + '%'
}

function switchView(t) {
  viewTab.value = t
  syncHash()
  if (t === 'strategy') loadPools()
  if (t === 'analysis') load()
}
function toggleActive() {
  onlyActive.value = !onlyActive.value
  page.value = 1
  load()
}
function goPage(p) {
  page.value = p
  load()
}
function doSearch() {
  page.value = 1
  load()
}

async function load() {
  loading.value = true
  syncHash()
  try {
    // 分析页需要全市场散点，不复用列表分页的 50 条
    const analysis = viewTab.value === 'analysis'
    const q = { page: analysis ? 1 : page.value, page_size: analysis ? 1000 : pageSize.value,
                sort: sortField.value, order: order.value, only_active: onlyActive.value }
    if (kw.value) q.kw = kw.value
    if (strategy.value) q.strategy = strategy.value
    const r = await axios.get(API + '/api/cb/list', { params: q })
    if (!alive) return
    rows.value = r.data.items || []
    total.value = r.data.total || 0
    asof.value = r.data.asof || ''
    if (analysis) nextTick(drawScatter)
  } catch (e) {
    message.error(e.response?.data?.detail || '可转债列表加载失败')
  } finally {
    loading.value = false
  }
}

async function loadPools() {
  try {
    const keys = Object.keys(STRATEGY_LABELS)
    const rs = await Promise.all(keys.map(k =>
      axios.get(API + '/api/cb/list', { params: { strategy: k, page_size: 8, sort: 'score', order: 'desc', only_active: onlyActive.value } })))
    if (!alive) return
    const out = {}
    keys.forEach((k, i) => { out[k] = rs[i].data.items || [] })
    pools.value = out
  } catch (e) {
    message.error('策略池加载失败')
  }
}

function drawScatter() {
  const el = scatterEl.value
  if (!el) return
  if (!scatterChart) { scatterChart = echarts.init(el); el._echart = scatterChart }
  const pts = rows.value.filter(x => x.price != null && x.premium_rate != null).map(x => {
    const size = Math.max(6, Math.min(22, Math.sqrt((x.remaining_size || 1e8) / 1e8) * 6))
    const c = x.score >= 70 ? T.up : (x.score >= 55 ? T.series[3] : (x.score >= 40 ? T.warn : T.down))
    return {
      value: [x.price, +(x.premium_rate * 100).toFixed(2)],
      symbolSize: size,
      itemStyle: { color: c, opacity: 0.82 },
      bond_code: x.bond_code,
      name: x.bond_name,
    }
  })
  scatterChart.setOption({
    tooltip: {
      formatter(p) {
        const x = rows.value.find(y => y.bond_code === p.data.bond_code) || {}
        return `<b>${x.bond_name || ''} ${x.bond_code || ''}</b><br/>价格 ${x.price ?? '—'}<br/>溢价 ${premText(x.premium_rate)}<br/>双低 ${x.double_low != null ? x.double_low.toFixed(1) : '—'}<br/>评分 ${x.score} ${x.label}`
      },
    },
    grid: { left: 56, right: 24, top: 24, bottom: 36 },
    xAxis: { name: '转债价', type: 'value', scale: true, axisLabel: { fontSize: 10, color: T.faint }, splitLine: { lineStyle: { color: T.line } } },
    yAxis: { name: '溢价率%', type: 'value', axisLabel: { fontSize: 10, color: T.faint, formatter: v => v.toFixed(0) + '%' }, splitLine: { lineStyle: { color: T.line, type: 'dashed' } } },
    series: [{ type: 'scatter', data: pts, emphasis: { focus: 'series' } }],
  }, true)
}

function onPopState() {
  if (nav.tab !== 'b') return
  parseHash()
  load()
  if (viewTab.value === 'strategy') loadPools()
}
onMounted(async () => {
  parseHash()
  load()
  if (viewTab.value === 'strategy') loadPools()
  window.addEventListener('resize', drawScatter)
  window.addEventListener('popstate', onPopState)
})
onUnmounted(() => {
  alive = false
  window.removeEventListener('resize', drawScatter)
  window.removeEventListener('popstate', onPopState)
  if (scatterChart) { scatterChart.dispose(); scatterChart = null }
})
</script>

<style>
.bond-asof { font-size: var(--text-meta); color: var(--c-text-faint); white-space: nowrap; }
.bond-pools { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; overflow-y: auto; }
.bond-pool { background: var(--c-card-bg); border: 1px solid var(--c-border); border-radius: var(--radius); padding: 10px 12px; }
.bp-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.bp-title { font-weight: 600; color: var(--c-text); }
.bp-count { font-size: var(--text-meta); color: var(--c-text-faint); }
.bp-rows { display: flex; flex-direction: column; gap: 4px; }
.bp-row {
  display: grid; grid-template-columns: 1fr 58px 62px 62px 82px 80px;
  align-items: center; gap: 6px; padding: 4px 6px; border: 0; border-radius: 4px;
  background: transparent; color: var(--c-text-dim); text-align: left; font-size: var(--text-meta); cursor: pointer;
}
.bp-row:hover { background: var(--c-card-bg-hover); color: var(--c-text); }
.bp-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--c-text); }
.bp-code, .bp-dl, .bp-score { color: var(--c-text-dim); }
.bp-price { font-variant-numeric: tabular-nums; color: var(--c-text); }
.bp-prem.up { color: var(--c-up); }
.bp-prem.down { color: var(--c-down); }
.bond-analysis { display: flex; flex-direction: column; gap: 8px; min-height: 0; }
.ba-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: var(--text-meta); color: var(--c-text-dim); }
.ba-note { color: var(--c-text-faint); }
.bond-scatter { flex: 1; min-height: 360px; width: 100%; }
</style>
