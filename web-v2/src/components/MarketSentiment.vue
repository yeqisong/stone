<template>
<div v-if="s" class="ms-bar" :class="'ms-' + s.level" :title="s.reasons?.join('；')">
  <span class="ms-main">市场情绪 <b>{{ s.label }}</b> {{ s.score }}/100</span>
  <span v-if="s.breadth?.total">涨 <b class="up">{{ s.breadth.up }}</b> / 跌 <b class="down">{{ s.breadth.down }}</b></span>
  <span v-if="s.breadth?.median != null">中位 {{ signed(s.breadth.median) }}%</span>
  <span>涨停 {{ s.breadth?.limit_up ?? '—' }} / 跌停 {{ s.breadth?.limit_down ?? '—' }}</span>
  <span v-for="i in indices" :key="i.code">{{ i.name }} {{ signed(i.chg_pct) }}%</span>
  <span class="ms-advice">{{ s.advice?.stop }}</span>
  <span class="ms-asof">{{ s.asof }}</span>
</div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import axios from 'axios'

const API = window.location.origin
const s = ref(null)
let timer = null
const signed = v => v == null ? '—' : ((Number(v) >= 0 ? '+' : '') + Number(v).toFixed(2))
const indices = computed(() => (s.value?.indices || []).filter(i => ['000001', '399006'].includes(i.code)))

async function load(){
  try{
    const r = await axios.get(API + '/api/market_sentiment', { timeout: 30000 })
    s.value = r.data
  }catch(e){ /* 情绪仪表失败不影响交易页 */ }
}
onMounted(() => { load(); timer = setInterval(load, 60000) })
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.ms-bar {
  display:flex; align-items:center; gap:12px; flex-wrap:wrap; flex-shrink:0;
  margin-bottom:8px; padding:6px 10px; font-size: var(--text-meta);
  color: var(--c-text-dim); background: var(--c-card-bg);
  border:1px solid var(--c-border); border-left:3px solid var(--c-text-faint);
  border-radius: var(--radius-sm);
}
.ms-main { color: var(--c-text); font-weight:600; }
.ms-main b { font-weight:700; }
.ms-bar b.up { color: var(--c-up); }
.ms-bar b.down { color: var(--c-down); }
.ms-advice { color: var(--c-text); }
.ms-asof { margin-left:auto; color: var(--c-text-faint); white-space:nowrap; }
.ms-panic { border-left-color: var(--c-error); background: color-mix(in srgb, var(--c-error) 5%, var(--c-card-bg)); }
.ms-panic .ms-main b { color: var(--c-error); }
.ms-weak { border-left-color: var(--c-warning); }
.ms-weak .ms-main b { color: var(--c-warning); }
.ms-strong { border-left-color: var(--c-info); }
.ms-strong .ms-main b { color: var(--c-info); }
.ms-neutral { border-left-color: var(--c-text-dim); }
@media (max-width: 900px) {
  .ms-bar { gap:8px; }
  .ms-asof { width:100%; margin-left:0; }
}
</style>
