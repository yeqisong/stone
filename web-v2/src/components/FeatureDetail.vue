<template>
<div style="padding:16px;max-width:1100px;margin:0 auto">
  <n-spin v-if="loading" style="padding:60px" />
  <template v-else-if="feat">
    <!-- 顶部导航 -->
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px">
      <n-button size="small" quaternary @click="$emit('back')">← 返回列表</n-button>
      <span style="font-size:17px;font-weight:700;color:var(--c-text)">{{ feat.feature_name }}</span>
      <n-tag :type="statusTypeMap[feat.status]||'default'" size="small" :bordered="false">{{ statusMap[feat.status] }}</n-tag>
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
            <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px">❌ 异常缺失</div>
            <div :style="{fontSize:'20px',fontWeight:700,color:feat.abnormal_missing_cells>0?'#ef4444':'var(--c-text-dim)'}">{{ (feat.abnormal_missing_cells||0).toLocaleString() }}</div>
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
            <div v-if="abnormalPct>50" style="background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:6px;padding:8px;font-size:11px;color:#ef4444;margin-top:8px">
              🔴 {{ abnormalPct }}% 的缺失为异常缺失（非停牌/lookback），请扩大补数日期范围或检查计算逻辑。
            </div>
          </div>
          <div style="flex:2;min-width:350px">
            <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:8px">缺失热力图（最近120日 × 前50股）</div>
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
        <n-data-table v-else-if="previewItems.length" :columns="previewCols" :data="previewItems" size="small" :pagination="previewPagination" />
        <n-empty v-else :description="previewEmptyReason || '暂无数据'" style="padding:20px" />
      </n-tab-pane>
    </n-tabs>

    <!-- 底部操作 -->
    <div style="display:flex;gap:8px;margin-top:16px">
      <n-button size="small" @click="$emit('edit', feat.id)">✎ 编辑</n-button>
    </div>
  </template>
</div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick, watch } from 'vue'
import { NButton, NTag, NSpin, NTabs, NTabPane, NInput, NDataTable, NEmpty } from 'naive-ui'
import axios from 'axios'
import * as echarts from 'echarts'

const props = defineProps({ featureId: Number })
defineEmits(['back', 'edit'])

const API = window.location.origin
const loading = ref(true)
const feat = ref(null)
const staleDays = ref(0)
const completenessPct = ref(0)
const abnormalPct = ref(0)

// 已计算 = 总格子 - 正常缺失 - 异常缺失 (或直接用实际行数估算)
const computedActual = computed(() => {
  const t = feat.value?.total_effective_cells || 0
  const n = feat.value?.missing_cells_total || 0
  const a = feat.value?.abnormal_missing_cells || 0
  return Math.max(0, t - n - a)
})

const activeTab = ref('info')

// 图表 refs
const pieChart = ref(null)
const heatmapChart = ref(null)
let pieInstance = null, heatmapInstance = null

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
  if (feat.value?.target_entity !== 'global') cols.push({ title:'代码', key:'stock_code', width:70 })
  cols.push({ title:'日期', key:'trade_date', width:90 })
  cols.push({ title:'值', key:'value', width:120, render(row) {
    if (row.value == null) return h('span', { style:{color:'#9ca3af',cursor:'help'}, title:'该日无数据（停牌/上市前/计算失败）' }, '—')
    return row.value
  }})
  return cols
})

const previewPagination = computed(() => ({
  page: previewPage.value, pageSize: 50, itemCount: previewTotal.value,
  prefix({ itemCount }) { return `共 ${itemCount} 条` },
  onChange(p) { previewPage.value = p; loadPreview() },
}))

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

  // 饼图：正常缺失 vs 异常缺失
  const pieDom = pieChart.value
  if (pieDom) {
    const old = echarts.getInstanceByDom(pieDom)
    if (old) old.dispose()
    pieInstance = echarts.init(pieDom)
    const normalMissing = feat.value?.missing_cells_total || 0   // 停牌+lookback
    const abnormalMissing = feat.value?.abnormal_missing_cells || 0
    const totalMissing = normalMissing + abnormalMissing
    abnormalPct.value = totalMissing > 0 ? Math.round(abnormalMissing / totalMissing * 100) : 0
    if (totalMissing > 0) {
      pieInstance.setOption({
        tooltip: { trigger:'item' },
        series: [{
          type:'pie', radius:['40%','70%'],
          data: [
            { value:normalMissing, name:'正常缺失(停牌/lookback)', itemStyle:{color:'#9ca3af'} },
            { value:abnormalMissing, name:'异常缺失(需排查)', itemStyle:{color:'#ef4444'} },
          ],
          label: { formatter:'{b}\n{d}%' },
        }],
      })
    } else {
      pieInstance.setOption({
        title: { text:'无缺失数据', left:'center', top:'center', textStyle:{fontSize:12,color:'#9ca3af'} },
      })
    }
  }

  // 热力图：缺失分布（最近120日 × 缺失率最高的前50只股票）
  const hmDom = heatmapChart.value
  if (hmDom && feat.value?.total_effective_cells > 0) {
    const old = echarts.getInstanceByDom(hmDom)
    if (old) old.dispose()
    heatmapInstance = echarts.init(hmDom)
    // 从后端获取热力图数据
    axios.get(API + `/api/features/${props.featureId}/missing-heatmap`).then(r => {
      const { days_labels, stock_labels, matrix } = r.data
      if (matrix && matrix.length) {
        heatmapInstance.setOption({
          tooltip: {
            formatter(p) {
              return `${stock_labels[p.data[1]] || '#N'}<br/>${days_labels[p.data[0]] || ''}<br/>${p.data[2] ? '🟥 缺失' : '🟩 有值'}`
            }
          },
          grid: { left:70, right:20, top:20, bottom:40 },
          xAxis: { type:'category', data: days_labels, axisLabel:{fontSize:8,interval:Math.max(1,Math.floor(days_labels.length/6))} },
          yAxis: { type:'category', data: stock_labels, axisLabel:{fontSize:8}, inverse:true },
          visualMap: { min:0, max:1, inRange:{color:['#10b981','#ef4444']}, show:false },
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

onMounted(loadDetail)
watch(() => props.featureId, loadDetail)
</script>