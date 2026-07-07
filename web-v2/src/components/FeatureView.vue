<template>
<div style="padding:20px;max-width:1200px;margin:0 auto">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
    <div style="font-size:18px;font-weight:700;color:var(--c-text)">特征管理（Feature Registry）</div>
    <div style="display:flex;gap:6px">
      <n-button size="small" quaternary @click="openDepGraph">🔗 依赖图</n-button>
      <n-button type="primary" size="small" @click="openCreate">+ 新增特征</n-button>
    </div>
  </div>

  <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
    <n-select v-model:value="filterEntity" :options="entityOptions" size="small" style="width:110px" placeholder="实体" clearable @update:value="loadData" />
    <n-select v-model:value="filterStatus" :options="statusOptions" size="small" style="width:130px" placeholder="状态" clearable @update:value="loadData" />
    <n-input v-model:value="searchText" size="small" style="width:180px" placeholder="搜索名称" clearable @keyup.enter="loadData" />
    <n-button size="small" @click="loadData">查询</n-button>
  </div>

  <n-data-table :columns="columns" :data="items" :loading="loading" size="small" :row-props="rowProps" />
  <div style="display:flex;justify-content:center;align-items:center;gap:10px;margin-top:10px;font-size:12px;color:var(--c-text-dim)">
    <span>共 {{ total }} 条</span>
    <n-pagination v-if="totalPages > 1" :page="page" :page-count="totalPages" @update:page="p => { page = p; loadData() }" size="small" />
  </div>

  <!-- Create/Edit Modal -->
  <n-modal v-model:show="showCreate" preset="card" :title="editId ? '编辑特征' : '新增特征'" style="width:800px;max-width:95vw" :mask-closable="false">
    <n-space vertical>
      <n-input v-model:value="form.feature_name" placeholder="特征英文名（小写字母开头，如 ma_5, rsi_14）" :disabled="!!editId" />
      <n-input v-model:value="form.display_name" placeholder="特征中文名" />
      <n-select v-model:value="form.target_entity" :options="entityOpts" placeholder="目标实体" :disabled="!!editId" />
      <n-input v-model:value="form.description" type="textarea" placeholder="功能描述，如：5日均线偏离度" :rows="2" />

      <div style="display:flex;align-items:center;justify-content:space-between">
        <div style="font-size:12px;font-weight:600;color:var(--c-text-dim)">KEPL 公式</div>
        <n-button size="tiny" quaternary @click="doValidate" :loading="validating" style="font-size:11px">🔍 验证公式</n-button>
      </div>
      <MonacoEditor v-model="form.formula" />

      <!-- 依赖预览 -->
      <div v-if="parseDeps.length > 0" style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:6px;padding:10px">
        <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:6px">📎 依赖项（自动提取）</div>
        <div style="display:flex;flex-wrap:wrap;gap:4px">
          <n-tag v-for="d in parseDeps" :key="d" size="tiny" :bordered="false" :type="d.includes('.') ? 'warning' : 'info'">
            {{ d.includes('.') ? '📄 ' : '🔗 ' }}{{ d }}
          </n-tag>
        </div>
      </div>

      <!-- 校验结果 -->
      <div v-if="validateResult">
        <div v-if="validateResult.ok" style="background:rgba(16,185,129,.08);border:1px solid rgba(16,185,129,.2);border-radius:6px;padding:8px;font-size:11px;color:#10b981">
          ✅ 公式语法正确，共 {{ validateResult.dependencies?.length || 0 }} 个依赖项
        </div>
        <div v-else style="background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:6px;padding:8px">
          <div v-for="(e,i) in validateResult.errors" :key="i" style="font-size:11px;color:#ef4444">{{ e.message }}</div>
        </div>
      </div>

      <div style="display:flex;align-items:center;gap:8px;margin-top:8px">
        <span style="font-size:12px;color:var(--c-text-dim)">创建后启用：</span>
        <n-switch v-model:value="form.enabled" />
      </div>
    </n-space>
    <template #footer>
      <n-space justify="flex-end">
        <n-button @click="showCreate = false">取消</n-button>
        <n-button @click="doCreate('draft')" :loading="creating">存为草稿</n-button>
        <n-button type="primary" @click="doCreate('enabled')" :loading="creating">保存并启用</n-button>
      </n-space>
    </template>
  </n-modal>

  <!-- Detail Modal -->
  <n-modal v-model:show="showDetail" preset="card" :title="detailItem?.feature_name" style="width:700px;max-width:92vw" :mask-closable="true">
    <n-spin v-if="detailLoading" style="padding:40px" />
    <template v-else-if="detailItem">
      <!-- 信息卡 -->
      <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px">
        <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:10px 14px;min-width:70px;text-align:center">
          <div style="font-size:10px;color:var(--c-text-faint)">实体</div>
          <div style="font-size:15px;font-weight:700;color:var(--c-text)">{{ entityLabel[detailItem.target_entity] || detailItem.target_entity }}</div>
        </div>
        <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:10px 14px;min-width:70px;text-align:center">
          <div style="font-size:10px;color:var(--c-text-faint)">状态</div>
          <n-tag :type="statusTypeMap[detailItem.status]||'default'" size="tiny" :bordered="false">{{ statusMap[detailItem.status] }}</n-tag>
        </div>
        <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:10px 14px;min-width:70px;text-align:center">
          <div style="font-size:10px;color:var(--c-text-faint)">中文名</div>
          <div style="font-size:13px;font-weight:600;color:var(--c-text)">{{ detailItem.display_name || '—' }}</div>
        </div>
        <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:10px 14px;min-width:70px;text-align:center">
          <div style="font-size:10px;color:var(--c-text-faint)">创建</div>
          <div style="font-size:12px;color:var(--c-text)">{{ (detailItem.created_at||'').slice(0,10) }}</div>
        </div>
      </div>

      <!-- 公式 -->
      <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;margin-bottom:14px">
        <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:6px">KEPL 公式</div>
        <code style="font-size:13px;color:var(--c-text);word-break:break-all">{{ detailItem.formula }}</code>
      </div>

      <!-- 质量仪表盘 -->
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px">
        <div style="flex:1;min-width:100px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;text-align:center">
          <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px">📊 数据完整度</div>
          <div :style="{fontSize:'22px',fontWeight:700,color:completenessPct>=80?'#10b981':completenessPct>=50?'#f59e0b':'#ef4444'}">{{ completenessPct }}%</div>
          <div style="background:var(--c-border);border-radius:4px;height:6px;margin-top:4px;overflow:hidden">
            <div :style="{width:completenessPct+'%',height:'100%',background:completenessPct>=80?'#10b981':completenessPct>=50?'#f59e0b':'#ef4444',borderRadius:'4px'}"></div>
          </div>
        </div>
        <div style="flex:1;min-width:80px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;text-align:center">
          <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px">📋 总格子</div>
          <div style="font-size:20px;font-weight:700;color:var(--c-text)">{{ (detailItem.total_effective_cells||0).toLocaleString() }}</div>
        </div>
        <div style="flex:1;min-width:80px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;text-align:center">
          <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px">❌ 异常缺失</div>
          <div :style="{fontSize:'20px',fontWeight:700,color:detailItem.abnormal_missing_cells>0?'#ef4444':'var(--c-text-dim)'}">{{ (detailItem.abnormal_missing_cells||0).toLocaleString() }}</div>
        </div>
        <div style="flex:1;min-width:80px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;text-align:center">
          <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px">📅 最近计算</div>
          <div :style="{fontSize:'13px',fontWeight:600,color:detailStale?'#f59e0b':'var(--c-text)'}">{{ detailItem.latest_computed_date || '—' }}</div>
          <div v-if="detailStale" style="font-size:10px;color:#f59e0b;margin-top:2px">⚠ 超过5天未更新</div>
        </div>
      </div>

      <!-- 依赖关系 -->
      <div style="display:flex;gap:12px;flex-wrap:wrap">
        <div style="flex:1;min-width:180px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px">
          <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:8px">⬆ 上游依赖（本特征依赖谁）</div>
          <div v-if="(detailItem.depends_on||[]).length">
            <n-tag v-for="d in detailItem.depends_on" :key="d" size="tiny" :bordered="false" type="info" style="margin-right:4px;margin-bottom:4px">{{ d }}</n-tag>
          </div>
          <div v-else style="font-size:11px;color:var(--c-text-faint)">无（仅依赖原始字段）</div>
        </div>
        <div style="flex:1;min-width:180px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px">
          <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:8px">⬇ 下游引用（谁依赖本特征）</div>
          <div v-if="(detailItem.downstream||[]).length">
            <div v-for="ds in detailItem.downstream" :key="ds.feature_name" style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
              <span style="font-size:12px;color:var(--c-text)">{{ ds.feature_name }}</span>
              <n-tag :type="statusTypeMap[ds.status]||'default'" size="tiny" :bordered="false">{{ statusMap[ds.status] }}</n-tag>
            </div>
          </div>
          <div v-else style="font-size:11px;color:var(--c-text-faint)">无下游引用</div>
        </div>
      </div>
    </template>
    <template #footer>
      <n-space justify="space-between">
        <span v-if="detailItem?.description" style="font-size:11px;color:var(--c-text-dim)">{{ detailItem.description }}</span>
        <div style="display:flex;gap:8px">
          <n-button size="small" @click="showDetail=false">关闭</n-button>
          <n-button v-if="detailItem" size="small" type="primary" @click="showDetail=false; openEdit(detailItem)">✎ 编辑</n-button>
        </div>
      </n-space>
    </template>
  </n-modal>

  <!-- Compute Range Modal -->
  <n-modal v-model:show="showCompute" preset="card" title="📥 特征补数" style="width:420px;max-width:92vw" :mask-closable="false">
    <n-space vertical>
      <div style="font-size:12px;color:var(--c-text)">特征：<b>{{ computeTarget?.feature_name }}</b></div>
      <div style="font-size:11px;color:var(--c-text-dim)">选择补数日期范围，系统将对该特征在指定日期内重新计算并入库。</div>
      <n-date-picker v-model:value="computeDateRange" type="daterange" size="small" style="width:100%" clearable />
      <n-checkbox v-model:checked="computeForce">强制更新（覆盖已有数据）</n-checkbox>
      <div v-if="computeProgress" style="margin-top:8px">
        <div style="font-size:11px;color:var(--c-text-dim);margin-bottom:4px">
          {{ computeProgress.current_date || '计算中...' }}
        </div>
        <n-progress type="line" :percentage="computeProgress.progress_pct || 0" :height="8" :border-radius="4"
          :status="computeProgress.status === 'failed' ? 'error' : computeProgress.status === 'completed' ? 'success' : 'default'" />
      </div>
    </n-space>
    <template #footer>
      <n-space justify="flex-end">
        <n-button @click="showCompute=false">取消</n-button>
        <n-button type="primary" @click="doCompute" :loading="computeSubmitting" :disabled="!!computeProgress">开始补数</n-button>
      </n-space>
    </template>
  </n-modal>

  <!-- Full Dependency Graph Modal -->
  <n-modal v-model:show="showDepGraph" preset="card" title="🔗 特征依赖关系图" style="width:96vw;max-width:96vw;height:90vh" :mask-closable="true">
    <div ref="depGraphContainer" style="width:100%;height:calc(90vh - 120px)"></div>
  </n-modal>
