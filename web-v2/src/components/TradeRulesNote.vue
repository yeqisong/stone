<template>
  <div v-if="r" class="no-shrink" style="font-size:10px;color:var(--c-text-faint);padding:4px 2px 0;line-height:1.7">
    <div>信号触发：{{ signalLine }}</div>
    <div>了结规则（效果追踪）：{{ closureLine }}</div>
    <div>纸面交易：{{ paperLine }}</div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import axios from 'axios'

const API = window.location.origin
const props = defineProps({ version: String })
const r = ref(null)

const pct = (v, d = 0) => v == null ? '—' : (v * 100).toFixed(d) + '%'

const signalLine = computed(() => {
  if (!r.value) return ''
  const s = r.value.signal_rules
  const parts = []
  if (s.ml_enabled) {
    parts.push(s.mode === 'quantile'
      ? `当日全市场 5/10/20 日预测均值排名前 ${pct(s.buy_top_pct)} 触发买入（quantile 截断，约每日百只）`
      : `预测收益 ≥ ${pct(s.abs_threshold, 2)} 触发买入（absolute 模式）`)
  } else {
    parts.push('规则评分模式：BOLL超卖/RSI超卖/MACD正柱 计分达标触发买入')
  }
  if (s.limit_up_blocked) parts.push('涨停股不发信号')
  if (s.regime?.enabled) parts.push(`沪深300 跌破 MA${s.regime.ma_window} 空仓闸门（连续 ${s.regime.max_skip_days} 天后放行）`)
  parts.push('模型仅产生买入信号，卖出由纸面持仓管理触发')
  return parts.join(' · ')
})

const closureLine = computed(() => {
  if (!r.value) return ''
  const c = r.value.closure_rules
  const s = r.value.signal_rules
  return `偏好 ${s.preference} → 止损 -${pct(c.stop_loss)} / 止盈 +${pct(c.take_profit)} / 持有 ${c.timeout_days} 个交易日到期，按信号日收盘价（后复权）计`
})

const paperLine = computed(() => {
  if (!r.value) return ''
  const p = r.value.paper_rules
  const parts = [`止损 -${pct(p.stop_loss)} 按触发价成交`]
  parts.push(p.take_profit >= 99 ? '固定止盈关闭（防截断右尾）' : `止盈 +${pct(p.take_profit)}`)
  if (p.trailing > 0) parts.push(`移动止盈=持仓期峰值回撤 ${pct(p.trailing)}`)
  parts.push(`持有 ${p.hold_days} 个交易日到期`)
  parts.push(`最大持仓 ${p.max_positions} 只`)
  if (p.portfolio_gate_dd) parts.push(`组合回撤 ${pct(p.portfolio_gate_dd)} 熔断停止开仓`)
  parts.push('T+1 / 涨停不买 / 跌停不卖')
  return parts.join(' · ')
})

async function load() {
  if (!props.version) { r.value = null; return }
  try {
    const res = await axios.get(API + `/api/v1/models/${props.version}/trade-rules`)
    r.value = res.data
  } catch (e) { r.value = null }
}
watch(() => props.version, load, { immediate: true })
</script>
