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
          <div style="display:flex;gap:4px;margin-bottom:8px">
    <n-button size="tiny" :type="currentEntity==='stock'?'primary':'default'" @click="switchEntity('stock')"><AppIcon name="filter" :size="13" />  个股</n-button>
    <n-button size="tiny" :type="currentEntity==='index'?'primary':'default'" @click="switchEntity('index')"><AppIcon name="bar-chart-2" :size="13" />  指数</n-button>
    <n-button size="tiny" :type="currentEntity==='etf'?'primary':'default'" @click="switchEntity('etf')"><AppIcon name="pie-chart" :size="13" />  ETF</n-button>
  </div>
          <div v-for="v in store.versions" :key="v.version"
            :style="{padding:'12px',marginBottom:'4px',borderRadius:'8px',border:'1px solid '+(store.selectedId===v.version?'var(--c-border)':'transparent'),cursor:'pointer',background:store.selectedId===v.version?'var(--c-card-bg-hover)':'transparent'}"
            @click="store.selectVersion(v.version)"
            @mouseenter="hoveredVersion = v.version" @mouseleave="hoveredVersion = null">
            <div style="display:flex;align-items:center;justify-content:space-between">
              <div style="display:flex;align-items:center;gap:8px">
                <span style="font-size:15px;font-weight:700;color:var(--c-text)">{{v.version}}</span>
                <n-tag :type="store.statusBadge(v.status)" size="tiny" :bordered="false">{{roleLabel(v)}}</n-tag>
              </div>
              <div v-if="!store.isMock && hoveredVersion === v.version" style="display:flex;gap:2px;flex-shrink:0">
                <!-- 主备制：备模型 hover 出【切换】；主/备 hover 出【归档】；其余状态保留删除 -->
                <n-button v-if="v.status==='ACTIVE' && v.role==='backup'" text size="tiny" type="primary" style="font-size:12px;padding:0 4px"
                  title="切换为主模型" @click.stop="handlePromote(v)"><AppIcon name="zap" :size="13" />  切换</n-button>
                <n-button v-if="v.status==='ACTIVE'" text size="tiny" type="warning" style="font-size:12px;padding:0 4px"
                  title="归档下线模型" @click.stop="handleArchive(v)"><AppIcon name="download" :size="13" />  归档</n-button>
                <n-button v-if="v.status!=='ACTIVE'" text size="tiny" type="error" style="font-size:12px;padding:0 4px" @click.stop="handleDeleteClick(v)" title="删除模型"><AppIcon name="trash" :size="13" /> </n-button>
              </div>
            </div>
            <div style="font-size:11px;color:var(--c-text-dim);margin-top:4px">{{v.model_name}}</div>
            <div style="display:flex;gap:12px;margin-top:6px;font-size:10px;color:var(--c-text-faint)">
              <span v-if="v.sharpe!=null"><AppIcon name="trending-up" :size="13" />  夏普 {{v.sharpe}}</span>
              <span v-if="v.win_rate!=null"><AppIcon name="check" :size="13" />  {{(v.win_rate*100).toFixed(0)}}%</span>
              <span><AppIcon name="calendar" :size="13" />  {{(v.created_at||'').slice(5)}}</span>
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
            <div v-if="store.selected?.status==='DRAFT'" style="display:flex;gap:8px;align-items:center">
              <n-button size="tiny" @click="startEditConfig"><AppIcon name="edit" :size="13" />  编辑配置</n-button>
            </div>
            <div style="display:flex;gap:16px;flex-wrap:wrap">
              <div v-for="m in basicMetrics" :key="m.label" style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:10px 14px;min-width:80px;text-align:center">
                <div style="font-size:10px;color:var(--c-text-faint)">{{m.label}}</div>
                <div :style="{fontSize:'16px',fontWeight:700,color:m.color||'var(--c-text)'}">{{m.value}}</div>
              </div>
            </div>
            <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:8px;padding:14px">
              <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:8px">配置详情</div>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:11px">
                <div v-for="row in cfgRows" :key="row[0]"><span style="color:var(--c-text-faint)">{{row[0]}}: </span><span style="color:var(--c-text)">{{row[1]}}</span></div>
              </div>
            </div>

          </div>
          <div v-else-if="store.detailTab==='train'">
            <div v-if="store.selected.status==='DRAFT' || store.selected.status==='REJECTED'" style="margin-bottom:14px">
              <n-button type="primary" @click="startTrain" :loading="trainingLoading">{{ store.selected.status==='REJECTED' ? '重新训练' : '开始训练' }}</n-button>
            </div>
            <ModelTraining :version="store.selected" />
          </div>
          <div v-else-if="store.detailTab==='eval'">
            <div v-if="store.selected.status==='PENDING'" style="margin-bottom:14px;display:flex;gap:8px;flex-wrap:wrap">
              <n-button type="success" @click="approveModel('primary')"><AppIcon name="zap" :size="13" />  发布为主模型</n-button>
              <n-button type="info" secondary @click="approveModel('backup')">发布为备模型</n-button>
              <n-button type="error" @click="rejectModel">拒绝</n-button>
            </div>
            <ModelEval :version="store.selected" />
          </div>
          <ModelLive v-else-if="store.detailTab==='live'" :version="store.selected" />
          <div v-else-if="store.detailTab==='indicators'" style="display:flex;flex-direction:column;gap:8px">
            <div style="display:flex;align-items:center;justify-content:space-between">
              <span style="font-size:12px;color:var(--c-text-dim)">模型使用的特征</span>
              <n-button size="tiny" @click="loadFeatureCheck" :loading="fcLoading"><AppIcon name="refresh" :size="13" />  预检</n-button>
            </div>
            <div v-if="featuresForModel.length" style="display:flex;flex-wrap:wrap;gap:4px">
              <n-tag v-for="f in featuresForModel" :key="f" size="small" type="info" :bordered="false">{{ f }}</n-tag>
            </div>
            <n-empty v-else description="未配置特征" style="padding:20px" />
            <div v-if="fcResult" style="margin-top:8px">
              <div :style="{fontSize:'11px',color:fcResult.ready?'#10b981':'#ef4444',marginBottom:'6px'}">
                {{ fcResult.ready ? '<AppIcon name="check" :size="13" />  全部特征数据就绪' : '<AppIcon name="alert" :size="13" /> ️ ' + fcResult.warnings.length + ' 个问题' }}
              </div>
              <div v-if="fcResult.warnings?.length" style="display:flex;flex-direction:column;gap:2px;margin-bottom:8px">
                <div v-for="w in fcResult.warnings" :key="w" style="font-size:10px;color:#f59e0b">{{ w }}</div>
              </div>
              <n-data-table v-if="fcResult.features?.length" :columns="fcCols" :data="fcResult.features" size="small" :bordered="false" scroll-x="550" />
            </div>
          </div>
        </template>
        <n-empty v-else description="选择一个模型版本" style="padding:60px 0" />
      </div>
    </div>

    <!-- Create Modal -->
    <n-modal v-model:show="showCreate" preset="card" :title="editMode ? '编辑配置' : '创建模型版本'" style="width:640px;max-width:92vw" :mask-closable="false">
        <n-space vertical>
          <n-input v-model:value="createName" placeholder="模型名称" />
          <n-divider style="margin:4px 0">数据配置</n-divider>
          <n-date-picker v-model:formatted-value="createForm.train_start" type="date" value-format="yyyy-MM-dd" placeholder="数据起点" />
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:11px">
            <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:55px">标签</span>
              <n-select v-model:value="createForm.label_mode" size="small" style="flex:1"
                :options="[{label:'超额收益（推荐）',value:'excess'},{label:'绝对收益（旧）',value:'absolute'}]" />
            </div>
            <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:55px">标准化</span>
              <n-select v-model:value="createForm.feature_norm" size="small" style="flex:1"
                :options="[{label:'截面排名（推荐）',value:'cs_rank'},{label:'原始值（旧）',value:'none'}]" />
            </div>
            <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:55px">标签变换</span>
              <n-select v-model:value="createForm.label_transform" size="small" style="flex:1"
                :options="[{label:'原始收益值',value:'none'},{label:'截面排名（v14）',value:'rank'}]" />
            </div>
            <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:55px">训练目标</span>
              <n-select v-model:value="createForm.train_objective" size="small" style="flex:1"
                :options="[{label:'回归（默认）',value:'regression'},{label:'排序 pairwise（v14）',value:'pairwise'}]" />
            </div>
            <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:55px">中性化</span>
              <n-switch v-model:value="createForm.feature_neut" size="small">
                <template #checked>市值+行业</template>
                <template #unchecked>关</template>
              </n-switch>
            </div>
          </div>
          <n-divider style="margin:4px 0">特征配置</n-divider>
          <n-space>
            <n-tag v-for="f in featureOptions" :key="f.key"
              :type="createForm.feature_names.includes(f.key)?'info':'default'"
              :style="{cursor:'pointer', opacity: f.ic_status==='excluded'?0.55:1,
                       border: f.ic_status==='excluded' ? '1px dashed #ef4444' : (f.traffic==='green' ? '1px solid #10b981' : (f.traffic==='yellow' ? '1px solid #f59e0b' : 'none'))}"
              @click="toggleFeature(f.key)" :bordered="false" size="small">{{ f.traffic==='green'?'●':f.traffic==='yellow'?'●':f.traffic==='red'?'●':'' }}{{f.label}}{{ f.direction==='-'?' ↩':'' }}</n-tag>
          </n-space>
          <div style="font-size:10px;color:var(--c-text-faint)">// = IC 体检灯（10日前瞻）； = 反向因子；红虚框 = 已剔除（仍可强制加入）；新建时自动预选「已入选」因子</div>
          <n-divider style="margin:4px 0">训练参数</n-divider>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:11px">
            <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:55px">Optuna</span><n-input-number v-model:value="createForm.optuna_trials" :min="10" :max="500" style="flex:1" size="small" /></div>
            <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:55px">初始资金</span><n-input-number v-model:value="createForm.initial_cash" :min="100000" :step="100000" style="flex:1" size="small" /></div>
            <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:55px">最大持仓</span><n-input-number v-model:value="createForm.max_positions" :min="3" :max="30" style="flex:1" size="small" /></div>
            <div style="display:flex;align-items:center;gap:6px" title="purged CV：训练窗内按时间连续切 N 折（折前隔离 25 交易日 ≥ 标签前瞻），折外均值选超参——val 退出选择成为诚实 OOS。每 trial 拟合 ×折数，trials 建议 ≤20">
              <span style="color:var(--c-text-dim);min-width:55px">CV选择</span>
              <n-switch v-model:value="createForm.selection_cv" size="small">
                <template #checked>purged</template><template #unchecked>单val</template>
              </n-switch>
              <n-input-number v-model:value="createForm.cv_folds" :min="2" :max="6" :disabled="!createForm.selection_cv" size="small" style="width:64px" />
            </div>
            <div style="display:flex;align-items:center;gap:6px" title="多种子集成：同一最优超参 × N 个随机种子取预测均值，压单模型种子噪声（1=关闭）。产物仍是单个 pkl，信号链路无感">
              <span style="color:var(--c-text-dim);min-width:55px">种子集成</span>
              <n-input-number v-model:value="createForm.ensemble_seeds" :min="1" :max="5" size="small" style="width:64px" />
            </div>
          </div>
          <n-divider style="margin:4px 0">ML 买入阈值</n-divider>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:11px">
            <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:55px">模式</span>
              <n-select v-model:value="createForm.signal_threshold_mode" size="small" style="flex:1"
                :options="[{label:'分位数 top N%（自适应）',value:'quantile'},{label:'绝对收益率阈值',value:'absolute'}]" />
            </div>
            <div v-if="createForm.signal_threshold_mode==='quantile'" style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:55px">买入top</span><n-input-number v-model:value="createForm.buy_top_pct" :min="0.001" :max="0.50" :step="0.01" :format="v => (v*100).toFixed(0)+'%'" :parse="v => parseFloat(v)/100" style="flex:1" size="small" /></div>
            <div v-else style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:55px">绝对阈值</span><n-input-number v-model:value="createForm.ml_confidence_threshold" :min="0" :max="0.2" :step="0.001" :format="v => (v*100).toFixed(2)+'%'" :parse="v => parseFloat(v)/100" style="flex:1" size="small" /></div>
          </div>
          <n-divider style="margin:4px 0">六层交易策略</n-divider>
          <div style="max-height:350px;overflow-y:auto;padding-right:4px">
            <n-collapse>
              <n-collapse-item title="① 执行模型" name="exec">
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:11px">
                  <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:60px">量比上限</span><n-input-number v-model:value="createForm.trading_rules.execution.volume_limit" :min="0.01" :max="0.30" :step="0.01" style="flex:1" size="small" /></div>
                </div>
              </n-collapse-item>
              <n-collapse-item title="② 信号过滤" name="sig">
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:11px">
                  <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:60px">最低收益</span><n-input-number v-model:value="createForm.trading_rules.signal_filter.min_pred_return" :min="-0.2" :max="0.2" :step="0.01" style="flex:1" size="small" /></div>
                  <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:60px">最高收益</span><n-input-number v-model:value="createForm.trading_rules.signal_filter.max_pred_return" :min="0.1" :max="0.5" :step="0.05" style="flex:1" size="small" /></div>
                </div>
              </n-collapse-item>
              <n-collapse-item title="③ 头寸管理" name="pos">
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:11px">
                  <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:60px">单票上限</span><n-input-number v-model:value="createForm.trading_rules.position_sizing.max_single_position" :min="0.05" :max="0.50" :step="0.05" style="flex:1" size="small" /></div>
                  <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:60px">日换手率</span><n-input-number v-model:value="createForm.trading_rules.position_sizing.max_turnover_per_day" :min="0.10" :max="1.00" :step="0.05" style="flex:1" size="small" /></div>
                </div>
              </n-collapse-item>
              <n-collapse-item title="④ 止盈止损" name="risk">
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:11px">
                  <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:60px">止损</span><n-input-number v-model:value="createForm.trading_rules.risk_management.stop_loss" :min="0.02" :max="0.15" :step="0.01" style="flex:1" size="small" /></div>
                  <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:60px">止盈</span><n-input-number v-model:value="createForm.trading_rules.risk_management.take_profit" :min="0.05" :max="0.50" :step="0.01" style="flex:1" size="small" /></div>
                  <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:60px">移动止盈</span><n-input-number v-model:value="createForm.trading_rules.risk_management.trailing_retracement" :min="0.02" :max="0.10" :step="0.01" style="flex:1" size="small" /></div>
                  <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:60px">持仓天数</span><n-input-number v-model:value="createForm.trading_rules.risk_management.max_holding_days" :min="5" :max="60" style="flex:1" size="small" /></div>
                </div>
              </n-collapse-item>
              <n-collapse-item title="⑤ 市场择时" name="mkt">
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:11px">
                  <div style="display:flex;align-items:center;gap:6px">
                    <n-switch v-model:value="createForm.trading_rules.market_filter.require_market_above_ma" size="small" />
                    <span style="color:var(--c-text-dim)">大盘MA以上开仓</span>
                  </div>
                  <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:60px">MA周期</span><n-input-number v-model:value="createForm.trading_rules.market_filter.market_ma_period" :min="10" :max="60" style="flex:1" size="small" /></div>
                </div>
              </n-collapse-item>
              <n-collapse-item title="⑥ 成本模型" name="cost">
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:11px">
                  <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:60px">佣金</span><n-input-number v-model:value="createForm.trading_rules.cost_model.commission_rate" :min="0.0005" :max="0.003" :step="0.0001" style="flex:1" size="small" :format="v => (v*100).toFixed(2)+'%'" :parse="v => parseFloat(v)/100" /></div>
                  <div style="display:flex;align-items:center;gap:6px"><span style="color:var(--c-text-dim);min-width:60px">滑点</span><n-input-number v-model:value="createForm.trading_rules.cost_model.slippage_rate" :min="0.0005" :max="0.002" :step="0.0001" style="flex:1" size="small" :format="v => (v*100).toFixed(2)+'%'" :parse="v => parseFloat(v)/100" /></div>
                </div>
              </n-collapse-item>
            </n-collapse>
          </div>
        </n-space>
        <template #footer>
          <n-space justify="flex-end">
            <n-button @click="showCreate=false">取消</n-button>
            <n-button type="primary" @click="editMode ? doSaveConfig() : doCreate()" :loading="creating">{{ editMode ? '保存' : '创建' }}</n-button>
          </n-space>
        </template>
    </n-modal>
    <!-- Delete Confirm Modal -->
    <n-modal v-model:show="showDeleteModal" preset="card" :title="deleteInfo?.can_physical_delete ? '永久删除模型' : '删除模型'" style="width:420px;max-width:92vw" :mask-closable="false">
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
              <div v-if="deleteInfo.related_data.signals > 0"><AppIcon name="bar-chart-2" :size="13" />  信号数据 {{ deleteInfo.related_data.signals }} 条</div>
              <div v-if="deleteInfo.related_data.training_trials > 0"><AppIcon name="e" :size="13" />  训练试验 {{ deleteInfo.related_data.training_trials }} 次</div>
              <div v-if="deleteInfo.related_data.health_records > 0"> 健康记录 {{ deleteInfo.related_data.health_records }} 条</div>
              <div v-if="deleteInfo.related_data.comparisons > 0"><AppIcon name="shuffle" :size="13" />  版本对比 {{ deleteInfo.related_data.comparisons }} 条</div>
              <div v-if="deleteInfo.related_data.activated"><AppIcon name="send" :size="13" />  曾上线运行</div>
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
import AppIcon from './AppIcon.vue'
import { ref, reactive, computed, onMounted, h, watch } from 'vue'
import { NButton, NTag, NSpin, NEmpty, NModal, NSpace, NInput, NInputNumber, NDatePicker, NCheckbox, NDivider, NCollapse, NCollapseItem, NSwitch, NDataTable, NSelect } from 'naive-ui'
import { useDialog, useMessage } from 'naive-ui'
import axios from 'axios'
import { useModelStore } from '../stores/model'
import { useNavStore } from '../stores/nav'
import ModelTraining from './ModelTraining.vue'
import ModelEval from './ModelEval.vue'
import ModelLive from './ModelLive.vue'
const store = useModelStore()
const nav = useNavStore()
const dialog = useDialog()
const message = useMessage()
const trainingLoading = ref(false)
const featuresForModel = computed(() => {
  const c = store.selected?.config || {}
  return c.feature_names || c.features || []
})

