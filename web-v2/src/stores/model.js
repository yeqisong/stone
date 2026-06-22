import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import axios from 'axios'

export const useModelStore = defineStore('model', () => {
  const versions = ref([])
  const selectedId = ref(null)
  const detailTab = ref('train')
  const sidebarOpen = ref(window.innerWidth >= 768)  // basic | train | eval | live | indicators
  const isMock = ref(false)       // 是否为 mock 数据（API 不可用时的兜底展示）

  const selected = computed(() => versions.value.find(v => v.version === selectedId.value) || null)

  const statusBadge = (s) => {
    return { DRAFT: 'info', TRAINING: 'warning', VALIDATING: 'warning', PENDING: 'warning', ACTIVE: 'success', REJECTED: 'error', ARCHIVED: 'default' }[s] || 'default'
  }

  const statusLabel = (s) => {
    return { DRAFT: '草稿', TRAINING: '训练中', VALIDATING: '验证中', PENDING: '待审批', ACTIVE: '已上线', REJECTED: '已拒绝', ARCHIVED: '已归档' }[s] || s
  }

  async function loadVersions() {
    try {
      const r = await axios.get(window.location.origin + '/api/v1/models')
      versions.value = r.data?.versions || []
      isMock.value = false
    } catch(e) {
      isMock.value = true
      versions.value = []
    }
  }

  function selectVersion(version) {
    selectedId.value = version
    detailTab.value = 'indicators'  // 先确认指标数据再训练
  }

  function switchTab(tab) {
    detailTab.value = tab
  }

  async function checkDelete(version) {
    const r = await axios.get(window.location.origin + `/api/v1/models/${version}/delete-check`)
    return r.data
  }

  async function deleteVersion(version, mode = 'soft') {
    await axios.delete(window.location.origin + `/api/v1/models/${version}`, { params: { mode } })
    // 删除后从本地列表移除
    versions.value = versions.value.filter(v => v.version !== version)
    // 如果删除的是当前选中项，自动选中第一个
    if (selectedId.value === version) {
      selectedId.value = versions.value.length > 0 ? versions.value[0].version : null
    }
  }

  return { versions, selectedId, detailTab, selected, statusBadge, statusLabel, isMock, sidebarOpen, loadVersions, selectVersion, switchTab, checkDelete, deleteVersion }
})