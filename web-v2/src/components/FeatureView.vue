<template>
<div class="page-fill" style="padding:10px 8px 4px">
  <div class="no-shrink" style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
    <div style="font-size:18px;font-weight:700;color:var(--c-text)">特征管理（Feature Registry）</div>
    <div style="display:flex;gap:6px">
      <n-button size="small" quaternary @click="openDepGraph"><AppIcon name="link" :size="13" />  依赖图</n-button>
      <n-button size="small" quaternary @click="showDedup = true"><AppIcon name="compass" :size="13" />  去冗推荐</n-button>
      <n-button type="primary" size="small" @click="openCreate">+ 新增特征</n-button>
    </div>
  </div>

  <div class="no-shrink" style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
    <n-select v-model:value="filterEntity" :options="entityOptions" size="small" style="width:110px" placeholder="实体" clearable @update:value="loadData" />
    <n-select v-model:value="filterStatus" :options="statusOptions" size="small" style="width:130px" placeholder="状态" clearable @update:value="loadData" />
    <n-select v-model:value="filterIcStatus" :options="icStatusOptions" size="small" style="width:130px" placeholder="IC 决策" clearable @update:value="loadData" />
    <n-input v-model:value="searchText" size="small" style="width:180px" placeholder="搜索名称" clearable @keyup.enter="loadData" />
    <n-button size="small" @click="loadData">查询</n-button>
  </div>

  <n-data-table class="fill-table" flex-height :columns="columns" :data="items" :loading="loading" size="small" :row-props="rowProps" scroll-x="1100" />
  <ListPagination :total="total" :page="page" :page-size="50" @change="p => { page = p; loadData() }" />

  <!-- Create/Edit Modal -->
  <n-modal v-model:show="showCreate" preset="card" :title="editId ? '编辑特征' : '新增特征'" style="width:800px;max-width:95vw" :mask-closable="false">
    <n-space vertical>
      <n-input v-model:value="form.feature_name" placeholder="特征英文名（小写字母开头，如 ma_5, rsi_14）" :disabled="!!editId" />
      <n-input v-model:value="form.display_name" placeholder="特征中文名" />
      <n-select v-model:value="form.target_entity" :options="entityOpts" placeholder="目标实体" :disabled="!!editId" />
      <div style="display:flex;gap:8px">
        <n-select v-model:value="form.feature_group" :options="groupOptions" placeholder="特征族（可选）" size="small" style="flex:1" clearable filterable tag @create="createGroup" />
        <n-select v-model:value="form.tags" :options="tagOptions" placeholder="标签（多选）" size="small" style="flex:1" multiple clearable filterable tag @create="createTag" />
      </div>
      <n-input v-model:value="form.description" type="textarea" placeholder="功能描述，如：5日均线偏离度" :rows="2" />

      <div style="display:flex;align-items:center;justify-content:space-between">
        <div style="font-size:12px;font-weight:600;color:var(--c-text-dim)">KEPL 公式</div>
        <div style="display:flex;gap:4px">
          <n-button size="tiny" quaternary @click="showAiFormula = true" :loading="aiFormulaLoading" style="font-size:11px"><AppIcon name="a" :size="13" />  AI</n-button>
          <n-button size="tiny" quaternary @click="doValidate" :loading="validating" style="font-size:11px"><AppIcon name="search" :size="13" />  验证</n-button>
        </div>
      </div>
      <MonacoEditor ref="formulaEditor" v-model="form.formula" :completions="keplCompletions" />
      <div style="display:flex;align-items:center;gap:8px;font-size:11px;color:var(--c-text-dim)">
        <span><AppIcon name="file-text" :size="13" />  算子速查：输入可自动补全</span>
        <n-button size="tiny" quaternary @click="showOpsPanel = !showOpsPanel">{{ showOpsPanel ? '收起' : '展开' }}</n-button>
      </div>
      <div v-if="showOpsPanel && keplFns" style="max-height:200px;overflow-y:auto;border:1px solid var(--c-border);border-radius:6px;padding:8px">
        <div v-for="grp in opGroups" :key="grp.label" style="margin-bottom:6px">
          <div style="font-size:10px;font-weight:600;color:var(--c-text-faint);margin-bottom:4px">{{ grp.label }}</div>
          <div style="display:flex;flex-wrap:wrap;gap:4px">
            <n-tag v-for="op in grp.items" :key="op.name" size="small" :bordered="false"
                   style="cursor:pointer;font-family:monospace" :title="`${op.desc}　例: ${op.eg}`"
                   @click="insertOp(op)">{{ op.sig }}</n-tag>
          </div>
        </div>
      </div>

      <!-- 依赖预览 -->
      <div v-if="parseDeps.length > 0" style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:6px;padding:10px">
        <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:6px"><AppIcon name="link" :size="13" />  依赖项（自动提取）</div>
        <div style="display:flex;flex-wrap:wrap;gap:4px">
          <n-tag v-for="d in parseDeps" :key="d" size="tiny" :bordered="false" :type="d.includes('.') ? 'warning' : 'info'">
            {{ d.includes('.') ? '<AppIcon name="file-text" :size="13" />  ' : '<AppIcon name="link" :size="13" />  ' }}{{ d }}
          </n-tag>
        </div>
      </div>

      <!-- 校验结果 -->
      <div v-if="validateResult">
        <div v-if="validateResult.ok" style="background:rgba(16,185,129,.08);border:1px solid rgba(16,185,129,.2);border-radius:6px;padding:8px;font-size:11px;color:#10b981">
          <AppIcon name="check" :size="13" />  公式语法正确，共 {{ validateResult.dependencies?.length || 0 }} 个依赖项
        </div>
        <div v-else style="background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:6px;padding:8px">
          <div v-for="(e,i) in validateResult.errors" :key="i" style="font-size:11px;color:#ef4444">{{ e.message }}</div>
        </div>
      </div>

      <div style="display:flex;align-items:center;gap:8px;margin-top:8px">
        <span style="font-size:12px;color:var(--c-text-dim)">创建后启用：</span>
        <n-switch v-model:value="form.enabled" />
      </div>
    </n-space>
    <template #footer>
      <n-space justify="flex-end">
        <n-button @click="showCreate = false">取消</n-button>
        <n-button @click="doCreate('draft')" :loading="creating">存为草稿</n-button>
        <n-button type="primary" @click="doCreate('enabled')" :loading="creating">保存并启用</n-button>
      </n-space>
    </template>
  </n-modal>

  <!-- Detail Modal -->
  <n-modal v-model:show="showDetail" preset="card" :title="detailItem?.feature_name" style="width:700px;max-width:92vw" :mask-closable="true">
    <n-spin v-if="detailLoading" style="padding:40px" />
    <template v-else-if="detailItem">
      <!-- 信息卡 -->
      <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px">
        <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:10px 14px;min-width:70px;text-align:center">
          <div style="font-size:10px;color:var(--c-text-faint)">实体</div>
          <div style="font-size:15px;font-weight:700;color:var(--c-text)">{{ entityLabel[detailItem.target_entity] || detailItem.target_entity }}</div>
        </div>
        <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:10px 14px;min-width:70px;text-align:center">
          <div style="font-size:10px;color:var(--c-text-faint)">状态</div>
          <n-tag :type="statusTypeMap[detailItem.status]||'default'" size="tiny" :bordered="false">{{ statusMap[detailItem.status] }}</n-tag>
        </div>
        <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:10px 14px;min-width:70px;text-align:center">
          <div style="font-size:10px;color:var(--c-text-faint)">中文名</div>
          <div style="font-size:13px;font-weight:600;color:var(--c-text)">{{ detailItem.display_name || '—' }}</div>
        </div>
        <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:10px 14px;min-width:70px;text-align:center">
          <div style="font-size:10px;color:var(--c-text-faint)">创建</div>
          <div style="font-size:12px;color:var(--c-text)">{{ (detailItem.created_at||'').slice(0,10) }}</div>
        </div>
      </div>

      <!-- 公式 -->
      <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;margin-bottom:14px">
        <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:6px">KEPL 公式</div>
        <code style="font-size:13px;color:var(--c-text);word-break:break-all">{{ detailItem.formula }}</code>
      </div>

      <!-- 质量仪表盘 -->
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px">
        <div style="flex:1;min-width:100px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;text-align:center">
          <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px"><AppIcon name="bar-chart-2" :size="13" />  数据完整度</div>
          <div :style="{fontSize:'22px',fontWeight:700,color:completenessPct>=80?'#10b981':completenessPct>=50?'#f59e0b':'#ef4444'}">{{ completenessPct }}%</div>
          <div style="background:var(--c-border);border-radius:4px;height:6px;margin-top:4px;overflow:hidden">
            <div :style="{width:completenessPct+'%',height:'100%',background:completenessPct>=80?'#10b981':completenessPct>=50?'#f59e0b':'#ef4444',borderRadius:'4px'}"></div>
          </div>
        </div>
        <div style="flex:1;min-width:80px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;text-align:center">
          <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px"><AppIcon name="w" :size="13" />  总格子</div>
          <div style="font-size:20px;font-weight:700;color:var(--c-text)">{{ (detailItem.total_effective_cells||0).toLocaleString() }}</div>
        </div>
        <div style="flex:1;min-width:80px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;text-align:center">
          <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px"><AppIcon name="close" :size="13" />  异常缺失</div>
          <div :style="{fontSize:'20px',fontWeight:700,color:detailItem.abnormal_missing_cells>0?'#ef4444':'var(--c-text-dim)'}">{{ (detailItem.abnormal_missing_cells||0).toLocaleString() }}</div>
        </div>
        <div style="flex:1;min-width:80px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;text-align:center">
          <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px"><AppIcon name="calendar" :size="13" />  最近计算</div>
          <div :style="{fontSize:'13px',fontWeight:600,color:detailStale?'#f59e0b':'var(--c-text)'}">{{ detailItem.latest_computed_date || '—' }}</div>
          <div v-if="detailStale" style="font-size:10px;color:#f59e0b;margin-top:2px"><AppIcon name="alert" :size="13" />  超过5天未更新</div>
        </div>
      </div>

      <!-- 依赖关系 -->
      <div style="display:flex;gap:12px;flex-wrap:wrap">
        <div style="flex:1;min-width:180px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px">
          <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:8px"><AppIcon name="trending-up" :size="13" />  上游依赖（本特征依赖谁）</div>
          <div v-if="(detailItem.depends_on||[]).length">
            <n-tag v-for="d in detailItem.depends_on" :key="d" size="tiny" :bordered="false" type="info" style="margin-right:4px;margin-bottom:4px">{{ d }}</n-tag>
          </div>
          <div v-else style="font-size:11px;color:var(--c-text-faint)">无（仅依赖原始字段）</div>
        </div>
        <div style="flex:1;min-width:180px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px">
          <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:8px"><AppIcon name="trending-down" :size="13" />  下游引用（谁依赖本特征）</div>
          <div v-if="(detailItem.downstream||[]).length">
            <div v-for="ds in detailItem.downstream" :key="ds.feature_name" style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
              <span style="font-size:12px;color:var(--c-text)">{{ ds.feature_name }}</span>
              <n-tag :type="statusTypeMap[ds.status]||'default'" size="tiny" :bordered="false">{{ statusMap[ds.status] }}</n-tag>
            </div>
          </div>
          <div v-else style="font-size:11px;color:var(--c-text-faint)">无下游引用</div>
        </div>
      </div>
    </template>
    <template #footer>
      <n-space justify="space-between">
        <span v-if="detailItem?.description" style="font-size:11px;color:var(--c-text-dim)">{{ detailItem.description }}</span>
        <div style="display:flex;gap:8px">
          <n-button size="small" @click="showDetail=false">关闭</n-button>
          <n-button v-if="detailItem" size="small" type="primary" @click="showDetail=false; openEdit(detailItem)"><AppIcon name="edit" :size="13" />  编辑</n-button>
        </div>
      </n-space>
    </template>
  </n-modal>

  <!-- AI 生成公式弹窗 -->
  <n-modal v-model:show="showAiFormula" preset="card" title="AI 生成 KEPL 公式" style="width:520px;max-width:92vw">
    <n-space vertical>
      <div style="font-size:12px;color:var(--c-text-dim)">描述计算逻辑，AI 根据 KEPL 语法和已注册函数生成公式。</div>
      <n-input v-model:value="aiFormulaText" type="textarea" placeholder="例如：收盘价相对5日均线的偏离度" :rows="4" />
      <div v-if="aiFormulaResult" style="margin-top:8px">
        <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:4px">生成结果</div>
        <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:6px;padding:8px;font-family:monospace;font-size:12px;white-space:pre-wrap;max-height:160px;overflow-y:auto">{{ aiFormulaResult }}</div>
        <n-button size="small" type="primary" style="margin-top:8px" @click="applyAiFormula"><AppIcon name="check" :size="13" />  填入公式框</n-button>
      </div>
    </n-space>
    <template #footer>
      <n-button @click="showAiFormula=false">取消</n-button>
      <n-button type="primary" @click="callAiFormula" :loading="aiFormulaLoading">生成</n-button>
    </template>
  </n-modal>

  <!-- Compute Range Modal -->
  <n-modal v-model:show="showCompute" preset="card" title="特征补数" style="width:420px;max-width:92vw" :mask-closable="false">
    <n-space vertical>
      <div style="font-size:12px;color:var(--c-text)">特征：<b>{{ computeTarget?.feature_name }}</b></div>
      <div style="font-size:11px;color:var(--c-text-dim)">选择补数日期范围，系统将对该特征在指定日期内重新计算并入库。</div>
      <n-date-picker v-model:value="computeDateRange" type="daterange" size="small" style="width:100%" clearable />
      <n-checkbox v-model:checked="computeForce">强制更新（覆盖已有数据）</n-checkbox>
      <div v-if="computeProgress" style="margin-top:8px">
        <div style="font-size:11px;color:var(--c-text-dim);margin-bottom:4px">
          {{ computeProgress.current_date || '计算中...' }}
        </div>
        <n-progress type="line" :percentage="computeProgress.progress_pct || 0" :height="8" :border-radius="4"
          :status="computeProgress.status === 'failed' ? 'error' : computeProgress.status === 'completed' ? 'success' : 'default'" />
        <div v-if="computeProgress.status === 'failed'" style="margin-top:8px;padding:10px;background:var(--c-error-bg, #fff0f0);border-radius:4px;font-size:12px;color:var(--c-error, #d03050)">
          <AppIcon name="x-circle" :size="13" />  计算失败：{{ computeProgress.error || '未知错误' }}
        </div>
      </div>
    </n-space>
    <template #footer>
      <n-space justify="flex-end">
        <n-button @click="showCompute=false">取消</n-button>
        <n-button type="primary" @click="doCompute" :loading="computeSubmitting" :disabled="!!computeProgress">开始补数</n-button>
      </n-space>
    </template>
  </n-modal>

  <!-- Full Dependency Graph Modal -->
  <n-modal v-model:show="showDepGraph" preset="card" title="特征依赖关系图" style="width:96vw;max-width:96vw;height:90vh" :mask-closable="true">
    <div ref="depGraphContainer" style="width:100%;height:calc(90vh - 120px)"></div>
  </n-modal>

  <!-- 去冗推荐弹窗 -->
  <n-modal v-model:show="showDedup" preset="card" title="因子去冗推荐" style="width:860px;max-width:94vw" :mask-closable="true">
    <n-space vertical>
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <n-select v-model:value="dedupHorizon" :options="[1,5,10,20].map(h=>({label:h+'日前瞻',value:h}))" size="small" style="width:110px" />
        <span style="font-size:11px;color:var(--c-text-dim)">近 3 年 · ≤120 个抽样截面 · 相关 > 0.7 视为冗余 · |ICIR| ≥ 0.10 才推荐</span>
        <div style="flex:1" />
        <n-button size="small" type="primary" :loading="dedupLoading" @click="loadDedup">计算</n-button>
      </div>
      <n-spin v-if="dedupLoading" style="padding:24px" />
      <template v-if="dedup">
        <div style="display:flex;gap:12px;flex-wrap:wrap">
          <div style="flex:1;min-width:380px">
            <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:4px">截面相关矩阵（逐日 Spearman 的时间平均）</div>
            <div ref="dedupHeatmap" style="width:100%;height:420px"></div>
          </div>
          <div style="flex:1;min-width:300px">
            <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:6px">
              <AppIcon name="check" :size="13" />  推荐入选（{{ dedup.recommended.length }} 个，按 |ICIR| 贪心）
            </div>
            <div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:12px">
              <n-tag v-for="f in dedup.recommended" :key="f" size="small" :bordered="false"
                :type="dedup.traffic[f]==='green'?'success':dedup.traffic[f]==='yellow'?'warning':'default'">
                {{ dedup.traffic[f]==='green'?'':dedup.traffic[f]==='yellow'?'':'' }}{{ f }}
              </n-tag>
            </div>
            <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:6px">⏭ 跳过（{{ dedup.skipped.length }} 个）</div>
            <n-data-table :columns="dedupSkipCols" :data="dedup.skipped" size="tiny" :max-height="240" />
            <n-button size="small" type="primary" style="margin-top:12px" :loading="dedupApplying"
              @click="applyRecommended"><AppIcon name="check" :size="13" />  推荐组设为入选</n-button>
            <div style="font-size:10px;color:var(--c-text-faint);margin-top:8px;line-height:1.7">
              口径：相关性 = 因子逐日截面排名（cs_rank）后的 pooled Pearson，等价于逐日截面 Spearman
              的时间平均，回答"两个因子是否在挑同一批股票"；贪心按 |ICIR| 降序，与已选因子相关
              超阈值即跳过，同簇保留 ICIR 最高者。入选决策仍由你确认，批量按钮只改推荐组成员。
            </div>
          </div>
        </div>
      </template>
      <n-empty v-else-if="!dedupLoading" description="点击「计算」生成相关矩阵与推荐组合" style="padding:30px" />
    </n-space>
  </n-modal>
