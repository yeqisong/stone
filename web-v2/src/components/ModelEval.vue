<template>
<div>
  <div v-if="!version.evaluation_report || version.status==='REJECTED'" style="color:var(--c-text-dim);padding:20px 0;text-align:center">暂未训练，无评估数据</div>
  <template v-else>
    <!-- Core Metrics -->
    <div style="display:flex;gap:16px;margin-bottom:20px;flex-wrap:wrap">
      <div v-for="m in metrics" :key="m.label" style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:14px 18px;min-width:90px;text-align:center">
        <div style="font-size:10px;color:var(--c-text-faint);margin-bottom:4px">{{m.label}}</div>
        <div :style="{fontSize:'20px',fontWeight:700,color:m.color||'var(--c-text)'}">{{m.value}}</div>
      </div>
    </div>

    <!-- 模型超参数 -->
    <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:16px;margin-bottom:16px">
      <div style="font-size:12px;font-weight:600;color:var(--c-text-dim);margin-bottom:10px">最优超参数</div>
      <div style="display:flex;gap:12px;flex-wrap:wrap">
        <div v-for="p in modelParams" :key="p.key" style="background:var(--c-card-bg-hover);border-radius:6px;padding:8px 14px;text-align:center;min-width:70px">
          <div style="font-size:9px;color:var(--c-text-faint)">{{p.label}}</div>
          <div style="font-size:15px;font-weight:700;color:var(--c-text)">{{p.value}}</div>
        </div>
      </div>
    </div>

    <!-- 质量诊断 -->
    <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:16px;margin-bottom:16px">
      <div style="font-size:12px;font-weight:600;color:var(--c-text-dim);margin-bottom:10px">质量诊断</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;font-size:11px">
        <div style="padding:8px;text-align:center" title="验证集与测试集夏普差值：正=过拟合, 负=欠拟合">
          <div style="color:var(--c-text-faint)">拟合度</div>
          <div style="font-weight:700;margin-top:2px" :style="{color:overfitColor}">{{overfitLabel}}</div>
          <div style="color:var(--c-text-faint);font-size:9px">Val-Test={{rep.overfit_gap?.toFixed(2)}}</div>
        </div>
        <div style="padding:8px;text-align:center" title="综合夏普+盈亏比评级：A优秀 B良好 C一般 D较差">
          <div style="color:var(--c-text-faint)">收益能力</div>
          <div style="font-weight:700;margin-top:2px" :style="{color:profitGrade==='A'?'#ef4444':profitGrade==='B'?'#f59e0b':'#6b7280'}">{{profitLabel}}</div>
          <div style="color:var(--c-text-faint);font-size:9px">{{gradeReason}}</div>
        </div>
        <div style="padding:8px;text-align:center" title="总盈利/总亏损：>1.2说明赚时比亏时多">
          <div style="color:var(--c-text-faint)">盈亏比</div>
          <div style="font-weight:700;margin-top:2px" :style="{color:(rep.profit_factor||0)>=1.2?'#ef4444':(rep.profit_factor||0)>0.8?'#f59e0b':'#10b981'}">{{(rep.profit_factor||0).toFixed(2)}}</div>
          <div style="color:var(--c-text-faint);font-size:9px">均赢{{rep.avg_win?.toFixed(0)}} vs 均亏{{rep.avg_loss?.toFixed(0)}}</div>
        </div>
        <div style="padding:8px;text-align:center" title="跑赢基准：模型收益 vs 沪深300同期收益，绿色=跑赢, 红色=跑输">
          <div style="color:var(--c-text-faint)">vs 沪深300</div>
          <div style="font-weight:700;margin-top:2px" :style="{color:modelReturn!=null?(modelReturn>rep.benchmark_return?'#ef4444':'#10b981'):'var(--c-text-dim)'}">{{modelReturn!=null?(modelReturn>rep.benchmark_return?'跑赢':'跑输'):'—'}}</div>
          <div style="color:var(--c-text-faint);font-size:9px">模型{{modelReturn!=null?(modelReturn*100).toFixed(1)+'%':'—'}} vs 基准{{rep.benchmark_return!=null?(rep.benchmark_return*100).toFixed(1)+'%':'—'}}</div>
        </div>
      </div>
    </div>

    <!-- Walk-Forward 回测明细 -->
    <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:16px;margin-bottom:16px">
      <div style="font-size:12px;font-weight:600;color:var(--c-text-dim);margin-bottom:10px">回测明细（各周期）</div>
      <div style="overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead>
            <tr style="color:var(--c-text-dim);text-align:left">
              <th style="padding:6px 10px;border-bottom:1px solid var(--c-border)">周期</th>
              <th style="padding:6px 10px;border-bottom:1px solid var(--c-border)">夏普</th>
              <th style="padding:6px 10px;border-bottom:1px solid var(--c-border)">胜率</th>
              <th style="padding:6px 10px;border-bottom:1px solid var(--c-border)">模型文件</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(t, i) in trials" :key="i" style="color:var(--c-text)">
              <td style="padding:5px 10px;border-bottom:1px solid var(--c-border-light);font-weight:600">{{t.train}}</td>
              <td style="padding:5px 10px;border-bottom:1px solid var(--c-border-light);color:#ef4444">{{t.sharpe}}</td>
              <td style="padding:5px 10px;border-bottom:1px solid var(--c-border-light)">{{t.signals}}</td>
              <td style="padding:5px 10px;border-bottom:1px solid var(--c-border-light);font-size:10px">{{t.test}}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Optuna 试验记录 -->
    <div v-if="optunaTrials.length" style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:16px;margin-bottom:16px">
      <div style="font-size:12px;font-weight:600;color:var(--c-text-dim);margin-bottom:10px">Optuna 最近试验</div>
      <div style="overflow-x:auto;max-height:240px;overflow-y:auto">
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead>
            <tr style="color:var(--c-text-dim);text-align:left">
              <th style="padding:4px 8px;border-bottom:1px solid var(--c-border)">#</th>
              <th style="padding:4px 8px;border-bottom:1px solid var(--c-border)">夏普</th>
              <th style="padding:4px 8px;border-bottom:1px solid var(--c-border)">参数</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in optunaTrials" :key="t.no" style="color:var(--c-text)">
              <td style="padding:3px 8px;border-bottom:1px solid var(--c-border-light)">{{t.no}}</td>
              <td style="padding:3px 8px;border-bottom:1px solid var(--c-border-light);color:#10b981">{{t.sharpe}}</td>
              <td style="padding:3px 8px;border-bottom:1px solid var(--c-border-light);font-size:10px">{{t.params}}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 交易明细 -->
    <div v-if="trades.length" style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:16px;margin-bottom:16px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
        <div style="font-size:12px;font-weight:600;color:var(--c-text-dim)">交易明细（共 {{rep.trade_count||trades.length}} 笔）</div>
        <n-button size="tiny" @click="downloadTrades">⬇ 下载CSV</n-button>
      </div>
      <div style="overflow-x:auto;max-height:400px;overflow-y:auto">
        <table style="width:100%;border-collapse:collapse;font-size:10px">
          <thead>
            <tr style="color:var(--c-text-dim);text-align:left;position:sticky;top:0;background:var(--c-card-bg)">
              <th style="padding:3px 6px;border-bottom:1px solid var(--c-border)">#</th>
              <th style="padding:3px 6px;border-bottom:1px solid var(--c-border)">股票</th>
              <th style="padding:3px 6px;border-bottom:1px solid var(--c-border)">操作</th>
              <th style="padding:3px 6px;border-bottom:1px solid var(--c-border)">日期</th>
              <th style="padding:3px 6px;border-bottom:1px solid var(--c-border)">价格</th>
              <th style="padding:3px 6px;border-bottom:1px solid var(--c-border)">股数</th>
              <th style="padding:3px 6px;border-bottom:1px solid var(--c-border)">金额</th>
              <th style="padding:3px 6px;border-bottom:1px solid var(--c-border)">累计盈亏</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(t, i) in pageTrades" :key="i" style="color:var(--c-text)">
              <td style="padding:2px 6px;border-bottom:1px solid var(--c-border-light);color:var(--c-text-faint)">{{t.trade_id}}</td>
              <td style="padding:2px 6px;border-bottom:1px solid var(--c-border-light);font-weight:600">{{t.code}}</td>
              <td style="padding:2px 6px;border-bottom:1px solid var(--c-border-light)" :style="{color:t.action==='BUY'?'#ef4444':'#10b981'}">{{t.action==='BUY'?'买入':'卖出'}}</td>
              <td style="padding:2px 6px;border-bottom:1px solid var(--c-border-light)">{{(t.date||'').slice(5)}}</td>
              <td style="padding:2px 6px;border-bottom:1px solid var(--c-border-light)">{{t.price?.toFixed(2)}}</td>
              <td style="padding:2px 6px;border-bottom:1px solid var(--c-border-light)">{{t.shares}}</td>
              <td style="padding:2px 6px;border-bottom:1px solid var(--c-border-light)">{{t.amount?.toFixed(0)}}</td>
              <td style="padding:2px 6px;border-bottom:1px solid var(--c-border-light);font-weight:600" :style="{color:(t.cumulative_pnl??0)>=0?'#ef4444':'#10b981'}">
                {{(t.cumulative_pnl??0)>=0?'+':''}}{{(t.cumulative_pnl||0).toFixed(0)}}
                <span v-if="t.action==='SELL' && t.pnl" style="font-size:9px;color:var(--c-text-faint)">({{t.pnl>=0?'+':''}}{{t.pnl.toFixed(0)}})</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <n-pagination v-if="tradeTotal > pageSize" :page="tradePage" :page-size="pageSize" :item-count="tradeTotal" :on-update:page="p=>tradePage=p" size="small" style="margin-top:8px;justify-content:center" />
    </div>

    <!-- 策略扫描 -->
    <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:16px;margin-bottom:16px">
      <div style="display:flex;align-items:center;justify-content:space-between">
        <div style="font-size:12px;font-weight:600;color:var(--c-text-dim)">🔍 策略参数扫描</div>
        <n-button size="tiny" @click="showScanModal=true" :disabled="scanRunning">开始扫描</n-button>
      </div>
      <div v-if="scanTask" style="margin-top:8px;font-size:11px;color:var(--c-text-dim)">
        {{ scanTask.status==='running' ? `扫描中 ${scanTask.completed}/${scanTask.total_combos}` : scanTask.status==='completed' ? `✅ 完成 — 最优 sharpe=${scanTask.best_so_far?.sharpe?.toFixed(2) || '?'}` : '' }}
      </div>
      <div v-if="scanBest" style="margin-top:8px;font-size:11px;color:var(--c-text)">
        📌 已应用最优：止损{{(scanBest.stop_loss*100).toFixed(0)}}% / 止盈{{(scanBest.take_profit*100).toFixed(0)}}% /
        trailing{{((scanBest.trailing_retracement||0)*100).toFixed(0)}}% —
        sharpe <b style="color:#10b981">{{scanBest.sharpe?.toFixed(2)}}</b> ·
        收益 <b>{{(scanBest.total_return*100).toFixed(1)}}%</b> ·
        胜率 {{((scanBest.win_rate||0)*100).toFixed(0)}}% · {{scanBest.total_trades}} 笔 · 成本 {{((scanBest.total_cost||0)/10000).toFixed(1)}} 万
      </div>
    </div>

    <!-- 归因分析 -->
    <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:16px;margin-bottom:16px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
        <div style="font-size:12px;font-weight:600;color:var(--c-text-dim)">📊 归因分析（基准锚定法）</div>
        <n-button size="tiny" @click="loadAttribution" :loading="attrLoading">🔄 重新分析</n-button>
      </div>
      <div v-if="attrLoading && !attribution" style="font-size:11px;color:var(--c-text-dim);padding:6px 0">分析中（宽表 + 三基线回测，约 1 分钟）…</div>
      <div v-if="attribution?.matrix" style="display:flex;gap:8px;flex-wrap:wrap">
        <div v-for="m in attrCards" :key="m.label" style="flex:1;min-width:90px;text-align:center;padding:8px;background:var(--c-bg);border-radius:6px">
          <div style="font-size:9px;color:var(--c-text-faint)">{{ m.label }}</div>
          <div style="font-size:15px;font-weight:700;color:var(--c-text)">{{ m.value }}</div>
          <div style="font-size:9px;color:var(--c-text-faint)">夏普 {{ m.sharpe }}</div>
        </div>
      </div>
      <div v-if="attribution?.brinson" style="margin-top:8px;font-size:11px;color:var(--c-text-dim)">
        选股贡献 {{ (attribution.brinson.model_contribution*100).toFixed(1) }}% | 策略贡献 {{ (attribution.brinson.strategy_contribution*100).toFixed(1) }}%
        <span style="margin-left:8px;font-weight:600" :style="{color:attrMatrixColor}">{{ attrMatrixLabel }}</span>
      </div>
      <div v-if="attrVerdict" style="margin-top:8px;font-size:11px;font-weight:600" :style="{color:attrVerdict.color}">
        {{ attrVerdict.text }}<span style="font-weight:400;color:var(--c-text-faint)">（基准沪深300 同期 {{((attribution.benchmark_return||0)*100).toFixed(1)}}%）</span>
      </div>
    </div>

    <!-- 置换检验 -->
    <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:16px;margin-bottom:16px">
      <div style="display:flex;align-items:center;justify-content:space-between">
        <div style="font-size:12px;font-weight:600;color:var(--c-text-dim)">🎲 置换检验（真预测 vs 打乱噪声分布）</div>
        <div style="display:flex;gap:6px;align-items:center">
          <n-select v-model:value="permN" size="tiny" style="width:84px"
            :options="[10,20,50].map(n=>({label:n+' 次',value:n}))" />
          <n-button size="tiny" @click="startPerm" :loading="permRunning">运行</n-button>
        </div>
      </div>
      <div v-if="permTask?.status==='running'" style="font-size:11px;color:var(--c-text-dim);margin-top:8px">
        检验中（宽表 + 预测 + {{permTask.params?.n_perms || permN}} 次打乱回测，约 5-10 分钟）…
      </div>
      <div v-if="permTask?.status==='failed'" style="font-size:11px;color:#ef4444;margin-top:8px">❌ {{permTask.error}}</div>
      <template v-if="permTest">
        <div style="margin-top:8px;font-size:13px;font-weight:700" :style="{color:permVerdict?.color}">{{permVerdict?.label}}</div>
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:8px">
          <div style="flex:1;min-width:110px;text-align:center;padding:8px;background:var(--c-bg);border-radius:6px">
            <div style="font-size:9px;color:var(--c-text-faint)">真预测 sharpe</div>
            <div style="font-size:15px;font-weight:700;color:#10b981">{{permTest.real?.sharpe?.toFixed(2)}}</div>
            <div style="font-size:9px;color:var(--c-text-faint)">收益 {{((permTest.real?.total_return||0)*100).toFixed(1)}}%</div>
          </div>
          <div style="flex:1;min-width:110px;text-align:center;padding:8px;background:var(--c-bg);border-radius:6px">
            <div style="font-size:9px;color:var(--c-text-faint)">打乱噪声（{{permTest.n}} 次）</div>
            <div style="font-size:15px;font-weight:700;color:var(--c-text)">{{permTest.perm?.mean?.toFixed(2)}} ± {{permTest.perm?.std?.toFixed(2)}}</div>
            <div style="font-size:9px;color:var(--c-text-faint)">p5={{permTest.perm?.p5?.toFixed(2)}} p95={{permTest.perm?.p95?.toFixed(2)}}</div>
          </div>
          <div style="flex:1;min-width:110px;text-align:center;padding:8px;background:var(--c-bg);border-radius:6px">
            <div style="font-size:9px;color:var(--c-text-faint)">z / 分位</div>
            <div style="font-size:15px;font-weight:700;color:var(--c-text)">{{permTest.z}} / {{(permTest.pct*100).toFixed(0)}}%</div>
            <div style="font-size:9px;color:var(--c-text-faint)">随机基线 {{permTest.random?.sharpe?.toFixed(2)}}</div>
          </div>
        </div>
        <div style="margin-top:6px;font-size:10px;color:var(--c-text-faint);line-height:1.7">
          窗口 {{permTest.params?.val_start}} ~ {{permTest.params?.val_end}} · sl {{permTest.params?.stop_loss}} / tp {{permTest.params?.take_profit}} · hold {{permTest.params?.hold_days}}d。
          打乱预测必须测不出超额——若噪声也能"赚钱"说明引擎在给噪声送分；真预测进入噪声分布前 5% 才算显著。
        </div>
      </template>
      <n-empty v-else-if="!permRunning" description="未运行——点击「运行」生成噪声分布对照" size="small" style="padding:12px" />
    </div>

    <!-- Walk-Forward 晋升门槛 -->
    <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:16px;margin-bottom:16px">
      <div style="display:flex;align-items:center;justify-content:space-between">
        <div style="font-size:12px;font-weight:600;color:var(--c-text-dim)">🧭 Walk-Forward 多窗口检验（晋升门槛）</div>
        <div style="display:flex;gap:6px;align-items:center">
          <n-select v-model:value="wfWindows" size="tiny" style="width:92px"
            :options="[2,3,4,6].map(n=>({label:n+' 窗口',value:n}))" />
          <n-button size="tiny" @click="startWF" :loading="wfRunning">运行</n-button>
        </div>
      </div>
      <div v-if="wfTask?.status==='running'" style="font-size:11px;color:var(--c-text-dim);margin-top:8px">
        检验中（基线 {{wfTask.params?.baseline_version || '无'}}，{{wfTask.params?.n_windows}} 窗口 × 2 模型评估，约 2-4 分钟）…
      </div>
      <div v-if="wfTask?.status==='failed'" style="font-size:11px;color:#ef4444;margin-top:8px">❌ {{wfTask.error}}</div>
      <template v-if="wf">
        <div style="margin-top:8px;font-size:13px;font-weight:700" :style="{color:wfVerdict?.color}">{{wfVerdict?.label}}</div>
        <div style="font-size:11px;color:var(--c-text-dim);margin-top:2px">{{wf.reason}}<span v-if="wf.baseline">（基线 {{wf.baseline}}）</span></div>
        <div style="overflow-x:auto;margin-top:8px">
          <table style="width:100%;border-collapse:collapse;font-size:11px">
            <thead>
              <tr style="color:var(--c-text-dim);text-align:left">
                <th style="padding:4px 8px;border-bottom:1px solid var(--c-border)">窗口</th>
                <th style="padding:4px 8px;border-bottom:1px solid var(--c-border)">新模型 sharpe</th>
                <th style="padding:4px 8px;border-bottom:1px solid var(--c-border)">RankIC</th>
                <th style="padding:4px 8px;border-bottom:1px solid var(--c-border)">基线 sharpe</th>
                <th style="padding:4px 8px;border-bottom:1px solid var(--c-border)">RankIC</th>
                <th style="padding:4px 8px;border-bottom:1px solid var(--c-border)">胜负</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="w in wf.windows" :key="w.start" style="color:var(--c-text)">
                <td style="padding:4px 8px;border-bottom:1px solid var(--c-border-light)">{{w.start.slice(5)}}~{{w.end.slice(5)}}</td>
                <td style="padding:4px 8px;border-bottom:1px solid var(--c-border-light);font-weight:600">{{w.new?.sharpe?.toFixed(2) ?? '—'}}</td>
                <td style="padding:4px 8px;border-bottom:1px solid var(--c-border-light)">{{w.new?.rank_ic!=null?(w.new.rank_ic>=0?'+':'')+w.new.rank_ic.toFixed(3):'—'}}</td>
                <td style="padding:4px 8px;border-bottom:1px solid var(--c-border-light)">{{w.baseline?.sharpe?.toFixed(2) ?? '—'}}</td>
                <td style="padding:4px 8px;border-bottom:1px solid var(--c-border-light)">{{w.baseline?.rank_ic!=null?(w.baseline.rank_ic>=0?'+':'')+w.baseline.rank_ic.toFixed(3):'—'}}</td>
                <td style="padding:4px 8px;border-bottom:1px solid var(--c-border-light)" :style="{color:(w.new?.sharpe||0)>(w.baseline?.sharpe||0)?'#10b981':'#ef4444'}">{{(w.new?.sharpe||0)>(w.baseline?.sharpe||0)?'✅ 胜':'❌ 负'}}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div style="margin-top:6px;font-size:10px;color:var(--c-text-faint);line-height:1.7">
          门槛：≥{{wfWindows}}-2 个窗口 sharpe 胜出且 RankIC 不低于基线（默认 2 窗口）；判定 FAIL 时 PENDING 审批会被拒绝，
          force 可覆盖。窗口 RankIC 只统计已成熟截面（今日−持有期之前）。审批时自动取最近一次判定。
        </div>
      </template>
      <n-empty v-else-if="!wfRunning" description="未运行——PENDING 审批前需要最近一次判定" size="small" style="padding:12px" />
    </div>

    <!-- 策略扫描弹窗 -->
    <n-modal v-model:show="showScanModal" preset="card" title="策略参数扫描" style="width:500px;max-width:92vw">
      <n-space vertical>
        <div style="font-size:11px;color:var(--c-text-dim)">选择参数候选值，系统将遍历所有组合在验证集上回测。</div>
        <div style="font-size:11px;font-weight:600">止损阈值</div>
        <n-checkbox-group v-model:value="scanStopLoss"><n-space><n-checkbox v-for="v in [0.03,0.05,0.08,0.10]" :key="v" :value="v" :label="(v*100)+'%'" /></n-space></n-checkbox-group>
        <div style="font-size:11px;font-weight:600">止盈阈值</div>
        <n-checkbox-group v-model:value="scanTakeProfit"><n-space><n-checkbox v-for="v in [0.05,0.08,0.10,0.15,0.20]" :key="v" :value="v" :label="(v*100)+'%'" /></n-space></n-checkbox-group>
        <div v-if="scanTask?.status==='running'" style="margin-top:8px">
          <n-progress type="line" :percentage="Math.round(scanTask.completed/scanTask.total_combos*100)" />
        </div>
        <div v-if="scanTask?.status==='completed' && scanTask.best_so_far" style="margin-top:8px;font-size:11px;color:#10b981">
          ✅ 最优：止盈{{ (scanTask.best_so_far.take_profit*100).toFixed(0) }}% 止损{{ (scanTask.best_so_far.stop_loss*100).toFixed(0) }}% 夏普{{ scanTask.best_so_far.sharpe?.toFixed(2) }}
          <n-button size="tiny" type="primary" style="margin-left:8px" @click="applyScan">应用</n-button>
        </div>
      </n-space>
      <template #footer>
        <n-button @click="showScanModal=false">取消</n-button>
        <n-button type="primary" @click="startScan" :loading="scanRunning">开始扫描</n-button>
      </template>
    </n-modal>

    <n-empty v-if="!trials.length" description="无回测记录" style="padding:20px" />
  </template>
