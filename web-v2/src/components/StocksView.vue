<template>
<div style="height:100%;display:flex;flex-direction:column;overflow:hidden">
  <n-space align="center" style="margin-bottom:8px;flex-shrink:0" wrap>
    <n-button-group size="tiny">
      <n-button :type="cat==='stock'?'primary':'default'" @click="switchCat('stock')">📌 个股</n-button>
      <n-button :type="cat==='index'?'primary':'default'" @click="switchCat('index')">📊 指数</n-button>
      <n-button :type="cat==='etf'?'primary':'default'" @click="switchCat('etf')">💹 ETF</n-button>
    </n-button-group>
    <n-input v-model:value="kw" placeholder="搜索代码或名称..." size="tiny" style="width:160px" clearable @keyup.enter="doSearch" />
    <n-button type="primary" size="tiny" :loading="loading" @click="doSearch">搜索</n-button>
    <span style="font-size:11px;color:var(--c-text-dim)">共 {{total}} 条 第 {{page}}/{{totalPages}} 页</span>
  </n-space>

  <div style="flex:1;min-height:0;overflow:hidden">
    <n-data-table :columns="columns" :data="rows" size="small" :row-props="rowProps" :loading="loading" :bordered="false" @update:sorter="handleSorter" scroll-x="900" />
  </div>
  <div style="display:flex;justify-content:center;margin-top:10px;flex-shrink:0">
    <n-pagination v-if="totalPages>1" :page="page" :page-count="totalPages" @update:page="p=>goPage(p)" size="small" />
  </div>
</div>
</template>
<script setup>
import { ref, reactive, h, computed, onMounted } from 'vue'
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
  { title:'代码', key:'stock_code', width:82, render(r){ return h('span', { style:{color:'#2080f0',cursor:'pointer',textDecoration:'underline'}, onClick:()=>emit('show-detail', r.stock_code) }, r.stock_code) } },
  { title:'名称', key:'stock_name', width:150, render(r){ return ell(r.stock_name, { maxWidth:'138px', color:'#2080f0', cursor:'pointer', onClick:()=>emit('show-detail', r.stock_code) }) } },
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
  { title:'PE', key:'pe_ttm', width:80, align:'right', sorter:true, sortOrder: sortField.value==='pe_ttm'?sortDir.value:false, render(r){return r.pe_ttm?r.pe_ttm.toFixed(1):'-'} },
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
  }catch(e){} finally { loading.value = false }
}
function doSearch(){ page.value = 1; load() }
function goPage(p){ page.value = p; load() }
function switchCat(c){ cat.value = c; page.value = 1; load() }

onMounted(() => {
  parseHash()
  load()
})
window.addEventListener('popstate', () => { parseHash(); load() })
</script>
