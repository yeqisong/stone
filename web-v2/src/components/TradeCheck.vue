<template>
<n-modal v-model:show="show" :mask-closable="!checking && !recording">
  <n-card class="tc-card" :bordered="false">
    <template #header>
      <div class="tc-head">
        <b><AppIcon name="shield" :size="15" />  交易前检查</b>
        <span class="tc-sub">先查再买：系统直接告诉你最多能买多少、买完仓位多少</span>
      </div>
    </template>

    <div class="tc-form">
      <div class="tc-side">
        <n-radio-group v-model:value="side" size="small" @update:value="onSide">
          <n-radio-button value="buy">买入</n-radio-button>
          <n-radio-button value="sell">卖出</n-radio-button>
        </n-radio-group>
        <span v-if="quoteName" class="tc-quote-name">{{ quoteName }} · 现价 ¥{{ quotePrice || '—' }}</span>
      </div>

      <label class="tc-field">
        <span>股票</span>
        <StockSuggestInput v-model:value="form.code" placeholder="代码 / 名称 / 拼音首字母" @select="onCodeSelect" @enter="doCheck" />
      </label>

      <div class="tc-row">
        <label class="tc-field">
          <span>数量（股）</span>
          <n-input-number v-model:value="form.qty" :min="0" :step="100" :precision="0" style="width:100%" />
        </label>
        <label class="tc-field">
          <span>价格（留空用现价）</span>
          <n-input-number v-model:value="form.price" :min="0" :step="0.01" :precision="2" style="width:100%" @update:value="onPrice" />
        </label>
      </div>

      <div v-if="side==='buy'" class="tc-row">
        <label class="tc-field">
          <span>止损价（必填）</span>
          <n-input-number v-model:value="form.stop" :min="0" :step="0.01" :precision="2" style="width:100%" @update:value="stopAuto=false" />
        </label>
        <div class="tc-quote">
          <n-button size="tiny" quaternary :loading="quoteLoading" @click="useQuote">用现价</n-button>
          <span v-if="form.price && form.stop" class="tc-hint">
            止损幅度 {{ ((form.stop/form.price-1)*100).toFixed(1) }}%
          </span>
        </div>
      </div>

      <label class="tc-field">
        <span>理由（为什么买 / 打算拿几天 / 什么情况认错）</span>
        <n-input v-model:value="form.reason" type="textarea" :rows="2" maxlength="200" show-count
          placeholder="一句话也行；写不出来就先别买" />
      </label>

      <div class="tc-actions">
        <n-button size="small" type="primary" :loading="checking" :disabled="!form.code" @click="doCheck">检查</n-button>
        <n-button size="small" :loading="recording" :disabled="!form.code || form.qty<=0" @click="doRecord">
          {{ result && !result.allow ? '已下单，照实记录' : '我已下单，记录这笔' }}
        </n-button>
        <n-button size="small" quaternary @click="show=false">关闭</n-button>
      </div>
    </div>

    <div v-if="result" class="tc-result">
      <div class="tc-banner" :class="result.allow ? 'ok' : 'block'">
        <b>{{ result.allow ? '可以买' : '先别买' }}</b>
        <span>{{ result.advice }}</span>
      </div>

      <div v-if="result.side==='buy'" class="tc-metrics">
        <div><span>最多可买</span><b>{{ result.max_qty }} 股</b></div>
        <div><span>买完仓位</span><b :style="{color: ratioColor(result.projected.position_ratio)}">{{ pct(result.projected.position_ratio) }}</b></div>
        <div><span>买完现金</span><b>{{ pct(result.projected.cash_ratio) }}</b></div>
        <div><span>单票占比</span><b>{{ pct(result.projected.single_weight) }}</b></div>
        <div><span>止损亏损</span><b>{{ money(result.projected.risk_amount) }}（{{ pct(result.projected.risk_pct) }}）</b></div>
      </div>

      <div v-if="result.side==='buy' && result.max_qty > 0 && form.qty > result.max_qty" class="tc-fill">
        <n-button size="tiny" @click="form.qty = result.max_qty">按上限填入 {{ result.max_qty }} 股</n-button>
      </div>

      <div v-if="result.violations?.length" class="tc-violations">
        <div v-for="v in result.violations" :key="v.rule + v.message" :class="'tc-v ' + v.level">
          <span class="tc-v-tag">{{ v.level === 'block' ? '拦截' : '提醒' }}</span>{{ v.message }}
        </div>
      </div>

      <div v-if="recorded" class="tc-recorded">
        已记录。当前仓位 <b>{{ pct(recorded.position_ratio) }}</b>，现金 <b>{{ money(recorded.cash) }}</b>。
        <span v-if="!recorded.allow_new_buy">今天不再开新仓。</span>
      </div>
    </div>

    <div v-if="error" class="tc-error">{{ error }}</div>
  </n-card>