const fcLoading = ref(false)
const fcResult = ref(null)
const fcCols = [
  { title:'特征', key:'name', width:110, fixed:'left', ellipsis:{tooltip:true} },
  { title:'股票', key:'stocks', width:45, align:'right' },
  { title:'总量', key:'total_rows', width:65, align:'right', render(r) { return (r.total_rows||0).toLocaleString() } },
  { title:'训练', key:'train', width:65, align:'right', render(r) { return (r.train||0).toLocaleString() } },
  { title:'', key:'train_pct', width:45, render(r) { return (r.train_pct||0)+'%' } },
  { title:'验证', key:'val', width:65, align:'right', render(r) { return (r.val||0).toLocaleString() } },
  { title:'', key:'val_pct', width:45, render(r) { return (r.val_pct||0)+'%' } },
  { title:'测试', key:'test', width:65, align:'right', render(r) { return (r.test||0).toLocaleString() } },
  { title:'', key:'test_pct', width:45, render(r) { return (r.test_pct||0)+'%' } },
]

async function loadFeatureCheck(force) {
  if (!store.selectedId) return
  const vid = store.selectedId
  fcLoading.value = true
  try {
    const params = force ? { force: 'true' } : {}
    const r = await axios.get(window.location.origin + `/api/v1/models/${vid}/feature-check`, { params })
    if (store.selectedId !== vid) return // 响应期间已切换模型 → 丢弃，防止旧响应覆盖新模型的预检
    fcResult.value = r.data
  } catch(e) { if (store.selectedId === vid) fcResult.value = null }
  finally { if (store.selectedId === vid) fcLoading.value = false }
}

