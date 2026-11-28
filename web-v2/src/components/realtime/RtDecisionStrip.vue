<template>
  <!-- 决策线：一眼回答「现在是什么时段、今天能不能做」；盘面/持仓/机会的明细在各自分区头部 -->
  <div class="rds" role="group" aria-label="盘中决策线">
    <div class="rds-seg rds-time">
      <b class="rds-session" :class="d ? 'ms-' + d.session : ''">{{ d ? d.session_label : '连接中' }}</b>
      <span class="rds-dim" :title="d ? '数据所属交易日与缓存更新时间是两回事：盘前/休市日各源都回退成上一交易日的数据' : ''">
        {{ d ? asofText(d) : (failed ? '盘面拉取失败，自动重试中' : '等待盘面数据') }}
      </span>
      <span v-if="d && failed" class="rds-stale">刷新失败，显示的是上次数据</span>
      <span v-else-if="d && d.stale && d.session !== 'closed'" class="rds-stale">{{ staleText(d) }}</span>
    </div>

    <div class="rds-seg rds-gate" :title="gateTip">
      <span class="rds-k">开仓闸门</span>
      <b class="rds-state" :class="gate.level !== 'none' ? 'gate-' + gate.level : ''">{{ gate.label }}</b>
      <!-- 结论不完整（情绪源不可用/仍是上一交易日口径）：不能把缺了一路的判断说成完整的「放行」 -->
      <span v-if="gate.partial" class="rds-partial" title="闸门结论不完整：有数据源不可用或还是上一交易日口径，详见整行提示">不完整</span>
      <span class="rds-dim rds-clip">{{ gate.reason }}</span>
      <template v-if="canRadar">
        <span class="rds-div"></span>
        <span class="rds-k">开新仓</span>
        <b v-if="newBuy" class="rds-state" :class="newBuy.allow ? 'gate-open' : 'gate-closed'">{{ newBuy.allow ? '允许' : '禁止' }}</b>
        <b v-else class="rds-state">—</b>
        <span class="rds-dim rds-clip">{{ newBuy ? newBuy.brief : '' }}</span>
      </template>
    </div>

    <button type="button" class="rds-seg rds-link" title="看今日盘面异动：指数急变 / 宽度反转 / 连板高度 / 情绪跨级"
            @click="emit('focus', 'market')">
      <span class="rds-k">今日异动</span>
      <b :class="alerts.critical ? 'crit' : alerts.total ? 'warn' : ''">{{ alerts.total }}</b>
      <span v-if="alerts.critical" class="rds-dim">严重 {{ alerts.critical }}</span>
    </button>

    <div class="rds-end">
      <button type="button" class="rds-refresh" :disabled="loading" title="立即刷新盘面数据" @click="emit('refresh')">
        <AppIcon name="refresh" :size="12" /> 刷新
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import AppIcon from '../AppIcon.vue'
import { alertCounts, asofText, gateSummary, newBuySummary, staleText } from '../../utils/realtime'

const props = defineProps({
  d: { type: Object, default: null },            // /api/market/realtime
  cand: { type: Object, default: null },         // /api/buy/candidates
  canRadar: { type: Boolean, default: false },   // 无 research.radar.view 时不显示「开新仓」（拿不到仓位纪律）
  loading: { type: Boolean, default: false },
  failed: { type: Boolean, default: false },    // 最近一次拉取失败（界面标出，不静默）
})
const emit = defineEmits(['focus', 'refresh'])

const gate = computed(() => gateSummary(props.d?.gate))
const newBuy = computed(() => newBuySummary(props.cand))
const alerts = computed(() => alertCounts(props.d?.alerts_today))
const gateTip = computed(() => {
  const g = props.d?.gate
  const parts = []
  if (g) {
    const rs = (g.reasons || []).join('；')
    parts.push(`实时空仓闸门（市场闸门 × regime × 盘面情绪）${rs ? '：' + rs : ''}${g.advice_entry ? '｜' + g.advice_entry : ''}`)
  }
  if (props.cand) {
    parts.push(`开新仓：${props.cand.allowed ? '仓位/纪律允许' : (props.cand.reason || '当前仓位/纪律不允许')}`)
  }
  return parts.join('\n')
})
</script>

<style scoped>
.rds {
  display: flex; align-items: center; flex-shrink: 0; flex-wrap: wrap;
  min-height: 44px; padding: 4px 6px 4px 12px;
  font-size: var(--text-meta); color: var(--c-text-dim);
  background: var(--c-card-bg); border: 1px solid var(--c-border); border-radius: var(--radius);
}
.rds-seg {
  display: flex; align-items: center; gap: 8px; min-width: 0; min-height: 34px;
  padding: 0 14px; border-left: 1px solid var(--c-border); white-space: nowrap;
}
.rds-seg:first-child { border-left: 0; padding-left: 0; }
.rds-k { color: var(--c-text-faint); }
.rds-dim { color: var(--c-text-dim); overflow: hidden; text-overflow: ellipsis; }
.rds-clip { max-width: 280px; }
.rds-div { width: 1px; height: 14px; background: var(--c-border); }

.rds-session {
  padding: 1px 8px; border-radius: var(--radius-sm); border: 1px solid var(--c-border);
  font-weight: 600; color: var(--c-text);
}
.rds-session.ms-open { color: var(--c-up); border-color: var(--c-up); }
.rds-session.ms-lunch, .rds-session.ms-pre { color: var(--c-warning); border-color: var(--c-warning); }

/* 闸门三态配色与 TradeGuard / MarketSentiment 一致：正常=下跌绿、警戒=warning、关闸=error。
   不用 --c-up（上涨红）：项目的红绿只表示涨跌，关闸是「停」不是「涨」；拿不到闸门时走默认灰 */
.rds-state {
  padding: 1px 8px; border-radius: var(--radius-sm); border: 1px solid var(--c-border);
  font-weight: 600; color: var(--c-text);
}
.rds-state.gate-open { color: var(--c-down); border-color: var(--c-down); }
.rds-state.gate-caution { color: var(--c-warning); border-color: var(--c-warning); }
.rds-state.gate-closed { color: var(--c-error); border-color: var(--c-error); }
.rds-partial { padding: 0 5px; border-radius: 3px; font-size: var(--text-micro); color: var(--c-warning); background: var(--warn-weak, var(--c-accent-weak)); white-space: nowrap; }
.rds-stale { color: var(--c-warning); }

.rds-link {
  border: 0; border-left: 1px solid var(--c-border); margin: 0;
  font: inherit; color: inherit; background: transparent; cursor: pointer;
}
.rds-link b { color: var(--c-text); font-size: var(--text-title); font-variant-numeric: tabular-nums; }
.rds-link b.warn { color: var(--c-warning); }
.rds-link b.crit { color: var(--c-up); }
.rds-link:hover { background: var(--c-card-bg-hover); color: var(--c-text); }

.rds-end { margin-left: auto; padding-left: 8px; }
.rds-refresh {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: transparent; color: var(--c-text-dim); font-size: var(--text-meta); cursor: pointer;
}
.rds-refresh:hover:not(:disabled) { color: var(--c-text); border-color: var(--c-text-faint); }
.rds-refresh:disabled { cursor: default; opacity: .55; }
</style>
