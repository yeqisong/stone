<template>
<div style="padding:16px 8px">
  <n-spin v-if="loading" style="padding:60px" />
  <template v-else-if="feat">
    <!-- 顶部导航 -->
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px">
      <n-button size="tiny" quaternary @click="$emit('back')"><AppIcon name="arrow-left" :size="13" />  返回列表</n-button>
      <span style="font-size:17px;font-weight:700;color:var(--c-text)">{{ feat.feature_name }}</span>
      <n-tag :type="statusTypeMap[feat.status]||'default'" size="tiny" :bordered="false">{{ statusMap[feat.status] }}</n-tag>
      <div style="flex:1" />
      <n-button size="tiny" @click="openEditInline"><AppIcon name="edit" :size="13" />  编辑</n-button>
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
          <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:6px"> KEPL 公式</div>
          <code style="font-size:14px;color:var(--c-text);word-break:break-all">{{ feat.formula }}</code>
        </div>

        <!-- 质量仪表盘 4 卡片 -->
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px">
          <div style="flex:1;min-width:100px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px"><AppIcon name="bar-chart-2" :size="13" />  数据完整度</div>
            <div :style="{fontSize:'22px',fontWeight:700,color:completenessPct>=60?'#10b981':completenessPct>=30?'#f59e0b':'#ef4444'}">{{ completenessPct }}%</div>
            <div style="background:var(--c-border);border-radius:4px;height:6px;margin-top:4px;overflow:hidden">
              <div :style="{width:completenessPct+'%',height:'100%',background:completenessPct>=60?'#10b981':completenessPct>=30?'#f59e0b':'#ef4444',borderRadius:'4px'}"></div>
            </div>
          </div>
          <div style="flex:1;min-width:80px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px"><AppIcon name="w" :size="13" />  总格子</div>
            <div style="font-size:20px;font-weight:700;color:var(--c-text)">{{ (feat.total_effective_cells||0).toLocaleString() }}</div>
            <div style="font-size:9px;color:var(--c-text-faint);margin-top:2px">交易日×股票数</div>
          </div>
          <div style="flex:1;min-width:80px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px"><AppIcon name="check" :size="13" />  已计算</div>
            <div style="font-size:20px;font-weight:700;color:#10b981">{{ computedActual.toLocaleString() }}</div>
          </div>
          <div style="flex:1;min-width:80px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px;text-align:center">
            <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px"><AppIcon name="close" :size="13" />  总缺失格</div>
            <div :style="{fontSize:'20px',fontWeight:700,color:feat.abnormal_missing_cells>0?'#f59e0b':'var(--c-text-dim)'}">{{ (feat.abnormal_missing_cells||0).toLocaleString() }}</div>
          </div>
        </div>

        <!-- 最近计算 -->
        <div style="font-size:12px;color:var(--c-text-dim);margin-bottom:14px">
          <AppIcon name="calendar" :size="13" />  最近计算日：<span :style="{color:feat.latest_computed_date?(staleDays>5?'#f59e0b':'var(--c-text)'):'var(--c-text-faint)'}">{{ feat.latest_computed_date || '从未计算' }}</span>
          <span v-if="staleDays>5" style="color:#f59e0b;margin-left:8px"><AppIcon name="alert" :size="13" />  超过 {{ staleDays }} 天未更新</span>
        </div>

        <!-- 依赖关系 -->
        <div style="display:flex;gap:12px;flex-wrap:wrap">
          <div style="flex:1;min-width:180px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px">
            <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:8px"><AppIcon name="trending-up" :size="13" />  上游依赖</div>
            <div v-if="(feat.depends_on||[]).length">
              <n-tag v-for="d in feat.depends_on" :key="d" size="tiny" :bordered="false" type="info" style="margin-right:4px;margin-bottom:4px">{{ d }}</n-tag>
            </div>
            <div v-else style="font-size:11px;color:var(--c-text-faint)">无（仅依赖原始字段）</div>
          </div>
          <div style="flex:1;min-width:180px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:12px">
            <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:8px"><AppIcon name="trending-down" :size="13" />  下游引用</div>
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
          <n-button size="tiny" quaternary @click="recomputeStats" :loading="statsLoading"><AppIcon name="refresh" :size="13" />  重新诊断</n-button>
        </div>
        <div v-if="!feat.total_effective_cells" style="text-align:center;padding:60px 20px;color:var(--c-text-dim)">
          <div style="font-size:14px;margin-bottom:8px"><AppIcon name="inbox" :size="44" />  暂无特征计算数据</div>
          <div style="font-size:12px">该特征尚未执行计算，请通过列表页 <AppIcon name="download" :size="13" />  补数功能或 DAG 流水线触发特征计算。</div>
        </div>
        <div v-else style="display:flex;gap:12px;flex-wrap:wrap">
          <div style="flex:1;min-width:300px">
            <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:8px">缺失归因饼图</div>
            <div ref="pieChart" style="width:100%;height:260px"></div>
            <div v-if="uncomputedPct>50" style="background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.2);border-radius:6px;padding:8px;font-size:11px;color:#f59e0b;margin-top:8px">
               {{ uncomputedPct }}% 的总格子尚未补数，请扩大补数日期范围覆盖更多历史数据。
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
          <StockSuggestInput v-model:value="previewCode" size="small" width="190px" @select="doLoadPreview" @enter="doLoadPreview" />
          <n-button size="small" @click="doLoadPreview">查询</n-button>
        </div>
        <n-spin v-if="previewLoading" style="padding:40px" />
        <!-- Section 表：限高内滚（50 行/页全展开约 1400px 会把分页器顶出一屏），表头吸顶 -->
        <n-data-table v-else-if="previewItems.length" :columns="previewCols" :data="previewItems" size="small" :max-height="460" scroll-x="500" />
        <ListPagination v-if="previewItems.length" :total="previewTotal" :page="previewPage" :page-size="50" @change="p => { previewPage = p; loadPreview() }" />
        <n-empty v-else :description="previewEmptyReason || '暂无数据'" style="padding:20px" />
      </n-tab-pane>

      <n-tab-pane name="ic" tab="IC 体检">
        <div v-if="feat.target_entity!=='stock'" style="text-align:center;padding:50px;color:var(--c-text-dim)">
          <div style="font-size:14px;margin-bottom:6px"> 仅支持个股（stock）实体因子的 IC 检验</div>
          <div style="font-size:12px">当前实体：{{ entityLabel[feat.target_entity] || feat.target_entity }}</div>
        </div>
        <template v-else>
          <!-- 工具行 -->
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap">
            <n-tag :type="trafficTypeMap[icDetail?.traffic]||'default'" size="small" :bordered="false">
              {{ trafficMap[icDetail?.traffic] || '○ 未检验' }}
            </n-tag>
            <n-tag size="small" :bordered="false" :type="icStatusTypeMap[feat.ic_status]">{{ icStatusMap[feat.ic_status] || '候选' }}</n-tag>
            <n-tag v-if="icDetail?.direction==='-'" size="small" type="warning" :bordered="false">↩ 反向因子（取反使用）</n-tag>
            <div style="flex:1" />
            <n-select v-model:value="icHorizon" :options="horizonOptions" size="tiny" style="width:96px" @update:value="loadIcDetail" />
            <n-button size="tiny" @click="openIcCompute"><AppIcon name="e" :size="13" />  重算…</n-button>
            <n-button v-if="feat.ic_status!=='included'" size="tiny" type="primary" @click="doSetIcStatus('included')"><AppIcon name="check" :size="13" />  入选</n-button>
            <n-button v-if="feat.ic_status!=='excluded'" size="tiny" type="error" quaternary @click="doSetIcStatus('excluded')"> 剔除</n-button>
            <n-button v-if="feat.ic_status==='candidate'||feat.ic_status==='excluded'" size="tiny" quaternary @click="doSetIcStatus('candidate')"><AppIcon name="refresh" :size="13" />  候选</n-button>
          </div>

          <n-spin v-if="icLoading" size="small" style="padding:24px" />
          <!-- 指标卡 -->
          <div v-else-if="icDetail" style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px">
            <div style="flex:1;min-width:100px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:10px;text-align:center">
              <div style="font-size:10px;color:var(--c-text-faint)">RankIC（{{ icHorizon }}日前瞻）</div>
              <div :style="{fontSize:'20px',fontWeight:700,color:icDetail.rank_ic_mean>=0?'#10b981':'#f59e0b'}">{{ icDetail.rank_ic_mean>=0?'+':'' }}{{ icDetail.rank_ic_mean?.toFixed(4) }}</div>
            </div>
            <div style="flex:1;min-width:100px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:10px;text-align:center">
              <div style="font-size:10px;color:var(--c-text-faint)">ICIR</div>
              <div :style="{fontSize:'20px',fontWeight:700,color:Math.abs(icDetail.rank_ic_ir)>=0.3?'#10b981':'#f59e0b'}">{{ icDetail.rank_ic_ir>=0?'+':'' }}{{ icDetail.rank_ic_ir?.toFixed(3) }}</div>
            </div>
            <div style="flex:1;min-width:100px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:10px;text-align:center">
              <div style="font-size:10px;color:var(--c-text-faint)">IC 同号占比</div>
              <div :style="{fontSize:'20px',fontWeight:700,color:icDetail.ic_win_rate>=0.55?'#10b981':'#f59e0b'}">{{ (icDetail.ic_win_rate*100).toFixed(1) }}%</div>
            </div>
            <div style="flex:1;min-width:100px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:10px;text-align:center">
              <div style="font-size:10px;color:var(--c-text-faint)">t 值 / 样本</div>
              <div style="font-size:20px;font-weight:700;color:var(--c-text)">{{ icDetail.t_stat?.toFixed(1) }}</div>
              <div style="font-size:9px;color:var(--c-text-faint);margin-top:2px">{{ icDetail.sample_days }} 天 × {{ icDetail.avg_names?.toFixed(0) }} 股</div>
            </div>
          </div>
          <n-empty v-else description="暂无检验记录，点右上角「重算」开始" style="padding:30px" />

          <!-- 三图 -->
          <div v-if="icDetail" style="display:flex;gap:12px;flex-wrap:wrap">
            <div style="flex:1;min-width:320px">
              <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:4px">日度 RankIC（柱）× 累计 IC（线）</div>
              <div ref="icChart" style="width:100%;height:230px"></div>
            </div>
            <div style="flex:1;min-width:320px">
              <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:4px">分层净值（{{ icHorizon }}日前瞻·日均化·未计成本）</div>
              <div ref="layerChart" style="width:100%;height:230px"></div>
            </div>
          </div>
          <div v-if="icDetail" style="margin-top:10px">
            <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:4px">预测衰减（各前瞻期 RankIC，取最近一次检验）</div>
            <div ref="decayChart" style="width:100%;height:180px"></div>
          </div>

          <!-- 历次记录 -->
          <div v-if="icRecords.length" style="margin-top:14px;background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:10px 12px">
            <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:6px">历次检验记录（点击行切换图表周期）</div>
            <n-data-table :columns="icHistoryCols" :data="icRecords" size="small" :max-height="200" :row-props="icRowProps" :bordered="false" :single-line="true" scroll-x="730" />
          </div>

          <div style="margin-top:12px;font-size:10px;color:var(--c-text-faint);line-height:1.8">
            口径：RankIC = 每日截面因子排名 vs 前瞻 N 日收益排名的 Spearman 相关；ICIR = mean(IC)/std(IC)；分层按因子值均分 5 组，
            净值按"前瞻收益/持有天数"日均化近似累计（未计交易成本，仅观察单调性，不作为回测依据）；
            红绿灯：|RankIC|≥0.02 且 |ICIR|≥0.30 且 同号占比≥55% <AppIcon name="arrow-right" :size="13" />  绿，两项 <AppIcon name="arrow-right" :size="13" />  黄，其余 <AppIcon name="arrow-right" :size="13" />  红；IC 为负时标注反向因子。
          </div>
        </template>
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
        <n-button size="tiny" quaternary @click="showAiPrompt = true" :loading="aiLoading" style="font-size:11px"><AppIcon name="a" :size="13" />  AI 生成</n-button>
      </div>
      <MonacoEditor ref="formulaEditor" v-model="editForm.formula" :completions="keplCompletions" />
      <div style="display:flex;align-items:center;gap:8px;font-size:11px;color:var(--c-text-dim)">
        <span style="font-weight:600"><AppIcon name="file-text" :size="13" />  算子速查</span>
        <span style="color:var(--c-text-faint)">编辑框内输入可自动补全；点击算子插入光标处</span>
        <n-button size="tiny" quaternary @click="showOpsPanel = !showOpsPanel">{{ showOpsPanel ? '收起' : '展开' }}</n-button>
      </div>
      <div v-if="showOpsPanel && keplFns" style="max-height:220px;overflow-y:auto;border:1px solid var(--c-border);border-radius:6px;padding:8px;display:flex;flex-direction:column;gap:6px">
        <div v-for="grp in opGroups" :key="grp.label">
          <div style="font-size:10px;font-weight:600;color:var(--c-text-faint);margin-bottom:4px">{{ grp.label }}</div>
          <div style="display:flex;flex-wrap:wrap;gap:4px">
            <n-tag v-for="op in grp.items" :key="op.name" size="small" :bordered="false"
                   style="cursor:pointer;font-family:monospace" :title="`${op.desc}　例: ${op.eg}`"
                   @click="insertOp(op)">{{ op.sig }}</n-tag>
          </div>
        </div>
      </div>
    </n-space>
    <template #footer>
      <n-space justify="flex-end">
        <n-button @click="showEditModal = false">取消</n-button>
        <n-button type="primary" @click="saveEdit" :loading="editSaving">保存</n-button>
      </n-space>
    </template>
  </n-modal>

  <!-- AI 生成公式弹窗 -->
  <n-modal v-model:show="showAiPrompt" preset="card" title="AI 生成 KEPL 公式" style="width:520px;max-width:92vw">
    <n-space vertical>
      <div style="font-size:12px;color:var(--c-text-dim)">描述你需要的特征计算逻辑，AI 会根据 KEPL 语法规范和已有函数生成公式。</div>
      <n-input v-model:value="aiRequirement" type="textarea" placeholder="例如：计算收盘价相对于5日均线的偏离度，即 (close - ma(close,5)) / ma(close,5)" :rows="4" />
      <div v-if="aiResult" style="margin-top:8px">
        <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:4px">生成结果</div>
        <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:6px;padding:8px;font-family:monospace;font-size:12px;white-space:pre-wrap;max-height:160px;overflow-y:auto">{{ aiResult }}</div>
        <n-button size="small" type="primary" style="margin-top:8px" @click="applyAiResult"><AppIcon name="check" :size="13" />  填入公式框</n-button>
      </div>
    </n-space>
    <template #footer>
      <n-space justify="flex-end">
        <n-button @click="showAiPrompt = false">取消</n-button>
        <n-button type="primary" @click="callAiGenerate" :loading="aiLoading">生成</n-button>
      </n-space>
    </template>
  </n-modal>
  <!-- IC 检验弹窗 -->
  <n-modal v-if="feat" v-model:show="showIcCompute" preset="card" title="因子 IC 检验" style="width:460px;max-width:92vw" :mask-closable="false">
    <n-space vertical>
      <div style="font-size:12px;color:var(--c-text-dim)">对 <b>{{ feat.feature_name }}</b> 做逐日截面 IC 检验，结果落档 factor_ic_stats（不覆盖入选/剔除决策）。</div>
      <div style="display:flex;gap:8px">
        <n-input v-model:value="icForm.val_start" placeholder="开始 YYYY-MM-DD" size="small" />
        <n-input v-model:value="icForm.val_end" placeholder="结束 YYYY-MM-DD" size="small" />
      </div>
      <div style="display:flex;gap:6px;align-items:center">
        <span style="font-size:11px;color:var(--c-text-dim)">快速区间：</span>
        <n-button v-for="p in [{label:'近1年',y:1},{label:'近3年',y:3},{label:'近5年',y:5}]" :key="p.label" size="tiny" quaternary @click="applyIcRange(p.y)">{{ p.label }}</n-button>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <span style="font-size:11px;color:var(--c-text-dim)">前瞻期：</span>
        <n-checkbox-group v-model:value="icForm.horizons">
          <n-checkbox v-for="hh in [1,5,10,20]" :key="hh" :value="hh" :label="hh+'日'" />
        </n-checkbox-group>
      </div>
      <div v-if="icTask && icTask.status==='running'" style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--c-text-dim)">
        <n-spin size="small" /> 检验中（全周期约 40-60 秒，请勿关闭）…
      </div>
      <div v-if="icTask && icTask.status==='failed'" style="font-size:12px;color:#ef4444"><AppIcon name="x-circle" :size="13" />  {{ icTask.error }}</div>
      <div v-if="icTask && icTask.status==='completed'" style="font-size:12px;color:#10b981">
        <AppIcon name="check" :size="13" />  完成：{{ (icTask.results||[]).filter(r=>!r.error).length }}/{{ icTask.horizons?.length }} 个周期落档
        <span v-if="(icTask.failed||[]).length" style="color:#f59e0b">；{{ icTask.failed.map(f=>f.horizon+'d:'+f.error).join('；') }}</span>
      </div>
    </n-space>
    <template #footer>
      <n-space justify="flex-end">
        <n-button @click="showIcCompute=false">关闭</n-button>
        <n-button type="primary" :loading="icSubmitting" :disabled="icTask?.status==='running'" @click="startIcCompute">开始检验</n-button>
      </n-space>
    </template>
  </n-modal>
