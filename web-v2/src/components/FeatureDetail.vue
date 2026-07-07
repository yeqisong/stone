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
            <div :style="{fontSize:'22px',fontWeight:700,color:completenessPct>=90?'#10b981':completenessPct>=80?'#f59e0b':'#ef4444'}">{{ completenessPct }}%</div>
            <div style="background:var(--c-border);border-radius:4px;height:6px;margin-top:4px;overflow:hidden">
              <div :style="{width:completenessPct+'%',height:'100%',background:completenessPct>=90?'#10b981':completenessPct>=80?'#f59e0b':'#ef4444',borderRadius:'4px'}"></div>
            </div>
          </div>
          <div style="flex:1;min-width:80px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px">📋 有效格总数</div>
            <div style="font-size:20px;font-weight:700;color:var(--c-text)">{{ (feat.total_effective_cells||0).toLocaleString() }}</div>
          </div>
          <div style="flex:1;min-width:80px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px">⏳ 待计算</div>
            <div :style="{fontSize:'20px',fontWeight:700,color:feat.pending_cells_total>0?'#f59e0b':'var(--c-text)'}">{{ (feat.pending_cells_total||0).toLocaleString() }}</div>
          </div>
          <div style="flex:1;min-width:80px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px">🚫 不适用格</div>
            <div style="font-size:20px;font-weight:700;color:var(--c-text-dim)">{{ ((feat.total_effective_cells||0) - (feat.missing_cells_total||0) - (feat.pending_cells_total||0)).toLocaleString() }}</div>
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
        <div style="display:flex;gap:12px;flex-wrap:wrap">
          <div style="flex:1;min-width:300px">
            <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:8px">缺失归因饼图</div>
            <div ref="pieChart" style="width:100%;height:260px"></div>
            <div v-if="abnormalPct>50" style="background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:6px;padding:8px;font-size:11px;color:#ef4444;margin-top:8px">
              🔴 大部分缺失非停牌导致（{{ abnormalPct }}%），请检查计算逻辑或数据源。
            </div>
          </div>
          <div style="flex:2;min-width:350px">
            <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:8px">缺失热力图（最近120日 × 前50股）</div>
            <div ref="heatmapChart" style="width:100%;height:360px"></div>
          </div>
        </div>
      </n-tab-pane>

      <n-tab-pane name="preview" tab="数据预览">
        <div v-if="feat.target_entity!=='global'" style="margin-bottom:10px">
          <n-input v-model:value="previewCode" placeholder="输入股票代码，如 000001" size="small" style="width:160px" clearable @keyup.enter="loadPreview" />
          <n-button size="small" @click="loadPreview" style="margin-left:8px">查询</n-button>
        </div>
        <n-data-table v-if="previewItems.length" :columns="previewCols" :data="previewItems" size="small" :pagination="previewPagination" />
        <n-empty v-else-if="previewLoaded" description="暂无数据" style="padding:20px" />
        <div v-else style="font-size:12px;color:var(--c-text-dim);padding:12px">输入代码后点击「查询」加载数据</div>
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

const activeTab = ref('info')

// 图表 refs
const pieChart = ref(null)
const heatmapChart = ref(null)
let pieInstance = null, heatmapInstance = null

// 监听 Tab 切换：切到诊断时渲染图表
watch(activeTab, (tab) => {
  if (tab === 'diagnosis') {
    nextTick(() => {
      // 延迟一帧确保 Naive UI 完成 display 切换
      setTimeout(() => {
        renderDiagnosis()
      }, 50)
    })
  }
})

// 数据预览
const previewCode = ref('')
const previewItems = ref([])
const previewTotal = ref(0)
const previewPage = ref(1)
const previewLoaded = ref(false)

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
  onChange(p) { previewPage.value = p; loadPreview() },
}))

import { h } from 'vue'

async function loadDetail() {
  loading.value = true
  try {
    const r = await axios.get(API + `/api/features/${props.featureId}`)
    feat.value = r.data
    completenessPct.value = Math.round((r.data.data_completeness||0)*100)
    if (r.data.latest_computed_date) {
      staleDays.value = Math.round((new Date() - new Date(r.data.latest_computed_date))/86400000)
    }
  } catch (e) {
    console.error(e)
  }
  loading.value = false
}

function renderDiagnosis() {
  // 饼图：先清理 DOM 上的旧实例
  const pieDom = pieChart.value
  if (pieDom) {
    const old = echarts.getInstanceByDom(pieDom)
    if (old) old.dispose()
    pieInstance = echarts.init(pieDom)
    const pie = pieInstance
    // Mock 数据（实际应从后端获取停牌/非停牌缺失统计）
    const suspended = 35
    const abnormal = 65
    abnormalPct.value = abnormal
    pie.setOption({
      tooltip: { trigger:'item' },
      series: [{
        type:'pie', radius:['40%','70%'],
        data: [
          { value:suspended, name:'停牌导致', itemStyle:{color:'#9ca3af'} },
          { value:abnormal, name:'非停牌异常', itemStyle:{color:'#ef4444'} },
        ],
        label: { formatter:'{b}\n{d}%' },
      }],
    })
  }

  // 热力图 (mock)
  const hmDom = heatmapChart.value
  if (hmDom) {
    const old = echarts.getInstanceByDom(hmDom)
    if (old) old.dispose()
    heatmapInstance = echarts.init(hmDom)
    const hm = heatmapInstance
    const days = 120
    const stocks = 40
    const data = []
    for (let d=0; d<days; d++) {
      for (let s=0; s<stocks; s++) {
        data.push([d, s, Math.random()>0.85?1:0])
      }
    }
    hm.setOption({
      tooltip: { formatter(p) { return `日期: T-${days-p.data[0]}\n股票#${p.data[1]}\n${p.data[2]?'缺失':'有值'}` } },
      grid: { left:60, right:20, top:20, bottom:40 },
      xAxis: { type:'category', data: Array.from({length:days},(_,i)=>`T-${days-i}`), axisLabel:{fontSize:8,interval:19} },
      yAxis: { type:'category', data: Array.from({length:stocks},(_,i)=>`#${i+1}`), axisLabel:{fontSize:8}, inverse:true },
      visualMap: { min:0, max:1, inRange:{color:['#10b981','#ef4444']}, show:false },
      series: [{ type:'heatmap', data, label:{show:false} }],
    })
  }
}

async function loadPreview() {
  previewLoaded.value = false
  try {
    const params = { page: previewPage.value, page_size: 50 }
    if (previewCode.value) params.code = previewCode.value
    const r = await axios.get(API + `/api/features/${props.featureId}/data`, { params })
    previewItems.value = r.data.items || []
    previewTotal.value = r.data.total || 0
  } catch (e) {
    console.error(e)
  }
  previewLoaded.value = true
}

onMounted(loadDetail)
watch(() => props.featureId, loadDetail)
</script>