</div>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import { ref, computed, h, onMounted, onUnmounted } from 'vue'
import { NButton, NDataTable, NModal, NSpace, NInput, NSelect, NTag, NSwitch, NSpin, NPagination, NDatePicker, NCheckbox, NProgress, NEmpty, useMessage } from 'naive-ui'
import MonacoEditor from './MonacoEditor.vue'
import { useNavStore } from '../stores/nav'
import * as echarts from 'echarts'
import axios from 'axios'
import { addWsListener } from '../utils/ws'

const API = window.location.origin
const message = useMessage()
const loading = ref(false)
const items = ref([])
const total = ref(0)
const page = ref(1)
const PAGE_SIZE = 20
const filterEntity = ref(null)
const filterStatus = ref(null)
const filterIcStatus = ref(null)
const searchText = ref('')

const icStatusOptions = [
  { label:'全部决策', value:'all' },
  { label:'已入选', value:'included' },
  { label:'候选', value:'candidate' },
  { label:'已剔除', value:'excluded' },
]
// IC 排行板数据（horizon=10 最新一次），按 feature_name 合并进列表行
const icBoardMap = ref({})

async function loadIcBoard() {
  try {
    const r = await axios.get(API + '/api/features/ic/board', { params: { horizon: 10 } })
    const m = {}
    for (const b of (r.data.board || [])) m[b.feature_name] = b
    icBoardMap.value = m
  } catch (e) { console.error(e) }
}