</div>
</template>

<script setup>
import { ref, computed, h, onMounted, onUnmounted, watch } from 'vue'
import { NButton, NDataTable, NModal, NSpace, NInput, NSelect, NTag, NSwitch, NSpin, NPagination, NDatePicker, NCheckbox, NProgress } from 'naive-ui'
import MonacoEditor from './MonacoEditor.vue'
import { useNavStore } from '../stores/nav'
import * as echarts from 'echarts'
import axios from 'axios'
import { addWsListener } from '../utils/ws'

const API = window.location.origin
const loading = ref(false)
const items = ref([])
const total = ref(0)
const page = ref(1)
const PAGE_SIZE = 20
const filterEntity = ref(null)
const filterStatus = ref(null)
const searchText = ref('')

const showCreate = ref(false)
const editId = ref(null)
const creating = ref(false)
const validating = ref(false)
const validateResult = ref(null)
const parseDeps = ref([])

// ── Detail modal state ──
const showDetail = ref(false)
const detailItem = ref(null)
const detailLoading = ref(false)
const detailStale = ref(false)
const completenessPct = ref(0)

const form = ref({
  feature_name: '',
  display_name: '',
  target_entity: 'stock',
  description: '',
  formula: '',
  enabled: false,
})

function resetForm() {
  editId.value = null
  form.value = { feature_name:'', display_name:'', target_entity:'stock', description:'', formula:'', enabled:false }
  validateResult.value = null
  parseDeps.value = []
}

