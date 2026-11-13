<template>
<div class="page-fill">
  <n-space align="center" style="margin-bottom:8px;flex-shrink:0" wrap>
    <n-button-group size="tiny">
      <n-button size="tiny" :type="cat==='stock'?'primary':'default'" @click="switchCat('stock')"><AppIcon name="filter" :size="13" />  个股</n-button>
      <n-button size="tiny" :type="cat==='index'?'primary':'default'" @click="switchCat('index')"><AppIcon name="bar-chart-2" :size="13" />  指数</n-button>
      <n-button size="tiny" :type="cat==='etf'?'primary':'default'" @click="switchCat('etf')"><AppIcon name="pie-chart" :size="13" />  ETF</n-button>
    </n-button-group>
    <StockSuggestInput v-model:value="kw" size="tiny" width="180px" placeholder="搜索代码或名称..." @select="onSuggestPick" @enter="doSearch" />
    <n-button class="cta" type="primary" size="tiny" :loading="loading" @click="doSearch">搜索</n-button>
    <n-button size="tiny" :type="radarMode?'primary':'default'" @click="toggleRadar"><AppIcon name="activity" :size="13" />  盘中雷达</n-button>
  </n-space>

  <template v-if="radarMode">
    <div class="radar-head">
      <span>关注池实时排序 · {{ radarAt || '—' }} · 15s 刷新</span>
      <n-button size="tiny" text :loading="radarLoading" @click="loadRadar">刷新</n-button>
    </div>
    <n-data-table class="fill-table" flex-height :columns="radarColumns" :data="radarRows"
      size="small" :row-props="rowProps" :loading="radarLoading" :bordered="false" scroll-x="1100" />
    <n-empty v-if="!radarLoading && !radarRows.length" description="关注池暂无实时快照" style="padding:24px" />
  </template>
  <template v-else>
    <n-data-table class="fill-table" flex-height :columns="columns" :data="rows" size="small" :row-props="rowProps" :loading="loading" :bordered="false" @update:sorter="handleSorter" scroll-x="900" />
    <ListPagination :total="total" :page="page" :page-size="50" @change="goPage" />
  </template>
</div>
</template>
<script setup>
import AppIcon from './AppIcon.vue'
import ListPagination from './ListPagination.vue'
import StockSuggestInput from './StockSuggestInput.vue'
import { useViewport } from '../utils/viewport'
const { isNarrow } = useViewport()
import { ref, reactive, h, computed, onMounted, onUnmounted } from 'vue'
import { useNavStore } from '../stores/nav'
import { NCard, NDataTable, NButton, NButtonGroup, NInput, NEmpty, NPagination, NTag, NSpace, NSpin, useMessage } from 'naive-ui'
import axios from 'axios'

const emit = defineEmits(['show-detail'])
const message = useMessage()
const API = window.location.origin
const cat = ref('stock'), kw = ref(''), page = ref(1)
const sortField = ref('trade_date'), sortDir = ref('descend')
const rows = ref([])
const total = ref(0)
const totalPages = ref(1)
const loading = ref(false)

const exName = e => ({SSE:'沪',SZSE:'深',BSE:'京'}[e]||e)

// URL 状态持久化（#/stocks?cat=&kw=&page=&sf=&sd=）：浏览器返回/直达链接都还原
// 页码与排序（sf/sd = sortField/sortDir，naive-ui 的 ascend/descend）
function parseHash() {
  try {
    const q = new URLSearchParams((location.hash.split('?')[1] || ''))
    const c = q.get('cat'); if (c) cat.value = c
    const k = q.get('kw'); if (k) kw.value = k
    const p = parseInt(q.get('page')); if (p && p > 0) page.value = p
    const sf = q.get('sf'); if (sf) sortField.value = sf
    const sd = q.get('sd'); if (sd === 'ascend' || sd === 'descend') sortDir.value = sd
  } catch (e) {}
}
function syncHash() {
  const q = new URLSearchParams()
  q.set('cat', cat.value); q.set('kw', kw.value); q.set('page', page.value)
  q.set('sf', sortField.value); q.set('sd', sortDir.value)
  history.replaceState(null, '', location.pathname + '#/stocks?' + q.toString())
}

