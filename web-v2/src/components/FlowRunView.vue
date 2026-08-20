<template>
<div style="padding:16px;max-width:100%;margin:0 auto;height:calc(100vh - 100px);display:flex;flex-direction:column">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;flex-shrink:0">
    <div style="display:flex;align-items:center;gap:10px">
      <n-button size="small" quaternary @click="$emit('back')">← 返回</n-button>
      <span style="font-size:17px;font-weight:700;color:var(--c-text)">查看: {{ flowName }}</span>
    </div>
    <div style="display:flex;gap:6px;align-items:center">
      <span v-if="taskInfo" :style="{fontSize:'12px',color:taskInfo.status==='running'?'#3b82f6':taskInfo.status==='completed'?'#10b981':taskInfo.status==='failed'?'#ef4444':'var(--c-text-dim)'}">
        {{ taskInfo.status==='running'?'⟳ '+(taskInfo.progress_pct||0)+'%':taskInfo.status==='completed'?'✅ 完成':taskInfo.status==='failed'?'❌ 失败':'空闲' }}
      </span>
      <n-button v-if="!taskInfo || taskInfo.status!=='running'" size="small" type="primary" @click="doExecute">▶ 执行</n-button>
      <n-button size="small" @click="goLogs">📋 日志</n-button>
    </div>
  </div>

  <div v-if="taskInfo" style="padding:4px 0;margin-bottom:6px;flex-shrink:0">
    <div style="height:4px;background:var(--c-border);border-radius:2px;overflow:hidden">
      <div :style="{height:'100%',width:(taskInfo.progress_pct||0)+'%',background:taskInfo.status==='failed'?'#ef4444':'#3b82f6',transition:'width 0.3s',borderRadius:'2px'}"></div>
    </div>
  </div>

  <div style="flex:1;min-height:0;display:flex;gap:0;border:1px solid var(--c-border);border-radius:8px;overflow:hidden">
    <div style="flex:1;min-width:0;position:relative">
      <VueFlow v-model:nodes="nodes" v-model:edges="edges" id="flow-run-view"
        :node-types="customNodeTypes"
        :default-viewport="{x:0,y:0,zoom:1}"
        :nodes-draggable="false" :nodes-connectable="false"
        :pan-on-drag="true" :zoom-on-scroll="true"
        @node-click="onNodeClick" @pane-click="selectedNode=null">
        <Background variant="dots" :gap="20" />
        <Controls position="bottom-right" />
      </VueFlow>
    </div>

    <div v-if="selectedNode" style="width:260px;flex-shrink:0;background:var(--c-card-bg);border-left:1px solid var(--c-border);padding:14px;overflow-y:auto;font-size:12px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <span style="font-weight:700;color:var(--c-text)">{{ selectedNode.data?.label || selectedNode.id }}</span>
        <span style="cursor:pointer;font-size:16px;color:var(--c-text-dim)" @click="selectedNode=null">✕</span>
      </div>
      <div v-if="selectedNodeInfo" style="display:flex;flex-direction:column;gap:6px">
        <div style="font-size:10px;color:var(--c-text-dim)">
          <span :style="{color:selectedNodeInfo.status==='success'?'#10b981':selectedNodeInfo.status==='running'?'#3b82f6':selectedNodeInfo.status==='failed'?'#ef4444':'#94a3b8'}">
            {{ selectedNodeInfo.status==='success'?'✅':selectedNodeInfo.status==='running'?'⟳':selectedNodeInfo.status==='failed'?'❌':'◻' }} {{ selectedNodeInfo.status }}
          </span>
        </div>
        <div v-if="selectedNodeInfo.started_at" style="font-size:10px;color:var(--c-text-faint)">▶ {{ selectedNodeInfo.started_at }}</div>
        <div v-if="selectedNodeInfo.finished_at" style="font-size:10px;color:var(--c-text-faint)">🏁 {{ selectedNodeInfo.finished_at }}</div>
        <div v-if="selectedNodeInfo.rows !== undefined && selectedNodeInfo.rows !== null" style="font-size:10px;color:var(--c-text-dim)">📊 行数: {{ selectedNodeInfo.rows }}</div>
        <div v-if="selectedNodeInfo.detail" style="font-size:10px;color:var(--c-text-dim)">📝 {{ selectedNodeInfo.detail }}</div>
        <div v-if="selectedNodeInfo.error" style="font-size:10px;color:#ef4444">❌ {{ selectedNodeInfo.error }}</div>
      </div>
      <div v-else style="font-size:11px;color:var(--c-text-faint)">暂无执行数据</div>
    </div>
  </div>
