<template>
<div style="padding:16px;max-width:1100px;margin:0 auto">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
    <div style="font-size:17px;font-weight:700;color:var(--c-text)">DAG 流程编排</div>
    <div style="display:flex;gap:6px">
      <n-button size="small" @click="loadFlows">🔄 刷新</n-button>
      <n-button size="small" type="primary" @click="openCreate">+ 新建流程</n-button>
    </div>
  </div>

  <n-data-table v-if="!editFlow" :columns="flowCols" :data="flows" size="small" :loading="loading" />

  <!-- Editor View -->
  <div v-if="editFlow" style="display:flex;flex-direction:column;gap:12px">
    <div style="display:flex;align-items:center;gap:8px">
      <n-button size="small" quaternary @click="editFlow=null">← 返回</n-button>
      <n-input v-model:value="flowForm.name" size="small" style="width:200px" placeholder="流程名称" :disabled="!!editFlow.id" />
      <n-input v-model:value="flowForm.cron" size="small" style="width:140px" placeholder="Cron (如 0 18 * * 1-5)" />
      <n-tag :type="flowForm.status==='published'?'success':'warning'" size="small">{{ flowForm.status || 'draft' }}</n-tag>
    </div>

    <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
        <span style="font-size:12px;font-weight:600;color:var(--c-text-dim)">节点列表</span>
        <n-button size="tiny" @click="addNode">+ 添加节点</n-button>
      </div>
      <div v-for="(n,i) in flowForm.nodes" :key="i" style="display:flex;align-items:center;gap:8px;margin-bottom:6px;padding:6px 8px;background:var(--c-bg);border-radius:6px">
        <span style="font-size:11px;color:var(--c-text-faint);width:20px">{{ i+1 }}</span>
        <n-input v-model:value="n.node_name" size="tiny" style="width:150px" placeholder="节点名" />
        <span style="font-size:10px;color:var(--c-text-dim)">依赖:</span>
        <n-input v-model:value="n.depsStr" size="tiny" style="width:200px" placeholder="逗号分隔，如 kline,index" />
        <n-button size="tiny" type="error" quaternary @click="flowForm.nodes.splice(i,1)">✕</n-button>
      </div>
    </div>

    <!-- Validation -->
    <div v-if="validation" :style="{background:validation.ok?'rgba(16,185,129,.08)':'rgba(239,68,68,.08)',border:'1px solid '+(validation.ok?'rgba(16,185,129,.2)':'rgba(239,68,68,.2)'),borderRadius:'6px',padding:'8px',fontSize:'11px'}">
      <div v-if="validation.ok" style="color:#10b981">✅ 校验通过</div>
      <div v-else v-for="(e,i) in (validation.errors||[])" :key="i" style="color:#ef4444">❌ {{ e }}</div>
    </div>

    <div style="display:flex;gap:8px">
      <n-button size="small" @click="doValidate">🔍 校验</n-button>
      <n-button size="small" type="primary" @click="doSave" :loading="saving">💾 保存</n-button>
      <n-button v-if="editFlow.id" size="small" type="error" quaternary @click="doDelete" :loading="saving">🗑 删除</n-button>
    </div>

    <!-- Versions -->
    <div v-if="editFlow.id && editFlow.versions?.length">
      <div style="font-size:12px;font-weight:600;color:var(--c-text-dim);margin:8px 0">版本历史</div>
      <div v-for="v in editFlow.versions" :key="v.version" style="font-size:11px;color:var(--c-text-dim);padding:3px 0;border-bottom:1px solid var(--c-border-light)">
        v{{ v.version }} — {{ (v.created_at||'').slice(0,16) }} — {{ v.change_log || '无说明' }}
      </div>
    </div>
  </div>
</div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { NButton, NDataTable, NInput, NTag } from 'naive-ui'
import axios from 'axios'

const API = window.location.origin
const loading = ref(false)
const flows = ref([])
const editFlow = ref(null)
const flowForm = ref({ name:'', cron:'', nodes:[], status:'draft' })
const validation = ref(null)
const saving = ref(false)

const flowCols = [
  { title:'流程名', key:'flow_name', width:160 },
  { title:'状态', key:'status', width:70, render(row) { return row.status === 'published' ? '✅ 已发布' : '📝 草稿' } },
  { title:'Cron', key:'cron_expr', width:130, render(row) { return row.cron_expr || '—' } },
  { title:'创建', key:'created_at', width:100 },
  { title:'操作', key:'actions', width:120, render(row) {
    return h('div', { style:{display:'flex',gap:'4px'} }, [
      h(NButton, {size:'tiny',quaternary:true,onClick:() => openEdit(row.id)}, () => '编辑'),
    ])
  }},
]

import { h } from 'vue'

async function loadFlows() {
  loading.value = true
  try {
    const r = await axios.get(API + '/api/dag/flows')
    flows.value = r.data.items || []
  } catch(e) { console.error(e) }
  loading.value = false
}

function openCreate() {
  editFlow.value = { id: null }
  flowForm.value = { name:'', cron:'', nodes:[], status:'draft' }
  validation.value = null
}

async function openEdit(id) {
  try {
    const r = await axios.get(API + `/api/dag/flows/${id}`)
    editFlow.value = r.data
    flowForm.value = {
      name: r.data.flow_name,
      cron: r.data.cron_expr || '',
      nodes: (r.data.nodes||[]).map(n => ({ node_name:n.node_name, depsStr:(n.deps||[]).join(',') })),
      status: r.data.status,
    }
    validation.value = null
  } catch(e) { console.error(e) }
}

function addNode() {
  flowForm.value.nodes.push({ node_name:'', depsStr:'' })
}

async function doValidate() {
  const nodes = flowForm.value.nodes.map(n => ({
    node_name: n.node_name,
    deps: (n.depsStr||'').split(',').map(s => s.trim()).filter(Boolean),
  }))
  try {
    const r = await axios.post(API + '/api/dag/flows/validate', { nodes })
    validation.value = r.data
  } catch(e) {
    validation.value = { ok:false, errors:[e.response?.data?.detail||e.message] }
  }
}

async function doSave() {
  saving.value = true
  const nodes = flowForm.value.nodes.map(n => ({
    node_name: n.node_name,
    deps: (n.depsStr||'').split(',').map(s => s.trim()).filter(Boolean),
  }))
  try {
    if (editFlow.value?.id) {
      await axios.put(API + `/api/dag/flows/${editFlow.value.id}`, {
        nodes,
        cron_expr: flowForm.value.cron,
        change_log: '手动编辑',
      })
    } else {
      await axios.post(API + '/api/dag/flows', {
        flow_name: flowForm.value.name,
        nodes,
        cron_expr: flowForm.value.cron,
      })
    }
    editFlow.value = null
    loadFlows()
  } catch(e) {
    alert(e.response?.data?.detail || '保存失败')
  }
  saving.value = false
}

async function doDelete() {
  if (!confirm('确定删除？')) return
  saving.value = true
  try {
    await axios.delete(API + `/api/dag/flows/${editFlow.value.id}`)
    editFlow.value = null
    loadFlows()
  } catch(e) { alert(e.response?.data?.detail||e.message) }
  saving.value = false
}

onMounted(loadFlows)
</script>