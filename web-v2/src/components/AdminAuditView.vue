<template>
  <div style="padding:12px 16px;display:flex;flex-direction:column;gap:10px;flex:1;min-height:0">
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <n-input v-model:value="actor" size="small" placeholder="操作者" style="width:160px" clearable />
      <n-input v-model:value="action" size="small" placeholder="动作，如 model.train" style="width:180px" clearable />
      <n-input v-model:value="resource" size="small" placeholder="资源类型，如 user" style="width:160px" clearable />
      <n-button size="small" type="primary" @click="load">查询</n-button>
    </div>
    <n-data-table :columns="columns" :data="rows" :loading="loading" size="small" :bordered="false"
                  :row-key="r => r.id" style="flex:1;min-height:0" @update:page="p => { page = p; load() }" />
    <n-modal v-model:show="showDetail" preset="card" title="审计详情" style="width:640px">
      <pre style="white-space:pre-wrap;word-break:break-all;font-size:12px;color:var(--c-text)">{{ detailText }}</pre>
    </n-modal>
  </div>
</template>

<script setup>
import { h, onMounted, ref } from 'vue'
import axios from 'axios'
import { NButton, NInput, NDataTable, NModal, useMessage } from 'naive-ui'

const message = useMessage()
const rows = ref([])
const loading = ref(false)
const actor = ref('')
const action = ref('')
const resource = ref('')
const page = ref(1)
const showDetail = ref(false)
const detailText = ref('')

const columns = [
  { title: '时间', key: 'created_at', width: 160 },
  { title: '操作者', key: 'actor', width: 110 },
  { title: '动作', key: 'action', width: 190 },
  { title: '资源', key: 'resource_type', width: 100 },
  { title: '资源 ID', key: 'resource_id', width: 100 },
  { title: '目标用户', key: 'target_user_id', width: 90 },
  { title: 'IP', key: 'ip', width: 130 },
  { title: '', key: 'op', width: 70, render: r => h(NButton, { size: 'tiny', onClick: () => showRow(r) }, () => '详情') },
]

async function load() {
  loading.value = true
  try {
    const r = await axios.get('/api/admin/audit', { params: {
      actor: actor.value, action: action.value, resource: resource.value,
      limit: 100, offset: (page.value - 1) * 100,
    } })
    rows.value = r.data.items || []
  } catch (e) {
    message.error(e.response?.data?.detail || '加载审计失败')
  } finally { loading.value = false }
}
function showRow(r) {
  detailText.value = JSON.stringify(r, null, 2)
  showDetail.value = true
}
onMounted(load)
</script>
