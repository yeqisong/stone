<template>
  <!-- 机会区（决策顺序 ③）：后找机会——候选 → 买点检查（市场/板块/结构/风险四道闸门） -->
  <section class="rt-zone" aria-label="机会">
    <header class="rt-zh">
      <div class="rt-zt"><b>机会</b><span>后找机会：候选 → 四道闸门</span></div>
      <div class="rt-zr">
        <span class="rz-chip" :class="{ 'rz-hot rz-buy': items.length }">候选 <b>{{ items.length }}</b></span>
        <span class="rz-chip" :class="{ 'rz-hot': watch.length }">观察 <b>{{ watch.length }}</b></span>
        <span v-if="error" class="rz-chip rz-err" title="候选扫描失败，稍后自动重试；下方是上一次的结论">扫描失败</span>
      </div>
    </header>

    <div class="rt-zb">
      <div class="ro-gate" :class="allowed ? 'is-open' : 'is-block'">
        <b>{{ cand ? (allowed ? '可以找买点' : '先别开新仓') : (loading ? '扫描中…' : '—') }}</b>
        <span :title="cand?.reason">{{ cand?.reason || '关注池候选扫描每 60 秒一次' }}</span>
        <!-- 候选是日频口径、决策线是盘中口径：allowed 而实时闸门关闭/警戒时必须注明，别让同屏两个结论互相打架 -->
        <span v-if="gateNote" class="ro-gate-note" :title="gateNote">{{ gateNote }}</span>
      </div>

      <div v-if="allowed && items.length" class="ro-group">
        <div class="ro-gh">买点候选<span class="rz-note">四道闸门全过；仅候选，下单前再检查</span></div>
        <button v-for="c in items" :key="c.code" type="button" class="ro-row" :title="c.reason"
                @click="emit('check', buyPresetOf(c))">
          <span class="ro-l1">
            <b class="ro-name">{{ c.name }}</b><i class="ro-code">{{ c.code }}</i>
            <span class="ro-setup">{{ c.setup?.label }}</span>
            <span class="ro-ind">{{ c.industry }}</span>
          </span>
          <span class="ro-l2">
            <span>买 {{ fmtPrice(c.price) }} / 止 {{ fmtPrice(c.setup?.stop) }} / 目标 {{ fmtPrice(c.setup?.target) }}</span>
            <span class="ro-rr">盈亏比 {{ Number(c.setup?.rr || 0).toFixed(1) }}</span>
            <span class="ro-qty">最多 {{ c.max_qty }} 股</span>
          </span>
        </button>
      </div>

      <div v-if="watch.length" class="ro-group">
        <div class="ro-gh">观察候选<span class="rz-note">闸门未过或仓位不足，只观察不下单；点开看拦截原因</span></div>
        <button v-for="c in watch" :key="c.code" type="button" class="ro-row is-watch" :title="c.reason"
                @click="emit('check', buyPresetOf(c))">
          <span class="ro-l1">
            <b class="ro-name">{{ c.name }}</b><i class="ro-code">{{ c.code }}</i>
            <span class="ro-setup">{{ c.setup?.label }}</span>
            <span class="ro-ind">{{ c.industry }}</span>
          </span>
          <span class="ro-l2">
            <span>买 {{ fmtPrice(c.price) }} / 止 {{ fmtPrice(c.setup?.stop) }} / 目标 {{ fmtPrice(c.setup?.target) }}</span>
            <span class="ro-rr">盈亏比 {{ Number(c.setup?.rr || 0).toFixed(1) }}</span>
            <span class="ro-qty">观察</span>
          </span>
        </button>
      </div>

      <div v-if="cand && allowed && !items.length && !watch.length" class="ro-empty">
        {{ cand.reason || '当前没有同时通过四道闸门的标的' }}
        <span v-if="String(cand.reason || '').includes('关注池')">—— 把想跟踪的票加进自选，这里会自动扫描</span>
      </div>

      <!-- 被拦时给出下一步：仓位/纪律类 → 回持仓页处理；市场闸门类 → 今天只管持仓 -->
      <div v-if="next" class="ro-next" :class="'is-' + next.kind">
        <span class="ro-next-k">下一步</span>
        <span class="ro-next-t">{{ next.text }}</span>
        <n-button v-if="next.kind === 'reduce'" size="tiny" secondary @click="emit('go-portfolio')">去持仓处理</n-button>
      </div>

      <div class="ro-foot">
        <n-button size="tiny" title="任意个股过四道闸门：市场 / 板块 / 个股结构 / 风险" @click="emit('check', null)">
          <AppIcon name="target" :size="13" /> 买点检查
        </n-button>
        <n-button size="tiny" quaternary title="候选只扫关注池（持仓 ∪ 自选）" @click="emit('go-watch')">管理自选</n-button>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { NButton } from 'naive-ui'
