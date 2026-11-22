<template>
  <div style="padding:12px 16px;display:flex;flex-direction:column;gap:10px;flex:1;min-height:0">
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <n-input v-model:value="q" size="small" placeholder="搜索用户名/显示名" style="width:220px" clearable @keyup.enter="load" />
      <n-select v-model:value="status" size="small" :options="statusOptions" style="width:130px" clearable placeholder="状态" />
      <n-button size="small" type="primary" @click="load">查询</n-button>
      <n-button size="small" type="primary" ghost @click="openCreate">+ 新建用户</n-button>
      <span style="margin-left:auto;font-size:12px;color:var(--c-text-dim)">共 {{ total }} 个用户</span>
    </div>

    <n-data-table :columns="columns" :data="rows" :loading="loading" :bordered="false"
                  size="small" :row-key="r => r.id" style="flex:1;min-height:0" />

    <n-modal v-model:show="showCreate" preset="card" title="新建用户" style="width:460px">
      <n-form label-placement="left" label-width="80">
        <n-form-item label="用户名"><n-input v-model:value="form.username" /></n-form-item>
        <n-form-item label="显示名"><n-input v-model:value="form.display_name" /></n-form-item>
        <n-form-item label="初始密码"><n-input v-model:value="form.password" type="password" /></n-form-item>
        <n-form-item label="角色">
          <n-select v-model:value="form.role_codes" multiple :options="roleOptions" />
        </n-form-item>
        <n-form-item label="首次改密">
          <n-switch v-model:value="form.must_change_password" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end"><n-button @click="showCreate=false">取消</n-button><n-button type="primary" @click="createUser">创建</n-button></n-space>
      </template>
    </n-modal>

    <n-modal v-model:show="showRoles" preset="card" :title="'分配角色：' + (editing?.username || '')" style="width:420px">
      <n-select v-model:value="editRoles" multiple :options="roleOptions" />
      <template #footer>
        <n-space justify="end"><n-button @click="showRoles=false">取消</n-button><n-button type="primary" @click="saveRoles">保存</n-button></n-space>
      </template>
    </n-modal>

    <n-modal v-model:show="showSupport" preset="card" title="创建支持会话（只读）" style="width:460px">
      <n-form label-placement="left" label-width="80">
        <n-form-item label="目标用户"><n-input :value="supportTarget" disabled /></n-form-item>
        <n-form-item label="理由"><n-input v-model:value="supportReason" type="textarea" :rows="3" placeholder="至少 10 字，说明协助排查的问题" /></n-form-item>
        <n-form-item label="时长"><n-select v-model:value="supportTtl" :options="ttlOptions" /></n-form-item>
      </n-form>
      <div v-if="supportToken" style="font-size:12px;color:var(--c-text-dim);word-break:break-all">
        支持令牌（仅显示一次）：<code>{{ supportToken }}</code>
      </div>
      <template #footer>
        <n-space justify="end"><n-button @click="showSupport=false">关闭</n-button><n-button type="primary" @click="createSupport">创建</n-button></n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup>
import { h, onMounted, ref } from 'vue'
import axios from 'axios'
import {
  NButton, NTag, NSpace, NInput, NSelect, NDataTable, NModal, NForm,
  NFormItem, NSwitch, useMessage,
} from 'naive-ui'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const message = useMessage()
const rows = ref([])
const roles = ref([])
const total = ref(0)
const q = ref('')
const status = ref('')
const loading = ref(false)
const showCreate = ref(false)
const showRoles = ref(false)
const showSupport = ref(false)
const editing = ref(null)
const editRoles = ref([])
const supportTarget = ref('')
const supportReason = ref('')
const supportTtl = ref(30)
const supportToken = ref('')
const supportUserId = ref(null)

const statusOptions = [{ label: '正常', value: 'active' }, { label: '禁用', value: 'disabled' }]
const ttlOptions = [10, 30, 60].map(v => ({ label: v + ' 分钟', value: v }))
const roleOptions = ref([])
const form = ref({ username: '', display_name: '', password: '', role_codes: ['trader'], must_change_password: true })

