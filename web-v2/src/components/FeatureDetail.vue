<template>
<div style="padding:16px;max-width:1100px;margin:0 auto">
  <n-spin v-if="loading" style="padding:60px" />
  <template v-else-if="feat">
    <!-- 顶部导航 -->
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px">
      <n-button size="small" quaternary @click="$emit('back')">← 返回列表</n-button>
      <span style="font-size:17px;font-weight:700;color:var(--c-text)">{{ feat.feature_name }}</span>
      <n-tag :type="statusTypeMap[feat.status]||'default'" size="small" :bordered="false">{{ statusMap[feat.status] }}</n-tag>
      <div style="flex:1" />
      <n-button size="small" @click="openEditInline">✎ 编辑</n-button>
    </div>

    <!-- 3 Tab 切换 -->
    <n-tabs v-model:value="activeTab" type="line" size="small">
      <n-tab-pane name="info" tab="基本信息">
        <!-- 信息卡行 -->
        <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px">
          <div v-for="m in infoCards" :key="m.label" style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:10px 14px;min-width:80px;text-align:center">
            <div style="font-size:10px;color:var(--c-text-faint)">{{ m.label }}</div>
            <div :style="{fontSize:m.size||'14px',fontWeight:600,color:m.color||'var(--c-text)'}">{{ m.value }}</div>
          </div>
        </div>

        <!-- 公式 -->
        <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;margin-bottom:14px">
          <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:6px">📐 KEPL 公式</div>
          <code style="font-size:14px;color:var(--c-text);word-break:break-all">{{ feat.formula }}</code>
        </div>

        <!-- 质量仪表盘 4 卡片 -->
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px">
          <div style="flex:1;min-width:100px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px">📊 数据完整度</div>
            <div :style="{fontSize:'22px',fontWeight:700,color:completenessPct>=60?'#10b981':completenessPct>=30?'#f59e0b':'#ef4444'}">{{ completenessPct }}%</div>
            <div style="background:var(--c-border);border-radius:4px;height:6px;margin-top:4px;overflow:hidden">
              <div :style="{width:completenessPct+'%',height:'100%',background:completenessPct>=60?'#10b981':completenessPct>=30?'#f59e0b':'#ef4444',borderRadius:'4px'}"></div>
            </div>
          </div>
          <div style="flex:1;min-width:80px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px">📋 总格子</div>
            <div style="font-size:20px;font-weight:700;color:var(--c-text)">{{ (feat.total_effective_cells||0).toLocaleString() }}</div>
            <div style="font-size:9px;color:var(--c-text-faint);margin-top:2px">交易日×股票数</div>
          </div>
          <div style="flex:1;min-width:80px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px">✅ 已计算</div>
            <div style="font-size:20px;font-weight:700;color:#10b981">{{ computedActual.toLocaleString() }}</div>
          </div>
          <div style="flex:1;min-width:80px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px">📊 总缺失格</div>
            <div :style="{fontSize:'20px',fontWeight:700,color:feat.abnormal_missing_cells>0?'#f59e0b':'var(--c-text-dim)'}">{{ (feat.abnormal_missing_cells||0).toLocaleString() }}</div>
          </div>
        </div>

        <!-- 最近计算 -->
        <div style="font-size:12px;color:var(--c-text-dim);margin-bottom:14px">
          📅 最近计算日：<span :style="{color:feat.latest_computed_date?(staleDays>5?'#f59e0b':'var(--c-text)'):'var(--c-text-faint)'}">{{ feat.latest_computed_date || '从未计算' }}</span>
          <span v-if="staleDays>5" style="color:#f59e0b;margin-left:8px">⚠ 超过 {{ staleDays }} 天未更新</span>
        </div>

        <!-- 依赖关系 -->
        <div style="display:flex;gap:12px;flex-wrap:wrap">
          <div style="flex:1;min-width:180px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px">
            <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:8px">⬆ 上游依赖</div>
            <div v-if="(feat.depends_on||[]).length">
              <n-tag v-for="d in feat.depends_on" :key="d" size="tiny" :bordered="false" type="info" style="margin-right:4px;margin-bottom:4px">{{ d }}</n-tag>
            </div>
            <div v-else style="font-size:11px;color:var(--c-text-faint)">无（仅依赖原始字段）</div>
          </div>
          <div style="flex:1;min-width:180px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px">
            <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:8px">⬇ 下游引用</div>
            <div v-if="(feat.downstream||[]).length">
              <div v-for="ds in feat.downstream" :key="ds.feature_name" style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
                <span style="font-size:12px;color:var(--c-text)">{{ ds.feature_name }}</span>
                <n-tag :type="statusTypeMap[ds.status]||'default'" size="tiny" :bordered="false">{{ statusMap[ds.status] }}</n-tag>
              </div>
            </div>
            <div v-else style="font-size:11px;color:var(--c-text-faint)">无下游引用</div>
          </div>
        </div>
      </n-tab-pane>

      <n-tab-pane name="diagnosis" tab="数据缺失诊断">
        <div style="display:flex;justify-content:flex-end;margin-bottom:8px">
          <n-button size="tiny" quaternary @click="recomputeStats" :loading="statsLoading">🔄 重新诊断</n-button>
        </div>
        <div v-if="!feat.total_effective_cells" style="text-align:center;padding:60px 20px;color:var(--c-text-dim)">
          <div style="font-size:14px;margin-bottom:8px">📭 暂无特征计算数据</div>
          <div style="font-size:12px">该特征尚未执行计算，请通过列表页 📥 补数功能或 DAG 流水线触发特征计算。</div>
        </div>
        <div v-else style="display:flex;gap:12px;flex-wrap:wrap">
          <div style="flex:1;min-width:300px">
            <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:8px">缺失归因饼图</div>
            <div ref="pieChart" style="width:100%;height:260px"></div>
            <div v-if="uncomputedPct>50" style="background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.2);border-radius:6px;padding:8px;font-size:11px;color:#f59e0b;margin-top:8px">
              🟠 {{ uncomputedPct }}% 的总格子尚未补数，请扩大补数日期范围覆盖更多历史数据。
            </div>
          </div>
          <div style="flex:2;min-width:350px">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
              <span style="font-size:11px;font-weight:600;color:var(--c-text-dim)">缺失热力图（最近120日 × 前{{ heatmapTopN }}股）</span>
              <n-select v-model:value="heatmapMode" :options="heatmapModeOptions" size="tiny" style="width:110px" @update:value="loadHeatmap" />
            </div>
            <div ref="heatmapChart" style="width:100%;height:360px"></div>
          </div>
        </div>
      </n-tab-pane>

      <n-tab-pane name="preview" tab="数据预览">
        <div v-if="feat.target_entity!=='global'" style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
          <n-input v-model:value="previewCode" placeholder="输入股票代码筛选，如 000001" size="small" style="width:180px" clearable @keyup.enter="doLoadPreview" />
          <n-button size="small" @click="doLoadPreview">查询</n-button>
        </div>
        <n-spin v-if="previewLoading" style="padding:40px" />
        <n-data-table v-else-if="previewItems.length" :columns="previewCols" :data="previewItems" size="small" />
        <div v-if="previewItems.length" style="display:flex;justify-content:center;align-items:center;gap:10px;margin-top:10px;font-size:12px;color:var(--c-text-dim)">
          <span>共 {{ previewTotal }} 条</span>
          <n-pagination v-if="previewTotalPages > 1" :page="previewPage" :page-count="previewTotalPages" @update:page="p => { previewPage = p; loadPreview() }" size="small" />
        </div>
        <n-empty v-else :description="previewEmptyReason || '暂无数据'" style="padding:20px" />
      </n-tab-pane>
    </n-tabs>

  </template>

  <!-- 编辑弹窗 -->
  <n-modal v-if="feat" v-model:show="showEditModal" preset="card" title="编辑特征" style="width:800px;max-width:95vw" :mask-closable="false">
    <n-space vertical>
      <n-input v-model:value="editForm.display_name" placeholder="中文名" />
      <n-input v-model:value="editForm.description" type="textarea" placeholder="描述" :rows="2" />
      <div style="display:flex;align-items:center;justify-content:space-between">
        <div style="font-size:11px;font-weight:600;color:var(--c-text-dim)">KEPL 公式</div>
        <n-button size="tiny" quaternary @click="showAiPrompt = true" :loading="aiLoading" style="font-size:11px">🤖 AI 生成</n-button>
      </div>
      <MonacoEditor v-model="editForm.formula" />
    </n-space>
    <template #footer>
      <n-space justify="flex-end">
        <n-button @click="showEditModal = false">取消</n-button>
        <n-button type="primary" @click="saveEdit" :loading="editSaving">保存</n-button>
      </n-space>
    </template>
  </n-modal>

  <!-- AI 生成公式弹窗 -->
  <n-modal v-model:show="showAiPrompt" preset="card" title="🤖 AI 生成 KEPL 公式" style="width:520px;max-width:92vw">
    <n-space vertical>
      <div style="font-size:12px;color:var(--c-text-dim)">描述你需要的特征计算逻辑，AI 会根据 KEPL 语法规范和已有函数生成公式。</div>
      <n-input v-model:value="aiRequirement" type="textarea" placeholder="例如：计算收盘价相对于5日均线的偏离度，即 (close - ma(close,5)) / ma(close,5)" :rows="4" />
      <div v-if="aiResult" style="margin-top:8px">
        <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:4px">生成结果</div>
        <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:6px;padding:8px;font-family:monospace;font-size:12px;white-space:pre-wrap;max-height:160px;overflow-y:auto">{{ aiResult }}</div>
        <n-button size="small" type="primary" style="margin-top:8px" @click="applyAiResult">✅ 填入公式框</n-button>
      </div>
    </n-space>
    <template #footer>
      <n-space justify="flex-end">
        <n-button @click="showAiPrompt = false">取消</n-button>
        <n-button type="primary" @click="callAiGenerate" :loading="aiLoading">生成</n-button>
      </n-space>
    </template>
  </n-modal>