// 行点击、链接、tooltip 辅助（render 列 ellipsis 需自控）
const ell = (txt, extra = {}) => {
  const s = String(txt ?? '')
  const { onClick, style: extraStyle, maxWidth, ...rest } = extra
  return h('span', { style: { display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: maxWidth || '100%', ...extraStyle }, title: s, onClick, ...rest }, s || '—')
}

const columns = computed(() => [
  { title:'代码', key:'stock_code', width:82, fixed:'left', render(r){ return h('span', { style:{color:'var(--c-text-dim)',cursor:'pointer',textDecoration:'underline',textDecorationColor:'var(--c-border)',textUnderlineOffset:'2px'}, onClick:()=>emit('show-detail', r.stock_code) }, r.stock_code) } },
  { title:'名称', key:'stock_name', width: isNarrow.value ? 90 : 150, fixed:'left', render(r){ return ell(r.stock_name, { maxWidth:'138px', color:'var(--c-text)', cursor:'pointer', onClick:()=>emit('show-detail', r.stock_code) }) } },
  { title:'交易所', key:'exchange', width:70, render(r){return exName(r.exchange)} },
  { title:'最新价', key:'price', width:105, align:'right', sorter:true, sortOrder: sortField.value==='price'?sortDir.value:false, render(r){
    const color = r.chg_pct!=null ? (r.chg_pct>=0?'var(--c-up)':'var(--c-down)') : 'var(--c-text)'
    return h('span',{style:{fontWeight:600,color,whiteSpace:'nowrap'}}, '¥'+(r.price||0).toFixed(2))
  }},
  { title:'涨跌幅', key:'chg_pct', width:90, align:'right', sorter:true, sortOrder: sortField.value==='chg_pct'?sortDir.value:false, render(r){
    if(r.chg_pct==null) return h('span',{style:{whiteSpace:'nowrap'}},'-')
    const color = r.chg_pct>=0?'var(--c-up)':'var(--c-down)'
    return h('span',{style:{color,fontWeight:500,whiteSpace:'nowrap'}}, (r.chg_pct>=0?'+':'')+r.chg_pct.toFixed(2)+'%')
  }},
  { title:'PE', key:'pe_ttm', width:80, align:'right', sorter:true, sortOrder: sortField.value==='pe_ttm'?sortDir.value:false, render(r){return r.pe_ttm?r.pe_ttm.toFixed(1):'亏损'} },
  { title:'行业', key:'industry', width:160, render(r){ return ell(r.industry, { maxWidth:'148px' }) } },
  { title:'数据日期', key:'trade_date', width:105, align:'center', render(r){return r.trade_date||'-'} },
])

function rowProps(row){
  return { style:'cursor:pointer', onClick:()=>emit('show-detail', row.stock_code) }
}
function handleSorter(sorter){
  const map = {price:'price',chg_pct:'chg_pct',pe_ttm:'pe_ttm',market_cap:'market_cap'}
  const newField = map[sorter?.columnKey] || 'price'
  const newDir = sorter?.order || false
  sortField.value = newField
  sortDir.value = newDir
  page.value = 1
  load()
}

async function load(){
  loading.value = true
  syncHash()
  try{
    const apiDir = sortDir.value === 'ascend' ? 'asc' : 'desc'
    const r = await axios.get(API+'/api/stocks',{params:{page:page.value,page_size:50,keyword:kw.value,category:cat.value,order_by:sortField.value,order_dir:apiDir}})
    rows.value = r.data.stocks||[]
    total.value = r.data.total
    totalPages.value = r.data.total_pages||1
    // 写入详情页导航上下文（上一只/下一只基于当前列表顺序）
    try { localStorage.setItem('detail_nav_list', JSON.stringify(rows.value.map(x => x.stock_code))) } catch (e) {}
  }catch(e){ message.error('列表加载失败，请重试') } finally { loading.value = false }
}
function onSuggestPick(o) {
  kw.value = o.code
  page.value = 1
  load()
}
function doSearch(){ page.value = 1; load() }
function goPage(p){ page.value = p; load() }
function switchCat(c){ cat.value = c; page.value = 1; load() }