</div>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import ListPagination from './ListPagination.vue'
import StockSuggestInput from './StockSuggestInput.vue'
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { NButton, NTag, NSpin, NTabs, NTabPane, NInput, NSelect, NDataTable, NEmpty, NPagination, NModal, NSpace, NCheckbox, NCheckboxGroup, useMessage } from 'naive-ui'
import MonacoEditor from './MonacoEditor.vue'
import axios from 'axios'
import * as echarts from 'echarts'

const props = defineProps({ featureId: Number })
defineEmits(['back', 'edit'])

const API = window.location.origin
const message = useMessage()
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
  } else if (tab === 'ic') {
    if (!icRecords.value.length && !icLoading.value) loadIcAll()
    else nextTick(() => renderIcCharts())
  }
})

// ── IC 体检 ──
const icHorizon = ref(10)
const horizonOptions = [1, 5, 10, 20].map(h => ({ label: h + '日前瞻', value: h }))
const trafficMap = { green: '达标', yellow: '边缘', red: '未达标' }
const trafficTypeMap = { green: 'success', yellow: 'warning', red: 'error' }
const icStatusMap = { candidate: '候选', included: '已入选', excluded: '已剔除' }
const icStatusTypeMap = { candidate: 'default', included: 'success', excluded: 'error' }
const icRecords = ref([])
const icDetail = ref(null)
const icLoading = ref(false)
const showIcCompute = ref(false)
const icSubmitting = ref(false)
const icTask = ref(null)
const icForm = ref({ val_start: '', val_end: '', horizons: [1, 5, 10, 20] })
const icChart = ref(null)
const layerChart = ref(null)
const decayChart = ref(null)
let icChartInst = null, layerChartInst = null, decayChartInst = null