// IC 红绿灯：渲染为彩色 CSS 圆点（canvas/文本皆不依赖彩色 emoji）
const TRAFFIC_COLOR = { green:'#10b981', yellow:'#f59e0b', red:'#ef4444' }
const icStatusTagMap = { candidate:{ label:'候选', type:'default' }, included:{ label:'已入选', type:'success' }, excluded:{ label:'已剔除', type:'error' } }

// ── 去冗推荐 ──
const showDedup = ref(false)
const dedupHorizon = ref(10)
const dedupLoading = ref(false)
const dedup = ref(null)
const dedupApplying = ref(false)
const dedupHeatmap = ref(null)
let dedupHeatInst = null

async function loadDedup() {
  dedupLoading.value = true
  try {
    const r = await axios.get(API + '/api/features/ic/correlation', { params: { horizon: dedupHorizon.value } })
    dedup.value = r.data
    setTimeout(() => renderDedupHeatmap(), 50)
  } catch (e) {
    message.error(e.response?.data?.detail || '计算失败')
  } finally {
    dedupLoading.value = false
  }
}

function renderDedupHeatmap() {
  if (!dedupHeatmap.value || !dedup.value) return
  dedupHeatInst?.dispose()
  dedupHeatInst = echarts.init(dedupHeatmap.value)
  const { factors, matrix } = dedup.value
  const data = []
  for (let i = 0; i < factors.length; i++)
    for (let j = 0; j < factors.length; j++)
      data.push([j, i, matrix[i][j]])
  dedupHeatInst.setOption({
    tooltip: { formatter: p => `${factors[p.data[1]]} × ${factors[p.data[0]]}<br/>ρ = ${p.data[2]}` },
    grid: { left: 90, right: 10, top: 4, bottom: 70 },
    xAxis: { type: 'category', data: factors, axisLabel: { fontSize: 8, rotate: 60 } },
    yAxis: { type: 'category', data: factors, axisLabel: { fontSize: 8 } },
    visualMap: { min: -1, max: 1, calculable: true, orient: 'horizontal', left: 'center', bottom: 0,
                 inRange: { color: ['#2080f0', '#ffffff', '#ef4444'] }, textStyle: { fontSize: 9 } },
    series: [{ type: 'heatmap', data, label: { show: factors.length <= 15, fontSize: 8, formatter: p => p.data[2].toFixed(2) } }],
  })
}

