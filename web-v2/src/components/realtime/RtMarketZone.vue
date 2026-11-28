<template>
  <!-- 盘面区（决策顺序 ①）：今天是什么环境——指数分时、宽度、盘面异动。深度图表在分析带 -->
  <section class="rt-zone" aria-label="盘面">
    <header class="rt-zh">
      <div class="rt-zt"><b>盘面</b><span>先看环境：能不能做</span></div>
      <div class="rt-zr">
        <span v-if="d && d.senti && d.senti.score != null" class="rz-chip" :title="'实时四成分分（含指数 VWAP）：' + d.senti.label">
          情绪 <b>{{ d.senti.score }}</b>{{ d.senti.label }}
        </span>
        <span v-if="d && d.breadth" class="rz-chip" :title="`涨 ${d.breadth.up} / 平 ${d.breadth.flat} / 跌 ${d.breadth.down}`">
          涨 <b class="up">{{ d.breadth.up }}</b> / 跌 <b class="down">{{ d.breadth.down }}</b>
        </span>
      </div>
    </header>

    <div class="rt-zb">
      <template v-if="d">
        <!-- 指数分时：四张小图，点开在分析带看大图 -->
        <div class="rm-sec">
          <div class="rm-sh"><span>指数分时</span><span class="rm-meta">点击小图放大</span></div>
          <div class="rm-idx">
            <button v-for="ix in idxList" :key="ix.code" type="button" class="rm-idx-cell"
                    :title="`${ix.name} 分时：点击在下方分析带放大`"
                    @click="emit('open-tab', 'idx', ix.code)">
              <span class="rm-idx-h"><span class="rm-idx-n">{{ ix.name }}</span><b :class="chgCls(ix.chg_pct)">{{ signed(ix.chg_pct) }}%</b></span>
              <RtChart :option="miniOptions[ix.code] || null" height="58px" />
            </button>
            <div v-if="!idxList.length" class="rm-empty rm-inline">分时数据待采集</div>
          </div>
        </div>

        <!-- 盘面宽度 -->
        <div class="rm-sec">
          <div class="rm-sh">
            <span>盘面宽度</span>
            <button type="button" class="rm-link" @click="emit('open-tab', 'turn')">量能对比 →</button>
          </div>
          <template v-if="d.breadth">
            <div class="rm-bar" :title="`涨 ${d.breadth.up} / 平 ${d.breadth.flat} / 跌 ${d.breadth.down}`">
              <i class="up" :style="{ width: pctW(d.breadth.up) }"></i>
              <i class="flat" :style="{ width: pctW(d.breadth.flat) }"></i>
              <i class="down" :style="{ width: pctW(d.breadth.down) }"></i>
            </div>
            <div class="rm-stat">
              <span>涨停 <b class="up">{{ d.breadth.limit_up }}</b></span>
              <span>跌停 <b class="down">{{ d.breadth.limit_down }}</b></span>
              <span>炸板 <b class="warn">{{ d.zt ? d.zt.broken : '—' }}</b></span>
              <span>中位 <b :class="chgCls(d.breadth.median)">{{ signed(d.breadth.median) }}%</b></span>
            </div>
            <div v-if="d.turnover" class="rm-stat">
              <span>两市成交 <b>{{ yi(d.turnover.today_cum) }}</b></span>
              <span>昨日同期 <b>{{ yi(d.turnover.yday_same) }}</b></span>
              <span>全天预估 <b class="hl">{{ yi(d.turnover.est_full) }}</b><i v-if="d.turnover.est_basis === 'linear'" class="rm-basis">（线性）</i></span>
            </div>
          </template>
          <div v-else class="rm-empty rm-inline">宽度数据待采集</div>
        </div>

        <!-- 今日盘面异动：WS 同步推送 + 此列表回看 -->
        <div class="rm-sec rm-sec-grow">
          <div class="rm-sh">
            <span>今日盘面异动</span>
            <button type="button" class="rm-link" @click="emit('open-tab', 'boards')">板块 · 梯队 →</button>
          </div>
          <ul v-if="alerts.length" class="rm-alerts" ref="alertsEl">
            <li v-for="a in alerts" :key="a.id" class="rm-alert" :class="{ crit: a.level === 'critical' }">
              <span class="rm-alert-t">{{ a.time || '—' }}</span>
              <span class="rm-mk">{{ mkLabel(a.kind) }}</span>
              <span class="rm-alert-b"><b>{{ a.title }}</b> {{ a.body }}</span>
            </li>
          </ul>
          <div v-else class="rm-empty rm-inline">今日暂无异动告警</div>
        </div>
      </template>
      <div v-else class="rm-empty rm-wait">等待数据…（后台每 60s 采集一次，开盘后约 1 分钟出数）</div>
    </div>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import RtChart from './RtChart.vue'