function openIcCompute() {
  // 默认区间：右端=特征最新数据日，左端=3 年前
  const end = feat.value?.latest_computed_date || new Date().toISOString().slice(0, 10)
  icForm.value.val_end = end
  icForm.value.val_start = new Date(new Date(end) - 3 * 365 * 86400000).toISOString().slice(0, 10)
  icForm.value.horizons = [1, 5, 10, 20]
  icTask.value = null
  showIcCompute.value = true
}

function applyIcRange(years) {
  icForm.value.val_start = new Date(new Date(icForm.value.val_end) - years * 365 * 86400000).toISOString().slice(0, 10)
}

async function loadIcAll() {
  icLoading.value = true
  try {
    const r = await axios.get(API + `/api/features/${props.featureId}/ic`)
    icRecords.value = r.data.records || []
    // 默认展示：有 10d 记录用 10d，否则第一个有记录的周期
    const hs = [...new Set(icRecords.value.map(x => x.horizon))]
    if (hs.length) {
      icHorizon.value = hs.includes(10) ? 10 : hs[0]
      await loadIcDetail()
    } else {
      icDetail.value = null
    }
  } catch (e) {
    console.error(e)
  } finally {
    icLoading.value = false
  }
}

async function loadIcDetail() {
  icLoading.value = true
  try {
    const r = await axios.get(API + `/api/features/${props.featureId}/ic`, {
      params: { detail: true, horizon: icHorizon.value },
    })
    icDetail.value = r.data
    nextTick(() => renderIcCharts())
  } catch (e) {
    icDetail.value = null
  } finally {
    icLoading.value = false
  }
}

