<template>
<div style="padding:16px;max-width:100%;margin:0 auto;height:calc(100vh - 100px);display:flex;flex-direction:column">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;flex-shrink:0">
    <div style="display:flex;align-items:center;gap:10px">
      <n-button v-if="editMode" size="small" quaternary @click="doExitEdit">← 返回</n-button>
      <template v-if="editMode">
        <span v-if="!editingName" style="font-size:17px;font-weight:700;color:var(--c-text)">{{ '编辑 ' + (flowName || '新流程') }}</span>
        <n-input v-else v-model:value="flowName" size="small" style="width:180px" @blur="editingName=false" @keyup.enter="editingName=false" ref="nameInputRef" />
        <n-button v-if="!editingName" size="tiny" quaternary @click="startEditName" style="font-size:12px">✎</n-button>
      </template>
      <span v-else style="font-size:17px;font-weight:700;color:var(--c-text)">DAG 流程编排</span>
    </div>
    <div style="display:flex;gap:6px">
      <n-button v-if="editMode && flowStatus==='draft'" size="small" type="success" @click="doPublish" :loading="saving">🚀 发布</n-button>
      <n-button v-if="editMode && flowStatus==='published'" size="small" type="warning" @click="doUnpublish" :loading="saving">⬇ 下线</n-button>
      <n-button v-if="editMode && flowStatus==='published'" size="small" type="primary" @click="showExecModal=true">▶ 执行</n-button>
      <n-button v-if="editMode" size="small" @click="autoLayout">🔀 自动布局</n-button>
      <n-button v-if="editMode" size="small" @click="doValidate" :loading="saving">🔍 校验</n-button>
      <n-button v-if="editMode" size="small" type="primary" @click="doSave" :loading="saving">💾 保存</n-button>
      <n-button v-if="!editMode" size="small" type="primary" @click="nav.showFlowEditor('new')">+ 新建</n-button>
    </div>
  </div>

  <!-- 属性栏 (编辑模式 - 只保留简洁校验+警告) -->
  <div v-if="editMode && validation" :style="{flexShrink:0,marginBottom:'6px',padding:'6px 10px',borderRadius:'6px',fontSize:'11px',background:validation.ok?'rgba(16,185,129,.08)':'rgba(239,68,68,.08)',border:'1px solid '+(validation.ok?'rgba(16,185,129,.2)':'rgba(239,68,68,.2)')}">
    <span v-if="validation.ok" style="color:#10b981">✅ 校验通过</span>
    <span v-else v-for="(e,i) in validation.errors" :key="i" style="color:#ef4444;margin-right:12px">❌ {{ e }}</span>
  </div>

  <!-- 已发布警告 -->
  <div v-if="editMode && flowStatus==='published'" style="padding:6px 12px;margin-bottom:6px;background:rgba(245,158,11,0.1);border:1px solid rgba(245,158,11,0.3);border-radius:6px;font-size:11px;color:#f59e0b;flex-shrink:0">⚠ 此流程已发布，修改后将影响线上执行</div>

  <!-- Editor Area -->
  <div v-if="editMode" style="display:flex;flex:1;min-height:0;gap:0;border:1px solid var(--c-border);border-radius:8px;overflow:hidden">
    <!-- Node Palette -->
    <div style="width:160px;flex-shrink:0;background:var(--c-card-bg);border-right:1px solid var(--c-border);padding:10px;overflow-y:auto">
      <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:8px">节点类型</div>
      <div v-for="nt in nodeTypes.filter(t => t.node_name !== 'cron')" :key="nt.name"
        :style="{padding:'8px 10px',marginBottom:'4px',borderRadius:'6px',border:'1px solid var(--c-border)',cursor:'pointer',fontSize:'11px',background:'var(--c-bg)',color:'var(--c-text)'}"
        @click="addNode(nt)">
        {{ nt.label || nt.name }}
      </div>
    </div>
    <!-- X6 Canvas -->
    <div ref="canvasRef" style="flex:1;min-width:0;position:relative">
      <div style="position:absolute;bottom:10px;right:10px;z-index:10;font-size:10px;color:var(--c-text-faint);background:var(--c-card-bg);padding:4px 10px;border-radius:12px;border:1px solid var(--c-border);opacity:.7">
        Shift+拖拽=平移 · Ctrl+滚轮=缩放 · Delete=删除 · 双击节点=设置/详情
      </div>
    </div>

    <!-- 统一右侧边栏：流程属性 / 节点详情 -->
    <div v-if="sidebarMode" style="width:260px;flex-shrink:0;background:var(--c-card-bg);border-left:1px solid var(--c-border);padding:14px;overflow-y:auto;font-size:12px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <span style="font-weight:700;color:var(--c-text)">{{ sidebarMode==='flow' ? '⚙️ 流程属性' : (nodeDetail?.label || nodeDetail?.node_name || '') }}</span>
        <span style="cursor:pointer;font-size:16px;color:var(--c-text-dim)" @click="sidebarMode=null">✕</span>
      </div>
      <!-- 流程属性 -->
      <template v-if="sidebarMode==='flow'">
        <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:4px">📋 流程名</div>
        <n-input v-model:value="flowName" size="small" placeholder="流程名称" />
        <div style="margin-top:12px;font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:4px">⏰ 定时执行 (Cron)</div>
        <n-tabs v-model:value="cronTab" type="segment" size="small" @update:value="onCronTab">
          <n-tab-pane name="day" tab="每天" />
          <n-tab-pane name="week" tab="每周" />
          <n-tab-pane name="month" tab="每月" />
          <n-tab-pane name="custom" tab="自定义" />
        </n-tabs>
        <div style="margin-top:6px">
          <div v-if="cronTab==='day'" style="display:flex;gap:4px;align-items:center;flex-wrap:wrap">
            <n-select v-model:value="cronHour" :options="hourOpts" size="tiny" style="width:60px" />
            <span>:</span>
            <n-select v-model:value="cronMin" :options="minOpts" size="tiny" style="width:60px" />
          </div>
          <div v-if="cronTab==='week'">
            <div style="display:flex;gap:4px;align-items:center;margin-bottom:4px">
              <n-select v-model:value="cronWeekDay" :options="weekOpts" size="tiny" style="width:70px" />
              <n-select v-model:value="cronHour" :options="hourOpts" size="tiny" style="width:60px" />
              <span>:</span>
              <n-select v-model:value="cronMin" :options="minOpts" size="tiny" style="width:60px" />
            </div>
          </div>
          <div v-if="cronTab==='month'" style="display:flex;gap:4px;align-items:center">
            <n-select v-model:value="cronMonthDay" :options="monthDayOpts" size="tiny" style="width:60px" />
            <n-select v-model:value="cronHour" :options="hourOpts" size="tiny" style="width:60px" />
            <span>:</span>
            <n-select v-model:value="cronMin" :options="minOpts" size="tiny" style="width:60px" />
          </div>
          <div v-if="cronTab==='custom'">
            <n-input v-model:value="flowCron" size="tiny" placeholder="0 8 * * 1-5" />
          </div>
        </div>
        <div style="margin-top:6px;font-size:10px;color:var(--c-text-faint)">当前: {{ flowCron || '未设置' }}</div>
      </template>
      <!-- 节点详情 -->
      <template v-else-if="sidebarMode==='node' && nodeDetail">
        <n-tag size="tiny" :type="nodeDetail.has_function?'success':'default'" style="margin-bottom:8px">{{ nodeDetail.has_function ? '✅ 已注册' : '⏸ 未注册' }}</n-tag>
        <div v-if="nodeDetail.sub_steps?.length">
          <div style="color:var(--c-text-dim);margin-bottom:4px">内部步骤</div>
          <div v-for="(s,i) in nodeDetail.sub_steps" :key="i" style="padding:5px 0;border-bottom:1px solid var(--c-border)">
            <div style="font-weight:600;color:var(--c-text)">{{ i+1 }}. {{ s.name }}</div>
            <div style="font-size:10px;color:var(--c-text-dim)">{{ s.desc }}</div>
          </div>
        </div>
      </template>
    </div>
  </div>

  <!-- Flow List -->
  <div v-else style="flex:1;overflow-y:auto">
    <n-data-table :columns="flowCols" :data="flows" size="small" :loading="loading" />
    <n-empty v-if="!loading && !flows.length" description="暂无流程，点击「+ 新建」创建" style="padding:40px" />
  </div>

  <!-- 执行弹窗 -->
  <n-modal v-model:show="showExecModal" preset="card" :title="'▶ 执行「' + (flowName || selectedFlow) + '」'" style="width:360px;max-width:92vw">
    <n-space vertical>
      <div style="font-size:12px;color:var(--c-text-dim)">选择执行日期（默认今天）</div>
      <n-date-picker v-model:value="execDate" type="date" size="small" style="width:100%" />
      <div v-if="execResult" style="margin-top:8px;padding:8px;background:var(--c-card-bg);border-radius:6px;font-size:11px">
        <div v-if="execResult.ok" style="color:#10b981">✅ 已触发 — {{ execResult.run_id }}</div>
        <div v-else style="color:#ef4444">❌ {{ execResult.error }}</div>
      </div>
    </n-space>
    <template #footer>
      <n-button @click="showExecModal=false">取消</n-button>
      <n-button type="primary" @click="doExecute" :loading="execLoading">执行</n-button>
    </template>
  </n-modal>

  <!-- 保存/发布确认弹窗 -->
  <n-modal v-model:show="showConfirmModal" preset="card" :title="confirmTitle" style="width:400px;max-width:92vw">
    <n-space vertical>
      <div style="font-size:12px;color:var(--c-text-dim)">{{ confirmMsg }}</div>
      <n-input v-model:value="confirmChangelog" type="textarea" size="small" placeholder="变更说明（可选）" :autosize="{minRows:2,maxRows:4}" />
    </n-space>
    <template #footer>
      <n-button @click="showConfirmModal=false">取消</n-button>
      <n-button type="primary" @click="confirmAction" :loading="saving">确认</n-button>
    </template>
  </n-modal>