watch(() => store.selectedId, (id) => { if (id) { nav.modelVersion = store.selected?.version || ''; nav.syncHash() }; fcResult.value = null; if (store.selected && store.detailTab === 'indicators') loadFeatureCheck(false) })
watch(store.detailTab, (t) => { nav.modelTab = t; nav.syncHash(); if (t === 'indicators' && store.selected && fcResult.value === null) loadFeatureCheck(false) })
const loading = ref(true)
const showCreate = ref(false)
const editMode = ref(false)
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
  entity: 'stock',
  train_start: '2021-01-01', train_end: '2025-12-31',
  test_start: '2026-01-01', test_end: null,
  feature_names: [],
  optuna_trials: 50, initial_cash: 1000000, max_positions: 5,
  // v3.5 方法论：excess=超额收益标签（相对沪深300）；cs_rank=特征逐日截面排名
  label_mode: 'excess', feature_norm: 'cs_rank', label_transform: 'none', train_objective: 'regression',
  feature_neut: false,
  // 训练协议（v3.9.2 反过拟合默认开）：purged CV 折外选超参（val 退出选择）+ 多种子集成
  selection_cv: true, cv_folds: 4, ensemble_seeds: 3,
  // ML 买入阈值：quantile=当日预测分布 top N%（默认，自适应模型能力）；absolute=绝对预测收益率
  signal_threshold_mode: 'quantile', buy_top_pct: 0.05, ml_confidence_threshold: 0.02,
  // 六层策略配置默认值（策略扫描时搜索最优）
  trading_rules: {
    execution: { price_type: 'next_day_open', delay_days: 1, volume_limit: 0.10 },
    signal_filter: { min_pred_return: 0.0, max_pred_return: 0.30, allow_limit_up: false },
    position_sizing: { sizing_method: 'equal_weight', max_single_position: 0.20, max_turnover_per_day: 0.30 },
    risk_management: { stop_loss_type: 'percentage', stop_loss: 0.05, take_profit: 0.10,
                       trailing_retracement: 0.05, max_holding_days: 20 },
    market_filter: { require_market_above_ma: true, market_ma_period: 20, max_volatility_threshold: 0.30 },
    cost_model: { commission_rate: 0.0015, slippage_rate: 0.001, stamp_duty: 0.0005 },
  },
  stop_loss_pct: 8, signal_timeout_days: 20,  // 保留兼容旧字段
})
const currentEntity = ref(nav.modelEntity || 'stock')