</div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed, markRaw, nextTick } from 'vue'
import { NButton, useMessage } from 'naive-ui'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import axios from 'axios'
import DagNode from './DagNode.vue'

const props = defineProps({ flowId: [Number, String] })
defineEmits(['back'])
const message = useMessage()
const API = window.location.origin
const customNodeTypes = markRaw({ 'dag-node': DagNode })

const flowName = ref('')
const nodes = ref([])
const edges = ref([])
const taskInfo = ref(null)
const selectedNode = ref(null)
let wsHandler = null
const { fitView } = useVueFlow('flow-run-view')

function makeVfNode(id, label, x, y) {
  return { id, type: 'dag-node', position: { x: x ?? 100, y: y ?? 100 }, data: { label: label || id } }
}

const selectedNodeInfo = computed(() => {
  if (!selectedNode.value || !taskInfo.value) return null
  return taskInfo.value.nodes?.find(n => n.node_name === selectedNode.value.id) || null
})

async function loadFlow() {
  try {
    const r = await axios.get(API + `/api/dag/flows/${props.flowId}`)
    const d = r.data
    flowName.value = d.flow_name
    const ns = (d.nodes || []).map(n => makeVfNode(n.node_name, n.node_name, n.position?.x, n.position?.y))
    const es = []
    for (const n of d.nodes || []) {
      for (const dep of n.deps || []) {
        es.push({ id: dep+'->'+n.node_name, source: dep, target: n.node_name, type: 'default', style: { stroke: 'var(--c-text-dim)', strokeWidth: 2 } })
      }
    }
    nodes.value = ns; edges.value = es
    await nextTick(); fitView({ padding: 0.2, maxZoom: 1 })
  } catch(e) { console.error(e) }
}

function applyTaskStatus(task) {
  taskInfo.value = task
  if (!task.nodes) return
  for (const n of task.nodes) {
    const vn = nodes.value.find(v => v.id === n.node_name)
    if (vn) vn.data = { ...vn.data, status: n.status }
  }
}

async function loadTaskStatus() {
  try {
    const r = await axios.get(API + `/api/dag/flows/${props.flowId}/task-status`)
    if (r.data.has_task) applyTaskStatus(r.data)
  } catch(e) {}
}

function onNodeClick({ node }) { selectedNode.value = node }

async function doExecute() {
  try {
    const r = await axios.post(API + `/api/dag/flows/${props.flowId}/execute`, {})
    setTimeout(loadTaskStatus, 1500)
    setTimeout(loadTaskStatus, 4000)
  } catch(e) { message.error(e.response?.data?.detail || '执行失败') }
}

async function goLogs() {
  const { useNavStore } = await import('../stores/nav')
  const nav = useNavStore()
  nav.flowId = parseInt(props.flowId)
  nav.tab = 'q'
}

function onWsMessage(data) {
  if (data.type !== 'task_progress') return
  if (data.flow_id !== parseInt(props.flowId)) return
  applyTaskStatus(data)
}

onMounted(async () => {
  await loadFlow()
  await loadTaskStatus()
  const { addWsListener } = await import('../utils/ws.js')
  wsHandler = addWsListener(onWsMessage)
})

onUnmounted(() => {
  if (wsHandler) wsHandler()
})
</script>