</div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick, watch } from 'vue'
import { NButton, NTag, NSpin, NTabs, NTabPane, NInput, NSelect, NDataTable, NEmpty, NPagination, NModal, NSpace } from 'naive-ui'
import MonacoEditor from './MonacoEditor.vue'
import axios from 'axios'
import * as echarts from 'echarts'

const props = defineProps({ featureId: Number })
defineEmits(['back', 'edit'])

const API = window.location.origin
const loading = ref(true)
const feat = ref(null)
const staleDays = ref(0)
const completenessPct = ref(0)
const uncomputedPct = ref(0)

// 已计算 = 总格子 - 总缺失（abnormal_missing_cells 现在存的是总缺失）
const computedActual = computed(() => {
  const t = feat.value?.total_effective_cells || 0
  const a = feat.value?.abnormal_missing_cells || 0   // 总缺失 = 窗口期 + 未补
  return Math.max(0, t - a)
})

const activeTab = ref('info')

// 图表 refs
const pieChart = ref(null)
const heatmapChart = ref(null)
let pieInstance = null, heatmapInstance = null

const heatmapMode = ref('top_missing')
const heatmapTopN = ref(50)
const heatmapModeOptions = [
  { label: '缺失最多', value: 'top_missing' },
  { label: '随机抽样', value: 'random' },
]