function switchEntity(e) {
  currentEntity.value = e
  nav.modelEntity = e
  nav.modelVersion = ''
  nav.modelTab = ''
  nav.syncHash()
  loadFeatureOptions()
  store.loadVersions(e)
}

const featureOptions = ref([])
async function loadFeatureOptions() {
  try {
    const [fr, br] = await Promise.all([
      axios.get(window.location.origin + '/api/features', {
        params: { entity: currentEntity.value, status: 'enabled', page_size: 200 }
      }),
      axios.get(window.location.origin + '/api/features/ic/board', { params: { horizon: 10 } }).catch(() => null),
    ])
    const boardMap = {}
    for (const b of (br?.data?.board || [])) boardMap[b.feature_name] = b
    featureOptions.value = (fr.data.items || []).map(f => {
      const b = boardMap[f.feature_name] || {}
      return {
        key: f.feature_name, label: f.feature_name, display: f.display_name, completeness: f.data_completeness,
        traffic: b.traffic, direction: b.direction, ic_status: f.ic_status || 'candidate',
      }
    }).sort((a, b) => {
      const order = { included: 0, candidate: 1, excluded: 2 }
      return (order[a.ic_status] ?? 1) - (order[b.ic_status] ?? 1)
    })
    // 新建模式：预勾选 IC 入选因子（编辑模式以模型已配置为准）
    if (!editMode.value) {
      createForm.feature_names = featureOptions.value.filter(f => f.ic_status === 'included').map(f => f.key)
    }
  } catch(e) { console.error(e) }
}
loadFeatureOptions()
function toggleFeature(key) {
  const idx = createForm.feature_names.indexOf(key)
  if (idx >= 0) createForm.feature_names.splice(idx, 1)
  else createForm.feature_names.push(key)
}
function startEditConfig() {
  editMode.value = true
  const cfg = store.selected?.config || {}
  createName.value = store.selected?.model_name || ''
  // 确保模型已选的特征出现在选项列表中（即使它们不是 enabled 状态）
  const configured = cfg.feature_names || cfg.features || []
  for (const fn of configured) {
    if (!featureOptions.value.find(f => f.key === fn)) {
      featureOptions.value.push({ key: fn, label: fn, display: fn, completeness: 0 })
    }
  }
  createForm.train_start = cfg.train_start || '2021-01-01'
  createForm.train_end = cfg.train_end || '2025-12-31'
  createForm.test_start = cfg.test_start || '2026-01-01'
  createForm.test_end = cfg.test_end || null
  createForm.feature_names = cfg.feature_names || cfg.features || []
  createForm.optuna_trials = cfg.optuna_trials || 50
  createForm.initial_cash = cfg.initial_cash || 1000000
  createForm.max_positions = cfg.max_positions || 5
  createForm.trading_rules = cfg.trading_rules || createForm.trading_rules
  createForm.stamp_tax = cfg.trading_rules?.cost_model?.stamp_duty ?? 0.0005
  createForm.commission = cfg.trading_rules?.cost_model?.commission_rate ?? 0.0015
  createForm.slippage = cfg.trading_rules?.cost_model?.slippage_rate ?? 0.001
  createForm.stop_loss_pct = Math.round((cfg.trading_rules?.risk_management?.stop_loss ?? 0.05) * 100)
  createForm.signal_timeout_days = cfg.trading_rules?.risk_management?.max_holding_days ?? 20
  const sigCfg = cfg.signal || {}
  createForm.signal_threshold_mode = sigCfg.threshold_mode || 'quantile'
  createForm.buy_top_pct = sigCfg.buy_top_pct ?? 0.05
  createForm.ml_confidence_threshold = sigCfg.ml_confidence_threshold ?? 0.02
  // v3.5：旧模型 config 无此二键 → 回退旧口径，保证编辑保存不改变语义
  createForm.label_mode = cfg.label_mode || 'absolute'
  createForm.label_transform = cfg.label_transform || 'none'
  createForm.train_objective = cfg.train_objective || 'regression'
  createForm.feature_norm = cfg.feature_norm || 'none'
  createForm.feature_neut = cfg.feature_neut || false
  // v3.9.2 训练协议：旧模型无键时默认开（训练协议只在训练时消费，保存不影响已训产物）
  createForm.selection_cv = cfg.selection_cv?.enabled ?? true
  createForm.cv_folds = cfg.selection_cv?.folds ?? 4
  createForm.ensemble_seeds = cfg.ensemble?.seeds ?? 3
  showCreate.value = true
}

