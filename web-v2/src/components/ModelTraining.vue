<template>
<div>
  <div v-if="version.status==='TRAINING'" style="padding:16px 0">
    <!-- 训练步骤 -->
    <div style="margin-bottom:20px">
      <div style="font-size:12px;font-weight:600;color:var(--c-text-dim);margin-bottom:10px">训练进度</div>
      <div v-for="s in trainSteps" :key="s.idx" style="display:flex;align-items:center;gap:10px;padding:6px 0">
        <div :style="{width:'22px',height:'22px',borderRadius:'50%',display:'flex',alignItems:'center',justifyContent:'center',fontSize:'11px',fontWeight:600,background:s.done?'#10b981':s.active?'rgba(32,128,240,.15)':'rgba(255,255,255,.05)',color:s.done?'#fff':s.active?'#2080f0':'var(--c-text-faint)',border:s.active&&!s.done?'2px solid #2080f0':'2px solid transparent'}">
          {{ s.done ? '✓' : s.idx }}
        </div>
        <span :style="{fontSize:'12px',color:s.done||s.active?'var(--c-text)':'var(--c-text-faint)'}">{{ s.label }}</span>
        <span v-if="s.active && !s.done" style="font-size:10px;color:#2080f0;animation:pulse 1.5s infinite">⟳</span>
      </div>
    </div>
    <div style="font-size:11px;color:var(--c-text-faint);text-align:center">
      Started: {{ version.trained_at?.slice(0,19) || '—' }}
    </div>
    <div style="text-align:center;margin-top:12px"><n-button size="small" @click="refresh">刷新状态</n-button></div>
  </div>
  <div v-else-if="version.status==='PENDING'||version.status==='ACTIVE'" style="display:flex;gap:16px;flex-wrap:wrap">
    <div v-for="c in cards" :key="c.label" style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:14px 18px;min-width:100px">
      <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px">{{c.label}}</div>
      <div :style="{fontSize:'20px',fontWeight:700,color:c.color||'var(--c-text)'}">{{c.value}}</div>
    </div>
  </div>
  <div v-else style="text-align:center;padding:40px;color:var(--c-text-dim)">点击「开始训练」启动模型训练</div>
</div>
</template>

<script setup>
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { NButton } from 'naive-ui'
import { useModelStore } from '../stores/model'
import { addWsListener } from '../utils/ws'

const props = defineProps({ version: Object })
const store = useModelStore()

const steps = [
  { idx:1, label:'加载指标数据' },
  { idx:2, label:'特征工程（衍生特征）' },
  { idx:3, label:'标签计算（Forward收益）' },
  { idx:4, label:'XGBoost 模型训练' },
  { idx:5, label:'存储结果' },
]

const currentStep = ref(0)
let _wsCleanup = null

const trainSteps = computed(() => steps.map(s => ({
  ...s,
  done: currentStep.value > s.idx,
  active: currentStep.value === s.idx,
})))

function handleWs(data) {
  if (data.type !== 'dag_log') return
  const nodes = data.nodes || []
  const trainNode = nodes.find(n => n.node === 'model_train')
  if (!trainNode) return
  // Parse step from detail: "步骤1:加载指标" → step 1
  const detail = trainNode.detail || ''
  const m = detail.match(/步骤(\d+)/)
  if (m) currentStep.value = parseInt(m[1])
  if (trainNode.status === 'success') { currentStep.value = steps.length + 1; store.loadVersions() }
  if (trainNode.status === 'failed') { currentStep.value = -1; store.loadVersions() }
}

onMounted(() => { _wsCleanup = addWsListener(handleWs) })
onUnmounted(() => { if (_wsCleanup) _wsCleanup() })

const cards = computed(() => {
  const s = props.version
  if (!s || s.status === 'DRAFT') return []
  return [
    { label:'状态', value: s.status, color: s.status==='ACTIVE'?'#10b981':s.status==='PENDING'?'#f59e0b':undefined },
    { label:'夏普', value: s.sharpe?.toFixed(2)||'—', color:'#10b981' },
    { label:'胜率', value: s.win_rate ? (s.win_rate*100).toFixed(0)+'%' : '—' },
    { label:'创建', value: (s.created_at||'').slice(0,10) },
  ]
})

async function refresh() {
  await store.loadVersions()
}
</script>

<style>
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.6} }
</style>