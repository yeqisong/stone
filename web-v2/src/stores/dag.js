import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import axios from 'axios'
import { addWsListener } from '../utils/ws'

export const useDagStore = defineStore('dag', () => {
  const structure = ref([])
  const nodes = ref({})     // { nodeName: 'default'|'running'|'success'|'failed' }
  const edges = ref({})     // { 'from→to': 'default'|'running'|'success'|'failed' }
  const currentRunId = ref(null)
  const currentRunLatest = ref(null)
  const hasRunning = ref(false)
  const _prevRunId = ref(null)  // 追踪上一次 run_id，检测任务切换
  let _expireTimer = null       // 任务完成 5 分钟后本地清空状态

  // WS 只推送节点状态（不含结构）
  let unwatch = null
  function initWs() {
    if (unwatch) return
    unwatch = addWsListener((data) => {
      if (data.type === 'dag_status') {
        const newRunId = data.current_run_id || null
        const newHasRunning = data.has_running || false

        // 检测任务切换：run_id 变化且新任务启动 → 清空旧状态
        const runIdChanged = newRunId !== _prevRunId.value
        if (newRunId && runIdChanged && newHasRunning) {
          resetAll()
        }
        // 检测任务过期：run_id 从有值变为 null（后端 5 分钟窗口到期）
        if (!newRunId && _prevRunId.value && !newHasRunning) {
          resetAll()
        }
        _prevRunId.value = newRunId

        currentRunId.value = newRunId
        currentRunLatest.value = data.current_run_latest || null
        hasRunning.value = newHasRunning

        // 本地 5 分钟过期计时器：不依赖 WS 推送 current_run_id=null
        if (_expireTimer) { clearTimeout(_expireTimer); _expireTimer = null }
        if (!newHasRunning && newRunId) {
          _expireTimer = setTimeout(() => {
            resetAll()
            _expireTimer = null
          }, 300000)  // 5 分钟
        }

        if (data.run_status && Object.keys(data.run_status).length > 0) {
          for (const [name, s] of Object.entries(data.run_status)) {
            nodes.value[name] = s.status
          }
        } else if (!newHasRunning) {
          resetAll()
        }
        if (data.run_status) autoEdges()
      }
    })
  }

  const nodeList = computed(() => structure.value.map(n => ({
    ...n,
    state: nodes.value[n.name] || 'default'
  })))

  const edgeList = computed(() => {
    const result = []
    for (const n of structure.value) {
      for (const dep of n.deps) {
        const key = dep + '→' + n.name
        result.push({ from: dep, to: n.name, key, state: edges.value[key] || 'default' })
      }
    }
    return result
  })

  async function loadStructure() {
    try {
      const r = await axios.get(window.location.origin + '/api/dag_config')
      structure.value = r.data.structure || []
    } catch(e) {}
    // 初始化默认状态（无任务时流程图也能渲染）
    for (const n of structure.value) {
      if (!nodes.value[n.name]) nodes.value[n.name] = 'default'
    }
    for (const n of structure.value) {
      for (const dep of n.deps) {
        const key = dep + '→' + n.name
        if (!edges.value[key]) edges.value[key] = 'default'
      }
    }
  }

  function setNodeState(name, state) {
    nodes.value[name] = state
  }

  function setEdgeState(from, to, state) {
    edges.value[from + '→' + to] = state
  }

  function setNodesStates(map) {
    for (const [name, state] of Object.entries(map)) {
      nodes.value[name] = state
    }
  }

  function autoEdges() {
    // 连线状态由两端节点状态推导：
    //   success = 左端success + 右端success
    //   running = 左端success + 右端running   (有任一running且无失败)
    //   failed  = 左端failed 或 右端failed     (有任一failed)
    //   default = 其他
    for (const n of structure.value) {
      for (const dep of n.deps) {
        const key = dep + '→' + n.name
        const fromSt = nodes.value[dep] || 'default'
        const toSt = nodes.value[n.name] || 'default'
        if (fromSt === 'failed' || toSt === 'failed') {
          edges.value[key] = 'failed'
        } else if (fromSt === 'success' && toSt === 'success') {
          edges.value[key] = 'success'
        } else if (fromSt === 'success' && toSt === 'running') {
          edges.value[key] = 'running'
        } else if (toSt === 'running') {
          // 右端running、左端还没成功→等待态(虚线)
          edges.value[key] = 'running'
        } else {
          edges.value[key] = 'default'
        }
      }
    }
  }

  function resetAll() {
    for (const n of structure.value) {
      nodes.value[n.name] = 'default'
    }
    for (const n of structure.value) {
      for (const dep of n.deps) {
        edges.value[dep + '→' + n.name] = 'default'
      }
    }
    _prevRunId.value = null
    if (_expireTimer) { clearTimeout(_expireTimer); _expireTimer = null }
  }

  return {
    structure, nodes, edges, nodeList, edgeList, currentRunId, currentRunLatest, hasRunning,
    initWs, loadStructure, setNodeState, setEdgeState, setNodesStates, autoEdges, resetAll
  }
})
