<template>
<div class="page-fill">
  <n-space align="center" style="margin-bottom:8px;flex-shrink:0" wrap>
    <n-button-group size="tiny">
      <n-button size="tiny" :type="cat==='stock'?'primary':'default'" @click="switchCat('stock')"><AppIcon name="filter" :size="13" />  个股</n-button>
      <n-button size="tiny" :type="cat==='index'?'primary':'default'" @click="switchCat('index')"><AppIcon name="bar-chart-2" :size="13" />  指数</n-button>
      <n-button size="tiny" :type="cat==='etf'?'primary':'default'" @click="switchCat('etf')"><AppIcon name="pie-chart" :size="13" />  ETF</n-button>
    </n-button-group>
    <StockSuggestInput v-model:value="kw" size="tiny" width="180px" placeholder="搜索代码或名称..." @select="onSuggestPick" @enter="doSearch" />
    <n-button type="primary" size="tiny" :loading="loading" @click="doSearch">搜索</n-button>
  </n-space>

  <n-data-table class="fill-table" flex-height :columns="columns" :data="rows" size="small" :row-props="rowProps" :loading="loading" :bordered="false" @update:sorter="handleSorter" scroll-x="900" />
  <ListPagination :total="total" :page="page" :page-size="50" @change="goPage" />
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
import { NCard, NDataTable, NButton, NButtonGroup, NInput, NPagination, NTag, NSpace, NSpin } from 'naive-ui'
import axios from 'axios'

const emit = defineEmits(['show-detail'])
const API = window.location.origin
const cat = ref('stock'), kw = ref(''), page = ref(1)
const sortField = ref('trade_date'), sortDir = ref('descend')
const rows = ref([])
const total = ref(0)
const totalPages = ref(1)
const loading = ref(false)

const exName = e => ({SSE:'沪',SZSE:'深',BSE:'京'}[e]||e)

// URL 状态持久化（#/stocks?cat=&kw=&page=）
function parseHash() {
  try {
    const q = new URLSearchParams((location.hash.split('?')[1] || ''))
    const c = q.get('cat'); if (c) cat.value = c
    const k = q.get('kw'); if (k) kw.value = k
    const p = parseInt(q.get('page')); if (p && p > 0) page.value = p
  } catch (e) {}
}
function syncHash() {
  const q = new URLSearchParams()
  q.set('cat', cat.value); q.set('kw', kw.value); q.set('page', page.value)
  history.replaceState(null, '', location.pathname + '#/stocks?' + q.toString())
}

// 行点击、链接、tooltip 辅助（render 列 ellipsis 需自控）
const ell = (txt, extra = {}) => {
  const s = String(txt ?? '')
  const { onClick, style: extraStyle, maxWidth, ...rest } = extra
  return h('span', { style: { display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: maxWidth || '100%', ...extraStyle }, title: s, onClick, ...rest }, s || '—')
}

const columns = computed(() => [
  { title:'代码', key:'stock_code', width:82, fixed:'left', render(r){ return h('span', { style:{color:'var(--c-info)',cursor:'pointer',textDecoration:'underline'}, onClick:()=>emit('show-detail', r.stock_code) }, r.stock_code) } },
  { title:'名称', key:'stock_name', width: isNarrow.value ? 90 : 150, fixed:'left', render(r){ return ell(r.stock_name, { maxWidth:'138px', color:'var(--c-info)', cursor:'pointer', onClick:()=>emit('show-detail', r.stock_code) }) } },
  { title:'交易所', key:'exchange', width:70, render(r){return exName(r.exchange)} },
  { title:'最新价', key:'price', width:105, align:'right', sorter:true, sortOrder: sortField.value==='price'?sortDir.value:false, render(r){
    const color = r.chg_pct!=null ? (r.chg_pct>=0?'#ef4444':'#10b981') : 'var(--c-text)'
    return h('span',{style:{fontWeight:600,color,whiteSpace:'nowrap'}}, '¥'+(r.price||0).toFixed(2))
  }},
  { title:'涨跌幅', key:'chg_pct', width:90, align:'right', sorter:true, sortOrder: sortField.value==='chg_pct'?sortDir.value:false, render(r){
    if(r.chg_pct==null) return h('span',{style:{whiteSpace:'nowrap'}},'-')
    const color = r.chg_pct>=0?'#ef4444':'#10b981'
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
onUnmounted(() => window.removeEventListener('popstate', onPopstate))
</script>
