<template>
<div>
  <div style="font-size:12px;font-weight:600;color:#ddd;margin-bottom:6px">📊 数据状态</div>
  <div v-if="store.structure.length===0" style="padding:10px;text-align:center;color:rgba(255,255,255,.25);font-size:11px">加载中...</div>
  <div v-else style="overflow-x:auto;padding:4px 0">
    <svg :width="svgW" :height="svgH" style="display:block;margin:0 auto">
      <g v-for="e in store.edgeList" :key="e.key">
        <path :d="edgePaths[e.key]||''" fill="none" :stroke="edgeColor(e.state)" stroke-width="2.5"
          :stroke-dasharray="e.state==='running'?'6,4':'none'"
          :class="e.state==='running'?'flow-line':''" />
      </g>
      <g v-for="n in nodesWithPos" :key="n.name">
        <rect v-if="n.x!=null" :x="n.x-48" :y="n.y-18" width="96" height="36" rx="8" ry="8" :fill="nodeBg(n.state)" :stroke="nodeBd(n.state)" stroke-width="2" :class="n.state==='running'?'node-breathing':''" />
        <text v-if="n.x!=null" :x="n.x" :y="n.y+5" text-anchor="middle" :fill="nodeText(n.state)" font-size="12" font-weight="600">{{n.label}}</text>
        <text v-if="n.state!=='default' && n.x!=null" :x="n.x" :y="n.y+14" text-anchor="middle" :fill="nodeSub(n.state)" font-size="9">{{n.name==='cron'?'2026-06-12':''}}</text>
      </g>
    </svg>
  </div>
</div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { NButton } from 'naive-ui'
import axios from 'axios'
import { useDagStore } from '../stores/dag'

const store = useDagStore()
const svgW = ref(600)
const svgH = ref(400)

// ── 自动布局（Sugiyama 启发式：层级分配 + 交叉减少） ──

