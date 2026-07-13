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

  <div v-if="editMode && validation" :style="{flexShrink:0,marginBottom:'6px',padding:'6px 10px',borderRadius:'6px',fontSize:'11px',background:validation.ok?'rgba(16,185,129,.08)':'rgba(239,68,68,.08)',border:'1px solid '+(validation.ok?'rgba(16,185,129,.2)':'rgba(239,68,68,.2)')}">
    <span v-if="validation.ok" style="color:#10b981">✅ 校验通过</span>
    <span v-else v-for="(e,i) in validation.errors" :key="i" style="color:#ef4444;margin-right:12px">❌ {{ e }}</span>
  </div>

  <div v-if="editMode && flowStatus==='published'" style="padding:6px 12px;margin-bottom:6px;background:rgba(245,158,11,0.1);border:1px solid rgba(245,158,11,0.3);border-radius:6px;font-size:11px;color:#f59e0b;flex-shrink:0">⚠ 此流程已发布，修改后将影响线上执行</div>

  <div v-if="editMode" style="display:flex;flex:1;min-height:0;gap:0;border:1px solid var(--c-border);border-radius:8px;overflow:hidden">
    <div style="width:160px;flex-shrink:0;background:var(--c-card-bg);border-right:1px solid var(--c-border);padding:10px;overflow-y:auto">
      <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:8px">节点类型</div>
      <div v-for="nt in nodeTypes.filter(t => t.node_name !== 'cron')" :key="nt.name"
        :style="{padding:'8px 10px',marginBottom:'4px',borderRadius:'6px',border:'1px solid var(--c-border)',cursor:'pointer',fontSize:'11px',background:'var(--c-bg)',color:'var(--c-text)'}"
        @click="addNode(nt)">
        {{ nt.label || nt.name }}
      </div>
    </div>

    <div style="flex:1;min-width:0;position:relative">
      <VueFlow id="dag-flow" v-model:nodes="nodes" v-model:edges="edges" @nodes-change="filterCron"
        :node-types="customNodeTypes"
        :default-viewport="{ x: 0, y: 0, zoom: 1 }"
        :snap-to-grid="true" :snap-grid="[20,20]"
        :delete-key-code="'Backspace'" :multi-selection-key-code="'Shift'"
        :pan-on-drag="true" :zoom-on-scroll="true"
        :edges-updatable="true"
        :connection-line-style="{ stroke: 'var(--c-text-dim)', strokeWidth: 2 }"
        @node-double-click="onNodeDblClick" @pane-click="onPaneClick" @connect="onConnect" @node-drag-stop="onNodeDragStop"
        :default-edge-options="defaultEdgeOpts"
        :only-render-visible-elements="true">
        <Background variant="dots" :gap="20" />
        <Controls position="bottom-right" />
        <div style="position:absolute;bottom:12px;right:52px;z-index:10;font-size:11px;color:var(--c-text-dim);background:var(--c-card-bg);padding:2px 6px;border-radius:4px;border:1px solid var(--c-border);pointer-events:none">
          {{ Math.round(viewport.zoom * 100) }}%
        </div>
      </VueFlow>
    </div>

    <div v-if="sidebarMode" style="width:280px;flex-shrink:0;background:var(--c-card-bg);border-left:1px solid var(--c-border);padding:14px;overflow-y:auto;font-size:12px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <span style="font-weight:700;color:var(--c-text)">{{ sidebarMode==='flow' ? '⚙️ 流程属性' : (nodeDetail?.label || nodeDetail?.node_name || '') }}</span>
        <span style="cursor:pointer;font-size:16px;color:var(--c-text-dim)" @click="sidebarMode=null">✕</span>
      </div>
      <template v-if="sidebarMode==='flow'">
        <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:4px">📋 流程名</div>
        <n-input v-model:value="flowName" size="small" placeholder="流程名称" />
        <div style="margin-top:12px;font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:4px">⏰ 定时执行 (Cron)</div>
        <n-select v-model:value="cronTab" :options="cronTabs" size="small" style="width:100%" @update:value="onCronTab" />
        <div style="margin-top:6px">
          <!-- 每 N 分钟 -->
          <div v-if="cronTab==='min'" style="display:flex;gap:4px;align-items:center">
            <span style="font-size:11px;color:var(--c-text-dim);white-space:nowrap">每</span>
            <n-input-number v-model:value="cronInterval" :min="1" :max="60" size="tiny" style="flex:1;min-width:0" />
            <span style="font-size:11px;color:var(--c-text-dim);white-space:nowrap">分钟</span>
          </div>
          <!-- 每 N 小时 -->
          <div v-if="cronTab==='hour'" style="display:flex;flex-direction:column;gap:4px">
            <div style="display:flex;gap:4px;align-items:center">
              <span style="font-size:11px;color:var(--c-text-dim);white-space:nowrap">每</span>
              <n-input-number v-model:value="cronInterval" :min="1" :max="12" size="tiny" style="flex:1;min-width:0" />
              <span style="font-size:11px;color:var(--c-text-dim);white-space:nowrap">小时</span>
            </div>
            <div style="display:flex;gap:4px;align-items:center">
              <span style="font-size:11px;color:var(--c-text-dim);white-space:nowrap">在第</span>
              <n-select v-model:value="cronMin" :options="minOpts" size="tiny" style="flex:1;min-width:0" />
              <span style="font-size:11px;color:var(--c-text-dim);white-space:nowrap">分</span>
            </div>
          </div>
          <!-- 每天 / 每周 / 每月：n-time-picker 统一时间选择 -->
          <div v-if="['day','week','month'].includes(cronTab)" style="margin-bottom:4px">
            <n-time-picker v-model:value="cronTime" format="HH:mm" size="small" style="width:100%" @update:value="buildCron" />
          </div>
          <!-- 每周（多选） -->
          <div v-if="cronTab==='week'">
            <div style="font-size:10px;color:var(--c-text-dim);margin-bottom:2px">选择工作日</div>
            <div style="display:flex;flex-wrap:wrap;gap:2px">
              <n-tag v-for="d in weekOpts" :key="d.value" size="tiny" :type="cronWeekDays.includes(d.value)?'primary':'default'" :bordered="false" style="cursor:pointer" @click="toggleWeekDay(d.value)">{{ d.label }}</n-tag>
            </div>
          </div>
          <!-- 每月（多选） -->
          <div v-if="cronTab==='month'">
            <div style="font-size:10px;color:var(--c-text-dim);margin-bottom:2px">选择日期</div>
            <n-select v-model:value="cronMonthDays" :options="monthDayOpts" multiple size="tiny" style="width:100%" :max-tag-count="3" />
          </div>
          <!-- 自定义 -->
          <div v-if="cronTab==='custom'"><n-input v-model:value="cronCustomVal" size="small" placeholder="0 8 * * 1-5" @update:value="applyCustom" /></div>
        </div>
        <div v-if="cronError" style="margin-top:4px;font-size:10px;color:#ef4444">{{ cronError }}</div>
        <div v-if="cronPreview && !cronError" style="margin-top:4px;font-size:10px;color:var(--c-text-dim)">💡 {{ cronPreview }}</div>
      </template>
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

  <div v-else style="flex:1;overflow-y:auto">
    <n-data-table :columns="flowCols" :data="flows" size="small" :loading="loading" />
    <n-empty v-if="!loading && !flows.length" description="暂无流程，点击「+ 新建」创建" style="padding:40px" />
  </div>

  <n-modal v-model:show="showExecModal" preset="card" :title="'▶ 执行「' + (flowName || selectedFlow) + '」'" style="width:360px;max-width:92vw">
    <n-space vertical>
      <div style="font-size:12px;color:var(--c-text-dim)">选择执行日期（默认今天）</div>
      <n-date-picker v-model:value="execDate" type="date" size="small" style="width:100%" />
      <div v-if="execResult" style="margin-top:8px;padding:8px;background:var(--c-card-bg);border-radius:6px;font-size:11px">
        <div v-if="execResult.ok" style="color:#10b981">✅ 已触发 — {{ execResult.task_id }}</div>
        <div v-else style="color:#ef4444">❌ {{ execResult.error }}</div>
      </div>
    </n-space>
    <template #footer>
      <n-button @click="showExecModal=false">取消</n-button>
      <n-button type="primary" @click="doExecute" :loading="execLoading">执行</n-button>
    </template>
  </n-modal>

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
import { ref, onMounted, nextTick, h, watch, markRaw, onBeforeUnmount } from 'vue'
import { NButton, NSelect, NInput, NInputNumber, NDataTable, NTag, NModal, NSpace, NDatePicker, NEmpty, NTimePicker } from 'naive-ui'
import { useMessage } from 'naive-ui'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { cronstrue } from 'cronstrue'
import cronValidator from 'cron-validator'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import axios from 'axios'
import { useNavStore } from '../stores/nav'
import DagNode from './DagNode.vue'
import { addWsListener } from '../utils/ws'
const nav = useNavStore()
const message = useMessage()
const customNodeTypes = markRaw({ 'dag-node': DagNode })

