<template>
<n-modal v-model:show="show" :mask-closable="!checking">
  <n-card class="bc-card" :bordered="false">
    <template #header>
      <div class="bc-head">
        <b><AppIcon name="target" :size="15" />  买点检查</b>
        <span class="bc-sub">市场 / 板块 / 个股结构 / 风险四道闸门，全过才给买点</span>
      </div>
    </template>

    <div class="bc-form">
      <label class="bc-field">
        <span>股票</span>
        <StockSuggestInput v-model:value="form.code" placeholder="代码 / 名称 / 拼音首字母"
          @select="onCodeSelect" @enter="doCheck" />
      </label>

      <div class="bc-row3">
        <label class="bc-field">
          <span>买点 / 现价（留空用现价）</span>
          <n-input-number v-model:value="form.entry" :min="0" :step="0.01" :precision="2" placeholder="现价" style="width:100%" />
        </label>
        <label class="bc-field">
          <span>止损价（留空用结构建议）</span>
          <n-input-number v-model:value="form.stop" :min="0" :step="0.01" :precision="2" placeholder="结构建议" style="width:100%" />
        </label>
        <label class="bc-field">
          <span>目标价（留空用结构建议）</span>
          <n-input-number v-model:value="form.target" :min="0" :step="0.01" :precision="2" placeholder="结构建议" style="width:100%" />
        </label>
      </div>

      <div class="bc-row2">
        <label class="bc-field">
          <span>计划数量（可选，留空按上限）</span>
          <n-input-number v-model:value="form.qty" :min="0" :step="100" :precision="0" placeholder="按上限" style="width:100%" />
        </label>
        <div class="bc-actions">
          <n-button size="small" type="primary" :loading="checking" :disabled="!form.code" @click="doCheck">检查</n-button>
          <n-button size="small" :disabled="!result?.setup?.entry" @click="useSuggestion">用结构建议价</n-button>
          <n-button size="small" quaternary @click="show=false">关闭</n-button>
        </div>
      </div>
    </div>

    <div v-if="result" class="bc-result">
      <div class="bc-banner" :class="result.allowed ? 'ok' : 'block'">
        <b>{{ result.allowed ? '可以买' : '先别买' }}</b>
        <span>{{ result.advice }}</span>
      </div>

      <div class="bc-gates">
        <div v-for="g in result.gates" :key="g.id" class="bc-gate" :class="gateClass(g)">
          <div class="bc-gate-head">
            <span class="bc-gate-name">{{ g.label }}</span>
            <span class="bc-gate-tag">{{ g.pass ? '通过' : (g.level === 'skip' ? '跳过' : '未过') }}</span>
          </div>
          <div class="bc-gate-sum">{{ g.summary || g.reason }}</div>
          <div v-if="g.reason && g.reason !== g.summary" class="bc-gate-reason">{{ g.reason }}</div>
        </div>
      </div>

      <div class="bc-metrics">
        <div><span>参考买点</span><b>{{ fmt(result.order.entry) }}</b></div>
        <div><span>止损</span><b>{{ fmt(result.order.stop) }}</b></div>
        <div><span>目标</span><b>{{ fmt(result.order.target) }}</b></div>
        <div><span>盈亏比</span><b>{{ result.order.rr ? result.order.rr.toFixed(1) : '—' }}</b></div>
        <div><span>最多可买</span><b>{{ result.order.max_qty || 0 }} 股</b></div>
        <div v-if="result.risk?.projected"><span>买完仓位</span><b>{{ pct(result.risk.projected.position_ratio) }}</b></div>
        <div v-if="result.risk?.projected"><span>买完单票</span><b>{{ pct(result.risk.projected.single_weight) }}</b></div>
        <div v-if="result.risk?.projected"><span>止损亏损</span><b>{{ money(result.risk.projected.risk_amount) }}（{{ pct(result.risk.projected.risk_pct) }}）</b></div>
      </div>

      <div v-if="result.setup?.notes?.length" class="bc-notes">
        <span v-for="n in result.setup.notes" :key="n">· {{ n }}</span>
      </div>

      <div v-if="blockViolations.length" class="bc-violations">
        <div v-for="v in blockViolations" :key="v.rule + v.message" class="bc-violation">
          <span class="bc-v-tag">拦截</span>{{ v.message }}
        </div>
      </div>
    </div>

    <div v-if="error" class="bc-error">{{ error }}</div>
  </n-card>
</n-modal>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import StockSuggestInput from './StockSuggestInput.vue'
import { ref, reactive, computed, watch } from 'vue'
import { NModal, NCard, NButton, NInputNumber, useMessage } from 'naive-ui'
import axios from 'axios'

const props = defineProps({
  show: { type: Boolean, default: false },
  preset: { type: Object, default: null },
})
const emit = defineEmits(['update:show'])
const message = useMessage()
const API = window.location.origin
const show = computed({ get: () => props.show, set: v => emit('update:show', v) })

const form = reactive({ code: '', entry: null, stop: null, target: null, qty: null })
const result = ref(null)
const checking = ref(false)
const error = ref('')

const fmt = v => v == null || v === '' ? '—' : '¥' + Number(v).toFixed(2)
const pct = (v, d = 1) => v == null ? '—' : (Number(v) * 100).toFixed(d) + '%'
const money = v => v == null ? '—' : (v < 0 ? '-¥' : '¥') + Number(Math.abs(v)).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
const gateClass = g => g.pass ? 'g-ok' : (g.level === 'skip' ? 'g-skip' : 'g-fail')
const blockViolations = computed(() => (result.value?.risk?.violations || []).filter(v => v.level === 'block'))

