<template>
<div>
  <div style="font-size:12px;color:var(--c-text-dim);margin-bottom:12px">指标数据管理 — 通过 DAG 节点触发全量/增量计算</div>

  <div style="display:flex;gap:8px;margin-bottom:16px">
    <n-button size="small" type="primary" @click="trigger('indicator_incr')" :loading="loading==='incr'">增量更新今日</n-button>
    <n-button size="small" @click="trigger('indicator_full')" :loading="loading==='full'">全量初始化全部</n-button>
  </div>

  <div style="display:flex;flex-direction:column;gap:4px">
    <div v-for="t in tables" :key="t.name" style="display:flex;align-items:center;gap:12px;padding:8px 12px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:6px">
      <span style="font-size:12px;font-weight:600;color:var(--c-text);min-width:70px">{{t.label}}</span>
      <span style="font-size:10px;color:var(--c-text-dim)">{{t.rows}} 行</span>
      <span style="font-size:10px;color:var(--c-text-faint);margin-left:auto">{{t.latest||'—'}}</span>
    </div>
  </div>

  <div v-if="msg" :style="{marginTop:'12px',padding:'8px 12px',borderRadius:'6px',fontSize:'11px',color:msgOk?'#10b981':'#f59e0b',background:msgOk?'rgba(16,185,129,.08)':'rgba(245,158,11,.08)'}">{{msg}}</div>
</div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { NButton } from 'naive-ui'
import axios from 'axios'
const tables = ref([
  { name:'boll', label:'BOLL', rows:'—', latest:'—' },
  { name:'macd', label:'MACD', rows:'—', latest:'—' },
  { name:'rsi', label:'RSI', rows:'—', latest:'—' },
  { name:'atr', label:'ATR', rows:'—', latest:'—' },
  { name:'ma', label:'MA', rows:'—', latest:'—' },
  { name:'volume', label:'成交量', rows:'—', latest:'—' },
])
const loading = ref(null)
const msg = ref('')
const msgOk = ref(true)

async function trigger(node) {
  loading.value = node === 'indicator_incr' ? 'incr' : 'full'
  msg.value = ''
  try {
    const r = await axios.post(window.location.origin + '/api/dag_trigger', {
      node, date: new Date().toISOString().slice(0,10), include_downstream: false
    })
    if (r.data.busy) {
      msg.value = '任务进行中，请等待上一个任务完成'
      msgOk.value = false
    } else {
      msg.value = `已触发 ${node}，查看状态页了解进度`
      msgOk.value = true
    }
  } catch(e) {
    msg.value = '触发失败: ' + e.message
    msgOk.value = false
  }
  loading.value = null
}

onMounted(async () => {
  const labels = { boll:'BOLL', macd:'MACD', rsi:'RSI', atr:'ATR', ma:'MA', volume:'成交量' }
  const results = []
  for (const [name, label] of Object.entries(labels)) {
    try {
      const r = await axios.get(window.location.origin + `/api/v1/indicators/${name}/status`)
      results.push({ name, label, rows: r.data?.rows||0, latest: (r.data?.latest||'').slice(0,10)||'—' })
    } catch(e) {
      results.push({ name, label, rows:'—', latest:'—' })
    }
  }
  tables.value = results
})
</script>