</n-modal>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import StockSuggestInput from './StockSuggestInput.vue'
import { ref, reactive, computed, watch } from 'vue'
import { NModal, NCard, NButton, NInput, NInputNumber, NRadioGroup, NRadioButton, useMessage } from 'naive-ui'
import axios from 'axios'

const props = defineProps({
  show: { type: Boolean, default: false },
  defaultSide: { type: String, default: 'buy' },
})
const emit = defineEmits(['update:show', 'recorded'])
const message = useMessage()
const API = window.location.origin
const show = computed({ get: () => props.show, set: v => emit('update:show', v) })

const side = ref('buy')
const form = reactive({ code: '', qty: 0, price: null, stop: null, reason: '' })
const result = ref(null)
const recorded = ref(null)
const checking = ref(false), recording = ref(false), quoteLoading = ref(false)
const error = ref('')
const quoteName = ref(''), quotePrice = ref(null)
let stopAuto = true

const money = v => v == null ? '—' : (v < 0 ? '-¥' : '¥') + Number(Math.abs(v)).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
const pct = (v, d = 1) => v == null ? '—' : (v * 100).toFixed(d) + '%'
function ratioColor(v){
  if (v == null) return 'var(--c-text)'
  if (v >= 0.90) return 'var(--c-error)'
  if (v >= 0.70) return 'var(--c-warning)'
  return 'var(--c-down)'
}

function reset(){
  side.value = props.defaultSide === 'sell' ? 'sell' : 'buy'
  form.code = ''; form.qty = 0; form.price = null; form.stop = null; form.reason = ''
  result.value = null; recorded.value = null; error.value = ''
  quoteName.value = ''; quotePrice.value = null; stopAuto = true
}
watch(() => props.show, v => { if (v) reset() })

function onSide(v){ stopAuto = true; result.value = null; recorded.value = null }
function onCodeSelect({ code, name }){
  form.code = code; quoteName.value = name || ''
  useQuote()
}
function onPrice(v){
  if (stopAuto && v) form.stop = +(v * 0.92).toFixed(2)
}
async function useQuote(){
  if (!form.code) return
  quoteLoading.value = true
  try{
    const r = await axios.get(API + '/api/portfolio/trades/intraday/quote', { params: { code: form.code } })
    const d = r.data || {}
    quoteName.value = d.stock_name || quoteName.value
    if (d.price > 0) {
      quotePrice.value = d.price
      form.price = d.price
      if (stopAuto) form.stop = +(d.price * 0.92).toFixed(2)
    }
  }catch(e){ /* 快照失败就手填 */ }
  finally{ quoteLoading.value = false }
}

function payload(){
  return {
    stock_code: form.code.trim(),
    side: side.value,
    quantity: Number(form.qty || 0),
    price: Number(form.price || 0),
    stop_price: side.value === 'buy' ? Number(form.stop || 0) : null,
    reason: form.reason || '',
  }
}

