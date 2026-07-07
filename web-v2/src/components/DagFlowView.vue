<template>
<div style="position:relative">
  <div style="font-size:14px;font-weight:600;color:var(--c-text);margin-bottom:6px">
    📊 DAG 状态
    <span v-if="activeRunId" style="font-size:9px;color:var(--c-text-faint);margin-left:6px">#{{ activeRunId }}</span>
    <n-button v-if="store.hasRunning" size="tiny" quaternary type="error" style="margin-left:8px;font-size:10px" @click="terminateTask">⏹ 终止</n-button>
  </div>
  <div v-if="store.structure.length===0" style="padding:10px;text-align:center;color:var(--c-text-faint);font-size:11px">加载中...</div>
  <div v-else>
    <div ref="container" style="width:100%;height:400px;border:1px solid var(--c-border);border-radius:8px;overflow:hidden"></div>
    <!-- 节点详情 -->
    <div v-if="selectedNode" style="margin-top:8px;padding:10px 14px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;font-size:12px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
        <span style="font-weight:700;color:var(--c-text)">{{ selectedNode.label }}</span>
        <n-tag size="tiny" :type="stateTag(selectedNode.state)">{{ selectedNode.state }}</n-tag>
        <span style="flex:1"/>
        <span style="cursor:pointer;color:var(--c-text-dim)" @click="selectedNode=null">✕</span>
      </div>
      <div v-if="nodeSubSteps.length" style="margin-top:4px">
        <div style="font-size:10px;color:var(--c-text-dim);margin-bottom:4px">内部依赖子图</div>
        <div v-for="(s,i) in nodeSubSteps" :key="i" style="display:flex;align-items:center;gap:6px;padding:2px 0;font-size:10px">
          <span :style="{color: i < nodeSubComplete ? '#10b981' : '#6b7280'}">{{ i+1 }}. {{ s.name }}</span>
          <span style="color:var(--c-text-faint)">— {{ s.desc }}</span>
        </div>
      </div>
    </div>
  </div>
</div>
</template>

<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { Graph } from '@antv/x6'
import { useDagStore } from '../stores/dag'
import { connectWebSocket } from '../utils/ws'
import axios from 'axios'
import { NButton, NTag } from 'naive-ui'

const store = useDagStore()
const container = ref(null)
const selectedNode = ref(null)
const nodeSubSteps = ref([])
const nodeSubComplete = ref(0)

let graph = null

const activeRunId = computed(() => {
  const rid = store.currentRunId
  if (!rid) return null
  const ts = store.currentRunLatest
  if (ts) {
    const age = Date.now() - new Date(ts.replace(' ', 'T') + '+08:00').getTime()
    if (age > 300000) return null
  }
  return rid
})

function stateColor(s) {
  return {running:'#3b82f6',success:'#10b981',failed:'#ef4444',pending:'#f59e0b',default:'#6b7280'}[s] || '#6b7280'
}

function stateTag(s) {
  return {running:'info',success:'success',failed:'error',pending:'warning',default:'default'}[s] || 'default'
}

function nodeLabel(n) {
  const labels = {daily_update:'📋 更新',kline:'📈 K线',index:'📊 指数',etf:'💹 ETF',fund:'💰 基本面',treemap:'🌳 树图',stats:'📐 统计',cron:'⏰ 定时',indicator_incr:'🔢 指标',model_train:'🧠 训练',model_signal:'📡 信号',model_health:'💚 健康',feature_compute:'⚙️ 特征',feature_backfill:'🔄 补数',daily_completeness:'📅 日历'}
  return labels[n.name] || n.name
}