function openCreate() {
  resetForm()
  showCreate.value = true
}

const nav = useNavStore()

// ── 补数 ──
const showCompute = ref(false)
const computeTarget = ref(null)
const computeDateRange = ref(null)
const computeForce = ref(false)
const computeSubmitting = ref(false)
const computeProgress = ref(null)
let computeWsUnwatch = null

function openCompute(row) {
  computeTarget.value = row
  computeDateRange.value = null
  computeForce.value = false
  computeProgress.value = null
  showCompute.value = true
}

async function doCompute() {
  if (!computeTarget.value || !computeDateRange.value || computeDateRange.value.length !== 2) {
    alert('请选择补数日期范围')
    return
  }
  computeSubmitting.value = true
  try {
    const [s, e] = computeDateRange.value
    const fd = (d) => {
      const dt = new Date(d)
      return dt.getFullYear() + '-' + String(dt.getMonth()+1).padStart(2,'0') + '-' + String(dt.getDate()).padStart(2,'0')
    }
    const r = await axios.post(API + '/api/features/' + computeTarget.value.id + '/compute-range', {
      start_date: fd(s), end_date: fd(e), force: computeForce.value
    }, { headers: authHeaders() })
    if (r.data.ok) {
      computeProgress.value = { progress_pct: 0, current_date: '任务已提交...' }
      listenComputeProgress(r.data.task_id, computeTarget.value.id)
    }
  } catch(e) {
    alert(e.response?.data?.detail || '提交失败')
  }
  computeSubmitting.value = false
}

