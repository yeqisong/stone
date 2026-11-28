<template>
  <!-- 持仓区（决策顺序 ②）：先处理风险——持仓动作置顶，关注池雷达表持仓优先 -->
  <section class="rt-zone" aria-label="持仓">
    <header class="rt-zh">
      <div class="rt-zt"><b>持仓</b><span>先处理风险：止损 / 减仓 / 持有</span></div>
      <div class="rt-zr">
        <span v-for="c in chips" :key="c.key" class="rz-chip" :class="[{ 'rz-hot': c.n > 0 }, 'rz-' + c.key]">{{ c.label }} <b>{{ c.n }}</b></span>
        <span v-if="error" class="rz-chip rz-err" title="雷达快照失败，15s 后自动重试；下方是上一帧数据">快照失败</span>
      </div>
    </header>

    <div class="rt-zb rt-col">
      <div v-if="holdings.length" class="rh-signals">
        <div class="rh-sub">持仓信号<span class="rz-note">把日内涨跌翻译成动作；买点仅观察，不开新仓</span></div>
        <div v-for="s in holdings" :key="s.code" class="rh-row" :class="'rh-' + s.signal.level" role="button" tabindex="0"
             :title="s.signal.reason" @click="openDetail(s.code)" @keydown.enter.prevent="openDetail(s.code)">
          <span class="rh-tag" :style="{ background: signalMeta(s.signal.action).color }">{{ signalMeta(s.signal.action).label }}</span>
          <span class="rh-name">{{ s.name }}</span>
          <span class="rh-chg" :class="(s.change_pct || 0) >= 0 ? 'up' : 'down'">{{ radarPct(s.change_pct) }}</span>
          <span class="rh-cost">成本 {{ fmtPrice(s.cost) }} ·
            <b :class="(s.vs_cost_pct || 0) >= 0 ? 'up' : 'down'">{{ radarPct((s.vs_cost_pct || 0) * 100) }}</b></span>
          <span class="rh-weight" title="单票市值 / 账户权益">{{ ((s.weight || 0) * 100).toFixed(1) }}%</span>
          <span class="rh-reason">{{ s.signal.reason }}</span>
        </div>
      </div>

      <div class="rh-sub">
        <span>关注池实时排序</span>
        <span class="rz-note">{{ asof || '—' }} · 15s 刷新 · 持仓置顶</span>
        <button type="button" class="rz-toggle" :class="{ on: onlyHoldings }" :title="onlyHoldings ? '显示全部关注池' : '只看持仓'"
                @click="onlyHoldings = !onlyHoldings">{{ onlyHoldings ? '看全池' : '只看持仓' }}</button>
      </div>
      <n-data-table class="rh-table" :class="{ 'rh-table-fill': !stacked }" :flex-height="!stacked"
                    :max-height="stacked ? 520 : undefined"
                    :columns="columns" :data="tableRows" size="small" :row-props="rowProps" :loading="loading"
                    :bordered="false" scroll-x="1040" />
      <n-empty v-if="!loading && !rows.length" description="关注池暂无实时快照" style="padding:20px" />
    </div>
  </section>
</template>

<script setup>
import { computed, h, ref } from 'vue'
import { NDataTable, NEmpty } from 'naive-ui'
import { fmtPrice, holdingCounts, holdingsBySeverity, radarPct, radarRowsRiskFirst, radarScoreLabel, signalMeta } from '../../utils/realtime'

const props = defineProps({
  rows: { type: Array, default: () => [] },     // /api/stock/radar items
  asof: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  stacked: { type: Boolean, default: false },   // 堆叠布局：表格定高内滚，不参与分区 flex
  error: { type: Boolean, default: false },     // 最近一次快照失败（保留上一帧时标出）
})
const emit = defineEmits(['show-detail'])

const onlyHoldings = ref(false)
const holdings = computed(() => holdingsBySeverity(props.rows))
const counts = computed(() => holdingCounts(props.rows))
const chips = computed(() => [
  { key: 'stop', label: '止损', n: counts.value.stop },
  { key: 'reduce', label: '减仓', n: counts.value.reduce },
  { key: 'watch', label: '观察', n: counts.value.watch },
  { key: 'hold', label: '持有', n: counts.value.hold },
])
// 持仓置顶（风险优先），组内沿用后端 score 降序
const tableRows = computed(() => {
  const all = radarRowsRiskFirst(props.rows)
  return onlyHoldings.value ? all.filter(r => r.is_holding) : all
})

// 行点击、名称省略（render 列 ellipsis 需自控）
const ell = (txt, extra = {}) => {
  const s = String(txt ?? '')
  return h('span', { style: { display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '100%', ...extra } }, s || '—')
}
const codes = computed(() => tableRows.value.map(r => r.code))
function openDetail(code) {
  if (!code) return
  emit('show-detail', code, codes.value)
}
function rowProps(row) {
  return { style: 'cursor:pointer', onClick: () => openDetail(row.code) }
}
const numCell = (txt, color) => h('span', { style: { fontVariantNumeric: 'tabular-nums', color } }, txt)