function renderGraph() {
  if (!container.value || !store.structure.length) return
  if (graph) { graph.dispose(); graph = null }

  graph = new Graph({
    container: container.value,
    autoResize: true,
    grid: { visible: true, size: 20, args: { color: 'var(--c-border)' } },
    panning: { enabled: true, modifiers: 'shift' },
    mousewheel: { enabled: true, modifiers: ['ctrl','meta'] },
    interacting: { nodeMovable: false, edgeLabelMovable: false },
  })

  const nodeMap = {}
  const levelMap = {}
  // 计算层级
  for (const n of store.structure) levelMap[n.name] = 0
  let changed = true
  while (changed) {
    changed = false
    for (const n of store.structure) {
      for (const d of n.deps) {
        const newLevel = (levelMap[d] || 0) + 1
        if (newLevel > (levelMap[n.name] || 0)) {
          levelMap[n.name] = newLevel; changed = true
        }
      }
    }
  }
  const groups = {}
  for (const [k,v] of Object.entries(levelMap)) {
    (groups[v] || (groups[v] = [])).push(k)
  }
  const maxLevel = Math.max(...Object.keys(groups).map(Number), 0)
  const nodeW = 100, nodeH = 36, hGap = 160, vGap = 70

  for (let lv = 0; lv <= maxLevel; lv++) {
    const names = groups[lv] || []
    names.forEach((name, i) => {
      const n = store.structure.find(s => s.name === name)
      const state = store.nodes[name] || 'default'
      const x = 60 + lv * hGap
      const y = 40 + i * vGap + (maxLevel > 0 ? (maxLevel - names.length) * vGap / 2 : 0)
      const node = graph.addNode({
        id: name, x, y,
        width: nodeW, height: nodeH,
        shape: 'rect',
        label: nodeLabel(n || {name}),
        attrs: {
          body: {
            rx: 8, ry: 8,
            fill: state === 'running' ? 'rgba(59,130,246,0.15)' : 'var(--c-card-bg)',
            stroke: stateColor(state), strokeWidth: state === 'running' ? 3 : 2,
            class: state === 'running' ? 'dag-node-pulse' : '',
          },
          label: { fill: 'var(--c-text)', fontSize: 10, fontWeight: 600 },
        },
        ports: {
          groups: {
            top: { position: 'top', attrs: { circle: { r: 3, fill: '#6b7280' } } },
            bottom: { position: 'bottom', attrs: { circle: { r: 3, fill: '#6b7280' } } },
          },
          items: [{ group: 'top' }, { group: 'bottom' }],
        },
        data: { node_name: name },
      })
      nodeMap[name] = node
    })
  }

  // Edges
  for (const n of store.structure) {
    for (const dep of n.deps) {
      if (nodeMap[dep] && nodeMap[n.name]) {
        const fromState = store.nodes[dep] || 'default'
        const toState = store.nodes[n.name] || 'default'
        const edgeState = (fromState === 'success' && toState === 'success') ? 'success'
          : (fromState === 'failed' || toState === 'failed') ? 'failed'
          : (toState === 'running') ? 'running'
          : 'default'
        graph.addEdge({
          source: { cell: dep, port: 'bottom' },
          target: { cell: n.name, port: 'top' },
          attrs: {
            line: {
              stroke: stateColor(edgeState), strokeWidth: 2,
              strokeDasharray: edgeState === 'running' ? '6,4' : '',
              targetMarker: { name: 'block', width: 8, height: 6, fill: stateColor(edgeState) },
            },
          },
        })
      }
    }
  }

  // Click node
  graph.on('node:click', ({ node }) => {
    const name = node.getData()?.node_name
    if (!name) return
    if (selectedNode.value?.name === name) {
      selectedNode.value = null; nodeSubSteps.value = []; return
    }
    const state = store.nodes[name] || 'default'
    selectedNode.value = { name, label: nodeLabel({name}), state }
    axios.get(window.location.origin + '/api/dag/node-types/' + name).then(r => {
      nodeSubSteps.value = r.data.sub_steps || []
      nodeSubComplete.value = state === 'success' ? nodeSubSteps.value.length : 0
    }).catch(() => { nodeSubSteps.value = [] })
  })
}

// WS 驱动节点状态更新
function updateNodeStates() {
  if (!graph) return
  for (const [name, state] of Object.entries(store.nodes)) {
    const cell = graph.getCellById(name)
    if (!cell || !cell.isNode()) continue
    cell.setAttrs({
      body: {
        fill: state === 'running' ? 'rgba(59,130,246,0.15)' : 'var(--c-card-bg)',
        stroke: stateColor(state), strokeWidth: state === 'running' ? 3 : 2,
        class: state === 'running' ? 'dag-node-pulse' : '',
      },
    })
    // Update edges
    const incoming = graph.getIncomingEdges(name)
    for (const e of incoming) {
      const srcName = e.getSourceCellId()
      const fromState = store.nodes[srcName] || 'default'
      const edgeState = (fromState === 'success' && state === 'success') ? 'success'
        : (fromState === 'failed' || state === 'failed') ? 'failed'
        : (state === 'running') ? 'running' : 'default'
      e.setAttrs({
        line: {
          stroke: stateColor(edgeState),
          strokeDasharray: edgeState === 'running' ? '6,4' : '',
          targetMarker: { fill: stateColor(edgeState) },
        },
      })
    }
  }
}

watch(() => store.nodes, () => { nextTick(updateNodeStates) }, { deep: true })
watch(() => store.structure, () => { nextTick(renderGraph) }, { deep: true })

async function terminateTask() {
  const rid = store.currentRunId
  if (!rid || !confirm('确定终止任务 #' + rid + '？')) return
  try { await axios.post(window.location.origin + '/api/dag_terminate', {run_id: rid}) } catch(e) {}
}

onMounted(async () => {
  await store.loadStructure()
  if (store.structure.length === 0) {
    await new Promise(r => setTimeout(r, 1000))
    await store.loadStructure()
  }
  await nextTick()
  renderGraph()
  store.initWs()
  connectWebSocket()
})
</script>

<style>
.dag-node-pulse {
  animation: dagPulse 1.5s ease-in-out infinite;
}
@keyframes dagPulse {
  0% { filter: drop-shadow(0 0 2px rgba(59,130,246,0.3)); }
  50% { filter: drop-shadow(0 0 8px rgba(59,130,246,0.6)); }
  100% { filter: drop-shadow(0 0 2px rgba(59,130,246,0.3)); }
}
</style>