function listenComputeProgress(taskId, featureId) {
  if (computeWsUnwatch) computeWsUnwatch()
  computeWsUnwatch = addWsListener((data) => {
    if (data.type === 'feature_compute_progress' && data.task_id === taskId) {
      computeProgress.value = data
      if (data.status === 'completed' || data.status === 'failed') {
        setTimeout(() => {
          computeProgress.value = null
          showCompute.value = false
          computeTarget.value = null
          loadData()
        }, 2000)
        if (computeWsUnwatch) { computeWsUnwatch(); computeWsUnwatch = null }
      }
    }
  })
}

function authHeaders() {
  const t = localStorage.getItem('token')
  return t ? { Authorization: 'Bearer ' + t } : {}
}

// ── 依赖图 ──
const showDepGraph = ref(false)
const depGraphContainer = ref(null)

async function openDepGraph() {
  showDepGraph.value = true
  await nextTick()
  if (!depGraphContainer.value) return
  try {
    const r = await axios.get(API + '/api/features/dependency-graph')
    const { nodes, edges } = r.data
    const chart = echarts.init(depGraphContainer.value)
    const colors = {0:'#9ca3af',1:'#2080f0',2:'#f59e0b',3:'#ef4444'}
    chart.setOption({
      tooltip: { formatter(p) { return p.data.name + (p.data.display_name?'<br/>'+p.data.display_name:'') } },
      series: [{
        type: 'graph', layout: 'force', roam: true, draggable: true,
        force: { repulsion: 200, edgeLength: [80,200] },
        data: nodes.map(n => ({ name:n.id, display_name:n.display_name, itemStyle:{color:colors[n.level]||colors[0]}, symbolSize:12 })),
        links: edges.map(e => ({ source: e.source, target: e.target })),
        lineStyle: { color:'#6b7280', curveness:0.1, opacity:0.4 },
        label: { show:true, fontSize:9, color:'var(--c-text)' },
      }]
    })
  } catch(e) { console.error(e) }
}