// 窄栏按「决策需要的先后」排：价/涨跌/信号/雷达分靠前，结构指标次之，行业/成交额放最右
const columns = computed(() => [
  { title: '代码', key: 'code', width: 82, fixed: 'left',
    render: r => h('span', { style: { color: 'var(--c-text-dim)' } }, r.code) },
  { title: '名称', key: 'name', width: 112, fixed: 'left',
    render: r => ell(r.name, { color: 'var(--c-text)', fontWeight: 600, maxWidth: '104px' }) },
  { title: '最新价', key: 'price', width: 92, align: 'right',
    render: r => numCell(r.price != null ? '¥' + Number(r.price).toFixed(2) : '—', 'var(--c-text)') },
  { title: '涨跌幅', key: 'change_pct', width: 88, align: 'right',
    render: r => numCell(radarPct(r.change_pct), r.change_pct >= 0 ? 'var(--c-up)' : 'var(--c-down)') },
  { title: '信号', key: 'signal', width: 92, align: 'center',
    render: r => {
      if (!r.is_holding || !r.signal) return h('span', { style: { color: 'var(--c-text-faint)' } }, '—')
      const m = signalMeta(r.signal.action)
      return h('span', { title: r.signal.reason, style: { color: m.color, fontWeight: 600 } }, m.label)
    } },
  { title: '雷达分', key: 'score', width: 112,
    render: r => numCell(`${r.score.toFixed(1)} ${radarScoreLabel(r.score)}`,
      r.score >= 60 ? 'var(--c-up)' : r.score <= 40 ? 'var(--c-down)' : 'var(--c-text-dim)') },
  { title: 'VWAP偏离', key: 'vwap_dev_pct', width: 96, align: 'right',
    render: r => numCell(radarPct(r.vwap_dev_pct), r.vwap_dev_pct >= 0 ? 'var(--c-up)' : 'var(--c-down)') },
  { title: '日位置', key: 'day_position_pct', width: 80, align: 'right',
    render: r => numCell(r.day_position_pct != null ? r.day_position_pct.toFixed(0) + '%' : '—', 'var(--c-text)') },
  { title: '量比', key: 'volume_ratio', width: 68, align: 'right',
    render: r => numCell(r.volume_ratio != null ? Number(r.volume_ratio).toFixed(2) : '—', 'var(--c-text)') },
  { title: '跳空', key: 'gap_pct', width: 80, align: 'right',
    render: r => numCell(radarPct(r.gap_pct), r.gap_pct >= 0 ? 'var(--c-up)' : 'var(--c-down)') },
  { title: '成交额', key: 'amount', width: 88, align: 'right',
    render: r => numCell(r.amount != null ? (r.amount / 1e8).toFixed(2) + '亿' : '—', 'var(--c-text)') },
  { title: '行业', key: 'industry', width: 120, render: r => ell(r.industry, { maxWidth: '110px' }) },
])
</script>

<style scoped>
.rt-col { display: flex; flex-direction: column; overflow: hidden; }

/* 持仓信号块：最多占分区 46%，把剩余高度留给雷达表 */
.rh-signals {
  flex: 0 1 auto; min-height: 0; max-height: 46%; overflow-y: auto;
  padding: 6px 10px 4px; border-bottom: 1px solid var(--c-border);
}
.rh-sub {
  display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap;
  padding: 6px 10px; font-size: var(--text-body); font-weight: 600; color: var(--c-text);
}
.rz-note { font-size: var(--text-micro); font-weight: 400; color: var(--c-text-faint); }
.rz-toggle {
  margin-left: auto; padding: 0 6px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: transparent; font-size: var(--text-micro); color: var(--c-text-dim); cursor: pointer;
}
.rz-toggle:hover { color: var(--c-text); border-color: var(--c-text-faint); }
.rz-toggle.on { color: var(--c-accent); border-color: var(--c-accent); background: var(--c-accent-weak); }

.rh-row {
  display: grid; grid-template-columns: 60px 64px minmax(0, 1fr) 70px;
  gap: 6px 8px; align-items: center; padding: 5px 2px;
  font-size: var(--text-meta); color: var(--c-text-dim); cursor: pointer; border-top: 1px dashed var(--c-border);
}
.rh-sub + .rh-row { border-top: 0; }
.rh-row:hover, .rh-row:focus-visible { background: var(--c-card-bg-hover); outline: none; }
.rh-row.rh-critical { box-shadow: inset 2px 0 0 var(--c-error); padding-left: 8px; }
.rh-row.rh-warn { box-shadow: inset 2px 0 0 var(--c-warning); padding-left: 8px; }
.rh-tag {
  justify-self: start; padding: 0 6px; border-radius: 3px; font-size: var(--text-micro);
  line-height: 18px; color: var(--accent-ink); font-weight: 600; text-align: center; white-space: nowrap;
}
.rh-name { font-weight: 600; color: var(--c-text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rh-chg, .rh-cost, .rh-weight { font-variant-numeric: tabular-nums; white-space: nowrap; }
.rh-chg { text-align: right; }
.rh-cost b { font-weight: 600; }
.rh-weight { text-align: right; color: var(--c-text-faint); }
.rh-reason { grid-column: 1 / -1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--c-text-faint); }

.rh-table { flex-shrink: 0; }
.rh-table-fill { flex: 1; min-height: 0; }
.rh-table :deep(.n-data-table-td) { padding-top: 6px !important; padding-bottom: 6px !important; }
.up { color: var(--c-up); }
.down { color: var(--c-down); }
</style>