const dedupSkipCols = [
  { title: '因子', key: 'factor', width: 100 },
  { title: '原因', key: 'reason', ellipsis: { tooltip: true } },
]

async function applyRecommended() {
  const names = dedup.value?.recommended || []
  if (!names.length) return
  dedupApplying.value = true
  try {
    // 用 board 数据拿到 feature_id（board 已含全部 enabled stock 因子）
    const br = await axios.get(API + '/api/features/ic/board', { params: { horizon: dedupHorizon.value } })
    const idMap = {}
    for (const b of (br.data.board || [])) idMap[b.feature_name] = b.feature_id
    let ok = 0
    for (const f of names) {
      if (idMap[f] == null) continue
      try {
        await axios.put(API + `/api/features/${idMap[f]}/ic-status`, { status: 'included' }, { headers: authHeaders() })
        ok++
      } catch (e) { console.error(f, e) }
    }
    message.success(`已将 ${ok} 个推荐因子设为入选`)
    await loadIcBoard()
    loadData()
  } catch (e) {
    message.error('批量设置失败')
  } finally {
    dedupApplying.value = false
  }
}

const showCreate = ref(false)
const editId = ref(null)
const creating = ref(false)
const validating = ref(false)
const validateResult = ref(null)
const parseDeps = ref([])

