<template>
<div class="page-fill">
  <!-- 筛选区：主备模型下拉 + 三 Tab（带 icon） + 生成按钮 -->
  <n-space align="center" class="no-shrink" style="margin-bottom:10px" wrap>
    <n-select v-model:value="selModel" :options="modelOptions" size="tiny" style="width:220px"
      placeholder="选择上线中的模型" @update:value="onModelChange" />
    <n-button-group size="tiny">
      <n-button size="tiny" :type="tab==='list'?'primary':'default'" @click="tab='list'"><AppIcon name="list" :size="13" />  信号列表</n-button>
      <n-button size="tiny" :type="tab==='trades'?'primary':'default'" @click="tab='trades'; loadTrades()"><AppIcon name="bar-chart-2" :size="13" />  模拟交易</n-button>
      <n-button size="tiny" :type="tab==='stats'?'primary':'default'" @click="tab='stats'"><AppIcon name="trending-up" :size="13" />  效果追踪</n-button>
    </n-button-group>
    <n-button size="tiny" type="warning" :disabled="!selModel" @click="showGenModal=true"><AppIcon name="zap" :size="13" />  生成</n-button>
  </n-space>

  <!-- Tab1: 信号列表（该模型全部信号，按日期倒序分页） -->
  <template v-if="tab==='list'">
    <div class="fill-table" style="display:flex;flex-direction:column">
      <n-data-table v-if="sig.signals" class="fill-table" flex-height :columns="sigColumns" :data="sig.signals" size="small" :scroll-x="960" />
      <n-empty v-else description="暂无信号" style="flex:1" />
      <ListPagination :total="sig.total" :page="sigPage" :page-size="sigPageSize" @change="p=>{sigPage=p; loadSignals()}" />
    </div>
  </template>

  <!-- Tab2: 模拟交易（该模型全部买卖操作，按交易日期倒序分页） -->
  <template v-else-if="tab==='trades'">
    <div class="fill-table" style="display:flex;flex-direction:column">
      <n-data-table v-if="trd.trades" class="fill-table" flex-height :columns="trdColumns" :data="trd.trades" size="small" :scroll-x="1180" />
      <n-empty v-else description="暂无模拟成交" style="flex:1" />
      <ListPagination :total="trd.total" :page="trdPage" :page-size="trdPageSize" @change="p=>{trdPage=p; loadTrades()}" />
    </div>
  </template>

  <!-- Tab3: 效果追踪（跟随下拉选中的模型） -->
  <SignalStatsView v-else-if="tab==='stats'" :version="selModel" />

  <n-modal v-model:show="showGenModal" preset="card" title="重新生成当日数据" style="width:420px;max-width:85vw" :mask-closable="false">
    <div style="font-size:13px;color:var(--c-text);margin-bottom:8px">
      模型 <b>{{selModel}}</b> 将以今天（{{today}}）为业务日期，重新生成：
    </div>
    <div style="font-size:11px;color:var(--c-text-dim);margin-bottom:16px;line-height:1.8">
      ① 信号生产（全市场扫描，覆盖当日已有信号）<br />
      ② 模拟交易（清当日账本后按该模型交易规则重步进）<br />
      ③ 健康检查
    </div>
    <div style="display:flex;gap:8px;justify-content:flex-end">
      <n-button size="small" @click="showGenModal=false">取消</n-button>
      <n-button size="small" type="primary" :loading="genLoading" @click="doGenerate">开始重算</n-button>
    </div>
  </n-modal>
</div>
</template>
<script setup>
import AppIcon from './AppIcon.vue'
import { ref, reactive, h, computed, onMounted } from 'vue'
import { NDataTable, NButton, NButtonGroup, NSpace, NTag, NEmpty, NModal, NPagination, NSelect, useMessage } from 'naive-ui'
import axios from 'axios'
import SignalStatsView from './SignalStatsView.vue'
import ListPagination from './ListPagination.vue'
import { useViewport } from '../utils/viewport'
import { bjDateStr } from '../utils/date.js'
const emit = defineEmits(['show-detail'])
const API = window.location.origin
const message = useMessage()
const today = bjDateStr()
const { isNarrow } = useViewport()

const selModel = ref(null)
const modelOptions = ref([])
const tab = ref('list')

// ── Tab1 信号列表（服务端分页，日期倒序） ──
const sig = reactive({signals:null, total:null})
const sigPage = ref(1)
const sigPageSize = 50
// 预测列渲染格式由该模型 label_transform 决定（rank=分位 P52 / top20=概率 31%↑）
const predictFmt = reactive({rank:false, prob:false})