const props = defineProps({ flowId: [Number, String] })
const emit = defineEmits(['back', 'show-log'])

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
const cronWeekDays = ref(['1','2','3','4','5'])
const cronMonthDays = ref(['1'])
const cronInterval = ref(5)
const cronCustomVal = ref('')
const cronError = ref('')
const cronPreview = ref('')
const cronTime = ref(makeTimeMs(8, 0))  // n-time-picker 默认 08:00
const cronTabs = [{label:'每分钟',value:'min'},{label:'每小时',value:'hour'},{label:'每天',value:'day'},{label:'每周',value:'week'},{label:'每月',value:'month'},{label:'自定义',value:'custom'}]

function makeTimeMs(h, m) { const d = new Date(); d.setHours(h, m, 0, 0); return d.getTime() }

function toggleWeekDay(v) { const s = new Set(cronWeekDays.value); s.has(v)?s.delete(v):s.add(v); cronWeekDays.value = [...s]; buildCron() }
function toggleMonthDay(v) { const s = new Set(cronMonthDays.value); s.has(v)?s.delete(v):s.add(v); cronMonthDays.value = [...s]; buildCron() }

function buildCron() {
  let expr = ''
  // 从 cronTime 提取小时和分钟
  const d2 = new Date(cronTime.value)
  const h = String(d2.getHours()).padStart(2,'0')
  const m = String(d2.getMinutes()).padStart(2,'0')
  switch (cronTab.value) {
    case 'min': expr = `*/${cronInterval.value} * * * *`; break
    case 'hour': expr = `${cronMin.value} */${cronInterval.value} * * *`; break
    case 'day': expr = `${m} ${h} * * *`; break
    case 'week': { const days = [...cronWeekDays.value].sort((a,b)=>parseInt(a)-parseInt(b)).join(','); expr = `${m} ${h} * * ${days}`; break }
    case 'month': { const days = [...cronMonthDays.value].sort((a,b)=>parseInt(a)-parseInt(b)).join(','); expr = `${m} ${h} ${days} * *`; break }
    case 'custom': expr = cronCustomVal.value; break
  }
  flowCron.value = expr
  try { cronPreview.value = cronstrue.toString(expr, { locale: 'zh_CN' }) } catch { cronPreview.value = '' }
  try { cronError.value = cronValidator(expr) ? '' : '表达式格式有误' } catch { cronError.value = '' }
}
function onCronTab() { buildCron() }
function applyCustom() { buildCron() }

