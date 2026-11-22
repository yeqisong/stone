<template>
  <div class="roles-page">
    <!-- 角色列表 -->
    <aside class="role-panel">
      <div class="panel-head">
        <div>
          <div class="panel-title">角色</div>
          <div class="panel-sub">{{ roles.length }} 个角色</div>
        </div>
        <n-button size="tiny" type="primary" ghost @click="showCreate = true">+ 新建</n-button>
      </div>
      <div class="role-list">
        <button v-for="r in roles" :key="r.id" type="button" class="role-item"
                :class="{ active: selectedId === r.id }" @click="selectRole(r)">
          <div class="role-item-top">
            <span class="role-name">{{ r.name }}</span>
            <span class="role-code">{{ r.code }}</span>
            <span v-if="r.is_system" class="role-system">系统</span>
          </div>
          <div class="role-desc">{{ r.description || '—' }}</div>
          <div class="role-meta">{{ r.permission_count }} 项权限</div>
        </button>
      </div>
    </aside>

    <!-- 权限矩阵 -->
    <section class="perm-panel">
      <header class="perm-head">
        <div class="perm-head-main">
          <div class="perm-title">
            {{ selected?.name || '选择角色' }}
            <span v-if="selected?.is_system" class="role-system">系统</span>
          </div>
          <div class="perm-desc" :title="selected?.description">
            {{ selected?.description || '从左侧选择角色后编辑权限' }}
          </div>
        </div>
        <div class="perm-head-actions">
          <span class="perm-total">{{ checkedCount }} / {{ editablePerms.length }} 项</span>
          <n-button size="small" type="primary" :disabled="!selected" @click="save">保存权限</n-button>
        </div>
      </header>

      <div class="perm-toolbar">
        <n-input v-model:value="query" size="small" clearable
                 placeholder="搜索权限名称或权限码，例如：可转债 / research.cb" />
        <span v-if="query.trim()" class="perm-filter-hint">匹配 {{ filteredPerms.length }} 项</span>
      </div>

      <div class="perm-scroll">
        <div v-if="!grouped.length" class="perm-empty">没有匹配的权限</div>
        <div v-for="g in grouped" :key="g.key" class="perm-group">
          <div class="perm-group-head">
            <span class="perm-group-label">{{ g.label }}</span>
            <span class="perm-group-code">{{ g.key }}</span>
            <span class="perm-group-count">{{ g.selected }} / {{ g.items.length }}</span>
          </div>
          <div class="perm-rows">
            <div v-for="p in g.items" :key="p.code" class="perm-row"
                 :class="{ on: !!checked[p.code]?.checked }">
              <n-checkbox :checked="!!checked[p.code]?.checked"
                          @update:checked="v => setChecked(p, v)" />
              <div class="perm-main">
                <span class="perm-name">{{ p.name }}</span>
                <code class="perm-code" :title="p.code">{{ p.code }}</code>
              </div>
              <div class="perm-controls">
                <n-select v-if="checked[p.code]?.checked && isPersonal(p.code)"
                          v-model:value="checked[p.code].scope"
                          size="tiny" :options="scopeOptions" class="scope-select" />
                <n-checkbox v-if="checked[p.code]?.checked && isPersonal(p.code)"
                            v-model:checked="checked[p.code].support_only"
                            size="small" class="support-check">仅支持会话</n-checkbox>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <n-modal v-model:show="showCreate" preset="card" title="新建角色" style="width:420px">
      <n-form label-placement="left" label-width="80">
        <n-form-item label="角色码"><n-input v-model:value="createForm.code" placeholder="例如 auditor" /></n-form-item>
        <n-form-item label="名称"><n-input v-model:value="createForm.name" /></n-form-item>
        <n-form-item label="描述"><n-input v-model:value="createForm.description" /></n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showCreate = false">取消</n-button>
          <n-button type="primary" @click="createRole">创建</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.roles-page {
  padding: 16px;
  display: flex;
  gap: 14px;
  flex: 1;
  min-height: 0;
}

/* ── 左侧角色列表 ── */
.role-panel {
  width: 280px;
  flex: 0 0 280px;
  display: flex;
  flex-direction: column;
  min-height: 0;
  border: 1px solid var(--c-border);
  border-radius: 8px;
  background: var(--c-card-bg);
  overflow: hidden;
}
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 12px;
  border-bottom: 1px solid var(--c-border-light);
}
.panel-title { font-size: 13px; font-weight: 600; color: var(--c-text); }
.panel-sub { margin-top: 2px; font-size: 11px; color: var(--c-text-faint); }
.role-list { flex: 1; min-height: 0; overflow-y: auto; }
.role-item {
  display: block;
  width: 100%;
  padding: 10px 12px;
  border: 0;
  border-bottom: 1px solid var(--c-border-light);
  border-left: 3px solid transparent;
  background: transparent;
  color: var(--c-text);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}
