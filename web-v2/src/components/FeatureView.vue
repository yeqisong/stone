<template>
<div style="padding:20px;max-width:1200px;margin:0 auto">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
    <div style="font-size:18px;font-weight:700;color:var(--c-text)">特征管理（Feature Registry）</div>
    <n-button type="primary" size="small" @click="openCreate">+ 新增特征</n-button>
  </div>

  <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
    <n-select v-model:value="filterEntity" :options="entityOptions" size="small" style="width:110px" placeholder="实体" clearable @update:value="loadData" />
    <n-select v-model:value="filterStatus" :options="statusOptions" size="small" style="width:130px" placeholder="状态" clearable @update:value="loadData" />
    <n-input v-model:value="searchText" size="small" style="width:180px" placeholder="搜索名称" clearable @keyup.enter="loadData" />
    <n-button size="small" @click="loadData">查询</n-button>
  </div>

  <n-data-table :columns="columns" :data="items" :loading="loading" size="small" :pagination="pagination"
    :row-props="rowProps" />

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
</div>
</template>

<script setup>
import { ref, computed, h } from 'vue'
import { NButton, NDataTable, NModal, NSpace, NInput, NSelect, NTag, NSwitch } from 'naive-ui'
import MonacoEditor from './MonacoEditor.vue'
import axios from 'axios'

const API = window.location.origin
const loading = ref(false)
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const filterEntity = ref(null)
const filterStatus = ref(null)
const searchText = ref('')

const showCreate = ref(false)
const editId = ref(null)
const creating = ref(false)
const validating = ref(false)
const validateResult = ref(null)
const parseDeps = ref([])

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
  { title:'英文名', key:'feature_name', width:100, ellipsis:{tooltip:true} },
  { title:'中文名', key:'display_name', width:90, ellipsis:{tooltip:true} },
  { title:'实体', key:'target_entity', width:60, render:(row) => entityLabel[row.target_entity] || row.target_entity },
  { title:'状态', key:'status', width:80, render:(row) => h(NTag, { type:statusTypeMap[row.status]||'default', size:'tiny', bordered:false }, () => statusMap[row.status]||row.status) },
  { title:'完整度', key:'data_completeness', width:80, render:(row) => {
    const pct = Math.round((row.data_completeness||0)*100)
    const color = pct >= 80 ? '#10b981' : pct >= 50 ? '#f59e0b' : '#ef4444'
    return h('div', {}, [
      h('div', { style:{fontSize:'10px',color:'var(--c-text-dim)',marginBottom:'2px'} }, pct+'%'),
      h('div', { style:{background:'var(--c-border)',borderRadius:'3px',height:'4px',overflow:'hidden'} },
        [h('div', { style:{width:pct+'%',height:'100%',background:color,borderRadius:'3px'} })])
    ])
  }},
  { title:'待计算', key:'pending_cells_total', width:70, align:'right', render:(row) => (row.pending_cells_total||0).toLocaleString() },
  { title:'最近计算', key:'latest_computed_date', width:90, render:(row) => row.latest_computed_date || '—' },
  { title:'依赖数', key:'depends_on', width:60, align:'center', render:(row) => (row.depends_on?.length || 0) },
  { title:'操作', key:'actions', width:80, render(row) {
    return h('div', { style:{display:'flex',gap:'2px'} }, [
      h(NButton, { size:'tiny', quaternary:true, style:'fontSize:11px', onClick:() => openEdit(row) }, () => '编辑'),
    ])
  }},
]

const pagination = computed(() => ({
  page: page.value, pageSize, itemCount: total.value,
  onChange(p) { page.value = p; loadData() },
}))

function rowProps(row) {
  const colors = { enabled:'#10b981', draft:'#f59e0b', pending_recalc:'#2080f0', deprecated:'#9ca3af', data_anomaly:'#ef4444' }
  return { style: { borderLeft: '3px solid ' + (colors[row.status]||'transparent') } }
}

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize }
    if (filterEntity.value && filterEntity.value !== 'all') params.entity = filterEntity.value
    if (filterStatus.value && filterStatus.value !== 'all') params.status = filterStatus.value
    if (searchText.value) params.search = searchText.value
    const r = await axios.get(API + '/api/features', { params })
    items.value = r.data.items || []
    total.value = r.data.total || 0
  } catch (e) {
    console.error('loadData:', e)
  }
  loading.value = false
}

loadData()
</script>