function predRender(key) {
  return (r) => {
    const v = r[key]
    if (v == null) return '—'
    if (predictFmt.prob) return h('span',{style:{color:'var(--c-text-dim)',fontSize:'11px'}},(v*100).toFixed(0)+'%↑')
    if (predictFmt.rank) return h('span',{style:{color:'var(--c-text-dim)',fontSize:'11px'}},'P'+(v*100).toFixed(0))
    return h('span',{style:{color:v>=0?'#ef4444':'#10b981',fontSize:'11px'}},(v>=0?'+':'')+(v*100).toFixed(1)+'%')
  }
}
const sigColumns = computed(() => {
  const base = [
    { title:'信号日期', key:'signal_date', width:88, render(r){ return h('span',{style:{color:'var(--c-text-dim)'}}, (r.signal_date||'').slice(5)) } },
    { title:'代码', key:'stock_code', width:85, fixed:'left', render(r){return h('span',{style:{color:'var(--n-color-target)',cursor:'pointer'},onClick:()=>emit('show-detail',r.stock_code)},r.stock_code)} },
    { title:'名称', key:'stock_name', width:100, fixed:'left', render(r){return h('span',{style:{cursor:'pointer'},onClick:()=>emit('show-detail',r.stock_code)},r.stock_name)} },
    { title:'现价', key:'real_close', width:95, align:'right', render(r){ return r.real_close!=null ? '¥'+r.real_close.toFixed(2) : '—' } },
    { title:'方向', key:'direction', width:55, render(){return h(NTag,{type:'error',size:'small',bordered:false},{default:()=>'买'})} },
    { title:'预测5d', key:'predict_5d', width:70, align:'right', render: predRender('predict_5d') },
  ]
  if (isNarrow.value) return base
  return [
    ...base,
    { title:'预测10d', key:'predict_10d', width:70, align:'right', render: predRender('predict_10d') },
    { title:'预测20d', key:'predict_20d', width:70, align:'right', render: predRender('predict_20d') },
  ]
})

async function loadSignals() {
  if (!selModel.value) return
  try {
    const r = await axios.get(API + `/api/v1/models/${selModel.value}/signals`, { params: { page: sigPage.value, page_size: sigPageSize } })
    Object.assign(sig, r.data)
  } catch(e) { console.error(e) }
}

// ── Tab2 模拟交易 ──
const trd = reactive({trades:null, total:null, initial_cash:1000000})
const trdPage = ref(1)
const trdPageSize = 50
const REASON_CN = { signal: '信号买入', stop_loss: '止损', take_profit: '止盈', trailing: '移动止盈',
                    hold_expire: '持有到期', expire: '持有到期', regime: '空仓闸门' }