// 监听 Tab 切换
watch(activeTab, (tab) => {
  if (tab === 'diagnosis') {
    nextTick(() => {
      setTimeout(() => { renderDiagnosis() }, 50)
    })
  } else if (tab === 'preview') {
    // 首次切换到预览 Tab 时自动加载数据
    if (!previewItems.value.length && !previewLoading.value) {
      loadPreview()
    }
  }
})

// 数据预览
const previewCode = ref('')
const previewItems = ref([])
const previewTotal = ref(0)
const previewPage = ref(1)
const previewLoading = ref(false)
const previewEmptyReason = ref('')
const statsLoading = ref(false)
const showEditModal = ref(false)
const editSaving = ref(false)
const editForm = ref({ display_name:'', description:'', formula:'' })
const showAiPrompt = ref(false)
const aiRequirement = ref('')
const aiResult = ref('')
const aiLoading = ref(false)

function openEditInline() {
  editForm.value = {
    display_name: feat.value?.display_name || '',
    description: feat.value?.description || '',
    formula: feat.value?.formula || '',
  }
  showEditModal.value = true
}

async function saveEdit() {
  editSaving.value = true
  try {
    await axios.put(API + `/api/features/${props.featureId}`, {
      display_name: editForm.value.display_name,
      description: editForm.value.description,
      formula: editForm.value.formula,
    }, { headers: authHeaders() })
    showEditModal.value = false
    await loadDetail()
  } catch(e) {
    alert(e.response?.data?.detail || '保存失败')
  } finally {
    editSaving.value = false
  }
}

function authHeaders() {
  const t = localStorage.getItem('token')
  return t ? { Authorization: 'Bearer ' + t } : {}
}

async function recomputeStats() {
  statsLoading.value = true
  try {
    await axios.post(API + `/api/features/${props.featureId}/recompute-stats`, {}, { headers: authHeaders() })
    await loadDetail()
  } catch(e) {
    console.error(e)
  } finally {
    statsLoading.value = false
  }
}

const statusMap = { draft:'草稿', enabled:'已启用', pending_recalc:'待重算', deprecated:'已弃用', data_anomaly:'数据异常' }
const statusTypeMap = { draft:'warning', enabled:'success', pending_recalc:'info', deprecated:'default', data_anomaly:'error' }
const entityLabel = { stock:'个股', etf:'ETF', index:'指数', global:'全局' }