function reset(){
  form.code = ''; form.entry = null; form.stop = null; form.target = null; form.qty = null
  result.value = null; error.value = ''
}
function applyPreset(p){
  if (!p) return
  form.code = p.code || ''
  form.entry = p.entry ?? null
  form.stop = p.stop ?? null
  form.target = p.target ?? null
  form.qty = p.qty ?? null
  doCheck()
}
watch(() => props.show, v => {
  if (!v) return
  reset()
  if (props.preset) applyPreset(props.preset)
})
watch(() => props.preset, p => {
  if (props.show && p) applyPreset(p)
})

function blurActive(){
  // 选完代码/点检查后收起自动补全下拉，避免盖住下面的输入框
  try { document.activeElement?.blur?.() } catch (e) {}
}

function onCodeSelect(o){
  form.code = o?.code || ''
  result.value = null
  error.value = ''
  blurActive()
}

async function doCheck(){
  if (!form.code) { message.warning('先填股票代码'); return }
  blurActive()
  checking.value = true; error.value = ''
  try{
    const params = { code: String(form.code).trim() }
    if (form.entry > 0) params.entry = form.entry
    if (form.stop > 0) params.stop = form.stop
    if (form.target > 0) params.target = form.target
    if (form.qty > 0) params.qty = form.qty
    const r = await axios.get(API + '/api/buy/check', { params, timeout: 30000 })
    result.value = r.data
  }catch(e){
    error.value = e.response?.data?.detail || e.message || '检查失败'
  }finally{
    checking.value = false
  }
}

function useSuggestion(){
  const s = result.value?.setup
  if (!s?.entry) return
  form.entry = s.entry
  form.stop = s.stop
  form.target = s.target
  doCheck()
}
</script>

<style scoped>
.bc-card { width: min(760px, 95vw); max-height: 92vh; display:flex; flex-direction:column; overflow:hidden; }
.bc-card :deep(.n-card-content) { flex:1; min-height:0; overflow-y:auto; }
.bc-head { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }
.bc-sub { font-size: var(--text-meta); color: var(--c-text-dim); font-weight:400; }
.bc-form { display:flex; flex-direction:column; gap:10px; }
.bc-field { display:flex; flex-direction:column; gap:5px; }
.bc-field > span { font-size: var(--text-meta); color: var(--c-text-dim); }
.bc-row3 { display:grid; grid-template-columns: repeat(3, 1fr); gap:10px; }
.bc-row2 { display:grid; grid-template-columns: 1fr auto; gap:10px; align-items:end; }
.bc-actions { display:flex; gap:8px; flex-wrap:wrap; padding-bottom:1px; }
.bc-result { margin-top:14px; border-top:1px solid var(--c-border); padding-top:12px; }
.bc-banner { display:flex; flex-direction:column; gap:4px; padding:10px 12px; border-radius: var(--radius-sm); border:1px solid var(--c-border); }
.bc-banner b { font-size:15px; }
.bc-banner span { font-size: var(--text-meta); line-height:1.65; color: var(--c-text-dim); }
.bc-banner.ok { border-left:3px solid var(--c-info); }
.bc-banner.ok b { color: var(--c-info); }
.bc-banner.block { border-left:3px solid var(--c-error); background: color-mix(in srgb, var(--c-error) 5%, transparent); }
.bc-banner.block b { color: var(--c-error); }
.bc-gates { display:grid; grid-template-columns: repeat(2, 1fr); gap:8px; margin-top:10px; }
.bc-gate { border:1px solid var(--c-border); border-radius: var(--radius-sm); padding:8px 10px; background: var(--c-card-bg); }
.bc-gate.g-ok { border-left:3px solid var(--c-info); }
.bc-gate.g-fail { border-left:3px solid var(--c-error); background: color-mix(in srgb, var(--c-error) 4%, transparent); }
.bc-gate.g-skip { border-left:3px solid var(--c-text-faint); }
.bc-gate-head { display:flex; align-items:center; justify-content:space-between; gap:8px; }
.bc-gate-name { font-size:12px; font-weight:600; }
.bc-gate-tag { font-size:10px; padding:0 5px; border-radius:3px; color:#fff; background: var(--c-text-faint); white-space:nowrap; }
.bc-gate.g-ok .bc-gate-tag { background: var(--c-info); }
.bc-gate.g-fail .bc-gate-tag { background: var(--c-error); }
.bc-gate-sum { margin-top:4px; font-size: var(--text-meta); color: var(--c-text); line-height:1.55; }
.bc-gate-reason { margin-top:2px; font-size: var(--text-meta); color: var(--c-text-faint); line-height:1.5; }
.bc-metrics { display:grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap:8px; margin-top:10px; }
.bc-metrics > div { background: var(--c-card-bg-hover); border-radius: var(--radius-sm); padding:7px 9px; }
.bc-metrics span { display:block; font-size: var(--text-meta); color: var(--c-text-dim); }
.bc-metrics b { display:block; margin-top:2px; font-size:13px; font-variant-numeric: tabular-nums; }
.bc-notes { display:flex; flex-direction:column; gap:2px; margin-top:8px; font-size: var(--text-meta); color: var(--c-text-dim); }
.bc-violations { display:flex; flex-direction:column; gap:4px; margin-top:10px; }
.bc-violation { font-size: var(--text-meta); line-height:1.6; color: var(--c-text-dim); }
.bc-v-tag { display:inline-block; margin-right:6px; padding:0 4px; border-radius:3px; font-size:10px; color:#fff; background: var(--c-error); }
.bc-error { margin-top:10px; font-size:13px; color: var(--c-error); }
@media (max-width: 640px) {
  .bc-row3 { grid-template-columns: 1fr; }
  .bc-row2 { grid-template-columns: 1fr; }
  .bc-gates { grid-template-columns: 1fr; }
}
</style>
