<template>
<div>
  <n-spin v-if="loading" style="padding:60px" />
  <template v-else>
    <div :style="{display:'flex',gap:0,height:'calc(100vh - 110px)'}">
      <!-- Left: Version List (mobile collapsible, PC always visible) -->
      <div v-if="showSidebar" :style="{width:'280px',maxWidth:'100%',flexShrink:0,borderRight:'1px solid var(--c-border)',display:'flex',flexDirection:'column',overflow:'hidden',zIndex:10,background:'var(--c-bg)'}">
        <div style="padding:10px 16px;display:flex;align-items:center;justify-content:space-between">
          <span style="font-size:13px;font-weight:600;color:var(--c-text)">模型版本</span>
          <div style="display:flex;gap:4px">
            <n-button size="tiny" type="primary" ghost @click="showCreate=true">+ 创建</n-button>
            <n-button v-if="isMobile" size="tiny" quaternary @click="showSidebar=false" style="padding:0 4px" title="收起列表">
            <svg viewBox="0 0 1024 1024" width="18" height="18" style="fill:var(--c-text-dim)"><path d="M130.9 529.5l149.2 130.5a8.5 8.5 0 0 0 14.1-6.4V392.5a8.5 8.5 0 0 0-14.1-6.4L130.9 516.6a8.5 8.5 0 0 0 0 12.8z"/><path d="M128 213.3h682.7q42.7 0 42.7 42.7v0q0 42.7-42.7 42.7H128q-42.7 0-42.7-42.7v0q0-42.7 42.7-42.7z"/><path d="M128 725.3h682.7q42.7 0 42.7 42.7v0q0 42.7-42.7 42.7H128q-42.7 0-42.7-42.7v0q0-42.7 42.7-42.7z"/><path d="M384 469.3h426.7q42.7 0 42.7 42.7v0q0 42.7-42.7 42.7H384q-42.7 0-42.7-42.7v0q0-42.7 42.7-42.7z"/></svg>
          </n-button>
          </div>
        </div>
        <div style="flex:1;overflow-y:auto;padding:0 8px">
          <div v-for="v in store.versions" :key="v.version"
            :style="{padding:'12px',marginBottom:'4px',borderRadius:'8px',border:'1px solid '+(store.selectedId===v.version?'var(--c-border)':'transparent'),cursor:'pointer',background:store.selectedId===v.version?'var(--c-card-bg-hover)':'transparent'}"
            @click="store.selectVersion(v.version)"
            @mouseenter="hoveredVersion = v.version" @mouseleave="hoveredVersion = null">
            <div style="display:flex;align-items:center;justify-content:space-between">
              <div style="display:flex;align-items:center;gap:8px">
                <span style="font-size:15px;font-weight:700;color:var(--c-text)">{{v.version}}</span>
                <n-tag :type="store.statusBadge(v.status)" size="tiny" :bordered="false">{{store.statusLabel(v.status)}}</n-tag>
              </div>
              <n-button v-if="!store.isMock && v.status !== 'ACTIVE' && hoveredVersion === v.version" text size="tiny" type="error" style="font-size:12px;padding:0 4px" @click.stop="handleDeleteClick(v)" title="删除模型">✕</n-button>
            </div>
            <div style="font-size:11px;color:var(--c-text-dim);margin-top:4px">{{v.model_name}}</div>
            <div style="display:flex;gap:12px;margin-top:6px;font-size:10px;color:var(--c-text-faint)">
              <span v-if="v.sharpe!=null">📈 夏普 {{v.sharpe}}</span>
              <span v-if="v.win_rate!=null">✅ {{(v.win_rate*100).toFixed(0)}}%</span>
              <span>📅 {{(v.created_at||'').slice(5)}}</span>
            </div>
          </div>
          <n-empty v-if="!store.versions.length" description="暂无模型版本" style="padding:40px 0" />
        </div>
      </div>

      <!-- Right: Detail -->
      <div style="flex:1;min-width:0;overflow-y:auto;padding:16px 12px">
        <n-button v-if="isMobile && !showSidebar" size="tiny" quaternary @click="showSidebar=true" style="margin-bottom:8px" title="展开列表">
          <svg viewBox="0 0 1024 1024" width="18" height="18" style="fill:var(--c-text-dim)"><path d="M130.9 529.5l149.2 130.5a8.5 8.5 0 0 0 14.1-6.4V392.5a8.5 8.5 0 0 0-14.1-6.4L130.9 516.6a8.5 8.5 0 0 0 0 12.8z"/><path d="M128 213.3h682.7q42.7 0 42.7 42.7v0q0 42.7-42.7 42.7H128q-42.7 0-42.7-42.7v0q0-42.7 42.7-42.7z"/><path d="M128 725.3h682.7q42.7 0 42.7 42.7v0q0 42.7-42.7 42.7H128q-42.7 0-42.7-42.7v0q0-42.7 42.7-42.7z"/><path d="M384 469.3h426.7q42.7 0 42.7 42.7v0q0 42.7-42.7 42.7H384q-42.7 0-42.7-42.7v0q0-42.7 42.7-42.7z"/></svg>
        </n-button>
        <template v-if="store.selected">
          <div style="font-size:16px;font-weight:700;color:var(--c-text);margin-bottom:14px">
            {{store.selected.version}} · {{store.selected.model_name}}
          </div>

          <div style="display:flex;gap:2px;margin-bottom:16px;border-bottom:1px solid var(--c-border);overflow-x:auto;-webkit-overflow-scrolling:touch">
            <button v-for="t in tabs" :key="t.key"
              :style="{padding:'8px 14px',border:'none',background:'transparent',color:store.detailTab===t.key?'var(--c-text)':'var(--c-text-dim)',fontSize:'12px',cursor:'pointer',borderBottom:store.detailTab===t.key?'2px solid #2080f0':'2px solid transparent',marginBottom:'-1px',flexShrink:0,whiteSpace:'nowrap'}"
              @click="store.switchTab(t.key)">{{t.label}}</button>
          </div>

          <div v-if="store.detailTab==='basic'" style="display:flex;flex-direction:column;gap:14px">
            <div style="display:flex;gap:16px;flex-wrap:wrap">
              <div v-for="m in basicMetrics" :key="m.label" style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:10px 14px;min-width:80px;text-align:center">
                <div style="font-size:10px;color:var(--c-text-faint)">{{m.label}}</div>
                <div :style="{fontSize:'16px',fontWeight:700,color:m.color||'var(--c-text)'}">{{m.value}}</div>
              </div>
            </div>
            <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:14px">
              <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:8px">配置详情</div>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:11px">
                <div><span style="color:var(--c-text-faint)">特征: </span><span style="color:var(--c-text)">{{(cfg.features||[]).join(', ')||'—'}}</span></div>
                <div><span style="color:var(--c-text-faint)">ML增强: </span><span style="color:var(--c-text)">{{cfg.ml_enabled?'启用':'关闭'}}</span></div>
                <div><span style="color:var(--c-text-faint)">训练区间: </span><span style="color:var(--c-text)">{{cfg.train_start}} ~ {{cfg.train_end}}</span></div>
                <div><span style="color:var(--c-text-faint)">测试区间: </span><span style="color:var(--c-text)">{{cfg.test_start}} ~ {{cfg.test_end||'至今'}}</span></div>
                <div><span style="color:var(--c-text-faint)">止损: </span><span style="color:var(--c-text)">{{cfg.risk?.stop_loss_pct||8}}%</span></div>
                <div><span style="color:var(--c-text-faint)">信号超时: </span><span style="color:var(--c-text)">{{cfg.risk?.signal_timeout_days||20}}天</span></div>
              </div>
            </div>

          </div>
          <div v-else-if="store.detailTab==='train'">
            <div v-if="store.selected.status==='DRAFT'" style="margin-bottom:14px">
              <n-button type="primary" @click="startTrain">开始训练</n-button>
            </div>
            <ModelTraining :version="store.selected" />
          </div>
          <div v-else-if="store.detailTab==='eval'">
            <div v-if="store.selected.status==='PENDING'" style="margin-bottom:14px;display:flex;gap:8px">
              <n-button type="success" @click="approveModel">审批通过</n-button>
              <n-button type="error" @click="rejectModel">拒绝</n-button>
            </div>
            <ModelEval :version="store.selected" />
          </div>
          <ModelLive v-else-if="store.detailTab==='live'" :version="store.selected" />
          <ModelIndicators v-else-if="store.detailTab==='indicators'" />
        </template>
        <n-empty v-else description="选择一个模型版本" style="padding:60px 0" />
      </div>
    </div>

    <!-- Create Modal -->
    <n-modal v-model:show="showCreate" preset="card" title="✚ 创建模型版本" style="width:520px;max-width:92vw" :mask-closable="false">
        <n-space vertical>
          <n-input v-model:value="createName" placeholder="模型名称，例如：BOLL+MACD+RSI 多策略融合" />
          <n-divider style="margin:4px 0">数据配置</n-divider>
          <n-date-picker v-model:formatted-value="createForm.train_start" type="date" value-format="yyyy-MM-dd" placeholder="训练起点" />
          <n-date-picker v-model:formatted-value="createForm.train_end" type="date" value-format="yyyy-MM-dd" placeholder="训练终点" />
          <n-date-picker v-model:formatted-value="createForm.test_start" type="date" value-format="yyyy-MM-dd" placeholder="测试起点" />
          <n-date-picker v-model:formatted-value="createForm.test_end" type="date" value-format="yyyy-MM-dd" placeholder="测试终点（空=至今）" clearable />
          <n-divider style="margin:4px 0">特征配置</n-divider>
          <n-space>
            <n-tag v-for="f in featureOptions" :key="f.key"
              :type="createForm.features.includes(f.key)?'info':'default'"
              style="cursor:pointer" @click="toggleFeature(f.key)" :bordered="false" size="small">
              {{f.label}}
            </n-tag>
          </n-space>
          <n-divider style="margin:4px 0">风险控制</n-divider>
          <n-input-number v-model:value="createForm.stop_loss_pct" :min="1" :max="30" placeholder="止损比例(%)" />
          <n-input-number v-model:value="createForm.signal_timeout_days" :min="5" :max="60" placeholder="信号超时(交易日)" />
          <n-checkbox v-model:checked="createForm.ml_enabled">ML增强</n-checkbox>
        </n-space>
        <template #footer>
          <n-space justify="flex-end">
            <n-button @click="showCreate=false">取消</n-button>
            <n-button type="primary" @click="doCreate" :loading="creating">创建</n-button>
          </n-space>
        </template>
    </n-modal>
    <!-- Delete Confirm Modal -->
    <n-modal v-model:show="showDeleteModal" preset="card" :title="deleteInfo?.can_physical_delete ? '⚠️ 永久删除模型' : '🗑️ 删除模型'" style="width:420px;max-width:92vw" :mask-closable="false">
        <template v-if="deleteInfo">
          <template v-if="deleteInfo.can_physical_delete">
            <p style="font-size:13px;color:var(--c-text);margin:0">
              模型 <b>{{ deleteTarget?.version }}</b> 没有关联数据，将被永久删除且无法恢复。
            </p>
          </template>
          <template v-else>
            <p style="font-size:13px;color:var(--c-text);margin:0 0 8px 0">
              模型 <b>{{ deleteTarget?.version }}</b> 已产生关联数据，删除后将标记为已删除状态，历史数据不受影响。
            </p>
            <div style="font-size:11px;color:var(--c-text-dim);padding:8px 12px;background:var(--c-card-bg);border-radius:6px;border:1px solid var(--c-border)">
              <div v-if="deleteInfo.related_data.signals > 0">📊 信号数据 {{ deleteInfo.related_data.signals }} 条</div>
              <div v-if="deleteInfo.related_data.training_trials > 0">🧪 训练试验 {{ deleteInfo.related_data.training_trials }} 次</div>
              <div v-if="deleteInfo.related_data.health_records > 0">💊 健康记录 {{ deleteInfo.related_data.health_records }} 条</div>
              <div v-if="deleteInfo.related_data.comparisons > 0">📋 版本对比 {{ deleteInfo.related_data.comparisons }} 条</div>
              <div v-if="deleteInfo.related_data.activated">🚀 曾上线运行</div>
            </div>
          </template>
        </template>
        <template #footer>
          <n-space justify="flex-end">
            <n-button @click="showDeleteModal=false">取消</n-button>
            <n-button :type="deleteInfo?.can_physical_delete ? 'error' : 'warning'" @click="confirmDelete" :loading="deleting">
              {{ deleteInfo?.can_physical_delete ? '永久删除' : '删除' }}
            </n-button>
          </n-space>
        </template>
    </n-modal>
  </template>