import { useChartOption } from '../../utils/realtimePage'
import { miniTrendOption } from '../../utils/realtimeCharts'
import { chgCls, mkLabel, signed, yi } from '../../utils/realtime'

const props = defineProps({ d: { type: Object, default: null } })
const emit = defineEmits(['open-tab'])   // (tab, payload?)

const idxList = computed(() => props.d?.indices_trend || [])
const alerts = computed(() => props.d?.alerts_today || [])
const miniOptions = useChartOption(() => Object.fromEntries(
  idxList.value.map(ix => [ix.code, miniTrendOption(ix)])))

function pctW(n) {
  const t = Number(props.d?.breadth?.total) || 1
  return (Number(n) / t * 100).toFixed(2) + '%'
}

// 决策线「今日异动」点击：把异动列表滚进视野
const alertsEl = ref(null)
function revealAlerts() {
  const el = alertsEl.value
  if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' })
}
defineExpose({ revealAlerts })
</script>

<style scoped>
.rm-sec { padding: 8px 12px 10px; border-bottom: 1px solid var(--c-border); flex-shrink: 0; }
.rm-sec:last-child { border-bottom: 0; }
.rm-sec-grow { flex: 1 1 auto; min-height: 96px; display: flex; flex-direction: column; }
.rm-sh { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 6px; font-size: var(--text-body); font-weight: 600; color: var(--c-text); }
.rm-meta { font-size: var(--text-micro); font-weight: 400; color: var(--c-text-faint); }
.rm-link {
  padding: 0; border: 0; background: transparent; cursor: pointer;
  font-size: var(--text-meta); font-weight: 400; color: var(--c-accent);
}
.rm-link:hover { color: var(--c-accent-hover, var(--c-accent)); text-decoration: underline; text-underline-offset: 2px; }

/* 指数小图：四张并排，整块可点。栏宽不足 480（单列堆叠在手机上）时改 2×2，别把指数名挤成一个字 */
.rm-sec { container-type: inline-size; }
.rm-idx { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; }
@container (max-width: 480px) {
  .rm-idx { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
.rm-idx-cell {
  display: flex; flex-direction: column; min-width: 0; padding: 4px 6px 0;
  border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: transparent;
  cursor: pointer; text-align: left; font: inherit; color: inherit;
}
.rm-idx-cell:hover { border-color: var(--c-accent); background: var(--c-card-bg-hover); }
.rm-idx-h { display: flex; align-items: baseline; justify-content: space-between; gap: 4px; font-size: var(--text-micro); color: var(--c-text-dim); }
.rm-idx-n { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rm-idx-h b { font-variant-numeric: tabular-nums; font-weight: 600; white-space: nowrap; }

/* 宽度：比例条 + 两组数字 */
.rm-bar { display: flex; height: 12px; border-radius: 3px; overflow: hidden; margin: 2px 0 6px; }
.rm-bar i { display: block; height: 100%; }
.rm-bar i.up { background: var(--c-up); }
.rm-bar i.down { background: var(--c-down); }
.rm-bar i.flat { background: var(--c-text-faint); opacity: 0.4; }
.rm-stat { display: flex; flex-wrap: wrap; gap: 2px 14px; font-size: var(--text-meta); color: var(--c-text-dim); line-height: 1.7; }
.rm-stat b { color: var(--c-text); font-weight: 600; font-variant-numeric: tabular-nums; }
.rm-stat b.hl { color: var(--c-text); }
.rm-basis { font-style: normal; color: var(--c-text-faint); }

/* 异动流 */
.rm-alerts { flex: 1; min-height: 0; margin: 0; padding: 0; list-style: none; overflow-y: auto; }
.rm-alert { display: grid; grid-template-columns: 44px 74px minmax(0, 1fr); gap: 8px; align-items: baseline; padding: 4px 2px; font-size: var(--text-meta); color: var(--c-text-dim); border-top: 1px dashed var(--c-border); }
.rm-alert:first-child { border-top: 0; }
.rm-alert-t { color: var(--c-text-faint); font-variant-numeric: tabular-nums; }
.rm-alert-b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rm-alert-b b { color: var(--c-text); font-weight: 600; }
.rm-alert.crit .rm-alert-b b { color: var(--c-up); }
.rm-mk { padding: 0 5px; border: 1px solid var(--c-border); border-radius: 3px; text-align: center; white-space: nowrap; }
.rm-alert.crit .rm-mk { color: var(--c-up); border-color: var(--c-up); }

.rm-empty { color: var(--c-text-faint); font-size: var(--text-meta); text-align: center; }
.rm-inline { padding: 14px 0; }
.rm-wait { flex: 1; display: flex; align-items: center; justify-content: center; min-height: 160px; padding: 20px; }

.up { color: var(--c-up); }
.down { color: var(--c-down); }
.warn { color: var(--c-warning); }
</style>
