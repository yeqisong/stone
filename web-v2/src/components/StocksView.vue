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
    <n-button size="tiny" @click="openBuyCheck()"><AppIcon name="target" :size="13" />  买点检查</n-button>
  </n-space>

  <template v-if="radarMode">
    <div class="radar-head">
      <span>关注池实时排序 · {{ radarAt || '—' }} · 15s 刷新<template v-if="holdingSignals.length"> · 持仓信号 {{ holdingSignals.length }} 条</template></span>
      <n-button size="tiny" text :loading="radarLoading" @click="loadRadar">刷新</n-button>
    </div>
    <!-- 持仓动作：只对持仓；系统在仓位高于目标时不给买入信号 -->
    <div v-if="holdingSignals.length" class="radar-signals">
      <div class="rs-title">
        <span>持仓信号</span>
        <span class="rs-note">把日内涨跌翻译成动作：减仓 / 止损 / 持有；买点仅观察，不开新仓</span>
      </div>
      <div v-for="s in holdingSignals" :key="s.code" class="rs-row" :class="'rs-' + s.signal.level"
        @click="openDetail(s.code, radarRows)">
        <span class="rs-name">{{ s.name }}</span>
        <span class="rs-chg" :style="{color:(s.change_pct||0)>=0?'var(--c-up)':'var(--c-down)'}">{{ radarPct(s.change_pct) }}</span>
        <span class="rs-cost">
          成本 ¥{{ Number(s.cost||0).toFixed(2) }} ·
          <b :style="{color:(s.vs_cost_pct||0)>=0?'var(--c-up)':'var(--c-down)'}">{{ radarPct((s.vs_cost_pct||0)*100) }}</b>
        </span>
        <span class="rs-weight">仓位 {{ ((s.weight||0)*100).toFixed(1) }}%</span>
        <span class="rs-tag" :style="{background: signalMeta(s.signal.action).color}">{{ signalMeta(s.signal.action).label }}</span>
        <span class="rs-reason" :title="s.signal.reason">{{ s.signal.reason }}</span>
      </div>
    </div>
    <!-- 买点候选：只在“仓位/纪律允许开新仓 + 市场闸门通过”时出现 -->
    <div v-if="buyCandidates.allowed && buyCandidates.items.length" class="buy-candidates">
      <div class="bc-title">
        <span>买点候选</span>
        <span class="bc-note">市场/板块/结构/风险四道闸门全过；仅候选，下单前再检查</span>
        <n-button size="tiny" text :loading="buyLoading" @click="loadBuyCandidates">刷新</n-button>
      </div>
      <div v-for="c in buyCandidates.items" :key="c.code" class="bc-row" @click="openBuyCheck(c)"
        :title="c.reason">
        <span class="bc-name">{{ c.name }}</span>
        <span class="bc-industry">{{ c.industry }}</span>
        <span class="bc-setup">{{ c.setup?.label }}</span>
        <span class="bc-plan">买 {{ fmtPrice(c.price) }} / 止 {{ fmtPrice(c.setup?.stop) }} / 目标 {{ fmtPrice(c.setup?.target) }}</span>
        <span class="bc-rr">盈亏比 {{ Number(c.setup?.rr || 0).toFixed(1) }}</span>
        <span class="bc-qty">最多 {{ c.max_qty }} 股</span>
      </div>
    </div>
    <n-data-table class="fill-table" flex-height :columns="radarColumns" :data="radarRows"
      size="small" :row-props="radarRowProps" :loading="radarLoading" :bordered="false" scroll-x="1100" />
    <n-empty v-if="!radarLoading && !radarRows.length" description="关注池暂无实时快照" style="padding:24px" />
  </template>
  <template v-else>
    <n-data-table class="fill-table" flex-height :columns="columns" :data="rows" size="small" :row-props="rowProps" :loading="loading" :bordered="false" @update:sorter="handleSorter" scroll-x="900" />
    <ListPagination :total="total" :page="page" :page-size="50" @change="goPage" />
  </template>
  <BuyCheck v-model:show="buyCheckShow" :preset="buyPreset" />
</div>
</template>
<script setup>
import AppIcon from './AppIcon.vue'
import ListPagination from './ListPagination.vue'
import StockSuggestInput from './StockSuggestInput.vue'
import BuyCheck from './BuyCheck.vue'
import { useViewport } from '../utils/viewport'
import { rememberDetailNavList } from '../utils/detailNav'
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
    // tab=radar 是独立视图；普通个股列表不带该参数（返回/前进时也要能复位）
    radarMode.value = q.get('tab') === 'radar'
  } catch (e) {}
}
function syncHash() {
  const q = new URLSearchParams()
  q.set('cat', cat.value); q.set('kw', kw.value); q.set('page', page.value)
  q.set('sf', sortField.value); q.set('sd', sortDir.value)
  if (radarMode.value) q.set('tab', 'radar')
  history.replaceState(null, '', location.pathname + '#/stocks?' + q.toString())
}