function computeLayout(structure) {
  const U = 66  // 一个基本单位 = H+2h (H=36, h=15)

  // 1. 计算层级
  const levelMap = {}, nameMap = {}
  for (const n of structure) nameMap[n.name] = n

  function getLevel(name) {
    if (levelMap[name] !== undefined) return levelMap[name]
    const node = nameMap[name]
    if (!node || node.deps.length === 0) { levelMap[name] = 0; return 0 }
    let maxDep = -1
    for (const dep of node.deps) { const dl = getLevel(dep); if (dl > maxDep) maxDep = dl }
    levelMap[name] = maxDep + 1
    return levelMap[name]
  }
  for (const n of structure) getLevel(n.name)

  // 2. 按 level 分组
  const group = {}
  for (const [name, lv] of Object.entries(levelMap)) {
    (group[lv] || (group[lv] = [])).push(name)
  }
  const sortedLevels = Object.keys(group).sort((a,b)=>a-b).map(l => group[l])

  // 3. 交叉减少（重心排序，同前）
  for (let li = 0; li < sortedLevels.length - 1; li++) {
    const lv = sortedLevels[li], next = sortedLevels[li + 1]
    const bc = {}
    for (const name of lv) {
      const targets = next.filter(t => nameMap[t] && nameMap[t].deps.includes(name))
      bc[name] = targets.length > 0 ? targets.reduce((s,t)=>s+next.indexOf(t),0)/targets.length : -1
    }
    lv.sort((a,b) => (bc[a]||-1) - (bc[b]||-1))
  }
  for (let li = sortedLevels.length - 1; li > 0; li--) {
    const lv = sortedLevels[li], prev = sortedLevels[li - 1]
    const bc = {}
    for (const name of lv) {
      const sources = prev.filter(s => nameMap[name].deps.includes(s))
      bc[name] = sources.length > 0 ? sources.reduce((s,t)=>s+prev.indexOf(t),0)/sources.length : -1
    }
    lv.sort((a,b) => (bc[a]||-1) - (bc[b]||-1))
  }

  // 4. 计算每个节点的垂直跨度（子节点数，至少1）
  const span = {}  // name → 垂直单位数
  for (let li = sortedLevels.length - 1; li >= 0; li--) {
    for (const name of sortedLevels[li]) {
      const children = sortedLevels[li+1] ? sortedLevels[li+1].filter(t => nameMap[t] && nameMap[t].deps.includes(name)) : []
      span[name] = Math.max(children.length, 1)
    }
  }

  // 5. 分配Y坐标（父节点相对子节点居中）
  const yPos = {}
  const LEVEL_GAP = 140, PAD_L = 60

  function assignY(name, parentY, parentSpan) {
    if (yPos[name] !== undefined) return  // 已经分配过（处理DAG共享节点）
    const node = nameMap[name]
    // 在父节点的跨度内，找到此节点的偏移位置
    const li = levelMap[name]
    const peers = sortedLevels[li]
    const peersBefore = peers.filter(t => {
      // 找出同一level中排在此节点之前的节点
      const idx = peers.indexOf(t)
      return idx < peers.indexOf(name)
    })
    const offsetBefore = peersBefore.reduce((sum, p) => sum + span[p], 0)
    const totalPeerSpan = peers.reduce((sum, p) => sum + span[p], 0)

    // 此节点在父布局中的中心Y
    const centerOffset = (offsetBefore + span[name] / 2) / totalPeerSpan
    yPos[name] = parentY + (centerOffset - 0.5) * parentSpan * U
  }

  // 总高度 = 所有层级中最大跨度（子节点数之和最高的那层）
  let maxLevelSpan = 0
  for (const lv of sortedLevels) {
    const lvSpan = lv.reduce((sum, n) => sum + span[n], 0)
    if (lvSpan > maxLevelSpan) maxLevelSpan = lvSpan
  }
  maxLevelSpan = Math.max(maxLevelSpan, 1)
  const PAD_T = 20

  // 先分配根层节点的Y（在画布总高度内居中）
  const rootLevel = sortedLevels[0]
  const rootSpan = rootLevel.reduce((sum, n) => sum + span[n], 0)
  const rootOffsetY = (maxLevelSpan - rootSpan) / 2 * U
  let offset = 0
  for (const name of rootLevel) {
    yPos[name] = { sum: PAD_T + rootOffsetY + (offset + span[name] / 2) * U, count: 1 }
    offset += span[name]
  }

  // 逐层向下分配（children 查所有下游层级，不限于下一层）
  for (let li = 0; li < sortedLevels.length - 1; li++) {
    for (const name of sortedLevels[li]) {
      // 找出所有下游层级中依赖此节点的子节点
      const children = []
      for (let j = li + 1; j < sortedLevels.length; j++) {
        for (const t of sortedLevels[j]) {
          if (nameMap[t] && nameMap[t].deps.includes(name)) children.push(t)
        }
      }
      const childSpanTotal = children.reduce((sum, c) => sum + span[c], 0)
      const parentPos = yPos[name]
      const parentCenter = typeof parentPos === 'object' ? parentPos.sum / parentPos.count : parentPos
      const parentSpan = span[name]

      // 该节点在parent总跨度中的起始偏移
      const peerIdx = sortedLevels[li].indexOf(name)
      const peersBefore = sortedLevels[li].slice(0, peerIdx)
      const offsetBefore = peersBefore.reduce((sum, p) => sum + span[p], 0)
      const parentTotalSpan = sortedLevels[li].reduce((sum, p) => sum + span[p], 0)
      // parent在ROOT_Y参考系中的偏移比例
      const ratioOffset = offsetBefore / parentTotalSpan

      // 子节点在该父节点跨度内居中排列
      let childOffset = 0
      for (const child of children) {
        const childCenterInParent = (childOffset + span[child] / 2) / childSpanTotal
        const absoluteY = parentCenter + (childCenterInParent - 0.5) * childSpanTotal * U
        // 多个父节点共享子节点 → 收集所有位置后取真平均
        if (!yPos[child]) yPos[child] = { sum: 0, count: 0 }
        if (typeof yPos[child] === 'object') {
          yPos[child].sum += absoluteY
          yPos[child].count += 1
        }
        childOffset += span[child]
      }
    }
  }

  // 转换共享节点的平均Y
  for (const [name, v] of Object.entries(yPos)) {
    if (typeof v === 'object' && v.count > 0) {
      yPos[name] = v.sum / v.count
    }
  }

  // 6. 计算宽高
  svgH.value = Math.max(maxLevelSpan * U + 40, 300)
  svgW.value = Math.max(sortedLevels.length * LEVEL_GAP + PAD_L + 40, 600)

  // 7. 分配X坐标
  const positions = {}
  for (const [name, y] of Object.entries(yPos)) {
    const actualY = typeof y === 'object' ? y.sum / y.count : y
    const li = levelMap[name]
    positions[name] = { x: PAD_L + li * LEVEL_GAP, y: actualY }
  }

  // 5. 保存坐标
  nodePositions.value = positions

  // 6. 为每条边生成 path（存入独立 ref，不受 edgeList 重建影响）
  const paths = {}
  for (const e of store.edgeList) {
    const f = positions[e.from], t = positions[e.to]
    if (f && t) {
      const dx = Math.abs((t.x-48) - (f.x+48)) * 0.45
      paths[e.key] = `M ${f.x+48} ${f.y} C ${f.x+48+dx} ${f.y}, ${t.x-48-dx} ${t.y}, ${t.x-48} ${t.y}`
    }
  }
  edgePaths.value = paths
}