const fmtAmt = v => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(0)
const fmtPctCol = v => v == null ? '—' : (v >= 0 ? '+' : '') + (v * 100).toFixed(2) + '%'
const trdColumns = [
  { title:'日期', key:'date', width:78, render(r){ return h('span',{style:{color:'var(--c-text-dim)'}}, (r.date||'').slice(5)) } },
  { title:'代码', key:'stock_code', width:82, fixed:'left', render(r){return h('span',{style:{color:'var(--n-color-target)',cursor:'pointer'},onClick:()=>emit('show-detail',r.stock_code)},r.stock_code)} },
  { title:'名称', key:'stock_name', width:92, fixed:'left' },
  { title:'操作', key:'action', width:58, render(r){ return h('span',{style:{color:r.action==='BUY'?'#ef4444':'#10b981',fontWeight:600}}, r.action==='BUY'?'买入':'卖出') } },
  { title:'股数', key:'shares', width:64, align:'right', render(r){ return (r.shares||0).toLocaleString() } },
  { title:'成交单价', key:'price', width:80, align:'right', render(r){ return r.price!=null ? r.price.toFixed(2) : '—' } },
  { title:'操作前仓位', key:'pre_npos', width:70, align:'right', render(r){ return r.pre_npos==null?'—':r.pre_npos } },
  { title:'操作前总资产', key:'pre_equity', width:96, align:'right', render(r){ return r.pre_equity==null?'—':r.pre_equity.toLocaleString(undefined,{maximumFractionDigits:0}) } },
  { title:'操作后仓位', key:'post_npos', width:70, align:'right', render(r){ return r.post_npos==null?'—':r.post_npos } },
  { title:'操作后总资产', key:'post_equity', width:96, align:'right', render(r){ return r.post_equity==null?'—':r.post_equity.toLocaleString(undefined,{maximumFractionDigits:0}) } },
  { title:'了结盈亏额', key:'pnl', width:84, align:'right', render(r){ const v=r.pnl; if(v==null) return h('span',{style:{color:'var(--c-text-faint)'}},'—'); return h('span',{style:{color:v>=0?'#ef4444':'#10b981'}}, fmtAmt(v)) } },
  { title:'了结盈亏率', key:'pnl_pct', width:78, align:'right', render(r){ const p=r.pnl, s=r.shares, pr=r.price; if(p==null||!s||!pr) return h('span',{style:{color:'var(--c-text-faint)'}},'—'); const v=p/(pr*s); return h('span',{style:{color:v>=0?'#ef4444':'#10b981'}}, fmtPctCol(v)) } },
  { title:'浮动盈亏', key:'float_pnl', width:84, align:'right', render(r){ const v=r.float_pnl; if(v==null) return h('span',{style:{color:'var(--c-text-faint)'}},'—'); return h('span',{style:{color:v>=0?'#ef4444':'#10b981'}}, fmtAmt(v)) } },
  { title:'了结后总资产盈亏额', key:'tot_pnl_amt', width:110, align:'right', render(r){ const e=r.post_equity; if(e==null) return h('span',{style:{color:'var(--c-text-faint)'}},'—'); const v=e-trd.initial_cash; return h('span',{style:{color:v>=0?'#ef4444':'#10b981'}}, fmtAmt(v)) } },
  { title:'了结后总资产盈亏率', key:'tot_pnl_pct', width:110, align:'right', render(r){ const e=r.post_equity; if(e==null||!trd.initial_cash) return h('span',{style:{color:'var(--c-text-faint)'}},'—'); const v=e/trd.initial_cash-1; return h('span',{style:{color:v>=0?'#ef4444':'#10b981'}}, fmtPctCol(v)) } },
  { title:'原因', key:'reason', width:74, render(r){ return h('span',{style:{fontSize:'10px',color:'var(--c-text-dim)'}}, REASON_CN[r.reason]||r.reason||'—') } },
]

async function loadTrades() {
  if (!selModel.value) return
  try {
    const r = await axios.get(API + `/api/v1/models/${selModel.value}/paper-trades`, { params: { page: trdPage.value, page_size: trdPageSize } })
    Object.assign(trd, r.data)
  } catch(e) { console.error(e) }
}

// ── 模型下拉：仅上线中（主/备），主在前 ──
const _cfgCache = {}  // version -> config（预测列渲染格式依赖 label_transform）
async function loadModels() {
  try {
    const r = await axios.get(API + '/api/v1/models')
    const actives = (r.data?.versions || []).filter(v => v.status === 'ACTIVE')
    actives.forEach(v => { _cfgCache[v.version] = v.config || {} })
    modelOptions.value = actives.map(v => ({
      label: v.version + (v.role === 'primary' ? '（主模型）' : '（备模型）'),
      value: v.version,
    }))
    if (!selModel.value || !actives.some(v => v.version === selModel.value)) {
      const primary = actives.find(v => v.role === 'primary') || actives[0]
      selModel.value = primary?.version || null
    }
    applyPredictFmt()
  } catch(e) { console.error(e) }
}
function applyPredictFmt() {
  const c = selModel.value
  predictFmt.rank = predictFmt.prob = false
  if (c && _cfgCache[c]) {
    predictFmt.rank = _cfgCache[c].label_transform === 'rank'
    predictFmt.prob = _cfgCache[c].label_transform === 'top20'
  }
}

function onModelChange() {
  sigPage.value = 1; trdPage.value = 1
  sig.signals = null; trd.trades = null
  applyPredictFmt()
  loadSignals()
  if (tab.value === 'trades') loadTrades()
}

// ── 生成：重算该模型当日信号→模拟交易→健康 ──
const showGenModal = ref(false)
const genLoading = ref(false)
async function doGenerate() {
  genLoading.value = true
  try {
    await axios.post(API + `/api/v1/models/${selModel.value}/regenerate-day`, { date: today })
    showGenModal.value = false
    message.success('已提交后台重算（信号→模拟交易→健康检查），完成后自动刷新')
    let n = 0
    const timer = setInterval(async () => {
      n++
      await Promise.all([loadSignals(), tab.value === 'trades' ? loadTrades() : Promise.resolve()])
      if (n >= 4) clearInterval(timer)
    }, 15000)
  } catch(e) { message.error(e.response?.data?.detail || '提交失败') } finally { genLoading.value = false }
}

onMounted(async () => { await loadModels(); await loadSignals() })
</script>