const hourOpts = Array.from({length:24},(_,i)=> ({label:String(i).padStart(2,'0')+':00', value:String(i)}))
const minOpts = Array.from({length:60},(_,i)=> ({label:String(i).padStart(2,'0'), value:String(i)}))
const weekOpts = [
  {label:'周一',value:'1'},{label:'周二',value:'2'},{label:'周三',value:'3'},
  {label:'周四',value:'4'},{label:'周五',value:'5'},{label:'周六',value:'6'},{label:'周日',value:'0'},
]
const monthDayOpts = Array.from({length:28},(_,i)=> ({label:(i+1)+'号', value:String(i+1)}))

// ── Vue Flow ──
const nodes = ref([])
const edges = ref([])
const { screenToFlowCoordinate, getNodes, viewport, fitView } = useVueFlow('dag-flow')

const nodeDefaults = { type: 'default', style: { background: 'var(--c-card-bg)', border: '1px solid var(--c-border)', borderRadius: 8, color: 'var(--c-text)', fontSize: 11, fontWeight: 600, width: 120, padding: '10px 6px', textAlign: 'center' } }
const defaultEdgeOpts = { type: 'default', animated: false, updatable: true, style: { stroke: 'var(--c-text-dim)', strokeWidth: 2 } }

function makeVfNode(id, label, x, y) {
  return { id, type: 'dag-node', position: { x: x ?? 100, y: y ?? 100 }, data: { label: label || id } }
}

