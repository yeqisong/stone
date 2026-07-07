<template>
<div style="padding:16px;max-width:100%;margin:0 auto;height:calc(100vh - 100px);display:flex;flex-direction:column">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;flex-shrink:0">
    <div style="display:flex;align-items:center;gap:10px">
      <span style="font-size:17px;font-weight:700;color:var(--c-text)">DAG 流程编排</span>
      <n-select v-if="!editMode" v-model:value="selectedFlow" :options="flowOptions" size="small" style="width:180px" placeholder="选择流程" @update:value="openEdit" />
      <n-input v-if="editMode" v-model:value="flowName" size="small" style="width:180px" placeholder="流程名称" />
      <n-input v-if="editMode" v-model:value="flowCron" size="small" style="width:140px" placeholder="Cron 表达式" />
    </div>
    <div style="display:flex;gap:6px">
      <n-button v-if="editMode" size="small" @click="doValidate" :loading="saving">🔍 校验</n-button>
      <n-button v-if="editMode" size="small" type="primary" @click="doSave" :loading="saving">💾 保存</n-button>
      <n-button v-if="editMode" size="small" quaternary @click="editMode=false;validation=null">← 返回</n-button>
      <n-button v-if="!editMode" size="small" type="primary" @click="editMode=true;flowName='';flowCron='';validation=null;initGraph()">+ 新建</n-button>
    </div>
  </div>

  <!-- Validation -->
  <div v-if="validation" :style="{flexShrink:0,marginBottom:'8px',padding:'6px 10px',borderRadius:'6px',fontSize:'11px',background:validation.ok?'rgba(16,185,129,.08)':'rgba(239,68,68,.08)',border:'1px solid '+(validation.ok?'rgba(16,185,129,.2)':'rgba(239,68,68,.2)')}">
    <span v-if="validation.ok" style="color:#10b981">✅ 校验通过</span>
    <span v-else v-for="(e,i) in validation.errors" :key="i" style="color:#ef4444;margin-right:12px">❌ {{ e }}</span>
  </div>

  <!-- Editor Area -->
  <div v-if="editMode" style="display:flex;flex:1;min-height:0;gap:0;border:1px solid var(--c-border);border-radius:8px;overflow:hidden">
    <!-- Node Palette -->
    <div style="width:160px;flex-shrink:0;background:var(--c-card-bg);border-right:1px solid var(--c-border);padding:10px;overflow-y:auto">
      <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:8px">节点类型</div>
      <div v-for="nt in nodeTypes" :key="nt.name"
        :style="{padding:'8px 10px',marginBottom:'4px',borderRadius:'6px',border:'1px solid var(--c-border)',cursor:'pointer',fontSize:'11px',background:'var(--c-bg)',color:'var(--c-text)'}"
        @click="addNode(nt)">
        {{ nt.label || nt.name }}
      </div>
    </div>
    <!-- X6 Canvas -->
    <div ref="canvasRef" style="flex:1;min-width:0"></div>
  </div>

  <!-- Flow List -->
  <div v-else style="flex:1;overflow-y:auto">
    <n-data-table :columns="flowCols" :data="flows" size="small" :loading="loading" />
  </div>
</div>
</template>

<script setup>
import { ref, onMounted, nextTick, h } from 'vue'
import { NButton, NSelect, NInput, NDataTable } from 'naive-ui'
import { Graph } from '@antv/x6'
import axios from 'axios'

const API = window.location.origin
const loading = ref(false)
const flows = ref([])
const selectedFlow = ref(null)
const editMode = ref(false)
const flowName = ref('')
const flowCron = ref('')
const validation = ref(null)
const saving = ref(false)
const canvasRef = ref(null)

let graph = null
const nodeTypes = ref([])

const flowOptions = ref([])
const flowCols = [
  { title:'流程名', key:'flow_name', width:160 },
  { title:'状态', key:'status', width:80, render(r){ return r.status==='published'?'✅ 已发布':'📝 '+r.status } },
  { title:'Cron', key:'cron_expr', width:130 },
  { title:'操作', key:'actions', width:100, render(r){ return h(NButton,{size:'tiny',quaternary:true,onClick:()=>openEdit(r.id)},()=>'编辑') } },
]

async function loadFlows() {
  loading.value = true
  try {
    const r = await axios.get(API + '/api/dag/flows')
    flows.value = r.data.items || []
    flowOptions.value = flows.value.map(f => ({ label: f.flow_name, value: f.id }))
  } catch(e) { console.error(e) }
  loading.value = false
}

async function loadNodeTypes() {
  try {
    const r = await axios.get(API + '/api/dag/node-types')
    nodeTypes.value = r.data.items || []
  } catch(e) { console.error(e) }
}