async function doSaveConfig() {
  creating.value = true
  try {
    await axios.put(window.location.origin + `/api/v1/models/${store.selectedId}/config`, {
      model_name: createName.value.trim(),
      train_start: createForm.train_start,
      train_end: createForm.train_end,
      test_start: createForm.test_start,
      test_end: createForm.test_end || null,
      feature_names: createForm.feature_names,
      features: createForm.feature_names,
      optuna_trials: createForm.optuna_trials,
      label_mode: createForm.label_mode,
      label_transform: createForm.label_transform,
      train_objective: createForm.train_objective,
      feature_norm: createForm.feature_norm,
      feature_neut: createForm.feature_neut,
      selection_cv: { enabled: createForm.selection_cv, folds: createForm.cv_folds, embargo_days: 25 },
      ensemble: { seeds: createForm.ensemble_seeds },
      risk: { stop_loss_pct: createForm.stop_loss_pct, signal_timeout_days: createForm.signal_timeout_days },
      signal: {
        threshold_mode: createForm.signal_threshold_mode,
        buy_top_pct: createForm.buy_top_pct,
        ml_confidence_threshold: createForm.ml_confidence_threshold,
      },
    })
    showCreate.value = false
    editMode.value = false
    await store.loadVersions(currentEntity.value)
  } catch(e) {
    message.error(e.response?.data?.detail || '保存失败')
  } finally { creating.value = false }
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
      features: createForm.feature_names,
      feature_names: createForm.feature_names,
      trading_rules: createForm.trading_rules,
      optuna_trials: createForm.optuna_trials,
      initial_cash: createForm.initial_cash,
      max_positions: createForm.max_positions,
      stamp_tax: createForm.stamp_tax,
      commission: createForm.commission,
      slippage: createForm.slippage,
      stop_loss_pct: createForm.stop_loss_pct,
      signal_timeout_days: createForm.signal_timeout_days,
      signal_threshold_mode: createForm.signal_threshold_mode,
      buy_top_pct: createForm.buy_top_pct,
      ml_confidence_threshold: createForm.ml_confidence_threshold,
      label_mode: createForm.label_mode,
      label_transform: createForm.label_transform,
      train_objective: createForm.train_objective,
      feature_norm: createForm.feature_norm,
      feature_neut: createForm.feature_neut,
      selection_cv_enabled: createForm.selection_cv,
      cv_folds: createForm.cv_folds,
      ensemble_seeds: createForm.ensemble_seeds,
    })
    showCreate.value = false
    createName.value = ''
    await store.loadVersions(currentEntity.value)
    if (store.versions.length) store.selectVersion(store.versions[0].version)
  } catch(e) {
    message.error(e.response?.data?.detail || '创建失败')
  } finally { creating.value = false }
}