import AppIcon from '../AppIcon.vue'
import { blockedNextStep, buyPresetOf, fmtPrice, opportunityGateNote } from '../../utils/realtime'

const props = defineProps({
  cand: { type: Object, default: null },   // /api/buy/candidates
  gate: { type: Object, default: null },   // /api/market/realtime 的 gate（实时空仓闸门，仅展示口径）
  loading: { type: Boolean, default: false },
  error: { type: Boolean, default: false },     // 最近一次扫描失败（保留上一帧结论时标出）
})
const emit = defineEmits(['check', 'go-watch', 'go-portfolio'])   // check(preset|null)

const allowed = computed(() => !!props.cand?.allowed)
const items = computed(() => props.cand?.items || [])
const watch = computed(() => props.cand?.watch || [])
const next = computed(() => blockedNextStep(props.cand))
// 被拦时决策线已带原因，不需要再加注；allowed 而实时闸门有结论/异常时才提示口径差异
const gateNote = computed(() => (allowed.value ? opportunityGateNote(props.gate) : ''))
</script>

<style scoped>
/* 闸门结论行：整行一条结论，先告诉「今天能不能找买点」 */
.ro-gate {
  display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
  margin: 10px 10px 0; padding: 7px 10px; font-size: var(--text-meta);
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
}
.ro-gate b { font-size: var(--text-body); white-space: nowrap; }
.ro-gate span { color: var(--c-text-dim); }
.ro-gate span.ro-gate-note { color: var(--c-warning); }
.ro-gate.is-open { border-left: 3px solid var(--c-info); }
.ro-gate.is-open b { color: var(--c-info); }
.ro-gate.is-block { border-left: 3px solid var(--c-error); background: color-mix(in srgb, var(--c-error) 5%, transparent); }
.ro-gate.is-block b { color: var(--c-error); }

.ro-group { padding: 8px 10px 2px; }
.ro-gh { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; margin-bottom: 2px; font-size: var(--text-meta); font-weight: 600; color: var(--c-text); }
.rz-note { font-size: var(--text-micro); font-weight: 400; color: var(--c-text-faint); }

.ro-row {
  display: flex; flex-direction: column; gap: 2px; width: 100%;
  margin: 0 0 5px; padding: 6px 8px; text-align: left; font: inherit; color: var(--c-text-dim);
  border: 1px solid var(--c-border); border-left: 3px solid var(--c-info); border-radius: var(--radius-sm);
  background: transparent; cursor: pointer;
}
.ro-row:hover { background: var(--c-card-bg-hover); border-color: var(--c-text-faint); border-left-color: var(--c-info); }
.ro-row:focus-visible { background: var(--c-card-bg-hover); border-color: var(--c-accent); border-left-color: var(--c-info); }
.ro-row.is-watch { border-left-color: var(--c-warning); }
.ro-row.is-watch .ro-setup { color: var(--c-warning); }
.ro-row.is-watch:hover { border-left-color: var(--c-warning); }
.ro-l1 { display: flex; align-items: baseline; gap: 6px; min-width: 0; }
.ro-name { font-size: var(--text-body); color: var(--c-text); white-space: nowrap; }
.ro-code { font-style: normal; font-size: var(--text-micro); color: var(--c-text-faint); }
.ro-setup { margin-left: auto; font-size: var(--text-micro); font-weight: 600; color: var(--c-info); white-space: nowrap; }
.ro-ind { font-size: var(--text-micro); color: var(--c-text-faint); white-space: nowrap; }
.ro-l2 { display: flex; align-items: baseline; gap: 10px; font-size: var(--text-micro); font-variant-numeric: tabular-nums; white-space: nowrap; overflow: hidden; }
.ro-rr, .ro-qty { flex-shrink: 0; }

.ro-empty { margin: 12px 10px 0; padding: 18px 10px; text-align: center; font-size: var(--text-meta); line-height: 1.7; color: var(--c-text-faint); }
.ro-empty span { display: block; }
/* 下一步：贴在底部操作栏上方，被拦时一眼看到该去哪 */
.ro-next {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin: auto 10px 0; padding: 8px 10px; font-size: var(--text-meta); color: var(--c-text-dim);
  border: 1px dashed var(--c-border); border-radius: var(--radius-sm);
}
.ro-next-k { font-weight: 600; color: var(--c-text); white-space: nowrap; }
.ro-next-t { flex: 1; min-width: 0; }
.ro-next.is-reduce { border-color: var(--c-warning); }
.ro-next.is-reduce .ro-next-k { color: var(--c-warning); }
.ro-foot { display: flex; align-items: center; gap: 8px; padding: 8px 10px 10px; border-top: 1px solid var(--c-border); }
.rt-zb { display: flex; flex-direction: column; }
</style>
