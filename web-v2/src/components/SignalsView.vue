<template>
<div>
  <n-space align="center" style="margin-bottom:8px" wrap>
    <span style="font-size:12px;color:var(--c-text-dim)">日期:</span>
    <n-date-picker v-model:formatted-value="sigDate" type="date" value-format="yyyy-MM-dd" size="small" @update:formatted-value="load" />
    <n-button size="small" @click="sigDate=todayStr();load()">今天</n-button>
  </n-space>
  <div style="margin-bottom:8px;font-size:12px;color:var(--c-text-dim)">扫描 <b>{{data.scanned}}</b> 只, 买入 <b>{{data.total_signals}}</b> 只</div>
  <n-data-table v-if="data.signals" :columns="columns" :data="data.signals" size="small" />
  <n-empty v-else description="暂无信号" />
</div>
</template>
<script setup>
import { ref, reactive, h, onMounted } from 'vue'
import { NCard, NDataTable, NDatePicker, NButton, NSpace, NTag, NEmpty } from 'naive-ui'
import axios from 'axios'
const emit = defineEmits(['show-detail'])
const API = window.location.origin
const sigDate = ref(new Date().toISOString().slice(0,10))
const data = reactive({signals:null, scanned:0, total_signals:0})
const todayStr = () => new Date().toISOString().slice(0,10)
const columns = [
  { title:'#', key:'index', width:35, render:(_,i)=>i+1 },
  { title:'代码', key:'stock_code', width:85, render(r){return h('span',{style:{color:'var(--n-color-target)',cursor:'pointer'},onClick:()=>emit('show-detail',r.stock_code)},r.stock_code)} },
  { title:'名称', key:'stock_name', width:100, render(r){return h('span',{style:{cursor:'pointer'},onClick:()=>emit('show-detail',r.stock_code)},r.stock_name)} },
  { title:'现价', width:95, align:'right', render(r){return '¥'+((r.price||0).toFixed(2))} },
  { title:'方向', width:55, render(){return h(NTag,{type:'error',size:'small',bordered:false},{default:()=>'买'})} },
  { title:'强度', width:70, render(r){return '★'.repeat(r.strength||0)} },
  { title:'策略', minWidth:180, render(r){return h('span',{style:{fontSize:'11px'}}, r.reason)} },
]
async function doGenerate() {
  genLoading.value = true
  try {
    await axios.post(API + '/api/dag_trigger', { node: 'strategy', date: sigDate.value, include_downstream: false })
    showGenModal.value = false
  } catch(e) {} finally {
    genLoading.value = false
  }
}

async function load(){
  try{
    const d = typeof sigDate.value === 'string' ? sigDate.value : (sigDate.value||new Date()).toISOString().slice(0,10)
    const r = await axios.get(API+'/api/buy_signals',{params:{signal_date:d}})
    Object.assign(data, r.data)
  }catch(e){console.error('load signals error',e)}
}
onMounted(load)
</script>