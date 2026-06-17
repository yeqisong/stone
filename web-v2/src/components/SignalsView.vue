<template>
<div>
  <n-space align="center" style="margin-bottom:8px" wrap>
    <n-button-group size="tiny">
      <n-button :type="!showStats?'primary':'default'" @click="showStats=false">🔴 信号列表</n-button>
      <n-button :type="showStats?'primary':'default'" @click="showStats=true">📈 效果追踪</n-button>
    </n-button-group>
    <span style="font-size:12px;color:var(--c-text-dim)">日期:</span>
    <n-date-picker v-model:formatted-value="sigDate" type="date" value-format="yyyy-MM-dd" size="tiny" @update:formatted-value="load" />
    <n-button size="tiny" @click="sigDate=todayStr();load()">今天</n-button>
    <n-button size="tiny" @click="showGenModal=true" :disabled="!activeModel">⚡ 生成</n-button>
    <span v-if="activeModel" style="font-size:10px;color:var(--c-text-faint)">模型: {{activeModel}}</span>
  </n-space>
  <template v-if="!showStats">
    <div style="margin-bottom:8px;font-size:12px;color:var(--c-text-dim)">扫描 <b>{{data.scanned}}</b> 只, 买入 <b>{{data.total_signals}}</b> 只</div>
    <n-data-table v-if="data.signals" :columns="columns" :data="data.signals" size="small" />
    <n-empty v-else description="暂无信号" />
  </template>
  <SignalStatsView v-else />

  <n-modal v-model:show="showGenModal" preset="card" title="⚡ 重新生成信号" style="width:380px;max-width:85vw" :mask-closable="false">
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
import { ref, reactive, h, onMounted } from 'vue'
import { NDataTable, NDatePicker, NButton, NButtonGroup, NSpace, NTag, NEmpty, NModal } from 'naive-ui'
import axios from 'axios'
import SignalStatsView from './SignalStatsView.vue'
const emit = defineEmits(['show-detail'])
const API = window.location.origin
const sigDate = ref(new Date().toISOString().slice(0,10))
const data = reactive({signals:null, scanned:0, total_signals:0})
const showStats = ref(false)
const showGenModal = ref(false)
const genLoading = ref(false)
const activeModel = ref(null)
const todayStr = () => new Date().toISOString().slice(0,10)
const columns = [
  { title:'#', key:'index', width:35, render:(_,i)=>i+1 },
  { title:'代码', key:'stock_code', width:85, render(r){return h('span',{style:{color:'var(--n-color-target)',cursor:'pointer'},onClick:()=>emit('show-detail',r.stock_code)},r.stock_code)} },
  { title:'名称', key:'stock_name', width:100, render(r){return h('span',{style:{cursor:'pointer'},onClick:()=>emit('show-detail',r.stock_code)},r.stock_name)} },
  { title:'现价', width:95, align:'right', render(r){return '¥'+((r.price||0).toFixed(2))} },
  { title:'方向', width:55, render(){return h(NTag,{type:'error',size:'small',bordered:false},{default:()=>'买'})} },
  { title:'强度', width:70, render(r){return '★'.repeat(r.strength||0)} },
  { title:'预测5d', width:70, align:'right', render(r){ const v=r.predict_5d; return v!=null ? h('span',{style:{color:v>=0?'#ef4444':'#10b981',fontSize:'11px'}},(v>=0?'+':'')+(v*100).toFixed(1)+'%') : '—' }},
  { title:'预测10d', width:70, align:'right', render(r){ const v=r.predict_10d; return v!=null ? h('span',{style:{color:v>=0?'#ef4444':'#10b981',fontSize:'11px'}},(v>=0?'+':'')+(v*100).toFixed(1)+'%') : '—' }},
  { title:'策略', minWidth:140, render(r){return h('span',{style:{fontSize:'11px'}}, r.reason)} },
]
async function doGenerate() {
  genLoading.value = true
  try {
    await axios.post(API + '/api/dag_trigger', { node: 'model_signal', date: sigDate.value, include_downstream: false })
    showGenModal.value = false
    setTimeout(async () => { await load() }, 5000)
  } catch(e) {} finally { genLoading.value = false }
}

async function load(){
  try{
    const d = typeof sigDate.value === 'string' ? sigDate.value : (sigDate.value||new Date()).toISOString().slice(0,10)
    const r = await axios.get(API+'/api/buy_signals',{params:{signal_date:d}})
    Object.assign(data, r.data)
    const mr = await axios.get(API+'/api/v1/models')
    const active = (mr.data?.versions||[]).find(v=>v.status==='ACTIVE')
    activeModel.value = active?.version || null
  }catch(e){console.error('load signals error',e)}
}
onMounted(load)
</script>