</div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { NButton, NTag, NSpin, NEmpty, NModal, NSpace, NInput, NInputNumber, NDatePicker, NCheckbox, NDivider } from 'naive-ui'
import axios from 'axios'
import { useModelStore } from '../stores/model'
import ModelTraining from './ModelTraining.vue'
import ModelEval from './ModelEval.vue'
import ModelLive from './ModelLive.vue'
import ModelIndicators from './ModelIndicators.vue'

const store = useModelStore()
const loading = ref(true)
const showCreate = ref(false)
const createName = ref('')
const creating = ref(false)
const showDeleteModal = ref(false)
const deleteInfo = ref(null)
const deleteTarget = ref(null)
const deleting = ref(false)
const isMobile = ref(window.innerWidth < 768)
const showSidebar = computed({
  get: () => store.sidebarOpen,
  set: (v) => { store.sidebarOpen = v }
})
const hoveredVersion = ref(null)
const createForm = reactive({
  train_start: '2021-01-01', train_end: '2025-12-31',
  test_start: '2026-01-01', test_end: null,
  features: ['boll','macd','rsi','atr','ma','volume'],
  ml_enabled: false,
  stop_loss_pct: 8, signal_timeout_days: 20,
})
const featureOptions = [
  { key:'boll', label:'BOLL(20,2)' }, { key:'macd', label:'MACD(12,26,9)' },
  { key:'rsi', label:'RSI(14)' }, { key:'atr', label:'ATR(14)' },
  { key:'ma', label:'MA(5,20,60,250)' }, { key:'volume', label:'量能' },
]
function toggleFeature(key) {
  const idx = createForm.features.indexOf(key)
  if (idx >= 0) createForm.features.splice(idx, 1)
  else createForm.features.push(key)
}
async function doCreate() {
  if (!createName.value.trim()) return
  creating.value = true
  try {
    await axios.post(window.location.origin + '/api/v1/models', {
      model_name: createName.value.trim(),
      train_start: createForm.train_start,
      train_end: createForm.train_end,
      test_start: createForm.test_start,
      test_end: createForm.test_end || '',
      features: createForm.features,
      ml_enabled: createForm.ml_enabled,
      stop_loss_pct: createForm.stop_loss_pct,
      signal_timeout_days: createForm.signal_timeout_days,
    })
    showCreate.value = false
    createName.value = ''
    await store.loadVersions()
    if (store.versions.length) store.selectVersion(store.versions[0].version)
  } catch(e) {
    alert(e.response?.data?.detail || '创建失败')
  } finally { creating.value = false }
}