.role-item:hover { background: var(--c-card-bg-hover); }
.role-item.active {
  border-left-color: var(--c-accent);
  background: var(--c-accent-weak);
}
.role-item-top { display: flex; align-items: center; gap: 6px; min-width: 0; }
.role-name { font-size: 13px; font-weight: 600; white-space: nowrap; }
.role-code {
  max-width: 96px;
  padding: 0 5px;
  border-radius: 3px;
  background: var(--c-input-bg);
  color: var(--c-text-dim);
  font: 10px/18px ui-monospace, SFMono-Regular, Consolas, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.role-system {
  padding: 0 5px;
  border: 1px solid var(--c-border);
  border-radius: 3px;
  color: var(--c-text-faint);
  font-size: 10px;
  line-height: 16px;
  white-space: nowrap;
}
.role-desc {
  margin-top: 4px;
  color: var(--c-text-dim);
  font-size: 11px;
  line-height: 1.45;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.role-meta { margin-top: 4px; color: var(--c-text-faint); font-size: 10px; }

/* ── 右侧权限区 ── */
.perm-panel {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--c-border);
  border-radius: 8px;
  background: var(--c-card-bg);
  overflow: hidden;
}
.perm-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--c-border-light);
}
.perm-head-main { min-width: 0; }
.perm-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--c-text);
}
.perm-desc {
  margin-top: 2px;
  max-width: 720px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--c-text-dim);
  font-size: 12px;
}
.perm-head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
  flex: none;
}
.perm-total {
  color: var(--c-text-faint);
  font: 11px ui-monospace, SFMono-Regular, Consolas, monospace;
  font-variant-numeric: tabular-nums;
}
.perm-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 16px;
  border-bottom: 1px solid var(--c-border-light);
}
.perm-toolbar :deep(.n-input) { max-width: 360px; }
.perm-filter-hint { color: var(--c-text-faint); font-size: 11px; white-space: nowrap; }
.perm-empty { padding: 28px 8px; text-align: center; color: var(--c-text-faint); font-size: 12px; }
.perm-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 0 16px 18px; }

.perm-group { padding-top: 14px; }
.perm-group-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 0 8px 6px;
  border-bottom: 1px solid var(--c-border-light);
}
.perm-group-label { color: var(--c-text); font-size: 12px; font-weight: 600; letter-spacing: .02em; }
.perm-group-code {
  color: var(--c-text-faint);
  font: 10px ui-monospace, SFMono-Regular, Consolas, monospace;
}
.perm-group-count {
  margin-left: auto;
  color: var(--c-text-faint);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.perm-rows { padding-top: 3px; }
.perm-row {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  min-height: 38px;
  padding: 4px 8px;
  border: 1px solid transparent;
  border-radius: 5px;
}
.perm-row:hover { background: var(--c-card-bg-hover); }
.perm-row.on { border-color: transparent; }
.perm-row.on .perm-name { font-weight: 550; }
.perm-main { display: flex; align-items: baseline; gap: 8px; min-width: 0; flex-wrap: wrap; }
.perm-name { color: var(--c-text); font-size: 12.5px; line-height: 1.4; }
.perm-code {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--c-text-faint);
  font: 10.5px ui-monospace, SFMono-Regular, Consolas, monospace;
}
.perm-controls {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  white-space: nowrap;
}
.scope-select { width: 92px; flex: none; }
.support-check { color: var(--c-text-dim); font-size: 11px; white-space: nowrap; }

@media (max-width: 900px) {
  .roles-page { flex-direction: column; padding: 10px; }
  .role-panel { width: auto; flex: none; max-height: 210px; }
  .role-list { display: flex; overflow-x: auto; overflow-y: hidden; }
  .role-item { width: 220px; flex: 0 0 220px; border-bottom: 0; border-right: 1px solid var(--c-border-light); }
  .perm-head { align-items: flex-start; }
  .perm-desc { max-width: 56vw; }
}

@media (max-width: 640px) {
  .perm-head { flex-wrap: wrap; }
  .perm-head-actions { width: 100%; justify-content: space-between; margin-left: 0; }
  .perm-row { grid-template-columns: 20px minmax(0, 1fr); row-gap: 4px; }
  .perm-controls { grid-column: 2; justify-content: flex-start; }
  .perm-code { white-space: normal; overflow-wrap: anywhere; }
}
</style>