const columns = [
  { title: 'ID', key: 'id', width: 60 },
  { title: '用户名', key: 'username', width: 130 },
  { title: '显示名', key: 'display_name', width: 120 },
  { title: '角色', key: 'roles', render: r => h(NSpace, { size: 2 }, () => (r.roles || []).map(x => h(NTag, { size: 'small' }, () => x))) },
  { title: '状态', key: 'status', width: 80, render: r => h(NTag, { size: 'small', type: r.status === 'active' ? 'success' : 'error' }, () => r.status === 'active' ? '正常' : '禁用') },
  { title: '最后登录', key: 'last_login_at', width: 160 },
  {
    title: '操作', key: 'actions', width: 300, render: r => h(NSpace, { size: 2 }, () => [
      h(NButton, { size: 'tiny', onClick: () => openRoles(r) }, () => '改角色'),
      h(NButton, { size: 'tiny', onClick: () => resetPassword(r) }, () => '重置密码'),
      h(NButton, { size: 'tiny', type: r.status === 'active' ? 'error' : 'success', ghost: true, onClick: () => toggleStatus(r) }, () => r.status === 'active' ? '禁用' : '启用'),
      h(NButton, { size: 'tiny', onClick: () => openSupport(r) }, () => '支持会话'),
    ]),
  },
]

async function load() {
  loading.value = true
  try {
    const r = await axios.get('/api/admin/users', { params: { q: q.value, status: status.value, limit: 200 } })
    rows.value = r.data.items || []
    total.value = r.data.total || rows.value.length
  } catch (e) {
    message.error(e.response?.data?.detail || '加载用户失败')
  } finally { loading.value = false }
}

async function loadRoles() {
  try {
    const r = await axios.get('/api/admin/roles')
    roles.value = r.data.items || []
    roleOptions.value = roles.value.filter(x => x.status === 'active').map(x => ({ label: `${x.name} (${x.code})`, value: x.code }))
  } catch (_) {}
}

function openCreate() {
  form.value = { username: '', display_name: '', password: '', role_codes: ['trader'], must_change_password: true }
  showCreate.value = true
}

async function createUser() {
  try {
    await axios.post('/api/admin/users', form.value)
    showCreate.value = false
    message.success('用户已创建')
    load()
  } catch (e) { message.error(e.response?.data?.detail || '创建失败') }
}

function openRoles(r) {
  editing.value = r
  editRoles.value = [...(r.roles || [])]
  showRoles.value = true
}
async function saveRoles() {
  try {
    await axios.put(`/api/admin/users/${editing.value.id}/roles`, { role_codes: editRoles.value })
    showRoles.value = false
    message.success('角色已更新')
    load()
  } catch (e) { message.error(e.response?.data?.detail || '保存失败') }
}

async function resetPassword(r) {
  if (!window.confirm(`确认重置 ${r.username} 的密码？`)) return
  try {
    const resp = await axios.post(`/api/admin/users/${r.id}/reset-password`)
    message.success(`已重置。一次性密码：${resp.data.one_time_password}`, { duration: 15000 })
  } catch (e) { message.error(e.response?.data?.detail || '重置失败') }
}

async function toggleStatus(r) {
  const next = r.status === 'active' ? 'disabled' : 'active'
  if (!window.confirm(`确认${next === 'disabled' ? '禁用' : '启用'} ${r.username}？`)) return
  try {
    await axios.patch(`/api/admin/users/${r.id}/status`, { status: next })
    message.success('状态已更新')
    load()
  } catch (e) { message.error(e.response?.data?.detail || '操作失败') }
}

function openSupport(r) {
  supportTarget.value = `${r.username} (#${r.id})`
  supportUserId.value = r.id
  supportReason.value = ''
  supportTtl.value = 30
  supportToken.value = ''
  showSupport.value = true
}
async function createSupport() {
  try {
    const resp = await axios.post('/api/admin/support/sessions', {
      target_user_id: supportUserId.value,
      reason: supportReason.value,
      ttl_minutes: supportTtl.value,
    })
    supportToken.value = resp.data.support_token
    message.success('支持会话已创建，令牌仅显示一次')
  } catch (e) { message.error(e.response?.data?.detail || '创建失败') }
}

onMounted(() => { load(); loadRoles() })
</script>