const tabs = [
  { key:'basic', label:'基本信息' },
  { key:'indicators', label:'特征' },
  { key:'train', label:'训练' },
  { key:'eval', label:'评估' },
  { key:'live', label:'实盘' },
]

const cfg = computed(() => store.selected?.config || {})
// 配置详情行：全部取自 config 真实出处（risk/signal/regime/成本键），
// 训练回测与纸面链路实际读的就是这些键；旧 trading_rules.risk_management 已无人写入，弃用
const cfgRows = computed(() => {
  const c = cfg.value
  const risk = c.risk || {}
  const sig = c.signal || {}
  const reg = c.regime || {}
  const trail = risk.trailing_retracement || 0
  const stop = risk.stop_loss_pct ?? 8
  const feats = (c.feature_names || c.features) || []
  const trigger = c.ml_enabled
    ? (sig.threshold_mode === 'quantile'
        ? `预测分位 top${((sig.buy_top_pct ?? 0.05) * 100).toFixed(0)}%`
        : `预测 ≥ ${((sig.ml_confidence_threshold ?? 0.5) * 100).toFixed(2)}%`)
    : '规则评分（BOLL/RSI/MACD）'
  return [
    ['特征', feats.length ? feats.slice(0, 8).join(', ') + (feats.length > 8 ? '...' : '') : '—'],
    ['数据范围', (c.train_start || '—') + ' ~ 前天（自动60/20/20切分）'],
    ['Optuna轮数', c.optuna_trials || 50],
    ['初始资金', (c.initial_cash || 1000000).toLocaleString() + '元'],
    ['最大持仓', (c.max_positions || 5) + '只'],
    ['成本', `佣${((c.commission ?? 0.00025) * 100).toFixed(3)}% 滑${((c.slippage ?? 0.001) * 100).toFixed(1)}% 印${((c.stamp_tax ?? 0.001) * 100).toFixed(1)}%`],
    ['止损', stop + '%'],
    ['止盈', trail > 0 ? '关闭（移动止盈接管）' : (stop * 2) + '%'],
    ['移动止盈', trail > 0 ? (trail * 100).toFixed(0) + '%' : '关闭'],
    ['持有到期', (risk.signal_timeout_days ?? 20) + '个交易日'],
    ['买入触发', trigger],
    ['大盘择时', reg.enabled ? `沪深300<MA${reg.ma_window ?? 20}空仓（${reg.max_skip_days ?? 2}天后放行）` : '不限'],
    ['组合闸门', (c.portfolio_gate?.dd ?? 0) > 0 ? `组合回撤${(c.portfolio_gate.dd * 100).toFixed(0)}%停止开仓` : '关闭'],
    ['执行', '信号日收盘撮合 · 卖出T+1'],
  ]
})
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
  trainingLoading.value = true
  try {
    await loadFeatureCheck(false)
    if (fcResult.value && !fcResult.value.ready) {
      trainingLoading.value = false
      dialog.warning({
        title: '特征数据不完整（最近预检结果）',
        // 文本节点渲染，避免 warnings 内容注入 HTML（存储型 XSS）
        content: () => h('div', { style: 'font-size:12px;line-height:1.6' }, fcResult.value.warnings.map(w => h('div', null, String(w)))),
        positiveText: '仍然训练',
        negativeText: '取消',
        onPositiveClick: () => { trainingLoading.value = true; dostartTrain() },
      })
    } else {
      dostartTrain()
    }
  } catch(e) {
    trainingLoading.value = false
    dialog.error({ title: '预检失败', content: e.response?.data?.detail || e.message })
  }
}