<script setup>
import { computed, onMounted, ref } from 'vue'
import axios from 'axios'
import {
  NButton, NCheckbox, NSelect, NModal, NForm, NFormItem, NInput,
  NSpace, useMessage,
} from 'naive-ui'

const message = useMessage()
const roles = ref([])
const selectedId = ref(null)
const selected = computed(() => roles.value.find(r => r.id === selectedId.value) || null)
const perms = ref([])
const checked = ref({})
const showCreate = ref(false)
const createForm = ref({ code: '', name: '', description: '' })
const scopeOptions = [{ label: 'self', value: 'self' }, { label: 'global', value: 'global' }]

const GROUP_LABELS = {
  portfolio: '持仓与交易',
  watch: '自选分组',
  risk: '风险与告警',
  market: '大盘',
  treemap: '选股',
  stock: '个股',
  cb: '可转债',
  news: '资讯',
  signal: '信号',
  research: '研究工具',
  model: '模型',
  feature: '特征',
  function: '函数',
  dag: 'DAG 流程',
  agent: '问答 Agent',
  admin: '支持管理',
  system: '系统管理',
}
const GROUP_ORDER = [
  'portfolio', 'watch', 'risk', 'market', 'treemap', 'stock', 'cb', 'news',
  'signal', 'research', 'model', 'feature', 'function', 'dag', 'agent',
  'system', 'admin',
]
// 一级菜单权限单独成组，避免 research.cb.view 被埋在“研究数据”里
const GROUP_OVERRIDES = {
  'research.market.view': 'market',
  'research.treemap.view': 'treemap',
  'research.stock.view': 'stock',
  'research.cb.view': 'cb',
  'research.news.view': 'news',
  'research.signal.view': 'signal',
}

const PERSONAL_PREFIXES = ['portfolio.', 'watch.', 'risk.', 'signal.personal', 'agent.']
function isPersonal(code) { return PERSONAL_PREFIXES.some(p => code.startsWith(p)) }

const query = ref('')
const editablePerms = computed(() =>
  perms.value.filter(p => !p.is_public && p.code !== 'ws.connect'))
const filteredPerms = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return editablePerms.value
  return editablePerms.value.filter(p =>
    p.name.toLowerCase().includes(q) || p.code.toLowerCase().includes(q))
})

const grouped = computed(() => {
  const groups = new Map()
  for (const p of filteredPerms.value) {
    const key = GROUP_OVERRIDES[p.code] || p.code.split('.')[0]
    if (!groups.has(key)) groups.set(key, { key, label: GROUP_LABELS[key] || key, items: [] })
    groups.get(key).items.push(p)
  }
  return [...groups.values()]
    .sort((a, b) => {
      const ia = GROUP_ORDER.indexOf(a.key), ib = GROUP_ORDER.indexOf(b.key)
      return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib)
    })
    .map(g => ({
      ...g,
      selected: g.items.filter(p => checked.value[p.code]?.checked).length,
    }))
})

const checkedCount = computed(() =>
  editablePerms.value.filter(p => checked.value[p.code]?.checked).length)

async function loadRoles() {
  const r = await axios.get('/api/admin/roles')
  roles.value = r.data.items || []
  if (!selectedId.value && roles.value.length) selectRole(roles.value[0])
}
async function loadPerms() {
  const r = await axios.get('/api/admin/permissions')
  perms.value = r.data.items || []
}
async function selectRole(r) {
  selectedId.value = r.id
  const resp = await axios.get(`/api/admin/roles/${r.id}/permissions`)
  const map = {}
  for (const x of (resp.data.items || [])) {
    map[x.code] = { checked: true, scope: x.scope || 'self', support_only: !!x.support_only }
  }
  checked.value = map
}
function setChecked(p, v) {
  if (v) checked.value[p.code] = { checked: true, scope: isPersonal(p.code) ? 'self' : 'global', support_only: false }
  else delete checked.value[p.code]
}
async function save() {
  try {
    const permissions = Object.entries(checked.value).filter(([, v]) => v.checked).map(([code, v]) => ({
      code, scope: v.scope || (isPersonal(code) ? 'self' : 'global'), support_only: !!v.support_only, conditions: {},
    }))
    await axios.put(`/api/admin/roles/${selectedId.value}/permissions`, { permissions })
    message.success('权限已保存')
    loadRoles()
  } catch (e) { message.error(e.response?.data?.detail || '保存失败') }
}
async function createRole() {
  try {
    await axios.post('/api/admin/roles', createForm.value)
    showCreate.value = false
    createForm.value = { code: '', name: '', description: '' }
    message.success('角色已创建')
    loadRoles()
  } catch (e) { message.error(e.response?.data?.detail || '创建失败') }
}
onMounted(() => { loadPerms().then(loadRoles) })
</script>
