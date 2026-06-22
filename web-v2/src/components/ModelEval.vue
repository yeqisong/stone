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

    <!-- Walk-Forward Folds -->
    <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:16px">
      <div style="font-size:12px;font-weight:600;color:var(--c-text-dim);margin-bottom:10px">Walk-Forward 各期明细</div>
      <div style="overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead>
            <tr style="color:var(--c-text-dim);text-align:left">
              <th style="padding:6px 10px;border-bottom:1px solid var(--c-border)">#</th>
              <th style="padding:6px 10px;border-bottom:1px solid var(--c-border)">训练区间</th>
              <th style="padding:6px 10px;border-bottom:1px solid var(--c-border)">测试区间</th>
              <th style="padding:6px 10px;border-bottom:1px solid var(--c-border)">模型文件</th>
              <th style="padding:6px 10px;border-bottom:1px solid var(--c-border)">R²</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(t, i) in trials" :key="i" style="color:var(--c-text)">
              <td style="padding:5px 10px;border-bottom:1px solid var(--c-border-light)">{{i+1}}</td>
              <td style="padding:5px 10px;border-bottom:1px solid var(--c-border-light)">{{t.train}}</td>
              <td style="padding:5px 10px;border-bottom:1px solid var(--c-border-light)">{{t.test}}</td>
              <td style="padding:5px 10px;border-bottom:1px solid var(--c-border-light);font-size:10px">{{t.signals}}</td>
              <td style="padding:5px 10px;border-bottom:1px solid var(--c-border-light);color:#10b981">{{t.sharpe}}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <n-empty v-if="!trials.length" description="无训练记录" style="padding:20px" />
    </div>
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
  { label:'胜率', value: props.version?.win_rate ? (props.version.win_rate*100).toFixed(0)+'%' : '—' },
  { label:'最大回撤', value: props.version?.max_drawdown ? (props.version.max_drawdown*100).toFixed(1)+'%' : '—', color:'#ef4444' },
  { label:'年化收益', value: props.version?.annual_return ? (props.version.annual_return*100).toFixed(1)+'%' : '—' },
  { label:'R²(5d)', value: bp.value?.['5d']?.r2?.toFixed(3) || '—' },
  { label:'R² avg', value: rep.value.r2_avg?.toFixed(3) || '—' },
])

const trials = computed(() => {
  const bp = props.version?.best_params
  if (!bp) return []
  // best_params 格式: {"5d": {"r2": 0.8, "model_path": "..."}, "10d": {...}, "20d": {...}}
  const folds = []
  for (const [label, info] of Object.entries(bp)) {
    if (info && typeof info === 'object') {
      folds.push({
        train: label,
        test: `R²=${(info.r2||0).toFixed(3)}`,
        signals: info.model_path?.split('/').pop() || '—',
        sharpe: info.r2?.toFixed(3) || '—',
      })
    }
  }
  return folds
})
</script>