// ── Detail modal state ──
const showDetail = ref(false)
const detailItem = ref(null)
const detailLoading = ref(false)
const detailStale = ref(false)
const completenessPct = ref(0)

const form = ref({
  feature_name: '',
  display_name: '',
  target_entity: 'stock',
  description: '',
  formula: '',
  feature_group: '',
  tags: [],
  enabled: false,
})

function resetForm() {
  editId.value = null
  form.value = { feature_name:'', display_name:'', target_entity:'stock', description:'', formula:'', feature_group:'', tags:[], enabled:false }
  validateResult.value = null
  parseDeps.value = []
}

// ── 特征族 / 标签 ──
const groupOptions = ref([])
const tagOptions = ref([])

async function loadGroupAndTags() {
  try {
    const [gr, tr] = await Promise.all([
      axios.get(API + '/api/features/groups'),
      axios.get(API + '/api/features/tags'),
    ])
    groupOptions.value = (gr.data.groups || []).map(g => ({ label: g, value: g }))
    tagOptions.value = (tr.data.tags || []).map(t => ({ label: t, value: t }))
  } catch(e) {}
}

function createGroup(label) { return { label, value: label } }
function createTag(label) { return { label, value: label } }

loadGroupAndTags()

function openCreate() {
  resetForm()
  showCreate.value = true
}

const nav = useNavStore()

// ── AI 生成公式 ──
const showAiFormula = ref(false)
const aiFormulaText = ref('')
const aiFormulaResult = ref('')
const aiFormulaLoading = ref(false)

// ── KEPL 算子目录（/api/kepl/functions 单一事实源）──
const formulaEditor = ref(null)
const keplFns = ref(null)
const showOpsPanel = ref(false)

const opGroups = computed(() => {
  if (!keplFns.value) return []
  return [
    { label: '时序算子', items: keplFns.value.time_series },
    { label: '截面算子', items: keplFns.value.cross_sectional },
  ]
})

function sigToSnippet(sig) {
  const m = sig.match(/^(\w+)\((.*)\)$/)
  if (!m) return sig
  const args = m[2].split(',').map(s => s.trim())
  return `${m[1]}(${args.map((a, i) => `\${${i + 1}:${a}}`).join(', ')})`
}

const keplCompletions = computed(() => {
  if (!keplFns.value) return []
  const all = [...keplFns.value.time_series, ...keplFns.value.cross_sectional]
  return all.map(op => ({ label: op.name, insert: sigToSnippet(op.sig), detail: op.desc }))
})

function insertOp(op) {
  formulaEditor.value?.insertSnippet(`${op.sig}`)
}

async function loadKeplFunctions() {
  try {
    const r = await axios.get(API + '/api/kepl/functions')
    keplFns.value = r.data
  } catch (e) { console.warn('算子目录加载失败', e) }
}
onMounted(loadKeplFunctions)

