<template>
<div>
  <div v-if="!health" style="color:var(--c-text-dim);padding:20px 0;text-align:center">暂无实盘数据</div>
  <template v-else>
    <div style="display:flex;gap:16px;margin-bottom:20px;flex-wrap:wrap">
      <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:14px 18px;min-width:90px;text-align:center">
        <div style="font-size:10px;color:var(--c-text-faint)">健康度</div>
        <div :style="{fontSize:'20px',fontWeight:700,color:healthColor}">{{health.health_status}}</div>
      </div>
      <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:14px 18px;min-width:90px;text-align:center">
        <div style="font-size:10px;color:var(--c-text-faint)">实盘胜率</div>
        <div style="fontSize:20px;fontWeight:700;color:var(--c-text)">{{(health.live_win_rate*100).toFixed(0)}}%</div>
      </div>
      <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:14px 18px;min-width:90px;text-align:center">
        <div style="font-size:10px;color:var(--c-text-faint)">信号总数</div>
        <div style="fontSize:20px;fontWeight:700;color:var(--c-text)">{{health.signal_count}}</div>
      </div>
      <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:14px 18px;min-width:90px;text-align:center">
        <div style="font-size:10px;color:var(--c-text-faint)">Avg Fwd 5D</div>
        <div :style="{fontSize:'20px',fontWeight:700,color:health.avg_forward_5d>=0?'#10b981':'#ef4444'}">{{(health.avg_forward_5d*100).toFixed(2)}}%</div>
      </div>
    </div>

    <!-- Signal Detail List -->
    <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:16px">
      <div style="font-size:12px;font-weight:600;color:var(--c-text-dim);margin-bottom:10px">最近信号明细</div>
      <div v-if="signals.length" style="display:flex;flex-direction:column;gap:4px">
        <div v-for="s in signals" :key="s.id" style="display:flex;align-items:center;gap:10px;padding:6px 10px;background:var(--c-card-bg-hover);border-radius:6px;font-size:11px">
          <span style="color:var(--c-text-faint);min-width:75px">{{(s.signal_date||'').slice(0,10)}}</span>
          <span :style="{color:s.direction==='buy'?'#ef4444':'#10b981',fontWeight:600,minWidth:30}">{{s.direction==='buy'?'买':'卖'}}</span>
          <span style="color:var(--c-text);min-width:70px">{{s.stock_code}}</span>
          <span style="color:var(--c-text-dim);min-width:50px">¥{{(s.price||0).toFixed(2)}}</span>
          <span :style="{color:(s.forward_5d_return||0)>=0?'#10b981':'#ef4444',marginLeft:'auto'}">{{s.forward_5d_return ? (s.forward_5d_return*100).toFixed(2)+'%' : '—'}}</span>
          <span :style="{color:(s.actual_return||0)>=0?'#10b981':'#ef4444'}">{{s.actual_return ? '实'+(s.actual_return*100).toFixed(1)+'%' : ''}}</span>
        </div>
      </div>
      <div v-else style="font-size:11px;color:var(--c-text-faint);text-align:center;padding:10px">暂无信号</div>
    </div>
  </template>
</div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import axios from 'axios'

const props = defineProps({ version: Object })
const health = ref(null)
const signals = ref([])

const healthColor = computed(() => {
  const s = health.value?.health_status
  return { HEALTHY:'#10b981', CAUTION:'#f59e0b', WARNING:'#f97316', CRITICAL:'#ef4444' }[s] || '#6b7280'
})

onMounted(async () => {
  try {
    const [hr, sr] = await Promise.all([
      axios.get(window.location.origin + `/api/v1/models/${props.version.version}/health`),
      axios.get(window.location.origin + `/api/v1/models/${props.version.version}/signals`),
    ])
    health.value = hr.data
    signals.value = sr.data?.signals || []
  } catch(e) {}
})
</script>