</div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { NEmpty, NButton, NPagination, NModal, NSpace, NCheckbox, NCheckboxGroup, NProgress, NSelect, useMessage } from 'naive-ui'
import axios from 'axios'

const API = window.location.origin
const message = useMessage()
const props = defineProps({ version: Object })

const rep = computed(() => props.version?.evaluation_report || {})
const bp = computed(() => props.version?.best_params || {})

const metrics = computed(() => [
  { label:'夏普比率', value: props.version?.sharpe?.toFixed(2) || '—', color:'#ef4444' },
  { label:'胜率',   value: props.version?.win_rate ? (props.version.win_rate*100).toFixed(0)+'%' : '—' },
  { label:'最大回撤', value: props.version?.max_drawdown ? (props.version.max_drawdown*100).toFixed(1)+'%' : '—', color:'#10b981' },
  { label:'年化收益', value: props.version?.annual_return ? (props.version.annual_return*100).toFixed(1)+'%' : '—' },
  { label:'Val夏普', value: rep.value.val_sharpe?.toFixed(2) || '—', color:'#f59e0b' },
  { label:'Test夏普', value: rep.value.test_sharpe?.toFixed(2) || '—', color:'#ef4444' },
])

const overfitLabel = computed(() => {
  const gap = rep.value.overfit_gap
  if (gap == null) return '—'
  if (gap > 0.5) return '⚠️ 过拟合'
  if (gap < -0.3) return '📉 欠拟合'
  return '✅ 正常'
})

