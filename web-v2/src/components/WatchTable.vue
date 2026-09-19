<template>
<div style="display:flex;flex-direction:column;flex:1;min-height:0">
  <n-space align="center" style="margin-bottom:8px;flex-shrink:0" wrap>
    <StockSuggestInput v-model:value="kw" size="tiny" width="180px" placeholder="搜索代码或名称..." @select="doSearch" @enter="doSearch" />
    <n-button type="primary" size="tiny" :loading="loading" @click="doSearch">搜索</n-button>
    <span style="font-size:11px;color:var(--c-text-dim)">{{ total }} 只</span>
  </n-space>

  <n-data-table class="fill-table" flex-height :columns="columns" :data="rows" size="small" :row-props="rowProps" :loading="loading" :bordered="false" @update:sorter="handleSorter" scroll-x="900" />
  <ListPagination :total="total" :page="page" :page-size="50" @change="goPage" />
</div>
</template>
<script setup>
import ListPagination from './ListPagination.vue'
import StockSuggestInput from './StockSuggestInput.vue'
import { useViewport } from '../utils/viewport'
const { isNarrow } = useViewport()
import { ref, h, computed, watch } from 'vue'
import { NDataTable, NButton, NSpace, useMessage } from 'naive-ui'
import axios from 'axios'

const props = defineProps({ groupId: { type: Number, required: true } })
const emit = defineEmits(['show-detail'])
const message = useMessage()
const API = window.location.origin

const kw = ref(''), page = ref(1)
const sortField = ref('trade_date'), sortDir = ref('descend')
const rows = ref([]), total = ref(0), loading = ref(false)

const exName = e => ({SSE:'沪',SZSE:'深',BSE:'京'}[e]||e)
const ell = (txt, extra = {}) => {
  const s = String(txt ?? '')
  const { onClick, style: extraStyle, maxWidth, ...rest } = extra
  return h('span', { style: { display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: maxWidth || '100%', ...extraStyle }, title: s, onClick, ...rest }, s || '—')
}

// 列定义与个股列表页（StocksView）一致：代码/名称/交易所/最新价/涨跌幅/PE/行业/数据日期
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
  { title:'加入时间', key:'added_at', width:120, align:'center', render(r){return r.added_at||'-'} },
])

function rowProps(row){ return { style:'cursor:pointer', onClick:()=>emit('show-detail', row.stock_code) } }
function handleSorter(sorter){
  const map = {price:'price',chg_pct:'chg_pct',pe_ttm:'pe_ttm'}
  sortField.value = map[sorter?.columnKey] || 'trade_date'
  sortDir.value = sorter?.order || false
  page.value = 1
  load()
}

async function load(){
  loading.value = true
  try{
    const apiDir = sortDir.value === 'ascend' ? 'asc' : 'desc'
    const r = await axios.get(API+`/api/watch/groups/${props.groupId}/stocks`,
      { params: { page: page.value, page_size: 50, keyword: kw.value, order_by: sortField.value, order_dir: apiDir } })
    rows.value = r.data.stocks||[]
    total.value = r.data.total||0
  }catch(e){ message.error('分组列表加载失败') } finally { loading.value = false }
}
function doSearch(){ page.value = 1; load() }
function goPage(p){ page.value = p; load() }

// 切组重置分页与搜索
watch(() => props.groupId, () => { page.value = 1; kw.value = ''; load() })
load()
</script>