import { nextTick } from 'vue'

function openDetail(id) {
  nav.showFeatureDetail(id)
}

function openEdit(row) {
  editId.value = row.id
  form.value = {
    feature_name: row.feature_name,
    display_name: row.display_name || '',
    target_entity: row.target_entity,
    description: row.description || '',
    formula: row.formula || '',
    enabled: row.status === 'enabled',
  }
  parseDeps.value = row.depends_on || []
  validateResult.value = null
  showCreate.value = true
}

async function doValidate() {
  validating.value = true
  validateResult.value = null
  try {
    const r = await axios.post(API + '/api/features/validate', {
      formula: form.value.formula,
      target_entity: form.value.target_entity,
    })
    validateResult.value = r.data
    if (r.data.ok) {
      parseDeps.value = r.data.dependencies || []
    }
  } catch (e) {
    validateResult.value = { ok: false, errors: [{ message: e.response?.data?.detail || e.message }] }
  }
  validating.value = false
}

async function doCreate(status) {
  creating.value = true
  try {
    const payload = {
      feature_name: form.value.feature_name,
      display_name: form.value.display_name,
      target_entity: form.value.target_entity,
      description: form.value.description,
      formula: form.value.formula,
      status,
    }
    if (editId.value) {
      await axios.put(API + `/api/features/${editId.value}`, {
        display_name: payload.display_name,
        description: payload.description,
        formula: payload.formula,
        status: payload.status,
      })
    } else {
      await axios.post(API + '/api/features', payload)
    }
    showCreate.value = false
    resetForm()
    loadData()
  } catch (e) {
    alert(e.response?.data?.detail || '保存失败')
  }
  creating.value = false
}

// ── Status labels ──
const statusMap = { draft:'草稿', enabled:'已启用', pending_recalc:'待重算', deprecated:'已弃用', data_anomaly:'数据异常' }
const statusTypeMap = { draft:'warning', enabled:'success', pending_recalc:'info', deprecated:'default', data_anomaly:'error' }
const entityLabel = { stock:'个股', etf:'ETF', index:'指数', global:'全局' }

const entityOptions = [
  { label:'全部实体', value:'all' },
  ...Object.entries(entityLabel).map(([k,v]) => ({ label:v, value:k })),
]
const statusOptions = [
  { label:'全部状态', value:'all' },
  ...Object.entries(statusMap).map(([k,v]) => ({ label:v, value:k })),
]
const entityOpts = Object.entries(entityLabel).map(([k,v]) => ({ label:v, value:k }))