const overfitColor = computed(() => {
  const gap = rep.value.overfit_gap
  if (gap == null) return 'var(--c-text-dim)'
  if (gap > 0.5) return '#f59e0b'
  if (gap < -0.3) return '#ef4444'
  return '#10b981'
})

// 收益能力评级
const profitGrade = computed(() => {
  const s = props.version?.sharpe || 0
  const pf = rep.value.profit_factor || 0
  if (s > 1.5 && pf > 1.5) return 'A'
  if (s > 0.8 && pf > 1.0) return 'B'
  if (s > 0.3) return 'C'
  return 'D'
})
const profitLabel = computed(() => ({A:'优秀',B:'良好',C:'一般',D:'较差'}[profitGrade.value]))
const modelReturn = computed(() => props.version?.annual_return || null)

const gradeReason = computed(() => {
  const s = props.version?.sharpe || 0
  const pf = rep.value.profit_factor || 0
  return `夏普${s.toFixed(1)} 盈亏比${pf.toFixed(1)}`
})

const trials = computed(() => {
  const bt = props.version?.evaluation_report || {}
  const bpData = props.version?.best_params || {}
  return ['5d','10d','20d'].map(label => {
    const sk = `sharpe_${label}`; const wk = `win_rate_${label}`
    return {
      train: label,
      sharpe: bt[sk]?.toFixed(3) || '—',
      signals: bt[wk] ? `${(bt[wk]*100).toFixed(0)}%` : '—',
      test: bpData[label]?.model_path?.split('/').pop() || '—',
    }
  }).filter(t => t.sharpe !== '—')
})