function onNodeDblClick({ node }) {
  if (node.id === 'cron') { sidebarMode.value = 'flow'; return }
  axios.get(API + `/api/dag/node-types/${node.id}`).then(r => {
    nodeDetail.value = r.data; sidebarMode.value = 'node'
  }).catch(() => {})
}
function onNodeDragStop({ node }) {
  // 拖拽后自动更新边端点方向（根据相对位置选择最优 Handle）
  edges.value.forEach(edge => {
    if (edge.source !== node.id && edge.target !== node.id) return
    const srcNode = nodes.value.find(n => n.id === edge.source)
    const tgtNode = nodes.value.find(n => n.id === edge.target)
    if (!srcNode || !tgtNode) return
    const dx = tgtNode.position.x - srcNode.position.x
    const dy = tgtNode.position.y - srcNode.position.y
    if (Math.abs(dx) > Math.abs(dy)) {
      edge.sourceHandle = dx > 0 ? 's-right' : 't-left'
      edge.targetHandle = dx > 0 ? 't-left' : 's-right'
    } else {
      edge.sourceHandle = 's-bottom'
      edge.targetHandle = 't-top'
    }
  })
}

function onPaneClick() { sidebarMode.value = null }
function onConnect(conn) {
  // 阻止自环
  if (conn.source === conn.target) return
  // 阻止反向边（避免双向边形成环）
  const reverse = conn.target + '->' + conn.source
  if (edges.value.find(e => e.id === reverse)) return
  const sid = conn.source + '->' + conn.target
  if (!edges.value.find(e => e.id === sid)) {
    edges.value.push({ id: sid, source: conn.source, target: conn.target, ...defaultEdgeOpts })
  }
  dirty.value = true
}

function filterCron(changes) {
  if (loadingFlow.value) return
  const removed = changes.find(c => c.type === 'remove' && c.id === 'cron')
  if (removed) { nodes.value.push({ id: 'cron', type: 'dag-node', position: { x: 100, y: 80 }, data: { label: 'cron' } }); dirty.value = true }
}

const nodeTypes = ref([])
const validation = ref(null)
const saving = ref(false)
const flowStatus = ref('draft')
const nodeDetail = ref(null)
const showExecModal = ref(false)
const execDate = ref(null)
const execLoading = ref(false)
const execResult = ref(null)
const editingName = ref(false)
const nameInputRef = ref(null)
const loadingFlow = ref(false)
const sidebarMode = ref(null)
const dirty = ref(false)
const showConfirmModal = ref(false)
const confirmTitle = ref('')
const confirmMsg = ref('')
const confirmChangelog = ref('')
const confirmCallback = ref(null)

function startEditName() { editingName.value = true; nextTick(() => nameInputRef.value?.focus()) }

function cronReadable(c) {
  if (!c) return '未设置'
  try { return cronstrue.toString(c, { locale: 'zh_CN' }) } catch { return c }
}