const columns = [
  { title:'英文名', key:'feature_name', width:100, ellipsis:{tooltip:true}, render(row) {
    return h('span', { style:{cursor:'pointer',color:'#2080f0',textDecoration:'underline'}, onClick:() => openDetail(row.id) }, row.feature_name)
  }},
  { title:'中文名', key:'display_name', width:90, ellipsis:{tooltip:true} },
  { title:'实体', key:'target_entity', width:60, render:(row) => entityLabel[row.target_entity] || row.target_entity },
  { title:'状态', key:'status', width:80, render:(row) => h(NTag, { type:statusTypeMap[row.status]||'default', size:'tiny', bordered:false }, () => statusMap[row.status]||row.status) },
  { title:'完整度', key:'data_completeness', width:80, render:(row) => {
    const pct = Math.round((row.data_completeness||0)*1000)/10
    const color = pct >= 60 ? '#10b981' : pct >= 30 ? '#f59e0b' : '#ef4444'
    return h('div', {}, [
      h('div', { style:{fontSize:'10px',color:'var(--c-text-dim)',marginBottom:'2px'} }, pct+'%'),
      h('div', { style:{background:'var(--c-border)',borderRadius:'3px',height:'4px',overflow:'hidden'} },
        [h('div', { style:{width:pct+'%',height:'100%',background:color,borderRadius:'3px'} })])
    ])
  }},
  { title:'异常缺失', key:'abnormal_missing_cells', width:70, align:'right', render:(row) => (row.abnormal_missing_cells||0).toLocaleString() },
  { title:'最近计算', key:'latest_computed_date', width:90, render:(row) => row.latest_computed_date || '—' },
  { title:'依赖数', key:'depends_on', width:60, align:'center', render:(row) => (row.depends_on?.length || 0) },
  { title:'操作', key:'actions', width:110, render(row) {
    return h('div', { style:{display:'flex',gap:'2px'} }, [
      h(NButton, { size:'tiny', quaternary:true, style:'fontSize:11px', onClick:() => openEdit(row) }, () => '编辑'),
      h(NButton, { size:'tiny', quaternary:true, style:'fontSize:11px', onClick:() => openCompute(row) }, () => '📥'),
    ])
  }},
]

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

function rowProps(row) {
  const colors = { enabled:'#10b981', draft:'#f59e0b', pending_recalc:'#2080f0', deprecated:'#9ca3af', data_anomaly:'#ef4444' }
  return { style: { borderLeft: '3px solid ' + (colors[row.status]||'transparent') } }
}

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: PAGE_SIZE }
    if (filterEntity.value && filterEntity.value !== 'all') params.entity = filterEntity.value
    if (filterStatus.value && filterStatus.value !== 'all') params.status = filterStatus.value
    if (searchText.value) params.search = searchText.value
    const r = await axios.get(API + '/api/features', { params })
    items.value = r.data.items || []
    total.value = r.data.total || 0
    syncHash()
  } catch (e) {
    console.error('loadData:', e)
  }
  loading.value = false
}

// URL hash 参数同步
function syncHash() {
  const qs = []
  if (page.value > 1) qs.push('page=' + page.value)
  if (filterEntity.value && filterEntity.value !== 'all') qs.push('entity=' + filterEntity.value)
  if (filterStatus.value && filterStatus.value !== 'all') qs.push('status=' + filterStatus.value)
  if (searchText.value) qs.push('search=' + encodeURIComponent(searchText.value))
  const target = '/features' + (qs.length ? '?' + qs.join('&') : '')
  if (location.hash.slice(1) !== target) history.replaceState(null, '', '#' + target)
}

function parseHashParams() {
  const hash = location.hash.slice(1)
  const q = hash.includes('?') ? hash.split('?')[1] : ''
  if (!q) return
  const sp = new URLSearchParams(q)
  if (sp.has('page')) page.value = parseInt(sp.get('page')) || 1
  if (sp.has('entity')) filterEntity.value = sp.get('entity')
  if (sp.has('status')) filterStatus.value = sp.get('status')
  if (sp.has('search')) searchText.value = sp.get('search')
}

onMounted(() => {
  parseHashParams()
  loadData()
})
// 从详情页点编辑时，自动打开编辑弹窗
watch(() => nav.pendingEditFeatureId, (id) => {
  if (id) {
    const row = items.value.find(i => i.id === id)
    if (row) openEdit(row)
    nav.pendingEditFeatureId = null
  }
})

onUnmounted(() => {
  if (computeWsUnwatch) computeWsUnwatch()
})
window.addEventListener('popstate', () => {
  parseHashParams()
  loadData()
})
</script>