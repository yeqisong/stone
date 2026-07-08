<template>
<div>
  <div style="font-size:12px;color:var(--c-text-dim);margin-bottom:12px">指标数据管理 — 旧 6 张指标表（stock_indicators_*）已下线。所有指标计算统一走「特征管理」→ 特征补数。</div>

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
import axios from 'axios'
const tables = ref([
  { name:'boll', label:'BOLL', rows:'—', latest:'—' },
  { name:'macd', label:'MACD', rows:'—', latest:'—' },
  { name:'rsi', label:'RSI', rows:'—', latest:'—' },
  { name:'atr', label:'ATR', rows:'—', latest:'—' },
  { name:'ma', label:'MA', rows:'—', latest:'—' },
  { name:'volume', label:'成交量', rows:'—', latest:'—' },
])
const msg = ref('')
const msgOk = ref(true)

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