// AI 上下文：语法规则 + 全部内置算子（动态生成，与注册表一致）
const KEPL_SPEC = computed(() => {
  const base = `KEPL 语法：
- 裸字段：close, open, high, low, volume, amount；基本面字段亦可用（pe_ttm/pb_mrq/turnover_rate 等）
- 算术运算：+ - * /（除零自动置空）；函数参数位支持负数（ref(close, -1)=未来值）
- 因子惯例：用 (close+1e-12) 做除法归一化防除零
- 不支持 if/比较/逻辑运算——条件逻辑用自定义 Python 函数`
  if (!keplFns.value) return base
  const ts = keplFns.value.time_series.map(o => `- ${o.sig}：${o.desc}；例：${o.eg}`).join('\n')
  const cs = keplFns.value.cross_sectional.map(o => `- ${o.sig}：${o.desc}；例：${o.eg}`).join('\n')
  return `${base}\n\n【时序算子（按股滚动计算）】\n${ts}\n\n【截面算子（同交易日全市场）】\n${cs}`
})

async function callAiFormula() {
  if (!aiFormulaText.value.trim()) return
  aiFormulaLoading.value = true
  aiFormulaResult.value = ''
  try {
    const fr = await axios.get(API + '/api/functions?page_size=200')
    const funcList = (fr.data.items || []).map(f =>
      `${f.name}(${(f.parameters||[]).map(p=>p.name+(p.default!==undefined?'='+p.default:'')).join(',')}): ${f.description||''}`
    ).join('\n')

    const prompt = `${KEPL_SPEC.value}\n\n【自定义函数】\n${funcList || '（无）'}\n\n【需求】\n${aiFormulaText.value}\n\n只返回公式。`
    const r = await axios.post(API + '/api/functions/ai-chat', {
      messages: [{ role: 'user', content: prompt }],
    }, { headers: authHeaders() })
    const content = r.data?.content?.content || r.data?.content || ''
    const m = content.match(/```[\s\S]*?\n([\s\S]*?)```/) || [null, content]
    aiFormulaResult.value = (m[1] || content).trim()
  } catch(e) {
    aiFormulaResult.value = '# 失败: ' + (e.response?.data?.detail || e.message)
  } finally { aiFormulaLoading.value = false }
}

function applyAiFormula() {
  form.value.formula = aiFormulaResult.value
  showAiFormula.value = false
  aiFormulaText.value = ''
  aiFormulaResult.value = ''
}

// ── 补数 ──
const showCompute = ref(false)
const computeTarget = ref(null)
const computeDateRange = ref(null)
const computeForce = ref(false)
const computeSubmitting = ref(false)
const computeProgress = ref(null)
let computeWsUnwatch = null

function openCompute(row) {
  computeTarget.value = row
  computeDateRange.value = null
  computeForce.value = false
  computeProgress.value = null
  showCompute.value = true
}

async function doCompute() {
  if (!computeTarget.value || !computeDateRange.value || computeDateRange.value.length !== 2) {
    message.warning('请选择补数日期范围')
    return
  }
  computeSubmitting.value = true
  // 先注册 WS 监听（避免错过 POST 返回前后端发出的进度消息）
  const featureId = computeTarget.value.id
  const currentDateRange = computeDateRange.value
  computeProgress.value = { progress_pct: 0, current_date: '任务已提交...' }
  listenComputeProgress(null, featureId)  // taskId=null 表示暂时监听该 feature 的所有进度
  try {
    const [s, e] = currentDateRange
    const fd = (d) => {
      const dt = new Date(d)
      return dt.getFullYear() + '-' + String(dt.getMonth()+1).padStart(2,'0') + '-' + String(dt.getDate()).padStart(2,'0')
    }
    const r = await axios.post(API + '/api/features/' + featureId + '/compute-range', {
      start_date: fd(s), end_date: fd(e), force: computeForce.value
    }, { headers: authHeaders() })
    if (r.data.ok) {
      // 拿到真实 taskId 后缩小 WS 过滤范围
      listenComputeProgress(r.data.task_id, featureId)
      // 立即轮询一次最新进度，弥补注册监听前丢失的 WS 消息
      try {
        const st = await axios.get(API + '/api/features/' + featureId + '/compute-status')
        if (st.data.has_task) {
          computeProgress.value = { ...st.data }
        }
      } catch(_) {}
    }
  } catch(e) {
    message.error(e.response?.data?.detail || '提交失败')
  }
  computeSubmitting.value = false
}

function listenComputeProgress(taskId, featureId) {
  if (computeWsUnwatch) computeWsUnwatch()
  computeWsUnwatch = addWsListener((data) => {
    if (data.type !== 'feature_compute_progress') return
    // taskId 为 null 时匹配该 feature 的任意 task；非 null 时精确匹配
    if (taskId && data.task_id !== taskId) return
    if (data.feature_id !== featureId) return
    computeProgress.value = data
    if (data.status === 'completed' || data.status === 'failed') {
        if (computeWsUnwatch) { computeWsUnwatch(); computeWsUnwatch = null }
        if (data.status === 'failed') {
          // 失败时不自动关闭，让用户看到错误信息
          return
        }
        setTimeout(() => {
          computeProgress.value = null
          showCompute.value = false
          computeTarget.value = null
          loadData()
        }, 2000)
    }
  })
}

function authHeaders() {
  const t = localStorage.getItem('token')
  return t ? { Authorization: 'Bearer ' + t } : {}
}