const tabs = [
  { key:'basic', label:'基本信息' },
  { key:'indicators', label:'指标' },
  { key:'train', label:'训练' },
  { key:'eval', label:'评估' },
  { key:'live', label:'实盘' },
]

const cfg = computed(() => store.selected?.config || {})
const basicMetrics = computed(() => {
  const s = store.selected
  if (!s) return []
  return [
    { label:'状态', value: store.statusLabel(s.status), color: s.status==='ACTIVE'?'#10b981':s.status==='PENDING'?'#f59e0b':s.status==='TRAINING'?'#f97316':undefined },
    { label:'夏普', value: s.sharpe?.toFixed(2)||'—', color:'#10b981' },
    { label:'胜率', value: s.win_rate ? (s.win_rate*100).toFixed(0)+'%' : '—' },
    { label:'最大回撤', value: s.max_drawdown ? (s.max_drawdown*100).toFixed(1)+'%' : '—' },
    { label:'创建时间', value: (s.created_at||'').slice(0,10) },
  ]
})

async function startTrain() {
  try {
    await axios.post(window.location.origin + '/api/dag_trigger', { node:'model_train', include_downstream:false })
    store.selected.status = 'TRAINING'
  } catch(e) {
    alert(e.response?.data?.detail || '启动训练失败')
  }
}
async function approveModel() {
  try {
    await axios.post(window.location.origin + `/api/v1/models/${store.selected.version}/approve`)
    await store.loadVersions()
  } catch(e) {
    alert(e.response?.data?.detail || '审批失败')
  }
}
async function rejectModel() {
  try {
    await axios.post(window.location.origin + `/api/v1/models/${store.selected.version}/reject`)
    await store.loadVersions()
  } catch(e) {
    alert(e.response?.data?.detail || '操作失败')
  }
}

async function handleDeleteClick(v) {
  deleteTarget.value = v
  try {
    deleteInfo.value = await store.checkDelete(v.version)
    showDeleteModal.value = true
  } catch(e) {
    const msg = e.response?.data?.detail || '检查失败'
    alert(msg)
  }
}
async function confirmDelete() {
  if (!deleteTarget.value || !deleteInfo.value) return
  deleting.value = true
  try {
    const mode = deleteInfo.value.can_physical_delete ? 'hard' : 'soft'
    await store.deleteVersion(deleteTarget.value.version, mode)
    showDeleteModal.value = false
    deleteTarget.value = null
    deleteInfo.value = null
  } catch(e) {
    const msg = e.response?.data?.detail || '删除失败'
    alert(msg)
  } finally { deleting.value = false }
}

onMounted(async () => {
  await store.loadVersions()
  if (store.versions.length) store.selectVersion(store.versions[0].version)
  loading.value = false
})
</script>