</div>
</template>

<script setup>
import { ref, onMounted, nextTick, h, watch } from 'vue'
import { NButton, NSelect, NInput, NDataTable, NTag, NModal, NSpace, NDatePicker, NPopover, NTabs, NTabPane, NEmpty } from 'naive-ui'
import { useMessage } from 'naive-ui'
import { Graph } from '@antv/x6'
import axios from 'axios'
import { useNavStore } from '../stores/nav'
const nav = useNavStore()
const message = useMessage()

const props = defineProps({ flowId: [Number, String] })
const emit = defineEmits(['back'])

const API = window.location.origin
const loading = ref(false)
const flows = ref([])
const selectedFlow = ref(null)
const editMode = ref(false)
const flowName = ref('')
const flowCron = ref('')
const cronTab = ref('day')
const cronMin = ref('0')
const cronHour = ref('8')
const cronWeekDay = ref('1')
const cronMonthDay = ref('1')

const hourOpts = Array.from({length:24},(_,i)=> ({label:String(i).padStart(2,'0')+':00', value:String(i)}))
const minOpts = Array.from({length:60},(_,i)=> ({label:String(i).padStart(2,'0'), value:String(i)}))
const weekOpts = [
  {label:'周一',value:'1'},{label:'周二',value:'2'},{label:'周三',value:'3'},
  {label:'周四',value:'4'},{label:'周五',value:'5'},{label:'周六',value:'6'},{label:'周日',value:'0'},
]
const monthDayOpts = Array.from({length:28},(_,i)=> ({label:(i+1)+'号', value:String(i+1)}))
const quickWeekDays = [
  {label:'周一',value:'1'},{label:'周二',value:'2'},{label:'周三',value:'3'},
  {label:'周四',value:'4'},{label:'周五',value:'5'},{label:'六日',value:'6,0'},
]