// ── 颜色函数 ──

function nodeBg(s) {
  return {default:'rgba(255,255,255,.04)', success:'rgba(16,185,129,0.15)', failed:'rgba(239,68,68,0.15)', running:'rgba(32,128,240,0.15)'}[s]||'rgba(255,255,255,.04)'
}
function nodeBd(s) {
  return {default:'rgba(255,255,255,.2)', success:'#10b981', failed:'#ef4444', running:'#2080f0'}[s]||'rgba(255,255,255,.2)'
}
function nodeText(s) {
  if (s==='running') return '#2080f0'
  if (s==='success') return '#10b981'
  if (s==='failed') return '#ef4444'
  return '#fff'
}
function nodeSub(s) {
  return s==='running'?'rgba(32,128,240,.5)':'rgba(255,255,255,.35)'
}
function edgeColor(s) {
  return {default:'rgba(255,255,255,.12)', success:'#10b981', failed:'#ef4444', running:'#2080f0'}[s]||'rgba(255,255,255,.12)'
}

// ── 预览 ──

const nodePositions = ref({})  // name → {x, y}
const edgePaths = ref({})      // edgeKey → path d
const nodesWithPos = computed(() => store.nodeList.map(n => ({
  ...n,
  x: (nodePositions.value[n.name] || {}).x,
  y: (nodePositions.value[n.name] || {}).y,
})))
let dagPollTimer = null

async function pollDagStatus() {
  try {
    const r = await axios.get(window.location.origin + '/api/dag_status')
    const runStatus = r.data.run_status || {}
    const now = Date.now()
    let anyRunning = false, newestTime = 0

    for (const [name, s] of Object.entries(runStatus)) {
      store.setNodeState(name, s.status)
      if (s.status === 'running') anyRunning = true
      if (s.time) {
        const ts = new Date(s.time.replace(' ', 'T')).getTime()
        if (ts > newestTime) newestTime = ts
      }
    }
    store.autoEdges()

    // 全部结束超过 5 分钟 → 恢复默认态
    if (!anyRunning && newestTime > 0 && (now - newestTime > 300000)) {
      store.resetAll()
    }
  } catch(e) {}
}

async function init() {
  await store.loadStructure()
  computeLayout(store.structure)
  // 启动轮询：每 5 秒更新一次 DAG 状态
  dagPollTimer = setInterval(pollDagStatus, 5000)
}

onMounted(init)
onUnmounted(() => {
  if (dagPollTimer) clearInterval(dagPollTimer)
})
</script>

<style>
.node-breathing { animation: breathe 1.5s ease-in-out infinite; }
.node-ripple { animation: breathe 1.8s ease-in-out infinite; }

.flow-line { animation: flowDash 0.8s linear infinite; }
@keyframes breathe {
  0% { filter: drop-shadow(0 0 3px rgba(32,128,240,0.2)); opacity: 0.7; }
  50% { filter: drop-shadow(0 0 12px rgba(32,128,240,0.6)); opacity: 1; }
  100% { filter: drop-shadow(0 0 3px rgba(32,128,240,0.2)); opacity: 0.7; }
}
@keyframes flowDash { to { stroke-dashoffset: -20; } }
</style>