const flowCols = [
  { title:'流程名', key:'flow_name', width:130 },
  { title:'状态', key:'status', width:65, render(r){ return r.status==='published'?'✅ 已发布':'📝 '+r.status } },
  { title:'节点', key:'node_count', width:45, align:'center', render(r){ return r.node_count||0 } },
  { title:'Cron', key:'cron_expr', width:130, render(r){ return cronReadable(r.cron_expr) } },
  { title:'执行', key:'_task', width:60, render(r){ const ts = taskStatuses[r.id]; if (!ts || ts.status!=='running') return '空闲'; return h('span',{style:{color:'#2080f0',fontSize:'11px'}},'⟳ 执行中') } },
  { title:'操作', key:'actions', width:240, render(r){
    const hasRun = taskStatuses[r.id] && taskStatuses[r.id].status === 'running'
    return h('div',{style:{display:'flex',gap:'4px',alignItems:'center'}},[
      h(NButton,{size:'tiny',quaternary:true,onClick:()=>nav.showFlowEditor(r.id)},()=>'✎ 编辑'),
      r.status==='draft' ? h(NButton,{size:'tiny',quaternary:true,type:'success',onClick:()=>doPublishList(r.id)},()=>'▶ 发布') : null,
      r.status==='published' ? h(NButton,{size:'tiny',quaternary:true,type:'warning',onClick:()=>doUnpublishList(r.id)},()=>'⏸ 下线') : null,
      r.status==='published' && !hasRun ? h(NButton,{size:'tiny',quaternary:true,type:'primary',onClick:()=>doExecuteQuick(r.id, r.flow_name)},()=>'⚡ 执行') : null,
      h(NButton,{size:'tiny',quaternary:true,onClick:()=>{ nav.flowId = r.id; nav.tab = 'r' }},()=>'👁 查看'),
      h(NButton,{size:'tiny',quaternary:true,onClick:()=>showFlowLog(r.id, r.flow_name)},()=>'📋 日志'),
    ])
  }},
]

const taskStatuses = ref({})
const wsUnwatch = ref(null)

function showFlowLog(id, name) {
  emit('show-log', id)
}

async function loadTaskStatuses() {
  for (const f of flows.value) {
    try {
      const r = await axios.get(API + `/api/dag/flows/${f.id}/task-status`)
      if (r.data.has_task) taskStatuses.value[f.id] = r.data
      else delete taskStatuses.value[f.id]
    } catch(e) {}
  }
}