function applyCron() {
  if (cronTab.value === 'day') flowCron.value = `${cronMin.value} ${cronHour.value} * * *`
  else if (cronTab.value === 'week') flowCron.value = `${cronMin.value} ${cronHour.value} * * ${cronWeekDay.value}`
  else if (cronTab.value === 'month') flowCron.value = `${cronMin.value} ${cronHour.value} ${cronMonthDay.value} * *`
}

function onCronTab() { applyCron() }
function applyCustom() {} // flowCron already bound
const validation = ref(null)
const saving = ref(false)
const flowStatus = ref('draft')
const canvasRef = ref(null)

let graph = null
const nodeTypes = ref([])
const showNodeDetail = ref(false)
const nodeDetail = ref(null)
const showExecModal = ref(false)
const execDate = ref(null)
const execLoading = ref(false)
const execResult = ref(null)
const changeLogInput = ref('')
const editingName = ref(false)
const nameInputRef = ref(null)
const sidebarMode = ref(null)  // null | 'flow' | 'node'
const showConfirmModal = ref(false)
const confirmTitle = ref('')
const confirmMsg = ref('')
const confirmChangelog = ref('')
const confirmCallback = ref(null)

function startEditName() {
  editingName.value = true
  nextTick(() => { nameInputRef.value?.focus() })
}