function initGraph() {
  if (graph) { graph.dispose(); graph = null }
  if (!canvasRef.value) return

  graph = new Graph({
    container: canvasRef.value,
    autoResize: true,
    grid: { visible: true, size: 20, args: { color: 'var(--c-border)' } },
    panning: { enabled: true, modifiers: 'shift' },
    mousewheel: { enabled: true, modifiers: ['ctrl','meta'] },
    connecting: { 
      snap: { radius: 20 },
      allowBlank: false,
      connector: { name: 'smooth' },
      createEdge() { return { attrs: { line: { stroke: '#6b7280', strokeWidth: 2, targetMarker: { name:'block',width:8,height:6 } } } } },
    },
    selecting: { enabled: true, multiple: false },
    keyboard: { enabled: true },
    history: { enabled: true },
  })

  // Delete key removes selected node
  graph.bindKey('delete', () => {
    const cells = graph.getSelectedCells()
    cells.forEach(c => graph.removeCell(c))
  })

  // Drop handler
  // Double-click node → show detail
  graph.on('node:dblclick', ({ node }) => {
    const name = node.getData()?.node_name
    if (!name) return
    axios.get(API + `/api/dag/node-types/${name}`).then(r => {
      const d = r.data
      const steps = (d.sub_steps || []).map(s => `${s.name}: ${s.desc}`).join('\n')
      alert(`${d.label || d.node_name}\n\n上游: ${d.deps.join(', ') || '无'}\n\n内部子图:\n${steps || '无子步骤'}`)
    }).catch(() => {})
  })
}

function makeNode(name, label, x, y) {
  return {
    x: x || 100, y: y || 100,
    width: 120, height: 40,
    shape: 'rect',
    label: label || name,
    data: { node_name: name },
    attrs: {
      body: { rx: 8, ry: 8, fill: '#1e293b', stroke: '#475569', strokeWidth: 2 },
      label: { fill: '#e2e8f0', fontSize: 11, fontWeight: 600 },
    },
    ports: {
      groups: {
        top: { position: 'top', attrs: { circle: { r: 5, magnet: true, fill: '#60a5fa', stroke: '#1e293b', strokeWidth: 2 } } },
        bottom: { position: 'bottom', attrs: { circle: { r: 5, magnet: true, fill: '#f59e0b', stroke: '#1e293b', strokeWidth: 2 } } },
      },
      items: [{ group: 'top' }, { group: 'bottom' }],
    },
  }
}

function addNode(nt) {
  if (!graph) return
  const c = graph.getGraphArea().getCenter()
  graph.addNode(makeNode(nt.node_name || nt.name, nt.label, c.x - 60 + Math.random() * 100, c.y - 20 + Math.random() * 60))
}

async function openEdit(id) {
  editMode.value = true
  validation.value = null
  try {
    const r = await axios.get(API + `/api/dag/flows/${id}`)
    const d = r.data
    flowName.value = d.flow_name
    flowCron.value = d.cron_expr || ''
    selectedFlow.value = id
    await nextTick()
    initGraph()
    // Load existing nodes
    const nodeMap = {}
    for (const n of d.nodes || []) {
      const node = graph.addNode(makeNode(n.node_name, n.node_name, 100 + Math.random() * 400, 50 + Math.random() * 300))
      nodeMap[n.node_name] = node
    }
    // Draw edges
    for (const n of d.nodes || []) {
      for (const dep of n.deps || []) {
        if (nodeMap[dep] && nodeMap[n.node_name]) {
          graph.addEdge({
            source: { cell: nodeMap[dep].id, port: 'bottom' },
            target: { cell: nodeMap[n.node_name].id, port: 'top' },
            attrs: { line: { stroke: '#6b7280', strokeWidth: 2, targetMarker: { name:'block',width:8,height:6 } } },
          })
        }
      }
    }
  } catch(e) { console.error(e) }
}

function getFlowData() {
  if (!graph) return { nodes: [], edges: [] }
  const nodes = graph.getNodes().map(n => {
    const incoming = graph.getIncomingEdges(n.id) || []
    const deps = incoming.map(e => graph.getCell(e.getSourceCellId())?.getData()?.node_name).filter(Boolean)
    return { node_name: n.getData()?.node_name || '', deps: [...new Set(deps)] }
  })
  return { nodes }
}

async function doValidate() {
  const data = getFlowData()
  try {
    const r = await axios.post(API + '/api/dag/flows/validate', data)
    validation.value = r.data
  } catch(e) {
    validation.value = { ok: false, errors: [e.response?.data?.detail || e.message] }
  }
}

async function doSave() {
  saving.value = true
  const data = getFlowData()
  try {
    if (selectedFlow.value) {
      await axios.put(API + `/api/dag/flows/${selectedFlow.value}`, {
        nodes: data.nodes,
        cron_expr: flowCron.value,
        change_log: 'X6 编辑器编辑',
      })
    } else {
      await axios.post(API + '/api/dag/flows', {
        flow_name: flowName.value,
        nodes: data.nodes,
        cron_expr: flowCron.value,
      })
    }
    validation.value = { ok: true, errors: [] }
    editMode.value = false
    loadFlows()
  } catch(e) {
    alert(e.response?.data?.detail || '保存失败')
  }
  saving.value = false
}

onMounted(() => {
  loadFlows()
  loadNodeTypes()
})
</script>