async function loadFlows() {
  loading.value = true
  try { const r = await axios.get(API + '/api/dag/flows'); flows.value = r.data.items || [] } catch(e) { console.error(e) }
  loading.value = false
}
async function loadNodeTypes() {
  try { const r = await axios.get(API + '/api/dag/node-types'); nodeTypes.value = r.data.items || [] } catch(e) { console.error(e) }
}
function labelFor(name) { return nodeTypes.value.find(nt => nt.node_name === name)?.label || name }
function parseCron(cron) {
  if (!cron) return; const p = cron.split(' ')
  if (p.length === 6) p.shift(); if (p.length < 5) return
  const [m, h, dom, mon, dow] = p; cronMin.value = m; cronHour.value = h
  // 设置 cronTime
  cronTime.value = makeTimeMs(parseInt(h), parseInt(m))
  // 检测模式
  if (dom === '*' && mon === '*' && dow === '*' && /^\*\//.test(m)) { cronTab.value = 'min'; cronInterval.value = parseInt(m.replace('*/','')) || 5 }
  else if (dom === '*' && mon === '*' && dow === '*' && /^\*\//.test(h)) { cronTab.value = 'hour'; cronInterval.value = parseInt(h.replace('*/','')) || 1 }
  else if (dom === '*' && mon === '*' && dow === '*') cronTab.value = 'day'
  else if (dom === '*' && mon === '*' && /^[\d,]+$/.test(dow)) { cronTab.value = 'week'; cronWeekDays.value = dow.split(',').sort() }
  else if (dow === '*' && mon === '*' && /^[\d,]+$/.test(dom)) { cronTab.value = 'month'; cronMonthDays.value = dom.split(',').sort() }
  else { cronTab.value = 'custom'; cronCustomVal.value = cron; buildCron(); return }
  buildCron()
}
function addNode(nt) {
  if (!nodes.value) return
  const vp = screenToFlowCoordinate({ x: window.innerWidth / 2, y: window.innerHeight / 2 })
  nodes.value.push(makeVfNode(nt.node_name || nt.name, nt.label || nt.name, vp.x + Math.random() * 100 - 50, vp.y + Math.random() * 60 - 30))
  dirty.value = true
}
async function openEdit(id) {
  editMode.value = true; validation.value = null; loadNodeTypes()
  loadingFlow.value = true
  try {
    const r = await axios.get(API + `/api/dag/flows/${id}`); const d = r.data
    flowName.value = d.flow_name; flowCron.value = d.cron_expr || ''
    parseCron(d.cron_expr); flowStatus.value = d.status || 'draft'; selectedFlow.value = id
    await nextTick()
    const newNodes = (d.nodes || []).map(n => { const pos = n.position || {}; return makeVfNode(n.node_name, labelFor(n.node_name), pos.x, pos.y) })
    if (!d.nodes?.find(n => n.node_name === 'cron')) newNodes.push(makeVfNode('cron', 'cron', 100, 80))
    const newEdges = []
    for (const n of d.nodes || []) { for (const dep of n.deps || []) { const sid = dep + '->' + n.node_name; if (!newEdges.find(e => e.id === sid)) newEdges.push({ id: sid, source: dep, target: n.node_name, ...defaultEdgeOpts }) } }
    nodes.value = newNodes; edges.value = newEdges; dirty.value = false
    await nextTick(); fitView({ padding: 0.2, maxZoom: 1 })
  } catch(e) { console.error(e) } finally { loadingFlow.value = false }
}
function getFlowData() {
  const ns = getNodes.value
  if (!ns.length) return { nodes: [] }
  return { nodes: ns.map(n => ({ node_name: n.id, deps: [...new Set(edges.value.filter(e => e.target === n.id).map(e => e.source))], position: { x: Math.round(n.position.x), y: Math.round(n.position.y) } })) }
}
async function doValidate() {
  try { const r = await axios.post(API + '/api/dag/flows/validate', getFlowData()); validation.value = r.data }
  catch(e) { validation.value = { ok: false, errors: [e.response?.data?.detail || e.message] } }
}
function doSave() {
  confirmTitle.value = '💾 保存流程'; confirmMsg.value = `即将保存「${flowName.value || '新流程'}」`; confirmChangelog.value = '手动编辑'
  confirmCallback.value = async () => {
    saving.value = true; const data = getFlowData()
    try {
      if (selectedFlow.value) await axios.put(API + `/api/dag/flows/${selectedFlow.value}`, { nodes: data.nodes, cron_expr: flowCron.value, change_log: confirmChangelog.value.trim() || '手动编辑' })
      else await axios.post(API + '/api/dag/flows', { flow_name: flowName.value, nodes: data.nodes, cron_expr: flowCron.value })
      validation.value = { ok: true, errors: [] }; showConfirmModal.value = false; message.success('保存成功'); dirty.value = false; loadFlows()
    } catch(e) { message.error(e.response?.data?.detail || '保存失败') }
    saving.value = false
  }; showConfirmModal.value = true
}
async function doPublish() {
  confirmTitle.value = '🚀 发布流程'; confirmMsg.value = `发布「${flowName.value}」，定时任务将生效`; confirmChangelog.value = ''
  confirmCallback.value = async () => {
    if (!selectedFlow.value) return; saving.value = true
    try { await axios.post(API + `/api/dag/flows/${selectedFlow.value}/publish`); flowStatus.value = 'published'; showConfirmModal.value = false; loadFlows() }
    catch(e) { message.error(e.response?.data?.detail || '发布失败') }
    saving.value = false
  }; showConfirmModal.value = true
}
function confirmAction() { if (confirmCallback.value) confirmCallback.value() }
async function doPublishList(id) { try { await axios.post(API + `/api/dag/flows/${id}/publish`); loadFlows() } catch(e) {} }
async function doUnpublishList(id) { try { await axios.post(API + `/api/dag/flows/${id}/unpublish`); loadFlows() } catch(e) {} }
async function doUnpublish() {
  if (!selectedFlow.value) return; saving.value = true
  try { await axios.post(API + `/api/dag/flows/${selectedFlow.value}/unpublish`); flowStatus.value = 'draft'; loadFlows() } catch(e) { alert(e.response?.data?.detail || '下线失败') }
  saving.value = false
}
async function doExecute() {
  if (!selectedFlow.value) return; execLoading.value = true; execResult.value = null
  try {
    const fd = (d) => { if (!d) return ''; const dt = new Date(d); return dt.getFullYear()+'-'+String(dt.getMonth()+1).padStart(2,'0')+'-'+String(dt.getDate()).padStart(2,'0') }
    const r = await axios.post(API + `/api/dag/flows/${selectedFlow.value}/execute`, { trade_date: fd(execDate.value) })
    execResult.value = r.data
  } catch(e) { execResult.value = { ok: false, error: e.response?.data?.detail || e.message } }
  execLoading.value = false
}
async function doExecuteQuick(id, name) {
  if (!confirm(`⚡ 立即执行「${name}」？`)) return
  try { const r = await axios.post(API + `/api/dag/flows/${id}/execute`, {}); alert(`✅ 已触发 — ${r.data.task_id}`) } catch(e) { alert(e.response?.data?.detail || '执行失败') }
}
function autoLayout() {
  const ns = getNodes.value; const es = edges.value
  if (!ns.length) return
  const inDeg = {}; const adj = {}
  ns.forEach(n => { inDeg[n.id] = 0; adj[n.id] = [] })
  es.forEach(e => { if (inDeg[e.target] !== undefined) { inDeg[e.target]++; adj[e.source]?.push(e.target) } })
  const layers = []; const queue = Object.keys(inDeg).filter(k => inDeg[k] === 0).map(k => ({ name: k, depth: 0 })); const visited = new Set()
  while (queue.length) { const cur = queue.shift(); if (visited.has(cur.name)) continue; visited.add(cur.name); if (!layers[cur.depth]) layers[cur.depth] = []; layers[cur.depth].push(cur.name); (adj[cur.name] || []).forEach(next => { if (!visited.has(next)) queue.push({ name: next, depth: cur.depth + 1 }) }) }
  ns.forEach(n => { if (!visited.has(n.id)) { if (!layers[0]) layers[0] = []; layers[0].push(n.id); visited.add(n.id) } })
  const gapX = 160; const gapY = 80
  layers.forEach((layer, li) => { const totalW = layer.length * gapX; const offsetX = 80 - totalW / 2 + gapX / 2; layer.forEach((name, ni) => { const n = ns.find(nd => nd.id === name); if (n) n.position = { x: offsetX + ni * gapX + 300, y: 60 + li * gapY } }) })
}
onMounted(async () => {
  addWsListener((data) => {
    if (data.type === "task_progress" && taskStatuses.value) {
      if (data.status === "running") taskStatuses.value[data.flow_id] = data
      else delete taskStatuses.value[data.flow_id]
    }
  })
  await loadFlows()
  loadTaskStatuses()
  if (props.flowId === 'new') { createNew() } else if (props.flowId > 0) { openEdit(props.flowId) } })
watch(() => props.flowId, (newId) => { if (newId === 'new') { createNew() } else if (newId > 0) { openEdit(newId) } })
watch(showExecModal, (v) => { if (!v) execResult.value = null })
function doExitEdit() { if (dirty.value && !confirm('有未保存的修改，确定退出？')) return; editMode.value = false; validation.value = null; emit('back') }
async function createNew() { editMode.value = true; flowName.value = ''; flowCron.value = ''; validation.value = null; await loadNodeTypes(); loadingFlow.value = true; nodes.value = []; edges.value = []; nodes.value.push(makeVfNode('cron', 'cron', 100, 80)); buildCron(); dirty.value = false; await nextTick(); fitView({ padding: 0.2, maxZoom: 1 }); loadingFlow.value = false }
</script>

<style>
.vue-flow__node-default { padding: 10px 6px !important; }
</style>