const flowOptions = ref([])
function cronReadable(c) {
  if (!c) return '未设置'
  const parts = c.split(' ')
  if (parts.length === 6) parts.shift() // 去除秒
  if (parts.length < 5) return c
  const [m, h, dom, mon, dow] = parts
  if (dom === '*' && mon === '*' && dow === '*') return `⏰ ${h.padStart(2,'0')}:${m.padStart(2,'0')} 每天`
  if (dom === '*' && mon === '*' && dow !== '*' && /^\d(,\d)*$/.test(dow.replaceAll('-',''))) {
    const days = {1:'一',2:'二',3:'三',4:'四',5:'五',6:'六',7:'日'}
    const ds = dow.split(',').map(d => days[parseInt(d)]).filter(Boolean).join(',')
    return `⏰ ${h.padStart(2,'0')}:${m.padStart(2,'0')} 周${ds}`
  }
  if (dow === '*' && dom === '*' && mon === '*') return `⏰ ${h.padStart(2,'0')}:${m.padStart(2,'0')}`
  return c
}

const flowCols = [
  { title:'流程名', key:'flow_name', width:150 },
  { title:'状态', key:'status', width:80, render(r){ return r.status==='published'?'✅ 已发布':'📝 '+r.status } },
  { title:'节点', key:'node_count', width:55, align:'center', render(r){ return r.node_count||0 } },
  { title:'Cron', key:'cron_expr', width:140, render(r){ return cronReadable(r.cron_expr) } },
  { title:'操作', key:'actions', width:200, render(r){
    return h('div',{style:{display:'flex',gap:'4px',alignItems:'center'}},[
      h(NButton,{size:'tiny',quaternary:true,onClick:()=>nav.showFlowEditor(r.id)},()=>'✎ 编辑'),
      r.status==='draft' ? h(NButton,{size:'tiny',quaternary:true,type:'success',onClick:()=>doPublishList(r.id)},()=>'▶ 发布') : null,
      r.status==='published' ? h(NButton,{size:'tiny',quaternary:true,type:'warning',onClick:()=>doUnpublishList(r.id)},()=>'⏸ 下线') : null,
      r.status==='published' ? h(NButton,{size:'tiny',quaternary:true,type:'primary',onClick:()=>doExecuteQuick(r.id, r.flow_name)},()=>'⚡ 执行') : null,
    ])
  }},
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
      createEdge() { return { attrs: { line: { stroke: 'var(--c-text-dim)', strokeWidth: 2, targetMarker: { name:'block',width:8,height:6 } } } } },
    },
    selecting: { enabled: true, multiple: false },
    keyboard: { enabled: true },
    history: { enabled: true },
  })

  // Delete key removes selected node (cron 不可删除)
  graph.bindKey('delete', () => {
    const cells = graph.getSelectedCells()
    cells.forEach(c => {
      if (c.getData()?.node_name === 'cron') return
      graph.removeCell(c)
    })
  })

  // 双击 cron 节点 → 流程属性侧边栏（含 Cron 设置）；双击其他 → 节点详情
  graph.on('node:dblclick', ({ node }) => {
    const name = node.getData()?.node_name
    if (!name) return
    if (name === 'cron') { sidebarMode.value = 'flow'; return }
    axios.get(API + `/api/dag/node-types/${name}`).then(r => {
      nodeDetail.value = r.data
      sidebarMode.value = 'node'
    }).catch(() => {})
  })
  // Click canvas → close sidebar
  graph.on('blank:click', () => { sidebarMode.value = null })
}

