<template>
<div>
  <n-space align="center" style="margin-bottom:8px" wrap>
    <n-button-group size="tiny">
      <n-button :type="cat==='stock'?'primary':'default'" @click="cat='stock';page=1;load()">📌 个股</n-button>
      <n-button :type="cat==='index'?'primary':'default'" @click="cat='index';page=1;load()">📊 指数</n-button>
      <n-button :type="cat==='etf'?'primary':'default'" @click="cat='etf';page=1;load()">💹 ETF</n-button>
    </n-button-group>
    <n-input v-model:value="kw" placeholder="搜索代码或名称..." size="small" style="width:160px" clearable @keyup.enter="page=1;load()" />
    <n-button type="primary" size="tiny" :loading="loading" @click="page=1;load()">搜索</n-button>
    <span style="font-size:11px;color:var(--c-text-dim)">共 {{total}} 条 第 {{page}}/{{totalPages}} 页</span>
  </n-space>

  <n-spin v-if="loading" style="padding:40px" />
  <div v-else>
    <div style="overflow-x:auto">
      <n-data-table :columns="columns" :data="rows" size="small" :row-props="rowProps" @update:sorter="handleSorter" scroll-x="720" />
    </div>
    <div style="display:flex;justify-content:center;margin-top:10px">
      <n-pagination v-if="totalPages>1" :page="page" :page-count="totalPages" @update:page="p=>{page=p;load()}" size="small" />
    </div>
  </div>
</div>
</template>
<script setup>
import { ref, reactive, h, computed } from 'vue'
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

const columns = computed(() => [
  { title:'代码', key:'stock_code', width:80 },
  { title:'名称', key:'stock_name', minWidth:100, ellipsis:{tooltip:true} },
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
  { title:'行业', key:'industry', minWidth:80, ellipsis:{tooltip:true} },
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
  try{
    const apiDir = sortDir.value === 'ascend' ? 'asc' : 'desc'
    const r = await axios.get(API+'/api/stocks',{params:{page:page.value,page_size:20,keyword:kw.value,category:cat.value,order_by:sortField.value,order_dir:apiDir}})
    rows.value = r.data.stocks||[]
    total.value = r.data.total
    totalPages.value = r.data.total_pages||1
  }catch(e){} finally { loading.value = false }
}
load()
</script>