const infoCards = computed(() => {
  if (!feat.value) return []
  return [
    { label:'实体', value: entityLabel[feat.value.target_entity]||feat.value.target_entity, size:'15px' },
    { label:'中文名', value: feat.value.display_name||'—', size:'13px' },
    { label:'创建', value: (feat.value.created_at||'').slice(0,10) },
    { label:'修改', value: (feat.value.updated_at||'').slice(0,10) },
    { label:'描述', value: feat.value.description||'—', size:'11px' },
  ]
})

const previewCols = computed(() => {
  const cols = []
  if (feat.value?.target_entity !== 'global') {
    cols.push({ title:'代码', key:'stock_code', width:70 })
    cols.push({ title:'名称', key:'stock_name', width:80, ellipsis:{tooltip:true} })
    cols.push({ title:'交易所', key:'exchange', width:55 })
  }
  cols.push({ title:'日期', key:'trade_date', width:90 })
  if (feat.value?.target_entity !== 'global') {
    cols.push({ title:'收盘价', key:'close', width:80, render(row) {
      if (row.close == null) return h('span', { style:{color:'#9ca3af'} }, '—')
      return row.close.toFixed(2)
    }})
  }
  cols.push({ title:'值', key:'value', width:120, render(row) {
    if (row.value == null) return h('span', { style:{color:'#9ca3af',cursor:'help'}, title:'该日无数据（停牌/上市前/计算失败）' }, '—')
    return row.value
  }})
  return cols
})

const previewTotalPages = computed(() => Math.max(1, Math.ceil(previewTotal.value / 50)))

import { h } from 'vue'

async function loadDetail() {
  loading.value = true
  try {
    const r = await axios.get(API + `/api/features/${props.featureId}`)
    feat.value = r.data
    completenessPct.value = Math.round((r.data.data_completeness||0)*1000)/10
    if (r.data.latest_computed_date) {
      staleDays.value = Math.round((new Date() - new Date(r.data.latest_computed_date))/86400000)
    }
  } catch (e) {
    console.error(e)
  }
  loading.value = false
}

function renderDiagnosis() {
  // 仅当有实际计算数据时才渲染图表；否则显示提示
  const totalCells = feat.value?.total_effective_cells || 0
  if (!totalCells) {
    // 无实际数据，清理旧图表
    const pieDom = pieChart.value
    if (pieDom) {
      const old = echarts.getInstanceByDom(pieDom)
      if (old) old.dispose()
    }
    const hmDom = heatmapChart.value
    if (hmDom) {
      const old = echarts.getInstanceByDom(hmDom)
      if (old) old.dispose()
    }
    return
  }

  // 饼图：已计算 / 窗口期(天然缺) / 未补(历史缺口)
  const pieDom = pieChart.value
  if (pieDom) {
    const old = echarts.getInstanceByDom(pieDom)
    if (old) old.dispose()
    pieInstance = echarts.init(pieDom)
    const windowMissing = feat.value?.missing_cells_total || 0     // 窗口期天然缺失
    const totalMissing = feat.value?.abnormal_missing_cells || 0   // 总缺失（窗口期+未补）
    const uncomputed = Math.max(0, totalMissing - windowMissing)   // 未补历史数据
    const computed = Math.max(0, totalCells - totalMissing)        // 已计算
    uncomputedPct.value = totalCells > 0 ? Math.round(uncomputed / totalCells * 100) : 0
    if (totalCells > 0) {
      pieInstance.setOption({
        tooltip: { trigger:'item', formatter(p){ return `${p.name}: ${p.value.toLocaleString()} (${p.percent}%)` } },
        series: [{
          type:'pie', radius:['40%','70%'],
          data: [
            { value:computed,    name:'已计算',         itemStyle:{color:'#10b981'} },
            { value:uncomputed,  name:'未补(历史缺口)',  itemStyle:{color:'#f59e0b'} },
            { value:windowMissing, name:'窗口期(天然缺失)', itemStyle:{color:'#9ca3af'} },
          ],
          label: { formatter:'{b}\n{d}%' },
        }],
      })
    } else {
      pieInstance.setOption({
        title: { text:'暂无统计数据', left:'center', top:'center', textStyle:{fontSize:12,color:'#9ca3af'} },
      })
    }
  }

  loadHeatmap()
}