// 行点击、链接、tooltip 辅助（render 列 ellipsis 需自控）
const ell = (txt, extra = {}) => {
  const s = String(txt ?? '')
  const { onClick, style: extraStyle, maxWidth, ...rest } = extra
  return h('span', { style: { display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: maxWidth || '100%', ...extraStyle }, title: s, onClick, ...rest }, s || '—')
}

const columns = computed(() => [
  { title:'代码', key:'stock_code', width:82, fixed:'left', render(r){ return h('span', { style:{color:'var(--c-text-dim)',cursor:'pointer',textDecoration:'underline',textDecorationColor:'var(--c-border)',textUnderlineOffset:'2px'}, onClick:()=>openDetail(r.stock_code, rows.value) }, r.stock_code) } },
  { title:'名称', key:'stock_name', width: isNarrow.value ? 90 : 150, fixed:'left', render(r){ return ell(r.stock_name, { maxWidth:'138px', color:'var(--c-text)', cursor:'pointer', onClick:()=>openDetail(r.stock_code, rows.value) }) } },
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

function openDetail(code, list){
  if (!code) return
  rememberDetailNavList((list || []).map(r => r.stock_code || r.code))
  emit('show-detail', code)
}
function rowProps(row){
  return { style:'cursor:pointer', onClick:()=>openDetail(row.stock_code, rows.value) }
}
function radarRowProps(row){
  return { style:'cursor:pointer', onClick:()=>openDetail(row.code, radarRows.value) }
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
  exitRadar()
  kw.value = o.code
  page.value = 1
  load()
}
function doSearch(){ exitRadar(); page.value = 1; load() }
function goPage(p){ page.value = p; load() }
function switchCat(c){ exitRadar(); cat.value = c; page.value = 1; load() }

// ── 盘中雷达：关注池实时排序 ──
const radarMode = ref(false)
const radarRows = ref([])
const radarLoading = ref(false)
const radarAt = ref('')
let radarTimer = null
const buyCandidates = ref({ allowed: false, items: [] })
const buyLoading = ref(false)
const buyCheckShow = ref(false)
const buyPreset = ref(null)
let buyTimer = null
const fmtPrice = v => v == null ? '—' : Number(v).toFixed(2)
const radarPct = v => v == null ? '—' : ((v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%')
const radarScoreLabel = s => s >= 70 ? '强' : s >= 55 ? '偏强' : s > 45 ? '中性' : s > 30 ? '偏弱' : '弱'
const SIGNAL_META = {
  stop:         { label:'止损',     color:'var(--c-error)' },
  reduce:       { label:'减仓',     color:'var(--c-error)' },
  trim:         { label:'超配减仓', color:'var(--c-warning)' },
  reduce_watch: { label:'冲高不追', color:'var(--c-warning)' },
  watch_stop:   { label:'减仓观察', color:'var(--c-warning)' },
  hold:         { label:'持有',     color:'var(--c-info)' },
}
const SIGNAL_ORDER = { stop:0, reduce:1, trim:2, reduce_watch:3, watch_stop:4, hold:5 }
const signalMeta = a => SIGNAL_META[a] || { label:a || '—', color:'var(--c-text-dim)' }
const holdingSignals = computed(() => (radarRows.value || [])
  .filter(r => r.is_holding && r.signal)
  .sort((a, b) => (SIGNAL_ORDER[a.signal.action] ?? 9) - (SIGNAL_ORDER[b.signal.action] ?? 9)))

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
async function loadBuyCandidates(){
  if(!radarMode.value) return
  buyLoading.value = true
  try{
    const r = await axios.get(API + '/api/buy/candidates', { timeout: 30000 })
    buyCandidates.value = r.data || { allowed: false, items: [] }
  }catch(e){
    buyCandidates.value = { allowed: false, items: [], reason: e.response?.data?.detail || '候选加载失败' }
  }finally{
    buyLoading.value = false
  }
}
function openBuyCheck(c){
  buyPreset.value = c ? {
    code: c.code,
    entry: c.price ?? c.setup?.entry,
    stop: c.setup?.stop,
    target: c.setup?.target,
  } : null
  buyCheckShow.value = true
}
function startRadar(){
  clearInterval(radarTimer); radarTimer = null
  clearInterval(buyTimer); buyTimer = null
  loadRadar()
  loadBuyCandidates()
  radarTimer = setInterval(loadRadar, 15000)
  buyTimer = setInterval(loadBuyCandidates, 60000)
}
function stopRadar(){
  clearInterval(radarTimer); radarTimer = null
  clearInterval(buyTimer); buyTimer = null
}
function exitRadar(){
  if(!radarMode.value) return
  radarMode.value = false
  stopRadar()
}
function toggleRadar(){
  radarMode.value = !radarMode.value
  syncHash()                       // tab=radar 写回地址栏，刷新/直达可还原
  if(radarMode.value){
    startRadar()
  }else{
    stopRadar()
    load()
  }
}

const radarColumns = computed(() => [
  { title:'代码', key:'code', width:82, fixed:'left',
    render:r => h('span', { style:{color:'var(--c-text-dim)', cursor:'pointer'}, onClick:()=>openDetail(r.code, radarRows.value) }, r.code) },
  { title:'名称', key:'name', width:140, fixed:'left',
    render:r => ell(r.name, { maxWidth:'128px', color:'var(--c-text)', cursor:'pointer', onClick:()=>openDetail(r.code, radarRows.value) }) },
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
  { title:'持仓信号', key:'signal', width:88, align:'center',
    render:r => {
      if (!r.is_holding) return h('span',{style:{color:'var(--c-text-faint)'}},'—')
      if (!r.signal) return h('span',{style:{color:'var(--c-text-faint)'}},'—')
      const m = signalMeta(r.signal.action)
      return h('span',{title:r.signal.reason, style:{color:m.color,fontWeight:600}}, m.label)
    } },
  { title:'雷达分', key:'score', width:130,
    render:r => h('span',{style:{color:r.score>=60?'var(--c-up)':r.score<=40?'var(--c-down)':'var(--c-text-dim)',fontWeight:600}}, `${r.score.toFixed(1)} ${radarScoreLabel(r.score)}`) },
])

const nav = useNavStore()
function applyRoute(){
  parseHash()
  if (radarMode.value) {
    startRadar()
  } else {
    stopRadar()
    load()
  }
}
function onPopstate() {
  // 组件可能已卸载（v-if 切换）：非本页时不响应，避免用本页 URL 覆盖地址栏
  if (nav.tab !== 'l') return
  applyRoute()
}
onMounted(() => {
  applyRoute()
  window.addEventListener('popstate', onPopstate)
})
onUnmounted(() => {
  window.removeEventListener('popstate', onPopstate)
  stopRadar()
})
</script>

<style scoped>
.radar-head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  margin-bottom: 6px; font-size: var(--text-meta); color: var(--c-text-dim);
}
/* 持仓信号：把日内涨跌翻译成动作 */
.radar-signals {
  margin-bottom: 6px; padding: 6px 9px; flex-shrink: 0;
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-card-bg);
}
.rs-title { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; font-size: 12px; font-weight: 600; }
.rs-note { font-size: var(--text-meta); color: var(--c-text-faint); font-weight: 400; }
.rs-row {
  display: grid; grid-template-columns: 96px 62px 170px 84px 72px minmax(0, 1fr);
  gap: 8px; align-items: center; padding: 4px 2px;
  font-size: var(--text-meta); cursor: pointer; border-top: 1px dashed var(--c-border);
}
.rs-title + .rs-row { border-top: none; }
.rs-row:hover { background: var(--c-card-bg-hover); }
.rs-row.rs-critical { border-left: 2px solid var(--c-error); padding-left: 6px; }
.rs-row.rs-warn { border-left: 2px solid var(--c-warning); padding-left: 6px; }
.rs-row.rs-ok { border-left: 2px solid var(--c-info); padding-left: 6px; }
.rs-name { font-weight: 600; color: var(--c-text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rs-chg, .rs-cost, .rs-weight { font-variant-numeric: tabular-nums; white-space: nowrap; }
.rs-tag { font-size: 10px; padding: 1px 5px; border-radius: 3px; color: #fff; text-align: center; white-space: nowrap; }
.rs-reason { color: var(--c-text-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 买点候选：只在仓位/纪律允许且市场闸门通过时出现 */
.buy-candidates {
  margin-bottom: 6px; padding: 6px 9px; flex-shrink: 0;
  border: 1px solid var(--c-border); border-left: 3px solid var(--c-info);
  border-radius: var(--radius-sm); background: var(--c-card-bg);
}
.bc-title { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; font-size: 12px; font-weight: 600; }
.bc-note { flex: 1; min-width: 0; font-size: var(--text-meta); color: var(--c-text-faint); font-weight: 400; }
.bc-row {
  display: grid; grid-template-columns: 96px 72px 72px minmax(0, 1fr) 76px 96px;
  gap: 8px; align-items: center; padding: 4px 2px;
  font-size: var(--text-meta); cursor: pointer; border-top: 1px dashed var(--c-border);
}
.bc-title + .bc-row { border-top: none; }
.bc-row:hover { background: var(--c-card-bg-hover); }
.bc-name { font-weight: 600; color: var(--c-text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bc-industry, .bc-plan, .bc-rr, .bc-qty {
  color: var(--c-text-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  font-variant-numeric: tabular-nums;
}
.bc-setup { color: var(--c-info); font-weight: 600; white-space: nowrap; }
@media (max-width: 900px) {
  .bc-row { grid-template-columns: 84px 1fr 72px; row-gap: 2px; }
  .bc-industry, .bc-rr { display: none; }
  .bc-plan { grid-column: 1 / -1; white-space: normal; }
  .bc-qty { grid-column: 3; grid-row: 1; text-align: right; }
}
@media (max-width: 900px) {
  .rs-row { grid-template-columns: 84px 56px 1fr; row-gap: 2px; }
  .rs-cost, .rs-weight { display: none; }
  .rs-tag { justify-self: start; }
  .rs-reason { grid-column: 1 / -1; white-space: normal; }
}
</style>
