<template>
<div style="padding:16px;max-width:100%;margin:0 auto;flex:1;min-height:0;display:flex;flex-direction:column">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-shrink:0">
    <n-button size="small" quaternary @click="$emit('back')">← 返回</n-button>
    <span style="font-size:17px;font-weight:700;color:var(--c-text)">📋 执行日志</span>
    <n-button size="tiny" :type="filterFlow ? 'primary' : 'default'" @click="toggleFilter" style="margin-left:auto;font-size:11px">
      {{ filterFlow ? '📌 仅当前流程' : '📂 全部流程' }}
    </n-button>
  </div>
  <div style="display:flex;flex:1;min-height:0;gap:12px">
    <!-- Left: Task List -->
    <div style="width:280px;flex-shrink:0;overflow-y:auto;display:flex;flex-direction:column;gap:6px">
      <div v-if="!tasks.length" style="font-size:11px;color:var(--c-text-faint);padding:20px;text-align:center">暂无任务记录</div>
      <div v-for="t in tasks" :key="t.task_id"
        :style="{padding:'10px 12px',borderRadius:'8px',border:'1px solid var(--c-border)',cursor:'pointer',fontSize:'11px',background:selectedTask?.task_id===t.task_id?'rgba(32,128,240,.08)':'var(--c-card-bg)'}"
        @click="selectTask(t)">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
          <span style="font-weight:600;color:var(--c-text)">{{ t.flow_name || t.task_id }}</span>
          <span :style="{fontSize:'10px',color:t.status==='running'?'#2080f0':t.status==='completed'?'#10b981':'#ef4444'}">
            {{ t.status==='running'?'⟳ 进行中':t.status==='completed'?'✅ 完成':'❌ 失败' }}
          </span>
        </div>
        <div style="font-size:10px;color:var(--c-text-dim)">{{ t.created_at }} · {{ t.progress_pct }}%</div>
        <div v-if="t.nodes?.length" style="display:flex;flex-wrap:wrap;gap:2px;margin-top:4px">
          <span v-for="n in t.nodes" :key="n.node_name" :style="{fontSize:'9px',padding:'1px 4px',borderRadius:'3px',background:n.status==='success'?'rgba(16,185,129,.1)':n.status==='running'?'rgba(32,128,240,.1)':n.status==='failed'?'rgba(239,68,68,.1)':'rgba(100,116,139,.1)',color:n.status==='success'?'#10b981':n.status==='running'?'#2080f0':n.status==='failed'?'#ef4444':'#6b7280'}">{{ n.node_name }}</span>
        </div>
      </div>
      <n-button v-if="tasks.length >= 10" size="tiny" quaternary @click="loadAllTasks">加载更多</n-button>
    </div>

    <!-- Right: Node Details -->
    <div style="flex:1;overflow-y:auto;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:14px;font-size:12px">
      <div v-if="!selectedTask" style="text-align:center;padding:40px;color:var(--c-text-faint);font-size:12px">选择左侧任务查看详情</div>
      <template v-else>
        <div style="font-weight:600;color:var(--c-text);margin-bottom:4px">{{ selectedTask.flow_name || selectedTask.task_id }}</div>
        <div style="font-size:10px;color:var(--c-text-dim);margin-bottom:12px">任务ID: {{ selectedTask.task_id }} · 创建: {{ selectedTask.created_at }}<span v-if="selectedTask.trade_date" style="margin-left:12px">📅 业务日期: {{ selectedTask.trade_date }}</span></div>
        <div v-if="selectedTask.error" style="padding:8px;background:rgba(239,68,68,.08);border-radius:6px;font-size:11px;color:#ef4444;margin-bottom:12px">{{ selectedTask.error }}</div>
        <!-- Node List -->
        <div v-for="n in selectedTask.nodes" :key="n.node_name" style="padding:8px 0;border-bottom:1px solid var(--c-border)">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <span style="font-weight:600;color:var(--c-text)">{{ n.node_name }}</span>
            <span :style="{fontSize:'10px',color:n.status==='success'?'#10b981':n.status==='running'?'#2080f0':n.status==='failed'?'#ef4444':'#6b7280'}">
              {{ n.status==='success'?'✅':n.status==='running'?'⟳':n.status==='failed'?'❌':'◻' }} {{ n.status }}
            </span>
          </div>
          <div style="font-size:10px;color:var(--c-text-faint);margin-top:4px;display:flex;flex-wrap:wrap;gap:4px 12px">
            <span v-if="n.started_at">▶ {{ n.started_at }}</span>
            <span v-if="n.finished_at">🏁 {{ n.finished_at }}</span>
          </div>
          <div v-if="n.rows !== undefined && n.rows !== null" style="font-size:10px;color:var(--c-text-dim);margin-top:2px">📊 行数: {{ n.rows }}</div>
          <div v-if="n.detail" style="font-size:10px;color:var(--c-text-dim);margin-top:2px">📝 {{ n.detail }}</div>
          <div v-if="n.error" style="font-size:10px;color:#ef4444;margin-top:2px">❌ {{ n.error }}</div>
        </div>
      </template>
    </div>
  </div>
</div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { NButton } from 'naive-ui'
import axios from 'axios'

defineEmits(['back'])
const props = defineProps({ flowId: [Number, String] })
const API = window.location.origin

const tasks = ref([])
const selectedTask = ref(null)
const filterFlow = ref(props.flowId ? parseInt(props.flowId) : 0)

function toggleFilter() {
  filterFlow.value = filterFlow.value ? 0 : (parseInt(props.flowId) || 0)
}

async function loadAllTasks() {
  try {
    const fid = filterFlow.value
    const r = await axios.get(API + '/api/dag/logs?limit=50' + (fid ? '&flow_id=' + fid : ''))
    tasks.value = r.data?.items || []
  } catch(e) {}
}

watch(filterFlow, () => loadAllTasks())

function selectTask(t) {
  selectedTask.value = t
  if (props.flowId && t.task_id) {
    history.pushState(null, '', '#/dag-logs/' + props.flowId + '/' + t.task_id)
  }
}

onMounted(() => {
  loadAllTasks().then(() => {
    const hash = location.hash.slice(1)
    const parts = hash.split('/')
    if (parts.length >= 4 && parts[3]) {
      const found = tasks.value.find(t => t.task_id === parts[3])
      if (found) selectedTask.value = found
    }
  })
})
</script>