function renderIcCharts() {
  const d = icDetail.value
  if (!d) return
  // IC 时序：柱（正负着色）+ 累计线
  if (icChart.value) {
    icChartInst?.dispose()
    icChartInst = echarts.init(icChart.value)
    const s = d.ic_series || {}
    icChartInst.setOption({
      tooltip: { trigger: 'axis' },
      grid: { left: 50, right: 14, top: 14, bottom: 22 },
      xAxis: { type: 'category', data: s.dates || [], axisLabel: { fontSize: 8, interval: Math.max(1, Math.floor((s.dates?.length || 1) / 5)) } },
      yAxis: [{ type: 'value', scale: true, axisLabel: { fontSize: 8 } }, { type: 'value', axisLabel: { fontSize: 8 } }],
      series: [
        { type: 'bar', name: 'RankIC', data: (s.rank_ic || []).map(v => ({ value: v, itemStyle: { color: v >= 0 ? '#10b981' : '#ef4444' } })), barWidth: '60%' },
        { type: 'line', name: '累计IC', data: s.cum_ic || [], yAxisIndex: 1, showSymbol: false, lineStyle: { width: 1.5, color: '#2080f0' } },
      ],
    })
  }
  // 分层净值 + 多空
  if (layerChart.value) {
    layerChartInst?.dispose()
    layerChartInst = echarts.init(layerChart.value)
    const q = d.q_returns || {}
    const colors = ['#ef4444', '#f59e0b', '#9ca3af', '#38bdf8', '#10b981']
    layerChartInst.setOption({
      tooltip: { trigger: 'axis' },
      legend: { top: 0, textStyle: { fontSize: 9 }, itemWidth: 12 },
      grid: { left: 50, right: 14, top: 24, bottom: 22 },
      xAxis: { type: 'category', data: q.dates || [], axisLabel: { fontSize: 8, interval: Math.max(1, Math.floor((q.dates?.length || 1) / 5)) } },
      yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 8 } },
      series: [
        ...(q.navs ? Object.keys(q.navs).sort((a, b) => a - b).map((k, i) => ({
          type: 'line', name: i === 0 ? `Q1最低` : i === Object.keys(q.navs).length - 1 ? `Q${i + 1}最高` : `Q${i + 1}`,
          data: q.navs[k], showSymbol: false, lineStyle: { width: 1.2, color: colors[i % 5] },
        })) : []),
        { type: 'line', name: '多空Q高-Q低', data: q.ls_nav || [], showSymbol: false, lineStyle: { width: 2, type: 'dashed', color: '#a855f7' } },
      ],
    })
  }
  // 衰减：各 horizon 最近一次 rank_ic
  if (decayChart.value) {
    decayChartInst?.dispose()
    decayChartInst = echarts.init(decayChart.value)
    const latest = {}
    for (const rec of icRecords.value) {
      if (latest[rec.horizon] === undefined) latest[rec.horizon] = rec.rank_ic
    }
    const hs = Object.keys(latest).sort((a, b) => a - b)
    decayChartInst.setOption({
      tooltip: { trigger: 'axis' },
      grid: { left: 50, right: 14, top: 14, bottom: 22 },
      xAxis: { type: 'category', data: hs.map(h => h + '日'), axisLabel: { fontSize: 9 } },
      yAxis: { type: 'value', axisLabel: { fontSize: 8 } },
      series: [{
        type: 'bar', barWidth: '40%',
        data: hs.map(h => ({ value: latest[h], itemStyle: { color: latest[h] >= 0 ? '#10b981' : '#ef4444' } })),
        label: { show: true, position: 'top', fontSize: 9, formatter: p => p.value?.toFixed(4) },
      }],
    })
  }
}