// ── 依赖图 ──
const showDepGraph = ref(false)
const depGraphContainer = ref(null)

async function openDepGraph() {
  showDepGraph.value = true
  await nextTick()
  if (!depGraphContainer.value) return
  try {
    const r = await axios.get(API + '/api/features/dependency-graph')
    const { nodes, edges } = r.data
    const chart = echarts.init(depGraphContainer.value)
    const colors = {0:'#9ca3af',1:'#2080f0',2:'#f59e0b',3:'#ef4444'}
    chart.setOption({
      tooltip: { formatter(p) { return p.data.name + (p.data.display_name?'<br/>'+p.data.display_name:'') } },
      series: [{
        type: 'graph', layout: 'force', roam: true, draggable: true,
        force: { repulsion: 200, edgeLength: [80,200] },
        data: nodes.map(n => ({ name:n.id, display_name:n.display_name, itemStyle:{color:colors[n.level]||colors[0]}, symbolSize:12 })),
        links: edges.map(e => ({ source: e.source, target: e.target })),
        lineStyle: { color:'#6b7280', curveness:0.1, opacity:0.4 },
        label: { show:true, fontSize:9, color:'var(--c-text)' },
      }]
    })
  } catch(e) { console.error(e) }
}

import { nextTick } from 'vue'

function openDetail(id) {
  nav.showFeatureDetail(id)
}

function openEdit(row) {
  editId.value = row.id
  form.value = {
    feature_name: row.feature_name,
    display_name: row.display_name || '',
    target_entity: row.target_entity,
    description: row.description || '',
    formula: row.formula || '',
    feature_group: row.feature_group || '',
    tags: row.tags || [],
    enabled: row.status === 'enabled',
  }
  parseDeps.value = row.depends_on || []
  validateResult.value = null
  showCreate.value = true
}

async function doValidate() {
  validating.value = true
  validateResult.value = null
  try {
    const r = await axios.post(API + '/api/features/validate', {
      formula: form.value.formula,
      target_entity: form.value.target_entity,
    })
    validateResult.value = r.data
    if (r.data.ok) {
      parseDeps.value = r.data.dependencies || []
    }
  } catch (e) {
    validateResult.value = { ok: false, errors: [{ message: e.response?.data?.detail || e.message }] }
  }
  validating.value = false
}

async function doCreate(status) {
  creating.value = true
  try {
    const payload = {
      feature_name: form.value.feature_name,
      display_name: form.value.display_name,
      target_entity: form.value.target_entity,
      description: form.value.description,
      formula: form.value.formula,
      feature_group: form.value.feature_group || null,
      tags: form.value.tags || [],
      status,
    }
    if (editId.value) {
      await axios.put(API + `/api/features/${editId.value}`, {
        display_name: payload.display_name,
        description: payload.description,
        formula: payload.formula,
        status: payload.status,
      })
    } else {
      await axios.post(API + '/api/features', payload)
    }
    showCreate.value = false
    resetForm()
    loadData()
  } catch (e) {
    message.error(e.response?.data?.detail || '保存失败')
  }
  creating.value = false
}

// ── Status labels ──
const statusMap = { draft:'草稿', enabled:'已启用', pending_recalc:'待重算', deprecated:'已弃用', data_anomaly:'数据异常' }
const statusTypeMap = { draft:'warning', enabled:'success', pending_recalc:'info', deprecated:'default', data_anomaly:'error' }
const entityLabel = { stock:'个股', etf:'ETF', index:'指数', global:'全局' }

const entityOptions = [
  { label:'全部实体', value:'all' },
  ...Object.entries(entityLabel).map(([k,v]) => ({ label:v, value:k })),
]
const statusOptions = [
  { label:'全部状态', value:'all' },
  ...Object.entries(statusMap).map(([k,v]) => ({ label:v, value:k })),
]
const entityOpts = Object.entries(entityLabel).map(([k,v]) => ({ label:v, value:k }))