// ── 盘中雷达：关注池实时排序 ──
const radarMode = ref(false)
const radarRows = ref([])
const radarLoading = ref(false)
const radarAt = ref('')
let radarTimer = null
const radarPct = v => v == null ? '—' : ((v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%')
const radarScoreLabel = s => s >= 70 ? '强' : s >= 55 ? '偏强' : s > 45 ? '中性' : s > 30 ? '偏弱' : '弱'

async function loadRadar(){
  if(!radarMode.value) return
  radarLoading.value = true
  try{
    const r = await axios.get(API + '/api/stock/radar')
    radarRows.value = r.data?.items || []
    radarAt.value = r.data?.asof || ''
  }catch(e){
    message.error(e.response?.data?.detail || '雷达快照失败')
  }finally{
    radarLoading.value = false
  }
}
function toggleRadar(){
  radarMode.value = !radarMode.value
  clearInterval(radarTimer); radarTimer = null
  if(radarMode.value){
    loadRadar()
    radarTimer = setInterval(loadRadar, 15000)
  }
}

const radarColumns = computed(() => [
  { title:'代码', key:'code', width:82, fixed:'left',
    render:r => h('span', { style:{color:'var(--c-text-dim)', cursor:'pointer'}, onClick:()=>emit('show-detail', r.code) }, r.code) },
  { title:'名称', key:'name', width:140, fixed:'left',
    render:r => ell(r.name, { maxWidth:'128px', color:'var(--c-text)', cursor:'pointer', onClick:()=>emit('show-detail', r.code) }) },
  { title:'行业', key:'industry', width:120, render:r => ell(r.industry, { maxWidth:'110px' }) },
  { title:'最新价', key:'price', width:95, align:'right',
    render:r => h('span',{style:{fontWeight:600,fontVariantNumeric:'tabular-nums'}}, r.price != null ? '¥'+Number(r.price).toFixed(2) : '—') },
  { title:'涨跌幅', key:'change_pct', width:88, align:'right',
    render:r => h('span',{style:{color:r.change_pct>=0?'var(--c-up)':'var(--c-down)',fontVariantNumeric:'tabular-nums'}}, radarPct(r.change_pct)) },
  { title:'跳空', key:'gap_pct', width:80, align:'right',
    render:r => h('span',{style:{color:r.gap_pct>=0?'var(--c-up)':'var(--c-down)'}}, radarPct(r.gap_pct)) },
  { title:'VWAP偏离', key:'vwap_dev_pct', width:96, align:'right',
    render:r => h('span',{style:{color:r.vwap_dev_pct>=0?'var(--c-up)':'var(--c-down)'}}, radarPct(r.vwap_dev_pct)) },
  { title:'日位置', key:'day_position_pct', width:82, align:'right',
    render:r => h('span',{style:{fontVariantNumeric:'tabular-nums'}}, r.day_position_pct != null ? r.day_position_pct.toFixed(0)+'%' : '—') },
  { title:'量比', key:'volume_ratio', width:72, align:'right',
    render:r => h('span',{style:{fontVariantNumeric:'tabular-nums'}}, r.volume_ratio != null ? Number(r.volume_ratio).toFixed(2) : '—') },
  { title:'成交额', key:'amount', width:92, align:'right',
    render:r => h('span',{style:{fontVariantNumeric:'tabular-nums'}}, r.amount != null ? (r.amount/1e8).toFixed(2)+'亿' : '—') },
  { title:'雷达分', key:'score', width:130,
    render:r => h('span',{style:{color:r.score>=60?'var(--c-up)':r.score<=40?'var(--c-down)':'var(--c-text-dim)',fontWeight:600}}, `${r.score.toFixed(1)} ${radarScoreLabel(r.score)}`) },
])

const nav = useNavStore()
function onPopstate() {
  // 组件可能已卸载（v-if 切换）：非本页时不响应，避免用本页 URL 覆盖地址栏
  if (nav.tab !== 'l') return
  parseHash()
  load()
}
onMounted(() => {
  parseHash()
  load()
  window.addEventListener('popstate', onPopstate)
})
onUnmounted(() => {
  window.removeEventListener('popstate', onPopstate)
  if (radarTimer) clearInterval(radarTimer)
})
</script>

<style scoped>
.radar-head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  margin-bottom: 6px; font-size: var(--text-meta); color: var(--c-text-dim);
}
</style>