function makeNode(name, label, x, y) {
  return {
    x: x || 100, y: y || 100,
    width: 120, height: 40,
    shape: 'rect',
    label: label || name,
    data: { node_name: name },
    attrs: {
      body: { rx: 8, ry: 8, fill: 'var(--c-card-bg)', stroke: 'var(--c-border)', strokeWidth: 2 },
      label: { fill: 'var(--c-text)', fontSize: 11, fontWeight: 600 },
    },
    ports: {
      groups: {
        top: { position: 'top', attrs: { circle: { r: 5, magnet: true, fill: '#60a5fa', stroke: 'var(--c-card-bg)', strokeWidth: 2 } } },
        bottom: { position: 'bottom', attrs: { circle: { r: 5, magnet: true, fill: '#f59e0b', stroke: 'var(--c-card-bg)', strokeWidth: 2 } } },
      },
      items: [{ group: 'top' }, { group: 'bottom' }],
    },
  }
}

function labelFor(name) {
  const found = nodeTypes.value.find(nt => nt.node_name === name)
  return found?.label || name
}

function parseCron(cron) {
  if (!cron) return
  const parts = cron.split(' ')
  if (parts.length === 6) parts.shift()
  if (parts.length < 5) return
  const [m, h, dom, mon, dow] = parts
  cronMin.value = m
  cronHour.value = h
  if (dom === '*' && mon === '*' && dow === '*') { cronTab.value = 'day' }
  else if (dom === '*' && mon === '*' && dow !== '*' && /^\d\d?$/.test(dow)) { cronTab.value = 'week'; cronWeekDay.value = dow }
  else if (dow === '*' && mon === '*' && /^\d\d?$/.test(dom)) { cronTab.value = 'month'; cronMonthDay.value = dom }
  else { cronTab.value = 'custom' }
}

function addNode(nt) {
  if (!graph) return
  const c = graph.getGraphArea().getCenter()
  graph.addNode(makeNode(nt.node_name || nt.name, nt.label, c.x - 60 + Math.random() * 100, c.y - 20 + Math.random() * 60))
}

async function openEdit(id) {
  editMode.value = true
  validation.value = null
  loadNodeTypes()  // 编辑时按需加载节点类型
  try {
    const r = await axios.get(API + `/api/dag/flows/${id}`)
    const d = r.data
    flowName.value = d.flow_name
    flowCron.value = d.cron_expr || ''
    parseCron(d.cron_expr)
    flowStatus.value = d.status || 'draft'
    selectedFlow.value = id
    await nextTick()
    initGraph()
    // Load existing nodes with saved positions
    const nodeMap = {}
    for (const n of d.nodes || []) {
      const pos = n.position || {}
      const node = graph.addNode(makeNode(n.node_name, labelFor(n.node_name), pos.x || 100 + Math.random()*400, pos.y || 50 + Math.random()*300))
      nodeMap[n.node_name] = node
    }
    // Draw edges
    for (const n of d.nodes || []) {
      for (const dep of n.deps || []) {
        if (nodeMap[dep] && nodeMap[n.node_name]) {
          graph.addEdge({
            source: { cell: nodeMap[dep].id, port: 'bottom' },
            target: { cell: nodeMap[n.node_name].id, port: 'top' },
            attrs: { line: { stroke: 'var(--c-text-dim)', strokeWidth: 2, targetMarker: { name:'block',width:8,height:6 } } },
          })
        }
      }
    }
    // auto-add cron if missing
    if (!nodeMap['cron']) {
      const cronNode = graph.addNode(makeNode('cron', 'cron', 100, 80))
      nodeMap['cron'] = cronNode
    }
  } catch(e) { console.error(e) }
}