const icHistoryCols = [
  { title: '前瞻', key: 'horizon', width: 56, fixed: 'left', render: r => r.horizon + '日' },
  { title: '区间', key: 'val_start', width: 150, render: r => `${r.val_start} ~ ${r.val_end}` },
  { title: 'RankIC', key: 'rank_ic', width: 76, render: r => r.rank_ic?.toFixed(4) ?? '—' },
  { title: 'ICIR', key: 'icir', width: 70, render: r => r.icir?.toFixed(3) ?? '—' },
  { title: 'IC>0', key: 'win_rate', width: 60, render: r => r.win_rate != null ? (r.win_rate * 100).toFixed(0) + '%' : '—' },
  { title: 't', key: 't_stat', width: 56, render: r => r.t_stat?.toFixed(1) ?? '—' },
  { title: '方向', key: 'direction', width: 50 },
  { title: '样本', key: 'sample_days', width: 56, align: 'right' },
  { title: '检验时间', key: 'created_at', width: 140, render: r => (r.created_at || '').slice(0, 16) },
]

function icRowProps(row) {
  return {
    style: { cursor: 'pointer', background: row.horizon === icHorizon.value ? 'var(--c-hover-bg, rgba(32,128,240,.06))' : '' },
    onClick: () => { icHorizon.value = row.horizon; loadIcDetail() },
  }
}

