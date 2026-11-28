<template>
<div v-if="s" class="ms-bar" :class="'ms-' + s.level" :title="tip">
  <span class="ms-main">市场情绪 {{ s.score }}/100 · <b>{{ s.label }}</b></span>
  <span class="ms-gauge-wrap" aria-hidden="true">
    <span class="ms-cap">恐慌</span>
    <span class="ms-gauge" :title="gaugeTip">
      <i class="seg seg-panic"></i><i class="seg seg-weak"></i><i class="seg seg-neutral"></i><i class="seg seg-strong"></i>
      <i class="pin" :style="{ left: pinLeft }"></i>
    </span>
    <span class="ms-cap">强势</span>
  </span>
  <span v-if="s.breadth?.total">涨 <b class="up">{{ s.breadth.up }}</b> / 跌 <b class="down">{{ s.breadth.down }}</b></span>
  <span v-if="s.breadth?.median != null">中位 {{ signed(s.breadth.median) }}%</span>
  <span>涨停 {{ s.breadth?.limit_up ?? '—' }} / 跌停 {{ s.breadth?.limit_down ?? '—' }}</span>
  <span v-for="i in indices" :key="i.code">{{ i.name }} {{ signed(i.chg_pct) }}%</span>
  <span v-if="s.gate" class="ms-gate" :class="'gate-' + s.gate.level"
        :title="gateTip">开仓闸门 <b>{{ s.gate.label }}</b></span>
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
const pinLeft = computed(() => {
  const v = Number(s.value?.score)
  return (Number.isFinite(v) ? Math.max(0, Math.min(100, v)) : 0) + '%'
})
const tip = computed(() => {
  if (!s.value) return ''
  const reasons = (s.value.reasons || []).join('；')
  return `0=极度恐慌，100=极度亢奋（数值越小越恐慌）${reasons ? '；' + reasons : ''}`
})
const gaugeTip = '恐慌 0–25 ｜ 偏弱 25–45 ｜ 中性 45–70 ｜ 偏强 70–100'
const gateTip = computed(() => {
  const g = s.value?.gate
  if (!g) return ''
  const rs = (g.reasons || []).join('；')
  return `实时空仓闸门（市场闸门 × regime × 盘面情绪）${rs ? '：' + rs : ''}${g.advice_entry ? '｜' + g.advice_entry : ''}`
})

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
.ms-gauge-wrap { display:inline-flex; align-items:center; gap:4px; }
.ms-cap { font-size:10px; color: var(--c-text-faint); white-space:nowrap; }
.ms-gauge { position:relative; display:inline-flex; width:88px; height:6px; }
.ms-gauge .seg { box-sizing:border-box; height:100%; }
.ms-gauge .seg + .seg { border-left:1px solid var(--c-card-bg); }
.ms-gauge .seg-panic { width:25%; background: var(--c-down); border-radius:3px 0 0 3px; }
.ms-gauge .seg-weak { width:20%; background: var(--c-warning); }
.ms-gauge .seg-neutral { width:25%; background: var(--c-text-faint); }
.ms-gauge .seg-strong { width:30%; background: var(--c-up); border-radius:0 3px 3px 0; }
.ms-gauge .pin {
  position:absolute; top:-2px; width:2px; height:10px; background: var(--c-text);
  border-radius:1px; box-shadow:0 0 0 1px var(--c-card-bg); transform:translateX(-1px);
  pointer-events:none;
}
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
.ms-gate { padding:1px 8px; border-radius:4px; border:1px solid var(--c-border); color:var(--c-text-dim); }
.ms-gate b { color:inherit; }
/* 闸门三态与 TradeGuard 一致：正常=下跌绿、警戒=warning、关闸=error（不用上涨红：关闸是「停」不是「涨」）；
   unknown/不可用 走上面 .ms-gate 的默认灰 */
.ms-gate.gate-open { color:var(--c-down); border-color:var(--c-down); }
.ms-gate.gate-caution { color:var(--c-warning, #8a5a00); border-color:var(--c-warning, #8a5a00); }
.ms-gate.gate-closed { color:var(--c-error); border-color:var(--c-error); }
</style>