async function doCheck(){
  if (!form.code) { message.warning('先填股票代码'); return }
  checking.value = true; error.value = ''; recorded.value = null
  try{
    const r = await axios.post(API + '/api/portfolio/trades/intraday/check', payload(), { timeout: 30000 })
    result.value = r.data.result
  }catch(e){ error.value = e.response?.data?.detail || e.message || '检查失败' }
  finally{ checking.value = false }
}

async function doRecord(){
  if (!form.code || form.qty <= 0) { message.warning('先填代码和数量'); return }
  recording.value = true; error.value = ''
  try{
    const r = await axios.post(API + '/api/portfolio/trades/intraday/record', payload(), { timeout: 30000 })
    result.value = r.data.check
    recorded.value = r.data.state
    emit('recorded', r.data.state)
    if (r.data.check?.allow) message.success('已记录')
    else message.warning('已记录，但这笔触发了护栏拦截；今天不要再开新仓')
  }catch(e){ error.value = e.response?.data?.detail || e.message || '记录失败' }
  finally{ recording.value = false }
}
</script>

<style scoped>
.tc-card { width: min(620px, 94vw); max-height: 90vh; display:flex; flex-direction:column; overflow:hidden; }
.tc-card :deep(.n-card-content) { flex:1; min-height:0; overflow-y:auto; }
.tc-head { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }
.tc-sub { font-size: var(--text-meta); color: var(--c-text-dim); font-weight:400; }
.tc-form { display:flex; flex-direction:column; gap:10px; }
.tc-side { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.tc-quote-name { font-size: var(--text-meta); color: var(--c-text-dim); }
.tc-field { display:flex; flex-direction:column; gap:5px; }
.tc-field > span { font-size: var(--text-meta); color: var(--c-text-dim); }
.tc-row { display:grid; grid-template-columns: 1fr 1fr; gap:10px; align-items:end; }
.tc-quote { display:flex; align-items:center; gap:8px; padding-bottom:2px; }
.tc-hint { font-size: var(--text-meta); color: var(--c-text-faint); }
.tc-actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:2px; }
.tc-result { margin-top:14px; border-top:1px solid var(--c-border); padding-top:12px; }
.tc-banner { display:flex; flex-direction:column; gap:4px; padding:10px 12px; border-radius: var(--radius-sm); border:1px solid var(--c-border); }
.tc-banner b { font-size:15px; }
.tc-banner span { font-size: var(--text-meta); line-height:1.65; color: var(--c-text-dim); }
.tc-banner.ok { border-left:3px solid var(--c-down); }
.tc-banner.block { border-left:3px solid var(--c-error); background: color-mix(in srgb, var(--c-error) 5%, transparent); }
.tc-banner.block b { color: var(--c-error); }
.tc-banner.ok b { color: var(--c-down); }
.tc-metrics { display:grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap:8px; margin-top:10px; }
.tc-metrics > div { background: var(--c-card-bg-hover); border-radius: var(--radius-sm); padding:7px 9px; }
.tc-metrics span { display:block; font-size: var(--text-meta); color: var(--c-text-dim); }
.tc-metrics b { display:block; margin-top:2px; font-size:13px; font-variant-numeric: tabular-nums; }
.tc-fill { margin-top:8px; }
.tc-violations { display:flex; flex-direction:column; gap:4px; margin-top:10px; }
.tc-v { font-size: var(--text-meta); line-height:1.6; color: var(--c-text-dim); }
.tc-v-tag { display:inline-block; margin-right:6px; padding:0 4px; border-radius:3px; font-size:10px; color:#fff; background: var(--c-warning); }
.tc-v.block .tc-v-tag { background: var(--c-error); }
.tc-recorded { margin-top:10px; font-size: var(--text-meta); color: var(--c-text); background: var(--c-card-bg-hover); padding:8px 10px; border-radius: var(--radius-sm); }
.tc-error { margin-top:10px; font-size:13px; color: var(--c-error); }
@media (max-width: 560px) { .tc-row { grid-template-columns: 1fr; } }
</style>