async function startIcCompute() {
  icSubmitting.value = true
  try {
    const r = await axios.post(API + `/api/features/${props.featureId}/ic`, {
      val_start: icForm.value.val_start, val_end: icForm.value.val_end,
      horizons: icForm.value.horizons,
    }, { headers: authHeaders() })
    // 轮询任务
    const taskId = r.data.task_id
    if (icPollTimer) clearInterval(icPollTimer)
    icPollTimer = setInterval(async () => {
      try {
        const tr = await axios.get(API + `/api/features/${props.featureId}/ic/task/${taskId}`, { headers: authHeaders() })
        icTask.value = tr.data
        if (tr.data.status === 'completed' || tr.data.status === 'failed') {
          clearInterval(icPollTimer); icPollTimer = null
          if (tr.data.status === 'completed') {
            message.success('IC 检验完成')
            await loadIcAll()
          }
        }
      } catch (e) { clearInterval(icPollTimer); icPollTimer = null }
    }, 3000)
  } catch (e) {
    message.error(e.response?.data?.detail || '启动失败')
  } finally {
    icSubmitting.value = false
  }
}

async function doSetIcStatus(status) {
  try {
    await axios.put(API + `/api/features/${props.featureId}/ic-status`, { status }, { headers: authHeaders() })
    feat.value.ic_status = status
    message.success('决策已保存：' + (icStatusMap[status] || status))
  } catch (e) {
    message.error(e.response?.data?.detail || '保存失败')
  }
}


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
    message.error(e.response?.data?.detail || '保存失败')
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
    cols.push({ title:'代码', key:'stock_code', width:70, fixed:'left' })
    cols.push({ title:'名称', key:'stock_name', width:80, fixed:'left', ellipsis:{tooltip:true} })
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
let icPollTimer = null
onUnmounted(() => { if (icPollTimer) clearInterval(icPollTimer) })

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
              const status = v === 2 ? '缺失' : v === 1 ? '未上市/不适用' : '有值'
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

