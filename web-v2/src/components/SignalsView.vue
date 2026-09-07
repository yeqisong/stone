<template>
<div>
  <n-space align="center" style="margin-bottom:8px" wrap>
    <n-button-group size="tiny">
      <n-button size="tiny" :type="!showStats?'primary':'default'" @click="showStats=false"> 信号列表</n-button>
      <n-button size="tiny" :type="showStats?'primary':'default'" @click="showStats=true"><AppIcon name="trending-up" :size="13" />  效果追踪</n-button>
    </n-button-group>
    <span style="font-size:12px;color:var(--c-text-dim)">日期:</span>
    <n-date-picker v-model:formatted-value="sigDate" type="date" value-format="yyyy-MM-dd" size="tiny" @update:formatted-value="load" />
    <n-button size="tiny" @click="sigDate=todayStr();load()">今天</n-button>
    <n-button size="tiny" @click="showGenModal=true" :disabled="!activeModel"><AppIcon name="zap" :size="13" />  生成</n-button>
    <span v-if="activeModel" style="font-size:10px;color:var(--c-text-faint)">模型: {{activeModel}}</span>
  </n-space>
  <template v-if="!showStats">
    <div style="margin-bottom:8px;font-size:12px;color:var(--c-text-dim)">扫描 <b>{{data.scanned}}</b> 只, 买入 <b>{{data.total_signals}}</b> 只</div>
    <div v-if="data.signals" style="overflow-x:auto;-webkit-overflow-scrolling:touch">
      <n-data-table :columns="columns" :data="pagedSignals" size="small" :scroll-x="880" />
    </div>
    <n-empty v-else description="暂无信号" />
    <n-space justify="end" style="margin-top:10px">
      <n-pagination v-if="(data.signals?.length||0) > sigPageSize" :page="sigPage" :item-count="data.signals?.length||0"
        :page-size="sigPageSize" size="small" @update:page="p=>sigPage=p" />
    </n-space>
  </template>
  <SignalStatsView v-else />

  <n-modal v-model:show="showGenModal" preset="card" title="重新生成信号" style="width:380px;max-width:85vw" :mask-closable="false">
    <div style="font-size:13px;color:var(--c-text);margin-bottom:8px">
      模型 <b>{{activeModel}}</b> 将为 {{sigDate}} 重新全市场扫描生成信号
    </div>
    <div style="font-size:11px;color:var(--c-text-dim);margin-bottom:16px">该日已有信号将被覆盖</div>
    <div style="display:flex;gap:8px;justify-content:flex-end">
      <n-button size="small" @click="showGenModal=false">取消</n-button>
      <n-button size="small" type="primary" @click="doGenerate" :loading="genLoading">生成</n-button>
    </div>
  </n-modal>
</div>
</template>
<script setup>
import AppIcon from './AppIcon.vue'
import { ref, reactive, h, computed, onMounted } from 'vue'
import { NDataTable, NDatePicker, NButton, NButtonGroup, NSpace, NTag, NEmpty, NModal, NPagination } from 'naive-ui'
import axios from 'axios'
import SignalStatsView from './SignalStatsView.vue'
import { useViewport } from '../utils/viewport'
import { bjDateStr } from '../utils/date.js'
const emit = defineEmits(['show-detail'])
const API = window.location.origin
const bjToday = () => bjDateStr()
const sigDate = ref(bjToday())
const data = reactive({signals:null, scanned:0, total_signals:0})
// 本地分页：单日信号数百条，一次拉全后前端翻页（与个股列表页 n-pagination 样式一致）
const sigPage = ref(1)
const sigPageSize = 50
const pagedSignals = computed(() => (data.signals || []).slice((sigPage.value-1)*sigPageSize, sigPage.value*sigPageSize))
const showStats = ref(false)
const showGenModal = ref(false)
const genLoading = ref(false)
const activeModel = ref(null)
const { isNarrow } = useViewport()
const todayStr = () => bjToday()
const columns = computed(() => {
  // H5 窄屏精简列：预测10d/策略 收起，代码/名称固定左
  const base = [
    { title:'#', key:'index', width:35, render:(_,i)=>i+1 },
    { title:'代码', key:'stock_code', width:85, fixed:'left', render(r){return h('span',{style:{color:'var(--n-color-target)',cursor:'pointer'},onClick:()=>emit('show-detail',r.stock_code)},r.stock_code)} },
    { title:'名称', key:'stock_name', width:100, fixed:'left', render(r){return h('span',{style:{cursor:'pointer'},onClick:()=>emit('show-detail',r.stock_code)},r.stock_name)} },
    { title:'现价', key:'price', width:95, align:'right', render(r){ const v=r.real_price??r.price; return '¥'+((v||0).toFixed(2)) } },
    { title:'方向', key:'direction', width:55, render(){return h(NTag,{type:'error',size:'small',bordered:false},{default:()=>'买'})} },
    { title:'强度', key:'strength', width:70, render(r){return '★'.repeat(r.strength||0)} },
    { title:'预测5d', key:'predict_5d', width:70, align:'right', render(r){ const v=r.predict_5d; return v!=null ? h('span',{style:{color:v>=0?'#ef4444':'#10b981',fontSize:'11px'}},(v>=0?'+':'')+(v*100).toFixed(1)+'%') : '—' }},
  ]
  if (isNarrow.value) return base
  return [
    ...base,
    { title:'预测10d', key:'predict_10d', width:70, align:'right', render(r){ const v=r.predict_10d; return v!=null ? h('span',{style:{color:v>=0?'#ef4444':'#10b981',fontSize:'11px'}},(v>=0?'+':'')+(v*100).toFixed(1)+'%') : '—' }},
    { title:'预测20d', key:'predict_20d', width:70, align:'right', render(r){ const v=r.predict_20d; return v!=null ? h('span',{style:{color:v>=0?'#ef4444':'#10b981',fontSize:'11px'}},(v>=0?'+':'')+(v*100).toFixed(1)+'%') : '—' }},
    { title:'策略', key:'reason', minWidth:260, render(r){return h('span',{style:{fontSize:'11px'}}, r.reason)} },
  ]
})
async function doGenerate() {
  genLoading.value = true
  try {
    await axios.post(API + '/api/dag_trigger', { node: 'model_signal', date: sigDate.value, include_downstream: false })
    showGenModal.value = false
    setTimeout(async () => { await load() }, 5000)
  } catch(e) { message.error('信号加载失败，请重试') } finally { genLoading.value = false }
}

async function load(){
  try{
    const d = typeof sigDate.value === 'string' ? sigDate.value : bjDateStr()
    // top_n=500：接口原默认 20 且上限 100，单日信号可达数百条被截断
    const r = await axios.get(API+'/api/buy_signals',{params:{signal_date:d, top_n:500}})
    Object.assign(data, r.data)
    sigPage.value = 1
    const mr = await axios.get(API+'/api/v1/models')
    const active = (mr.data?.versions||[]).find(v=>v.status==='ACTIVE')
    activeModel.value = active?.version || null
  }catch(e){console.error('load signals error',e)}
}
onMounted(load)
</script>