const modelParams = computed(() => {
  const first = bp.value?.['5d']?.params || bp.value?.['10d']?.params || bp.value?.['20d']?.params
  if (!first) return []
  return [
    { label:'学习率', value: first.learning_rate, key:'learning_rate' },
    { label:'最大深度', value: first.max_depth, key:'max_depth' },
    { label:'树数量', value: first.n_estimators, key:'n_estimators' },
    { label:'子采样', value: first.subsample?.toFixed?.(2) || first.subsample, key:'subsample' },
    { label:'列采样', value: first.colsample_bytree?.toFixed?.(2) || first.max_features?.toFixed?.(2) || '—', key:'colsample_bytree' },
  ]
})

// ── 策略扫描 ──
const showScanModal = ref(false)
const scanStopLoss = ref([0.05, 0.08])
const scanTakeProfit = ref([0.10, 0.15])
const scanRunning = ref(false)
const scanTask = ref(null)
let scanPollTimer = null

async function startScan() {
  if (!props.version?.version) return
  scanRunning.value = true; scanTask.value = null
  try {
    const r = await axios.post(API + `/api/v1/models/${props.version.version}/strategy-scan`, {
      param_grid: { stop_loss: scanStopLoss.value, take_profit: scanTakeProfit.value, trailing_retracement: [0.05] },
      val_start: cfgValStart.value, val_end: cfgValEnd.value
    })
    scanTask.value = r.data
    if (r.data.task_id) pollScan(r.data.task_id)
  } catch(e) { message.error(e.response?.data?.detail || '启动失败') }
  scanRunning.value = false
}