function loadHeatmap() {
  const hmDom = heatmapChart.value
  if (!hmDom || !feat.value?.total_effective_cells) return
  const old = echarts.getInstanceByDom(hmDom)
  if (old) old.dispose()
  heatmapInstance = echarts.init(hmDom)
  const params = new URLSearchParams({ days: 120, top_n: heatmapTopN.value, mode: heatmapMode.value })
  axios.get(API + `/api/features/${props.featureId}/missing-heatmap?${params}`).then(r => {
    const { days_labels, stock_labels, matrix } = r.data
      if (matrix && matrix.length) {
        heatmapInstance.setOption({
          tooltip: {
            formatter(p) {
              const v = p.data[2]
              const status = v === 2 ? '🟥 缺失' : v === 1 ? '⬜ 未上市/不适用' : '🟩 有值'
              return `${stock_labels[p.data[1]] || '#N'}<br/>${days_labels[p.data[0]] || ''}<br/>${status}`
            }
          },
          grid: { left:70, right:20, top:20, bottom:40 },
          xAxis: { type:'category', data: days_labels, axisLabel:{fontSize:8,interval:Math.max(1,Math.floor(days_labels.length/6))} },
          yAxis: { type:'category', data: stock_labels, axisLabel:{fontSize:8}, inverse:true },
          visualMap: { min:0, max:2, inRange:{color:['#10b981','#9ca3af','#ef4444']}, show:false },
          series: [{ type:'heatmap', data: matrix, label:{show:false} }],
        })
      } else {
        heatmapInstance.setOption({
          title: { text:'暂无缺失明细数据', left:'center', top:'center', textStyle:{fontSize:12,color:'#9ca3af'} },
        })
      }
    }).catch(() => {
      heatmapInstance.setOption({
        title: { text:'热力图数据加载失败', left:'center', top:'center', textStyle:{fontSize:12,color:'#9ca3af'} },
      })
    })
}

function doLoadPreview() {
  previewPage.value = 1
  loadPreview()
}

async function loadPreview() {
  previewLoading.value = true
  try {
    const params = { page: previewPage.value, page_size: 50 }
    if (previewCode.value) params.code = previewCode.value
    const r = await axios.get(API + `/api/features/${props.featureId}/data`, { params })
    previewItems.value = r.data.items || []
    previewTotal.value = r.data.total || 0
    previewEmptyReason.value = r.data.empty_reason || ''
  } catch (e) {
    console.error(e)
    previewItems.value = []
    previewTotal.value = 0
  } finally {
    previewLoading.value = false
  }
}

// KEPL 语法规范摘要（给 AI 的上下文）
const KEPL_SPEC = `KEPL 语法规则：
- 裸字段直接引用当前股票：close, open, high, low, volume
- 时间序列函数（参数必须是裸字段）：ma(close,5), ema(close,12), macd(close), rsi(close,14), boll(close), atr(high,low,close,14)
- 横截面聚合（参数必须是 stock.字段）：avg(stock.close), rank(stock.close), std(stock.pe), max(stock.high)
- 算术运算：+ - * / ( )
- 比较运算：> < >= <= == !=
- 逻辑运算：& | !
- 条件表达式：if(condition, true_val, false_val)
- 示例：close / ma(close, 5) - 1  表示收盘价相对于5日均线的偏离度`

async function callAiGenerate() {
  if (!aiRequirement.value.trim()) return
  aiLoading.value = true
  aiResult.value = ''
  try {
    // 获取已有函数列表
    const fr = await axios.get(API + '/api/functions?page_size=200')
    const funcList = (fr.data.items || []).map(f =>
      `${f.name}(${(f.parameters||[]).map(p=>p.name+(p.default!==undefined?'='+p.default:'')).join(',')}): ${f.description||f.display_name||''}`
    ).join('\n')

    const prompt = `${KEPL_SPEC}

【可用函数列表】
${funcList}

【用户需求】
${aiRequirement.value}

请根据 KEPL 语法和可用函数，生成一个特征计算公式。只返回公式本身，不要解释。`

    const r = await axios.post(API + '/api/functions/ai-chat', {
      messages: [{ role: 'user', content: prompt }],
    }, { headers: authHeaders() })
    const content = r.data?.content?.content || r.data?.content || r.data?.message || ''
    const codeMatch = content.match(/```(?:python)?\s*\n?([\s\S]*?)\n?```/)
    aiResult.value = codeMatch ? codeMatch[1].trim() : content.trim()
  } catch(e) {
    aiResult.value = '# 生成失败: ' + (e.response?.data?.detail || e.message)
  } finally {
    aiLoading.value = false
  }
}

function applyAiResult() {
  editForm.value.formula = aiResult.value
  showAiPrompt.value = false
  aiRequirement.value = ''
  aiResult.value = ''
}

onMounted(loadDetail)
watch(() => props.featureId, loadDetail)
</script>