function getFlowData() {
  if (!graph) return { nodes: [], edges: [] }
  const nodes = graph.getNodes().map(n => {
    const incoming = graph.getIncomingEdges(n.id) || []
    const pos = n.getPosition()
    const deps = incoming.map(e => graph.getCell(e.getSourceCellId())?.getData()?.node_name).filter(Boolean)
    return {
      node_name: n.getData()?.node_name || '',
      deps: [...new Set(deps)],
      position: { x: Math.round(pos.x), y: Math.round(pos.y) },
    }
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

function doSave() {
  confirmTitle.value = '💾 保存流程'
  confirmMsg.value = `即将保存「${flowName.value || '新流程'}」`
  confirmChangelog.value = '手动编辑'
  confirmCallback.value = async () => {
    saving.value = true
    const data = getFlowData()
    try {
      if (selectedFlow.value) {
        await axios.put(API + `/api/dag/flows/${selectedFlow.value}`, {
          nodes: data.nodes,
          cron_expr: flowCron.value,
          change_log: confirmChangelog.value.trim() || '手动编辑',
        })
      } else {
        await axios.post(API + '/api/dag/flows', {
          flow_name: flowName.value,
          nodes: data.nodes,
          cron_expr: flowCron.value,
        })
      }
      validation.value = { ok: true, errors: [] }
      showConfirmModal.value = false
      message.success('保存成功')
      loadFlows()
    } catch(e) {
      message.error(e.response?.data?.detail || '保存失败')
    }
    saving.value = false
  }
  showConfirmModal.value = true
}

async function doPublish() {
  confirmTitle.value = '🚀 发布流程'
  confirmMsg.value = `发布「${flowName.value}」，定时任务将生效`
  confirmChangelog.value = ''
  confirmCallback.value = async () => {
    if (!selectedFlow.value) return
    saving.value = true
    try {
      await axios.post(API + `/api/dag/flows/${selectedFlow.value}/publish`)
      flowStatus.value = 'published'
      showConfirmModal.value = false
      loadFlows()
    } catch(e) { message.error(e.response?.data?.detail || '发布失败') }
    saving.value = false
  }
  showConfirmModal.value = true
}

function confirmAction() {
  if (confirmCallback.value) confirmCallback.value()
}

async function doPublishList(id) {
  try { await axios.post(API + `/api/dag/flows/${id}/publish`); loadFlows() } catch(e) {}
}
async function doUnpublishList(id) {
  try { await axios.post(API + `/api/dag/flows/${id}/unpublish`); loadFlows() } catch(e) {}
}

async function doUnpublish() {
  if (!selectedFlow.value) return
  saving.value = true
  try {
    await axios.post(API + `/api/dag/flows/${selectedFlow.value}/unpublish`)
    flowStatus.value = 'draft'
    loadFlows()
  } catch(e) { alert(e.response?.data?.detail || '下线失败') }
  saving.value = false
}

async function doExecute() {
  if (!selectedFlow.value) return
  execLoading.value = true; execResult.value = null
  try {
    const fd = (d) => {
      if (!d) return ''
      const dt = new Date(d); return dt.getFullYear()+'-'+String(dt.getMonth()+1).padStart(2,'0')+'-'+String(dt.getDate()).padStart(2,'0')
    }
    const r = await axios.post(API + `/api/dag/flows/${selectedFlow.value}/execute`, {
      trade_date: fd(execDate.value)
    })
    execResult.value = r.data
  } catch(e) {
    execResult.value = { ok: false, error: e.response?.data?.detail || e.message }
  }
  execLoading.value = false
}

async function doExecuteQuick(id, name) {
  if (!confirm(`⚡ 立即执行「${name}」？`)) return
  try {
    const r = await axios.post(API + `/api/dag/flows/${id}/execute`, {})
    alert(`✅ 已触发 — ${r.data.run_id}`)
  } catch(e) {
    alert(e.response?.data?.detail || '执行失败')
  }
}

function autoLayout() {
  if (!graph) return
  const nodes = graph.getNodes()
  const edges = graph.getEdges()
  // 拓扑排序：统计入度
  const inDeg = {}
  const adj = {}
  nodes.forEach(n => {
    const name = n.getData()?.node_name
    inDeg[name] = 0; adj[name] = []
  })
  edges.forEach(e => {
    const src = graph.getCell(e.getSourceCellId())?.getData()?.node_name
    const tgt = graph.getCell(e.getTargetCellId())?.getData()?.node_name
    if (src && tgt && inDeg[tgt] !== undefined) { inDeg[tgt]++; adj[src].push(tgt) }
  })
  // BFS 分层
  const layers = []
  const queue = Object.keys(inDeg).filter(k => inDeg[k] === 0).map(k => ({ name: k, depth: 0 }))
  const visited = new Set()
  while (queue.length) {
    const cur = queue.shift()
    if (visited.has(cur.name)) continue
    visited.add(cur.name)
    if (!layers[cur.depth]) layers[cur.depth] = []
    layers[cur.depth].push(cur.name)
    ;(adj[cur.name] || []).forEach(next => {
      if (!visited.has(next)) queue.push({ name: next, depth: cur.depth + 1 })
    })
  }
  // 孤立节点放第0层
  nodes.forEach(n => {
    const name = n.getData()?.node_name
    if (!visited.has(name)) {
      if (!layers[0]) layers[0] = []
      layers[0].push(name)
      visited.add(name)
    }
  })
  // 按层排列
  const nodeMap = {}
  nodes.forEach(n => { nodeMap[n.getData()?.node_name] = n })
  const startX = 80, startY = 60, gapX = 160, gapY = 80
  layers.forEach((layer, li) => {
    const totalW = layer.length * gapX
    const offsetX = startX - totalW / 2 + gapX / 2
    layer.forEach((name, ni) => {
      const n = nodeMap[name]
      if (n) n.setPosition(offsetX + ni * gapX + graph.getGraphArea().width / 2, startY + li * gapY)
    })
  })
}

onMounted(() => {
  loadFlows()
  // 如果 URL 带有 flowId，自动打开编辑
  if (props.flowId === 'new') {
    createNew()
  } else if (props.flowId > 0) {
    openEdit(props.flowId)
  }
})

// 响应从列表点击编辑/新建的 prop 变化（组件不重新挂载）
watch(() => props.flowId, (newId) => {
  if (newId === 'new') {
    createNew()
  } else if (newId > 0) {
    openEdit(newId)
  }
})

// 关闭执行弹窗时清空结果
watch(showExecModal, (v) => { if (!v) execResult.value = null })

// 退出编辑模式时检测未保存的变更
function doExitEdit() {
  if (graph && graph.getNodes().length > 0 && !confirm('有未保存的修改，确定退出？')) return
  editMode.value = false
  validation.value = null
  emit('back')
}

// 新建流程：先加载节点类型，再初始化画布，默认添加 cron 节点
async function createNew() {
  editMode.value = true
  flowName.value = ''
  flowCron.value = ''
  validation.value = null
  await loadNodeTypes()
  initGraph()
  // 默认添加 cron 节点（左上角固定位置）
  graph.addNode(makeNode('cron', 'cron', 100, 80))
}
</script>