async function dostartTrain() {
  try {
    if (store.selected.status === 'REJECTED') {
      await axios.post(window.location.origin + `/api/v1/models/${store.selectedId}/retrain`)
    }
    await axios.post(window.location.origin + `/api/v1/models/${store.selectedId}/train`)
    store.selected.status = 'TRAINING'
  } catch(e) { message.error(e.response?.data?.detail || '启动训练失败') }
}
async function approveModel(role) {
  const isPrimary = role === 'primary'
  dialog.warning({
    title: isPrimary ? '发布为主模型？' : '发布为备模型？',
    content: isPrimary
      ? `版本 ${store.selected.version} 将成为主模型（原主模型自动转为备模型），立即按其规则生产信号与模拟交易。若最近一次 walk-forward 判定为 FAIL，本次审批将被拒绝。`
      : `版本 ${store.selected.version} 将作为备模型上线（主模型不变），同步生产信号与模拟交易用于对比。`,
    positiveText: isPrimary ? '发布为主模型' : '发布为备模型', negativeText: '取消',
    onPositiveClick: () => doApprove(role),
  })
}
async function doApprove(role) {
  try {
    await axios.post(window.location.origin + `/api/v1/models/${store.selected.version}/approve`, { role })
    await store.loadVersions(currentEntity.value)
  } catch(e) {
    message.error(e.response?.data?.detail || '审批失败')
  }
}
async function rejectModel() {
  dialog.warning({ title: '确认拒绝该版本？', content: '版本将转入 REJECTED，之后可重新训练。', positiveText: '拒绝', negativeText: '取消', onPositiveClick: () => doReject() })
}
async function doReject() {
  try {
    await axios.post(window.location.origin + `/api/v1/models/${store.selected.version}/reject`)
    await store.loadVersions(currentEntity.value)
  } catch(e) {
    message.error(e.response?.data?.detail || '操作失败')
  }
}

