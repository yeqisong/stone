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
    <div v-if="errorDetail" style="margin-top:16px;padding:10px 14px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:8px;font-size:11px">
      <div style="color:#ef4444;font-weight:600;margin-bottom:4px">❌ 训练失败</div>
      <div style="color:var(--c-text-dim);word-break:break-all;max-height:200px;overflow-y:auto">{{ errorDetail }}</div>
    </div>
    <div style="font-size:11px;color:var(--c-text-faint);text-align:center;margin-top:12px">
      Started: {{ version.trained_at?.slice(0,19) || '—' }}
    </div>
    <div style="text-align:center;margin-top:8px;display:flex;gap:8px;justify-content:center">
      <n-button size="small" @click="refresh">刷新状态</n-button>
      <n-button size="small" type="error" @click="stopTrain" :loading="stopping">停止训练</n-button>
    </div>
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
import axios from 'axios'
import { useModelStore } from '../stores/model'
import { addWsListener, connectWebSocket, wsState } from '../utils/ws'

const props = defineProps({ version: Object })
const store = useModelStore()

const currentStep = ref(0)
const errorDetail = ref('')
const currentTrial = ref(0)
const totalTrials = ref(50)
const stopping = ref(false)
let _wsCleanup = null

const trainSteps = computed(() => {
  const steps = []
  const done = (idx) => currentStep.value >= idx || currentStep.value < 0
  const active = (phase) => currentTrial.value > 0 && currentTrial.value < totalTrials.value && currentStep.value >= 0
  // 阶段：加载数据
  steps.push({ idx:1, label:'加载指标数据', done: done(1), active: currentStep.value === 0 })
  // 阶段：特征工程
  steps.push({ idx:2, label:'特征工程', done: done(2), active: currentStep.value === 1 })
  // 阶段：Optuna 搜索
  const trialLabel = currentTrial.value > 0 ? `${currentTrial.value}/${totalTrials.value}` : '—'
  steps.push({ idx:3, label:`Optuna 搜索 (${trialLabel})`, done: done(4), active: active('train') })
  // 阶段：存储
  steps.push({ idx:4, label:'存储最优模型', done: done(0) && currentStep.value < 0 ? false : currentStep.value >= 4, active: false })
  return steps
})

function handleWs(data) {
  if (data.type !== 'dag_log') return
  const nodes = data.nodes || []
  const trainNode = nodes.find(n => n.node_name === 'model_train')
  if (!trainNode) return
  const detail = trainNode.detail || ''
  // Optuna 格式: "Optuna实验:5/50 sharpe=1.234"
  const ot = detail.match(/Optuna实验:(\d+)\/(\d+)/)
  if (ot) { currentTrial.value = parseInt(ot[1]); totalTrials.value = parseInt(ot[2]); currentStep.value = 3 }
  // 旧格式: "步骤1:加载指标"
  const m = detail.match(/步骤(\d+)/)
  if (m && !ot) { currentStep.value = parseInt(m[1]); currentTrial.value = 0 }
  // 其他阶段
  if (detail.includes('特征工程')) currentStep.value = 2
  if (detail.includes('加载指标')) currentStep.value = 1
  if (detail.includes('存储最优')) currentStep.value = 4
  if (trainNode.status === 'success') {
    currentStep.value = steps.length + 1
    setTimeout(() => store.loadVersions(), 500)
  }
  if (trainNode.status === 'failed') {
    currentStep.value = -1
    errorDetail.value = trainNode.detail || ''
    setTimeout(() => store.loadVersions(), 500)
  }
}

onMounted(() => {
  if (!wsState.connected) connectWebSocket()
  _wsCleanup = addWsListener(handleWs)
})
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

async function stopTrain() {
  if (!confirm('确定要停止训练吗？训练数据将丢失，模型恢复为草稿状态。')) return
  stopping.value = true
  try {
    await axios.post(window.location.origin + '/api/dag_terminate', { node: 'model_train' })
    await axios.post(window.location.origin + `/api/v1/models/${props.version.version}/stop`)
    await store.loadVersions()
  } catch(e) {
    alert(e.response?.data?.detail || '停止失败')
  } finally {
    stopping.value = false
  }
}
</script>

<style>
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.6} }
</style>