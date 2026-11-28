<template>
  <!-- 分析带：深度图表。默认资金地图（分时市值树图 + 行业资金流），可切指数分时 / 板块资金 / 涨停梯队 / 量能情绪 -->
  <section class="rt-band" aria-label="分析带">
    <header class="rb-h">
      <div class="rb-tabs" role="tablist" aria-label="分析视图">
        <button v-for="t in tabs" :key="t.k" type="button" class="rb-tab" :class="{ active: tab === t.k }"
                role="tab" :aria-selected="tab === t.k" @click="emit('update:tab', t.k)">{{ t.label }}</button>
      </div>
      <span class="rb-hint">{{ current.hint }}</span>
      <button v-if="!stacked" type="button" class="rb-zoom" :title="zoomed ? '还原三区与分析带' : '放大分析带，收起上方三区'"
              @click="emit('update:zoom', !zoomed)">
        <AppIcon :name="zoomed ? 'chevron-up' : 'chevron-down'" :size="12" /> {{ zoomed ? '还原' : '放大' }}
      </button>
    </header>

    <div class="rb-body">
      <!-- 资金地图：TreemapView 的分时市值模式（embedded）；日期跟盘面数据的所属交易日（休市日/盘前即最近交易日） -->
      <TreemapView v-if="tab === 'map' && mapDate" embedded :initial-date="mapDate"
                   @show-detail="(...a) => emit('show-detail', ...a)" @pick="onPick" />
      <div v-else-if="tab === 'map'" class="rb-wait">等待数据…</div>

      <template v-else-if="!d">
        <div class="rb-wait">等待数据…（后台每 60s 采集一次，开盘后约 1 分钟出数）</div>
      </template>

      <!-- 指数分时大图 -->
      <div v-else-if="tab === 'idx'" class="rb-pane">
        <div class="rb-pane-h">
          <button v-for="ix in idxList" :key="ix.code" type="button" class="rb-chip"
                  :class="{ active: bigIdx === ix.code }" @click="emit('update:bigIdx', ix.code)">
            {{ ix.name }} <b :class="chgCls(ix.chg_pct)">{{ signed(ix.chg_pct) }}%</b>
          </button>
          <span class="rb-hint">价格 + 昨收（虚线）· 腾讯分时</span>
        </div>
        <RtChart :option="bigOpt" fill />
      </div>

      <!-- 板块雷达 + 大盘主力资金 -->
      <div v-else-if="tab === 'boards'" class="rb-cols">
        <div class="rb-pane">
          <div class="rb-pane-h">
            <span>板块雷达</span><span class="rb-hint">东财行业口径（一级+细分混排，≠树图申万 L1）</span>
            <span class="rb-spacer" />
            <button type="button" class="rb-chip" :class="{ active: boardDim === 'chg' }" @click="boardDim = 'chg'">涨幅</button>
            <button type="button" class="rb-chip" :class="{ active: boardDim === 'net' }" @click="boardDim = 'net'">主力净流入</button>
          </div>
          <RtChart :option="boardsOpt" fill />
        </div>
        <div class="rb-pane">
          <div class="rb-pane-h">
            <span>大盘主力资金</span><span class="rb-hint">沪深两市当日累计净流入（东财）<template v-if="fflowDateText(d)"> · {{ fflowDateText(d) }}</template></span>
            <span class="rb-spacer" />
            <b v-if="d.fflow && d.fflow.latest != null" :class="chgCls(yiNum(d.fflow.latest))">当前主力累计 {{ yi(d.fflow.latest) }}</b>
          </div>
          <RtChart :option="fflowOpt" fill />
        </div>
      </div>

      <!-- 涨停梯队：天梯 + 高度历史 + 涨停池 -->
      <div v-else-if="tab === 'zt'" class="rb-zt">
        <div v-if="d.zt" class="rb-pane-h rb-zt-stat">
          <span>涨停 <b class="up">{{ d.zt.limit_up }}</b></span>
          <span>炸板 <b class="warn">{{ d.zt.broken }}</b></span>
          <span>连板高度 <b class="up">{{ d.zt.max_lbc || '—' }} 板</b></span>
          <span class="rb-hint">东财 push2ex 口径<template v-if="ztDateText(d)"> · {{ ztDateText(d) }}</template>；行点击进个股详情</span>
        </div>
        <div class="rb-cols rb-zt-charts">
          <div class="rb-pane"><div class="rb-sub">连板天梯</div><RtChart :option="ladderOpt" fill /></div>
          <div class="rb-pane"><div class="rb-sub">连板高度 / 涨停家数历史（zt_pool_daily）</div><RtChart :option="ztHistOpt" fill /></div>
        </div>
        <div class="rb-pool">
          <table v-if="poolRows.length" class="rb-table">
            <thead><tr><th>名称</th><th>连板</th><th>几天几板</th><th>封单额</th><th>首封</th><th>炸板次</th><th>行业</th></tr></thead>
            <tbody>
              <tr v-for="p in poolRows" :key="p.code" class="rb-row-link" tabindex="0"
                  @click="emit('show-detail', p.code)" @keydown.enter.prevent="emit('show-detail', p.code)">
                <td><b>{{ p.name }}</b> <span class="rb-code">{{ p.code }}</span></td>
                <td><b :class="p.lbc >= 3 ? 'up' : ''">{{ p.lbc }}</b></td>
                <td>{{ p.zttj || '—' }}</td>
                <td>{{ yi(p.fund) }}</td>
                <td>{{ p.fbt || '—' }}</td>
                <td :class="p.zbc > 0 ? 'warn' : ''">{{ p.zbc }}</td>
                <td>{{ p.hybk }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="rb-wait">涨停池待采集</div>
        </div>
        <div v-if="d.zt && d.zt.pool_truncated" class="rb-note">仅展示前 {{ poolRows.length }} 家（按连板/封单排序），全量见日终落库 zt_pool_daily</div>
      </div>

      <!-- 量能对比 + 情绪轨迹 -->
      <div v-else-if="tab === 'turn'" class="rb-cols">
        <div class="rb-pane">
          <div class="rb-pane-h">
            <span>量能对比</span>
            <template v-if="d.turnover">
              <span class="rb-stat">今日 <b>{{ yi(d.turnover.today_cum) }}</b></span>
              <span class="rb-stat">昨日同期 <b>{{ yi(d.turnover.yday_same) }}</b></span>
              <span class="rb-stat">近4日均 <b>{{ yi(d.turnover.avg_prev_same) }}</b></span>
              <span class="rb-stat">全天预估 <b class="hl">{{ yi(d.turnover.est_full) }}</b><i v-if="d.turnover.est_basis === 'linear'">（线性）</i></span>
            </template>
          </div>
          <RtChart :option="turnoverOpt" fill />
          <div class="rb-note">量能 = 上证综指 + 深证综指 逐分钟累计（亿元）；预估优先「近4日同期占比」口径</div>
        </div>
        <div class="rb-pane">
          <div class="rb-pane-h">
            <span>{{ trackTitle(d) }}</span>
            <span class="rb-hint">三成分口径（宽度/中位/涨跌停）· 与日频恐慌指数序列可比 · 采集缺口处断线</span>
            <span class="rb-spacer" />
            <span v-if="d.senti && d.senti.score != null" class="rb-stat">实时四成分分 <b>{{ d.senti.score }}</b> {{ d.senti.label }}</span>
          </div>
          <RtChart :option="trackOpt" fill />
          <div v-if="!trackOpt" class="rb-empty">该日暂无 10 分钟快照</div>
          <div class="rb-note">0=极度恐慌，100=极度亢奋；虚线为 45 分界与实时四成分参考</div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import AppIcon from '../AppIcon.vue'
import RtChart from './RtChart.vue'
import TreemapView from '../TreemapView.vue'
import { useChartOption } from '../../utils/realtimePage'
import { bigTrendOption, boardsOption, fflowOption, ladderOption, trackOption, turnoverOption, ztHistOption } from '../../utils/realtimeCharts'
import { chgCls, fflowDateText, signed, trackTitle, yi, yiNum, ztDateText } from '../../utils/realtime'

const props = defineProps({
  d: { type: Object, default: null },            // /api/market/realtime
  ztHist: { type: Array, default: () => [] },    // /api/market/realtime/zt-history days
  tab: { type: String, default: 'map' },
  tabs: { type: Array, default: () => [] },      // 当前用户可见的 tab（已按权限过滤，且保证含 tab）
  bigIdx: { type: String, default: '' },
  mapDate: { type: String, default: '' },        // 资金地图日期；空 = 盘面数据未到，先不挂树图
  zoomed: { type: Boolean, default: false },
  stacked: { type: Boolean, default: false },
})
const emit = defineEmits(['update:tab', 'update:bigIdx', 'update:zoom', 'show-detail', 'pick'])

const ALL_TABS = {
  map:    { label: '资金地图', hint: '分时市值树图 + 行业资金流 · 验证板块强弱；点叶子可直接做买点检查' },
  idx:    { label: '指数分时', hint: '价格 + 昨收（虚线）· 腾讯分时' },
  boards: { label: '板块与资金', hint: '板块强弱与主力方向 · 买点「板块闸门」的确认依据' },
  zt:     { label: '涨停梯队', hint: '连板天梯 / 高度历史 / 涨停池 · 东财 push2ex 口径' },
  turn:   { label: '量能与情绪', hint: '两市成交额今日 vs 昨日 vs 近4日均 · 当日情绪轨迹' },
}
const tabs = computed(() => (props.tabs.length ? props.tabs : Object.keys(ALL_TABS))
  .map(k => ({ k, label: ALL_TABS[k]?.label || k })))
const current = computed(() => ALL_TABS[props.tab] || { hint: '' })

const boardDim = ref('chg')
const idxList = computed(() => props.d?.indices_trend || [])
const bigData = computed(() => idxList.value.find(x => x.code === props.bigIdx) || idxList.value[0] || null)
const poolRows = computed(() => (props.d?.zt?.pool || []).slice(0, 60))

const bigOpt = useChartOption(() => bigTrendOption(bigData.value))
const boardsOpt = useChartOption(() => boardsOption(props.d?.boards, boardDim.value))
const fflowOpt = useChartOption(() => fflowOption(props.d?.fflow?.series))
const ladderOpt = useChartOption(() => ladderOption(props.d?.zt?.ladder))
const ztHistOpt = useChartOption(() => ztHistOption(props.ztHist))
const turnoverOpt = useChartOption(() => turnoverOption(props.d?.turnover))
const trackOpt = useChartOption(() => trackOption(props.d?.senti_track, props.d?.senti))

// 树图叶子：把坐标一并交给页面，由页面在点击处弹「买点检查 / 个股详情」
function onPick(p) {
  emit('pick', p)
}
</script>

<style scoped>
.rt-band {
  display: flex; flex-direction: column; flex-shrink: 0; min-height: 0;
  background: var(--c-card-bg); border: 1px solid var(--c-border); border-radius: var(--radius); overflow: hidden;
}
.rb-h {
  display: flex; align-items: center; gap: 12px; min-height: 40px; flex-shrink: 0;
  padding: 0 8px 0 6px; border-bottom: 1px solid var(--c-border);
}
/* tab 下划线沿用顶部导航的激活样式 */
.rb-tabs { display: flex; align-items: stretch; align-self: stretch; }
.rb-tab {
  position: relative; padding: 0 12px; border: 0; background: transparent; cursor: pointer;
  font: inherit; font-size: var(--text-body); color: var(--c-text-dim); white-space: nowrap;
}
.rb-tab:hover { color: var(--c-text); }
.rb-tab.active { color: var(--c-accent); font-weight: 600; }
.rb-tab.active::after {
  content: ''; position: absolute; left: 10px; right: 10px; bottom: 0; height: 2px;
  border-radius: 1px; background: var(--c-accent);
}
.rb-hint { min-width: 0; font-size: var(--text-micro); color: var(--c-text-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rb-zoom {
  display: inline-flex; align-items: center; gap: 4px; margin-left: auto; flex-shrink: 0;
  padding: 3px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: transparent; color: var(--c-text-dim); font: inherit; font-size: var(--text-meta); cursor: pointer; white-space: nowrap;
}
.rb-zoom:hover { color: var(--c-text); border-color: var(--c-text-faint); background: var(--c-card-bg-hover); }

.rb-body { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
.rb-pane { position: relative; flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; padding: 6px 12px 8px; }
.rb-cols { flex: 1; min-height: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding-top: 4px; }
.rb-pane-h { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; flex-shrink: 0; min-height: 28px; font-size: var(--text-body); font-weight: 600; color: var(--c-text); }
.rb-sub { font-size: var(--text-meta); font-weight: 400; color: var(--c-text-dim); }
.rb-spacer { flex: 1; }
.rb-stat { font-size: var(--text-meta); font-weight: 400; color: var(--c-text-dim); }
.rb-stat b { color: var(--c-text); font-weight: 600; font-variant-numeric: tabular-nums; }
.rb-stat b.hl { color: var(--c-text); }
.rb-stat i { font-style: normal; color: var(--c-text-faint); }
.rb-note { flex-shrink: 0; padding: 4px 2px 0; font-size: var(--text-micro); color: var(--c-text-faint); }
.rb-chip {
  display: inline-flex; align-items: baseline; gap: 5px; padding: 2px 9px;
  border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: transparent;
  font: inherit; font-size: var(--text-meta); font-weight: 400; color: var(--c-text-dim); cursor: pointer; white-space: nowrap;
}
.rb-chip b { font-weight: 600; font-variant-numeric: tabular-nums; }
.rb-chip:hover { color: var(--c-text); border-color: var(--c-text-faint); }
.rb-chip.active { color: var(--c-accent); border-color: var(--c-accent); background: var(--c-accent-weak); }

/* 涨停梯队：上图下表。天梯至少 150px——再矮三行连板的标签会叠在一起 */
.rb-zt { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 6px 12px 8px; }
.rb-zt-stat { padding-bottom: 4px; border-bottom: 1px solid var(--c-border); }
.rb-zt-stat b { font-weight: 600; font-variant-numeric: tabular-nums; }
.rb-zt-charts { flex: 3 1 0; min-height: 150px; }
.rb-pool { flex: 2 1 0; min-height: 0; overflow: auto; border-top: 1px solid var(--c-border); }
.rb-table { width: 100%; border-collapse: collapse; font-size: var(--text-meta); }
.rb-table th { position: sticky; top: 0; z-index: 1; background: var(--c-card-bg); text-align: left; color: var(--c-text-faint); font-weight: 400; padding: 5px 8px; border-bottom: 1px solid var(--c-border); white-space: nowrap; }
.rb-table td { padding: 4px 8px; border-bottom: 1px solid var(--c-border); color: var(--c-text-dim); white-space: nowrap; font-variant-numeric: tabular-nums; }
.rb-table td b { color: var(--c-text); }
.rb-row-link { cursor: pointer; }
.rb-row-link:hover td { background: var(--c-bg); }
.rb-row-link:focus-visible td { background: var(--c-bg); }
.rb-row-link:focus-visible td:first-child { box-shadow: inset 2px 0 0 var(--c-accent); }
.rb-code { color: var(--c-text-faint); font-size: var(--text-micro); }

/* 图表无数据时的占位（图表容器保留，不卸载） */
.rb-empty { position: absolute; inset: 34px 12px 28px; display: flex; align-items: center; justify-content: center; font-size: var(--text-meta); color: var(--c-text-faint); pointer-events: none; }
.rb-wait { flex: 1; display: flex; align-items: center; justify-content: center; padding: 24px; font-size: var(--text-meta); color: var(--c-text-faint); }

@media (max-width: 1100px) { .rb-cols { grid-template-columns: 1fr; } }
.up { color: var(--c-up); }
.down { color: var(--c-down); }
.warn { color: var(--c-warning); }
</style>