async function handleDeleteClick(v) {
  deleteTarget.value = v
  try {
    deleteInfo.value = await store.checkDelete(v.version)
    showDeleteModal.value = true
  } catch(e) {
    message.error(e.response?.data?.detail || '检查失败')
  }
}

// ── 主备制：角色徽标 + 切换/归档 ──
const roleLabel = (v) => {
  if (v.status === 'ACTIVE') return v.role === 'primary' ? '已上线 · 主' : '已上线 · 备'
  return store.statusLabel(v.status)
}
async function handlePromote(v) {
  dialog.error({
    title: '切换主模型',
    content: `要将该模型切换为主模型？`,
    positiveText: '确认切换',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await axios.post(window.location.origin + `/api/v1/models/${v.version}/promote`)
        message.success(`${v.version} 已切换为主模型`)
        await store.loadVersions(currentEntity.value)
        if (store.selectedId) store.selectVersion(store.selectedId)
      } catch(e) { message.error(e.response?.data?.detail || '切换失败') }
    }
  })
}
async function handleArchive(v) {
  dialog.warning({
    title: '归档模型',
    content: '归档模型后，不再维护信号信息！' + (v.role === 'primary' ? '（主模型归档后，创建最早的备模型将自动接任主模型）' : ''),
    positiveText: '确认归档',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const r = await axios.post(window.location.origin + `/api/v1/models/${v.version}/archive`)
        message.success(r.data?.promoted_primary ? `${v.version} 已归档，${r.data.promoted_primary} 接任主模型` : `${v.version} 已归档`)
        await store.loadVersions(currentEntity.value)
        if (store.selectedId) store.selectVersion(store.selectedId)
      } catch(e) { message.error(e.response?.data?.detail || '归档失败') }
    }
  })
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
    message.error(e.response?.data?.detail || '删除失败')
  } finally { deleting.value = false }
}

onMounted(async () => {
  await store.loadVersions(currentEntity.value)
  if (store.versions.length) store.selectVersion(store.versions[0].version)
  loading.value = false
})

// URL 参数恢复
onMounted(async () => {
  await store.loadVersions(currentEntity.value)
  const savedVer = nav.modelVersion
  const savedTab = nav.modelTab
  if (store.versions.length) {
    if (savedVer && store.versions.find(v => v.version === savedVer)) {
      store.selectVersion(savedVer)
      if (savedTab) store.switchTab(savedTab)
    } else {
      store.selectVersion(store.versions[0].version)
    }
  }
  loading.value = false
})
</script>