function pollScan(taskId) {
  scanPollTimer = setInterval(async () => {
    try {
      const r = await axios.get(API + `/api/v1/models/${props.version.version}/strategy-scan/${taskId}`)
      scanTask.value = r.data
      if (r.data.status === 'completed' || r.data.status === 'failed') {
        clearInterval(scanPollTimer); scanPollTimer = null
      }
    } catch(e) { clearInterval(scanPollTimer) }
  }, 1000)
}

async function applyScan() {
  if (!scanTask.value?.task_id) return
  try {
    await axios.post(API + `/api/v1/models/${props.version.version}/strategy-scan/${scanTask.value.task_id}/apply`)
    message.success('最优参数已应用')
    showScanModal.value = false
  } catch(e) { message.error('应用失败') }
}

// ── 归因分析 ──
const attribution = ref(null)
const attrLoading = ref(false)

async function loadAttribution() {
  if (!props.version?.version) return
  attrLoading.value = true
  try {
    const r = await axios.post(API + `/api/v1/models/${props.version.version}/attribution`, {
      val_start: cfgValStart.value, val_end: cfgValEnd.value
    })
    attribution.value = r.data
  } catch(e) {} finally { attrLoading.value = false }
}

const attrCards = computed(() => {
  const a = attribution.value; if (!a) return []
  return [
    { label:'理想化', value: (a.ideal?.total_return*100).toFixed(1)+'%', sharpe: a.ideal?.sharpe?.toFixed(2) || '—' },
    { label:'随机信号', value: (a.random?.total_return*100).toFixed(1)+'%', sharpe: a.random?.sharpe?.toFixed(2) || '—' },
    { label:'真实策略', value: (a.real?.total_return*100).toFixed(1)+'%', sharpe: a.real?.sharpe?.toFixed(2) || '—' },
  ]
})