// ── KEPL 算子目录（/api/kepl/functions 单一事实源：与 parser 注册表强校验）──
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

// Monaco 补全项：snippet 模板（插入后光标停在第一个参数位逐个 Tab）
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
  } catch (e) {
    console.warn('算子目录加载失败（AI 上下文退化为简版）', e)
  }
}

// AI 上下文：语法规则 + 全部内置算子文档（从注册表单一事实源动态生成）
const KEPL_SPEC = computed(() => {
  const base = `KEPL 语法规则：
- 裸字段直接引用：close, open, high, low, volume, amount；基本面字段亦可用（pe_ttm/pb_mrq/ps_ttm/dv_ttm/turnover_rate/volume_ratio/circ_mv/total_mv）
- 算术运算：+ - * /（除零自动置空）
- 函数参数位支持负数字面量：ref(close, -1) 表示未来值（前值用正数）
- 因子惯例：用 (close+1e-12) 做除法归一化防除零，全市场可比
- 不支持 if/比较/逻辑运算——条件逻辑请用自定义 Python 函数（functions 表）`
  if (!keplFns.value) return base
  const ts = keplFns.value.time_series.map(o => `- ${o.sig}：${o.desc}；例：${o.eg}`).join('\n')
  const cs = keplFns.value.cross_sectional.map(o => `- ${o.sig}：${o.desc}；例：${o.eg}`).join('\n')
  return `${base}\n\n【时序算子（按股滚动计算）】\n${ts}\n\n【截面算子（同交易日全市场）】\n${cs}`
})

onMounted(loadKeplFunctions)

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

    const prompt = `${KEPL_SPEC.value}

【可用自定义函数列表】
${funcList || '（无）'}

【用户需求】
${aiRequirement.value}

请根据 KEPL 语法和可用算子，生成一个特征计算公式。只返回公式本身，不要解释。`

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