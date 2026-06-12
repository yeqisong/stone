import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import axios from 'axios'

export const useDagStore = defineStore('dag', () => {
  const structure = ref([])
  const nodes = ref({})     // { nodeName: 'default'|'running'|'success'|'failed' }
  const edges = ref({})     // { 'from→to': 'default'|'running'|'success'|'failed' }

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
      const r = await axios.get(window.location.origin + '/api/dag_status')
      structure.value = r.data.structure || []
    } catch(e) {
      // fallback: 硬编码结构
      structure.value = [
        {name:'cron',deps:[],label:'⏰ Corn'},
        {name:'daily_update',deps:['cron'],label:'更新汇总'},
        {name:'kline',deps:['daily_update'],label:'A股日K线'},
        {name:'index',deps:['daily_update'],label:'指数'},
        {name:'etf',deps:['daily_update'],label:'ETF'},
        {name:'fund',deps:['daily_update'],label:'基本面'},
        {name:'treemap',deps:['kline'],label:'树图'},
        {name:'strategy',deps:['kline'],label:'策略'},
        {name:'stats',deps:['treemap','strategy','index','etf','fund'],label:'统计'},
      ]
    }
    // 初始化状态
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
  }

  return {
    structure, nodes, edges, nodeList, edgeList,
    loadStructure, setNodeState, setEdgeState, setNodesStates, autoEdges, resetAll
  }
})
