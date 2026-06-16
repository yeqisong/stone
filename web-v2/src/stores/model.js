import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import axios from 'axios'

export const useModelStore = defineStore('model', () => {
  const versions = ref([])
  const selectedId = ref(null)
  const detailTab = ref('train')  // basic | train | eval | live | indicators

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
      if (r.data?.versions?.length) {
        versions.value = r.data.versions
        return
      }
    } catch(e) {
      // API 未就绪时使用 mock 数据
    }
    versions.value = [
      { version: 'v2.1', model_name: '布林+MACD+RSI 多策略融合', status: 'ACTIVE', sharpe: 2.15, win_rate: 0.58, created_at: '2026-06-10' },
      { version: 'v2.0', model_name: '布林+MACD 双策略', status: 'ARCHIVED', sharpe: 1.82, win_rate: 0.52, created_at: '2026-05-20' },
      { version: 'v1.3', model_name: '布林+MACD+量能 三策略', status: 'PENDING', sharpe: 2.31, win_rate: 0.61, created_at: '2026-06-08' },
      { version: 'v1.2', model_name: 'RSI+量能 实验策略', status: 'DRAFT', sharpe: null, win_rate: null, created_at: '2026-06-12' },
    ]
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

  return { versions, selectedId, detailTab, selected, statusBadge, statusLabel, loadVersions, selectVersion, switchTab, checkDelete, deleteVersion }
})