const attrMatrixLabel = computed(() => {
  const m = attribution.value?.matrix
  return {dual_driver:'双轮驱动',execution_loss:'执行损耗',beta_amplifier:'Beta放大',double_misjudge:'双重误判'}[m] || ''
})
const attrMatrixColor = computed(() => {
  const m = attribution.value?.matrix
  return {dual_driver:'#10b981',execution_loss:'#f59e0b',beta_amplifier:'#f59e0b',double_misjudge:'#ef4444'}[m] || 'var(--c-text)'
})

const attrVerdict = computed(() => {
  const a = attribution.value
  if (!a?.real || !a?.random) return null
  const rs = a.real.sharpe || 0, rd = a.random.sharpe || 0
  return rs > rd
    ? { text: `✅ real 优于 random（${rs.toFixed(2)} vs ${rd.toFixed(2)}）`, color: '#10b981' }
    : { text: `🔴 real 不及 random（${rs.toFixed(2)} vs ${rd.toFixed(2)}）——模型为负贡献`, color: '#ef4444' }
})

const cfgValStart = computed(() => props.version?.config?.val_strategy_range?.start || `${new Date().getFullYear()}-01-01`)
const cfgValEnd = computed(() => props.version?.config?.val_strategy_range?.end || new Date().toISOString().slice(0, 10))

