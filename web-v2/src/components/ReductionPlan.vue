<template>
<n-modal v-model:show="show">
  <n-card class="rp-card" :bordered="false">
    <template #header>
      <div class="rp-head">
        <b><AppIcon name="target" :size="15" />  精确减仓清单</b>
        <span class="rp-sub">同时满足目标仓位与单票上限，按 100 股整手估算</span>
      </div>
    </template>
    <div v-if="loading" class="rp-loading">计算中…</div>
    <div v-else-if="error" class="rp-error">{{ error }}</div>
    <template v-else-if="plan">
      <div class="rp-sum">
        <div><span>仓位</span><b>{{ pct(plan.before.position_ratio) }} → {{ pct(plan.after.position_ratio) }}</b></div>
        <div><span>最大单票</span><b>{{ pct(plan.before.top_weight) }} → {{ pct(plan.after.top_weight) }}</b></div>
        <div><span>需卖出</span><b>{{ money(plan.need) }}</b></div>
        <div><span>现金</span><b>{{ money(plan.before.cash) }} → {{ money(plan.after.cash) }}</b></div>
      </div>
      <div class="rp-binding">
        {{ plan.binding === 'single' ? '当前由单票上限约束（比仓位目标更严格）' :
           plan.binding === 'position' ? '当前由目标仓位约束' : '已满足仓位与单票上限' }}
      </div>
      <n-data-table :columns="cols" :data="plan.rows" size="small" :bordered="false"
        :max-height="380" scroll-x="760" />
      <div class="rp-note">{{ plan.note }}</div>
    </template>
    <div class="rp-actions">
      <n-button size="small" quaternary @click="load">刷新</n-button>
      <n-button size="small" @click="show=false">关闭</n-button>
    </div>
  </n-card>
</n-modal>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import { ref, computed, watch, h } from 'vue'
import { NModal, NCard, NButton, NDataTable } from 'naive-ui'
import axios from 'axios'

const props = defineProps({ show: { type: Boolean, default: false } })
const emit = defineEmits(['update:show'])
const API = window.location.origin
const show = computed({ get: () => props.show, set: v => emit('update:show', v) })
const plan = ref(null)
const loading = ref(false)
const error = ref('')

const money = v => v == null ? '—' : (v < 0 ? '-¥' : '¥') + Number(Math.abs(v)).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
const pct = (v, d = 1) => v == null ? '—' : (Number(v) * 100).toFixed(d) + '%'
const cols = [
  { title: '股票', width: 140, render: r => `${r.name} ${r.code}` },
  { title: '现价', key: 'price', width: 72, align: 'right' },
  { title: '现持仓', key: 'qty_before', width: 82, align: 'right' },
  { title: '建议卖出', key: 'sell_qty', width: 88, align: 'right',
    render: r => h('b', { style: { color: 'var(--c-warning)' } }, `${r.sell_qty} 股`) },
  { title: '金额', key: 'sell_amount', width: 96, align: 'right', render: r => money(r.sell_amount) },
  { title: '卖后单票', key: 'after_weight', width: 90, align: 'right', render: r => pct(r.after_weight) },
  { title: '卖后持仓', key: 'qty_after', width: 82, align: 'right' },
]

async function load(){
  loading.value = true; error.value = ''
  try{
    const r = await axios.get(API + '/api/portfolio/trades/reduction-plan', { timeout: 60000 })
    plan.value = r.data
  }catch(e){ error.value = e.response?.data?.detail || e.message || '计算失败' }
  finally{ loading.value = false }
}
watch(() => props.show, v => { if (v) load() })
</script>

<style scoped>
.rp-card { width: min(820px, 95vw); max-height: 90vh; display:flex; flex-direction:column; overflow:hidden; }
.rp-card :deep(.n-card-content) { flex:1; min-height:0; overflow-y:auto; }
.rp-head { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }
.rp-sub { font-size: var(--text-meta); color: var(--c-text-dim); font-weight:400; }
.rp-loading, .rp-error { padding:20px; text-align:center; color: var(--c-text-dim); }
.rp-error { color: var(--c-error); }
.rp-sum { display:grid; grid-template-columns:repeat(auto-fill,minmax(170px,1fr)); gap:8px; margin-bottom:8px; }
.rp-sum > div { background: var(--c-card-bg-hover); border-radius: var(--radius-sm); padding:8px 10px; }
.rp-sum span { display:block; font-size: var(--text-meta); color: var(--c-text-dim); }
.rp-sum b { display:block; margin-top:2px; font-size:13px; font-variant-numeric: tabular-nums; }
.rp-binding { font-size: var(--text-meta); color: var(--c-warning); margin-bottom:8px; }
.rp-note { margin-top:8px; font-size: var(--text-meta); color: var(--c-text-faint); line-height:1.6; }
.rp-actions { display:flex; gap:8px; justify-content:flex-end; margin-top:10px; }
</style>