const columns = [
  { title:'英文名', key:'feature_name', width:150, fixed:'left', ellipsis:{tooltip:true}, render(row) {
    return h('span', { style:{cursor:'pointer',color:'#2080f0',textDecoration:'underline'}, onClick:() => openDetail(row.id) }, row.feature_name)
  }},
  { title:'中文名', key:'display_name', width:100, fixed:'left', ellipsis:{tooltip:true} },
  { title:'族', key:'feature_group', width:70, render:(row) => row.feature_group || '—' },
  { title:'实体', key:'target_entity', width:60, render:(row) => entityLabel[row.target_entity] || row.target_entity },
  { title:'状态', key:'status', width:80, render:(row) => h(NTag, { type:statusTypeMap[row.status]||'default', size:'tiny', bordered:false }, () => statusMap[row.status]||row.status) },
  { title:'完整度', key:'data_completeness', width:80, render:(row) => {
    const pct = Math.round((row.data_completeness||0)*1000)/10
    const color = pct >= 60 ? '#10b981' : pct >= 30 ? '#f59e0b' : '#ef4444'
    return h('div', {}, [
      h('div', { style:{fontSize:'10px',color:'var(--c-text-dim)',marginBottom:'2px'} }, pct+'%'),
      h('div', { style:{background:'var(--c-border)',borderRadius:'3px',height:'4px',overflow:'hidden'} },
        [h('div', { style:{width:pct+'%',height:'100%',background:color,borderRadius:'3px'} })])
    ])
  }},
  { title:'IC 体检', key:'ic', width:150, render(row) {
    const b = icBoardMap.value[row.feature_name]
    if (row.target_entity !== 'stock') return h('span', { style:{color:'var(--c-text-faint)',fontSize:'11px'} }, '—')
    if (!b || b.rank_ic == null) return h('span', { style:{color:'var(--c-text-faint)',fontSize:'11px'} }, '○ 未检验')
    return h('div', { style:{display:'flex',alignItems:'center',gap:'6px'} }, [
      h('span', { style:{display:'inline-block', width:'8px', height:'8px', borderRadius:'50%',
        background: TRAFFIC_COLOR[b.traffic] || 'var(--c-text-faint)', verticalAlign:'middle', marginRight:'2px'} }, ''),
      h('span', { style:{fontSize:'11px',color:'var(--c-text)'} }, (b.rank_ic>=0?'+':'') + b.rank_ic.toFixed(4)),
      h('span', { style:{fontSize:'10px',color:'var(--c-text-dim)'} }, 'IR ' + (b.icir>=0?'+':'') + (b.icir??0).toFixed(2)),
      b.direction === '-' ? h('span', { style:{fontSize:'10px',color:'#f59e0b'} }, '↩') : null,
    ])
  }},
  { title:'决策', key:'ic_status', width:74, render(row) {
    const s = icStatusTagMap[row.ic_status || 'candidate']
    return h(NTag, { type:s.type, size:'tiny', bordered:false }, () => s.label)
  }},
  { title:'异常缺失', key:'abnormal_missing_cells', width:70, align:'right', render:(row) => (row.abnormal_missing_cells||0).toLocaleString() },
  { title:'最近计算', key:'latest_computed_date', width:90, render:(row) => row.latest_computed_date || '—' },
  { title:'依赖数', key:'depends_on', width:60, align:'center', render:(row) => (row.depends_on?.length || 0) },
  { title:'操作', key:'actions', width:110, fixed:'right', render(row) {
    return h('div', { style:{display:'flex',gap:'2px'} }, [
      h(NButton, { size:'tiny', quaternary:true, style:'fontSize:11px', onClick:() => openEdit(row) }, () => '编辑'),
      h(NButton, { size:'tiny', quaternary:true, style:'fontSize:11px', onClick:() => openCompute(row) }, () => h(AppIcon, { name: 'download', size: 12 })),
    ])
  }},
]

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

function rowProps(row) {
  const colors = { enabled:'#10b981', draft:'#f59e0b', pending_recalc:'#2080f0', deprecated:'#9ca3af', data_anomaly:'#ef4444' }
  return { style: { borderLeft: '3px solid ' + (colors[row.status]||'transparent') } }
}

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: PAGE_SIZE }
    if (filterEntity.value && filterEntity.value !== 'all') params.entity = filterEntity.value
    if (filterStatus.value && filterStatus.value !== 'all') params.status = filterStatus.value
    if (filterIcStatus.value && filterIcStatus.value !== 'all') params.ic_status = filterIcStatus.value
    if (searchText.value) params.search = searchText.value
    const r = await axios.get(API + '/api/features', { params })
    items.value = r.data.items || []
    total.value = r.data.total || 0
    syncHash()
  } catch (e) {
    console.error('loadData:', e)
  }
  loading.value = false
}

// URL hash 参数同步
function syncHash() {
  const qs = []
  if (page.value > 1) qs.push('page=' + page.value)
  if (filterEntity.value && filterEntity.value !== 'all') qs.push('entity=' + filterEntity.value)
  if (filterStatus.value && filterStatus.value !== 'all') qs.push('status=' + filterStatus.value)
  if (searchText.value) qs.push('search=' + encodeURIComponent(searchText.value))
  const target = '/features' + (qs.length ? '?' + qs.join('&') : '')
  if (location.hash.slice(1) !== target) history.replaceState(null, '', '#' + target)
}

function parseHashParams() {
  const hash = location.hash.slice(1)
  const q = hash.includes('?') ? hash.split('?')[1] : ''
  if (!q) return
  const sp = new URLSearchParams(q)
  if (sp.has('page')) page.value = parseInt(sp.get('page')) || 1
  if (sp.has('entity')) filterEntity.value = sp.get('entity')
  if (sp.has('status')) filterStatus.value = sp.get('status')
  if (sp.has('search')) searchText.value = sp.get('search')
}

onMounted(() => {
  parseHashParams()
  loadIcBoard()
  loadData().then(() => {
    const editId = nav.pendingEditFeatureId
    if (editId) {
      nav.pendingEditFeatureId = null
      // 延迟确保列表渲染完毕
      setTimeout(() => {
        const row = items.value.find(i => i.id === editId)
        if (row) openEdit(row)
      }, 200)
    }
  })
})

function onPopstate() {
  // 组件可能已卸载（v-if 切换）：非本页时不响应，避免用本页 URL 覆盖地址栏
  if (nav.tab !== 'e') return
  parseHashParams()
  loadData()
}
onUnmounted(() => {
  if (computeWsUnwatch) computeWsUnwatch()
  window.removeEventListener('popstate', onPopstate)
})
window.addEventListener('popstate', onPopstate)
</script>