// ── 训练后体检：详情自取（scan 落库结果 + 置换检验留档）──
const evalDetail = ref(null)
async function loadEvalDetail() {
  try {
    const r = await axios.get(API + `/api/v1/models/${props.version.version}`)
    evalDetail.value = r.data
  } catch (e) { console.error(e) }
}
onMounted(() => { loadEvalDetail(); loadAttribution(); loadWF() })

const scanResults = computed(() => evalDetail.value?.strategy_scan_results || null)
const scanBest = computed(() => {
  const list = scanResults.value
  if (!Array.isArray(list) || !list.length) return null
  return list.reduce((a, b) => ((b.sharpe || 0) > (a.sharpe || 0) ? b : a))
})

// ── 置换检验 ──
const permTest = computed(() => evalDetail.value?.perm_test || null)
const permRunning = ref(false)
const permTask = ref(null)
const permN = ref(20)
let permPollTimer = null

async function startPerm() {
  if (!props.version?.version) return
  permRunning.value = true; permTask.value = null
  try {
    const r = await axios.post(API + `/api/v1/models/${props.version.version}/permutation`, {
      n_perms: permN.value, val_start: cfgValStart.value, val_end: cfgValEnd.value,
      stop_loss: 0.05, take_profit: 0.15,
    })
    permTask.value = r.data
    permPollTimer = setInterval(async () => {
      try {
        const tr = await axios.get(API + `/api/v1/models/${props.version.version}/permutation`, { params: { task_id: r.data.task_id } })
        permTask.value = tr.data
        if (tr.data.status === 'completed' || tr.data.status === 'failed') {
          clearInterval(permPollTimer); permPollTimer = null; permRunning.value = false
          if (tr.data.status === 'completed') { message.success('置换检验完成'); loadEvalDetail() }
        }
      } catch (e) { clearInterval(permPollTimer); permPollTimer = null; permRunning.value = false }
    }, 5000)
  } catch (e) {
    message.error(e.response?.data?.detail || '启动失败'); permRunning.value = false
  }
}

