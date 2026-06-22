<template>
<div>
  <div v-if="!version.evaluation_report" style="color:var(--c-text-dim);padding:20px 0;text-align:center">暂未训练，无评估数据</div>
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
              <td style="padding:5px 10px;border-bottom:1px solid var(--c-border-light);color:#10b981">{{t.sharpe}}</td>
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

    <n-empty v-if="!trials.length" description="无回测记录" style="padding:20px" />
  </template>
</div>
</template>

<script setup>
import { computed } from 'vue'
import { NEmpty } from 'naive-ui'

const props = defineProps({ version: Object })

const rep = computed(() => props.version?.evaluation_report || {})
const bp = computed(() => props.version?.best_params || {})

const metrics = computed(() => [
  { label:'夏普比率', value: props.version?.sharpe?.toFixed(2) || '—', color:'#10b981' },
  { label:'胜率',   value: props.version?.win_rate ? (props.version.win_rate*100).toFixed(0)+'%' : '—' },
  { label:'最大回撤', value: props.version?.max_drawdown ? (props.version.max_drawdown*100).toFixed(1)+'%' : '—', color:'#ef4444' },
  { label:'年化收益', value: props.version?.annual_return ? (props.version.annual_return*100).toFixed(1)+'%' : '—' },
  { label:'试验轮数', value: rep.value.trials?.length || '—' },
  { label:'训练分割', value: rep.value.train_start?.slice(0,10) || '—' },
])

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
    { label:'子采样', value: first.subsample?.toFixed(2), key:'subsample' },
    { label:'特征采样', value: first.max_features?.toFixed(2), key:'max_features' },
  ]
})

const optunaTrials = computed(() => (rep.value.trials || []).slice(-10).reverse().map(t => ({
  no: t.trial,
  sharpe: t.sharpe?.toFixed(3) || '—',
  params: `lr=${t.params?.learning_rate?.toFixed(3)||'?'} d=${t.params?.max_depth||'?'} n=${t.params?.n_estimators||'?'}`,
})))
</script>