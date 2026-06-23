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
              <th style="padding:3px 6px;border-bottom:1px solid var(--c-border)">股票</th>
              <th style="padding:3px 6px;border-bottom:1px solid var(--c-border)">周期</th>
              <th style="padding:3px 6px;border-bottom:1px solid var(--c-border)">买入日</th>
              <th style="padding:3px 6px;border-bottom:1px solid var(--c-border)">卖出日</th>
              <th style="padding:3px 6px;border-bottom:1px solid var(--c-border)">股数</th>
              <th style="padding:3px 6px;border-bottom:1px solid var(--c-border)">盈亏</th>
              <th style="padding:3px 6px;border-bottom:1px solid var(--c-border)">原因</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(t, i) in pageTrades" :key="i" style="color:var(--c-text)">
              <td style="padding:2px 6px;border-bottom:1px solid var(--c-border-light);font-weight:600">{{t.code}}</td>
              <td style="padding:2px 6px;border-bottom:1px solid var(--c-border-light)">{{t.horizon}}</td>
              <td style="padding:2px 6px;border-bottom:1px solid var(--c-border-light)">{{(t.buy_date||'').slice(5)}}</td>
              <td style="padding:2px 6px;border-bottom:1px solid var(--c-border-light)">{{(t.sell_date||'').slice(5)}}</td>
              <td style="padding:2px 6px;border-bottom:1px solid var(--c-border-light)">{{t.shares}}</td>
              <td style="padding:2px 6px;border-bottom:1px solid var(--c-border-light);font-weight:600" :style="{color:t.pnl>=0?'#ef4444':'#10b981'}">{{t.pnl>=0?'+':''}}{{t.pnl.toFixed(0)}} ({{t.pnl_pct>=0?'+':''}}{{(t.pnl_pct*100).toFixed(1)}}%)</td>
              <td style="padding:2px 6px;border-bottom:1px solid var(--c-border-light);font-size:9px;color:var(--c-text-faint)">{{t.reason==='stop_loss'?'止损':'到期'}}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <n-pagination v-if="tradeTotal > pageSize" :page="tradePage" :page-size="pageSize" :item-count="tradeTotal" :on-update:page="p=>tradePage=p" size="small" style="margin-top:8px;justify-content:center" />
    </div>

    <n-empty v-if="!trials.length" description="无回测记录" style="padding:20px" />
  </template>
</div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { NEmpty, NButton, NPagination } from 'naive-ui'

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

const trades = computed(() => rep.value.trades || [])
const tradePage = ref(1)
const pageSize = 50
const tradeTotal = computed(() => trades.value.length)
const pageTrades = computed(() => trades.value.slice((tradePage.value-1)*pageSize, tradePage.value*pageSize))

function downloadTrades() {
  const headers = ['股票','周期','买入日','买入价','卖出日','卖出价','股数','盈亏','盈亏%','原因']
  const rows = trades.value.map(t => [t.code, t.horizon, t.buy_date, t.buy_price, t.sell_date, t.sell_price, t.shares, t.pnl.toFixed(2), (t.pnl_pct*100).toFixed(2)+'%', t.reason==='stop_loss'?'止损':'到期'])
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