const permVerdict = computed(() => {
  const v = permTest.value?.verdict
  return {
    strong: { label: '✅ 显著（进入噪声分布前 5%）', color: '#10b981' },
    above_mean: { label: '🟡 高于噪声均值（未达显著）', color: '#f59e0b' },
    noise: { label: '🔴 与噪声无异（无 alpha）', color: '#ef4444' },
  }[v] || null
})

// ── Walk-Forward 晋升门槛 ──
const wf = ref(null)
const wfRunning = ref(false)
const wfTask = ref(null)
const wfWindows = ref(4)
let wfPollTimer = null

async function loadWF() {
  try {
    const r = await axios.get(API + `/api/v1/models/${props.version.version}/walk-forward`)
    wf.value = r.data.result
  } catch (e) { console.error(e) }
}

async function startWF() {
  if (!props.version?.version) return
  wfRunning.value = true; wfTask.value = null
  try {
    const r = await axios.post(API + `/api/v1/models/${props.version.version}/walk-forward`, {
      n_windows: wfWindows.value, val_start: cfgValStart.value, val_end: cfgValEnd.value,
      hold_days: 10, stop_loss: 0.05, take_profit: 0.15,
    })
    wfTask.value = r.data
    wfPollTimer = setInterval(async () => {
      try {
        const tr = await axios.get(API + `/api/v1/models/${props.version.version}/walk-forward`, { params: { task_id: r.data.task_id } })
        wfTask.value = tr.data
        if (tr.data.status === 'completed' || tr.data.status === 'failed') {
          clearInterval(wfPollTimer); wfPollTimer = null; wfRunning.value = false
          if (tr.data.status === 'completed') { message.success('Walk-Forward 检验完成'); loadWF() }
        }
      } catch (e) { clearInterval(wfPollTimer); wfPollTimer = null; wfRunning.value = false }
    }, 5000)
  } catch (e) {
    message.error(e.response?.data?.detail || '启动失败'); wfRunning.value = false
  }
}

const wfVerdict = computed(() => {
  const v = wf.value?.verdict
  return {
    PASS: { label: '✅ 晋升门槛通过', color: '#10b981' },
    FAIL: { label: '🔴 晋升门槛未通过', color: '#ef4444' },
    NO_BASELINE: { label: '⚪ 无基线可比', color: '#6b7280' },
  }[v] || null
})

const trades = computed(() => rep.value.trades || [])
const tradePage = ref(1)
const pageSize = 50
const tradeTotal = computed(() => trades.value.length)
const pageTrades = computed(() => trades.value.slice((tradePage.value-1)*pageSize, tradePage.value*pageSize))

function downloadTrades() {
  const headers = ['ID','操作','日期','股票','价格','股数','金额','模型版本','信号标签','信号原因','盈亏','盈亏%','累计盈亏','原因','关联ID']
  const rows = trades.value.map(t => [
    t.trade_id, t.action, t.date, t.code, t.price, t.shares, t.amount,
    t.model_version||'', t.signal_label||'', t.signal_reason||'',
    t.pnl?.toFixed(2)||'', t.pnl_pct ? (t.pnl_pct*100).toFixed(2)+'%' : '',
    t.cumulative_pnl?.toFixed(2)||'', t.reason||'', t.buy_trade_id||''
  ])
  const csv = [headers.join(',')].concat(rows.map(r => r.join(','))).join('\n')
  const blob = new Blob(['\uFEFF' + csv], {type:'text/csv;charset=utf-8'})
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = `trades_${props.version.version}.csv`
  a.click(); URL.revokeObjectURL(url)
}

const optunaTrials = computed(() => (rep.value.trials || []).slice(-10).reverse().map(t => ({
  no: t.trial,
  sharpe: t.sharpe?.toFixed(3) || '—',
  params: `lr=${t.params?.learning_rate?.toFixed(3)||'?'} d=${t.params?.max_depth||'?'} n=${t.params?.n_estimators||'?'}`,
})))
</script>