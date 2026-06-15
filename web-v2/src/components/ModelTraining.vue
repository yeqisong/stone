<template>
<div>
  <!-- Status Cards -->
  <div style="display:flex;gap:16px;margin-bottom:20px;flex-wrap:wrap">
    <div v-for="c in cards" :key="c.label" style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:14px 18px;min-width:100px">
      <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px;text-transform:uppercase;letter-spacing:1px">{{c.label}}</div>
      <div :style="{fontSize:'20px',fontWeight:700,color:c.color||'var(--c-text)'}">{{c.value}}</div>
    </div>
  </div>

  <!-- Optuna Chart (placeholder) -->
  <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:20px;margin-bottom:16px">
    <div style="font-size:12px;font-weight:600;color:var(--c-text-dim);margin-bottom:12px">OPTUNA 优化曲线</div>
    <svg viewBox="0 0 600 160" style="width:100%;height:160px">
      <line v-for="y in [20,55,90,125]" :key="y" x1="40" :y1="y" x2="580" :y2="y" stroke="rgba(255,255,255,.04)"/>
      <text x="36" y="16" fill="rgba(255,255,255,.2)" font-size="9" text-anchor="end">2.5</text>
      <text x="36" y="51" fill="rgba(255,255,255,.2)" font-size="9" text-anchor="end">2.0</text>
      <text x="36" y="86" fill="rgba(255,255,255,.2)" font-size="9" text-anchor="end">1.5</text>
      <text x="36" y="121" fill="rgba(255,255,255,.2)" font-size="9" text-anchor="end">1.0</text>
      <polyline fill="none" stroke="#2080f0" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
        points="40,140 50,125 60,130 70,105 80,115 90,90 100,100 110,82 120,88 130,75 140,80 150,68 160,72 170,62 180,65 190,58 200,55 210,60 220,52 230,50 240,48 250,52 260,45 270,42 280,48 290,40 300,38 310,44 320,36 330,34 340,38 350,32 360,30 370,35 380,28 390,26 400,30 410,24 420,22 430,28 440,20 450,22 460,18 470,20 480,16 490,18 500,14 510,20 520,16 530,18 540,15 550,20"/>
      <circle cx="328" cy="36" r="5" fill="#10b981" stroke="var(--c-bg)" stroke-width="2"/>
      <text x="328" y="30" fill="#10b981" font-size="9" text-anchor="middle">2.31</text>
      <text x="310" y="155" fill="rgba(255,255,255,.15)" font-size="9" text-anchor="middle">trial →</text>
    </svg>
  </div>

  <!-- Parameter Importance -->
  <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:20px;margin-bottom:16px">
    <div style="font-size:12px;font-weight:600;color:var(--c-text-dim);margin-bottom:12px">参数重要性</div>
    <div v-for="b in bars" :key="b.label" style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
      <span style="font-size:11px;width:50px;text-align:right;color:var(--c-text-dim)">{{b.label}}</span>
      <div style="flex:1;height:8px;background:rgba(255,255,255,.06);border-radius:4px;overflow:hidden">
        <div :style="{width:b.pct+'%',height:'100%',borderRadius:'4px',background:'linear-gradient(90deg, #2080f0, rgba(32,128,240,.4))'}"></div>
      </div>
      <span style="font-size:10px;width:36px;color:var(--c-text-dim)">{{b.val}}</span>
    </div>
  </div>

  <!-- DAG Status -->
  <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:20px">
    <div style="font-size:12px;font-weight:600;color:var(--c-text-dim);margin-bottom:12px">DAG 任务状态</div>
    <div style="display:flex;align-items:center;gap:12px;font-size:13px">
      <div style="display:flex;align-items:center;gap:6px;padding:6px 14px;border:1px solid rgba(16,185,129,.3);background:rgba(16,185,129,.08);border-radius:6px">
        <span style="color:#10b981;font-size:14px">✓</span>
        <span style="font-size:11px;color:var(--c-text)">indicator_incr</span>
      </div>
      <span style="color:var(--c-border);font-size:18px">→</span>
      <div style="display:flex;align-items:center;gap:6px;padding:6px 14px;border:1px solid rgba(245,158,11,.3);background:rgba(245,158,11,.08);border-radius:6px;animation:pulse 1.5s infinite">
        <span style="color:#f59e0b;font-size:14px">⟳</span>
        <span style="font-size:11px;color:var(--c-text)">model_train</span>
      </div>
    </div>
  </div>
</div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ version: Object })

const cards = computed(() => [
  { label:'训练状态', value: props.version.status, color: props.version.status==='TRAINING'?'#f59e0b':props.version.status==='ACTIVE'?'#10b981':undefined },
  { label:'完成进度', value: props.version.status==='DRAFT'?'—':'85 / 100' },
  { label:'最佳 Score', value: props.version.sharpe||'—', color:'#10b981' },
  { label:'已运行', value: props.version.status==='DRAFT'?'—':'12m 30s' },
])

const bars = [
  { label:'BOLL', pct:72, val:'0.35' },
  { label:'RSI', pct:36, val:'0.18' },
  { label:'MACD', pct:18, val:'0.09' },
  { label:'ATR', pct:46, val:'0.22' },
  { label:'量能', pct:28, val:'0.14' },
]
</script>

<style>
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.6} }
</style>