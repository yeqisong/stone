<template>
  <!-- 右边缘入口：可转债辅导侧栏 -->
  <div class="cb-coach-edge" role="button" tabindex="0" aria-label="打开可转债辅导侧栏"
    title="可转债辅导：投资要领 / 建议 / 监控池"
    @click="open" @keydown.enter.prevent="open" @keydown.space.prevent="open">
    <div class="cc-tab">
      <span class="cc-tab-icon"><AppIcon name="compass" :size="14" /></span>
      <span class="cc-tab-label">转债辅导<span v-if="focusCount" class="cc-tab-count">{{ focusCount }}</span></span>
    </div>
  </div>

  <n-drawer v-model:show="show" placement="right" :width="isNarrow ? '94%' : 430">
    <n-drawer-content closable>
      <template #header>
        <div class="cc-head">
          <span class="cc-head-icon"><AppIcon name="bond" :size="16" /></span>
          <div>
            <div class="cc-head-title">可转债辅导</div>
            <div class="cc-head-sub">数据 {{ asof || '—' }} · 活跃 {{ active || '—' }} 只 · 规则筛选</div>
          </div>
        </div>
      </template>

      <div class="cc-body">
        <div class="cc-hero">
          <div class="cc-hero-line">
            <span>溢价 → 双低 → 正股 → 强赎/到期</span>
            <b>{{ active || '—' }} 只</b>
          </div>
          <div class="cc-hero-note">{{ suggestion || '加载中…' }}</div>
          <div class="cc-stats">
            <span>中位价 <b>{{ fmtNum(medianPrice) }}</b></span>
            <span>中位溢价 <b>{{ fmtPct(medianPremiumPct) }}</b></span>
            <span>低溢价 {{ counts.low_premium ?? '—' }}</span>
            <span>双低 {{ counts.double_low ?? '—' }}</span>
          </div>
        </div>

        <div class="cc-section">
          <div class="cc-section-title">投资要领</div>
          <div class="cc-guide">
            <details v-for="g in GUIDES" :key="g.title" class="cc-guide-item" :open="g.open">
              <summary>{{ g.title }}</summary>
              <p>{{ g.desc }}</p>
              <ul>
                <li v-for="p in g.points" :key="p">{{ p }}</li>
              </ul>
            </details>
          </div>
        </div>

        <div class="cc-section">
          <div class="cc-section-head">
            <div class="cc-section-title">可关注监控池</div>
            <n-button size="tiny" text :loading="loading" @click="load">刷新</n-button>
          </div>
          <div v-if="focus.length" class="cc-rows">
            <button v-for="x in focus" :key="x.bond_code" type="button" class="cc-row"
              :title="(x.reasons || []).join('；')" @click="openBond(x)">
              <div class="cc-row-main">
                <span class="cc-name">{{ x.bond_name }}</span>
                <span class="cc-code">{{ x.bond_code }}</span>
                <b class="cc-price">{{ x.price != null ? x.price.toFixed(2) : '—' }}</b>
                <span class="cc-prem" :class="premClass(x.premium_rate)">{{ premText(x.premium_rate) }}</span>
                <span class="cc-score">{{ x.score }} · {{ x.label }}</span>
              </div>
              <div class="cc-row-triggers">
                {{ (x.watch_triggers || []).join('；') || '暂无触发条件' }}
              </div>
            </button>
          </div>
          <n-empty v-else description="暂无可关注标的" style="padding:10px" />
        </div>

        <div class="cc-section">
          <div class="cc-section-head">
            <div class="cc-section-title">风险观察池</div>
            <span class="cc-note">只复盘，不建议介入</span>
          </div>
          <div v-if="risk.length" class="cc-rows">
            <button v-for="x in risk" :key="x.bond_code" type="button" class="cc-row cc-risk"
              :title="(x.risk_flags || []).join('；')" @click="openBond(x)">
              <div class="cc-row-main">
                <span class="cc-name">{{ x.bond_name }}</span>
                <span class="cc-code">{{ x.bond_code }}</span>
                <b class="cc-price">{{ x.price != null ? x.price.toFixed(2) : '—' }}</b>
                <span class="cc-risk-tags">{{ (x.risk_flags || []).join(' / ') || '评分低' }}</span>
              </div>
              <div class="cc-row-triggers">
                {{ (x.watch_triggers || []).join('；') || '风险标记，等待数据修复' }}
              </div>
            </button>
          </div>
          <n-empty v-else description="暂无风险观察标的" style="padding:10px" />
        </div>

        <div class="cc-disclaimer">
          以上为规则筛选与监控条件，不构成投资建议；实际操作请结合仓位与止损纪律。
        </div>
      </div>

      <template #footer>
        <n-button size="tiny" quaternary @click="show = false">收起</n-button>
      </template>
    </n-drawer-content>
  </n-drawer>
</template>

<script setup>
import { computed, ref } from 'vue'
import { NButton, NDrawer, NDrawerContent, NEmpty } from 'naive-ui'
import axios from 'axios'
import AppIcon from './AppIcon.vue'
import { useNavStore } from '../stores/nav'
import { useViewport } from '../utils/viewport'

const nav = useNavStore()
const { isNarrow } = useViewport()
const API = window.location.origin
const show = ref(false)
const loading = ref(false)
const asof = ref('')
const active = ref(null)
const medianPrice = ref(null)
const medianPremiumPct = ref(null)
const counts = ref({})
const suggestion = ref('')
const focus = ref([])
const risk = ref([])
const focusCount = computed(() => focus.value.length)

const GUIDES = [
  {
    title: '先看溢价，再看价格',
    desc: '同样 120 元的转债，溢价 10% 和 50% 完全不是同一种资产。',
    points: [
      '低溢价：跟正股更紧，适合跟随正股动量',
      '高溢价：更接近“情绪票”，回撤时通常更快',
      '溢价升破阈值时，优先降级观察',
    ],
  },
  {
    title: '双低是筛选，不是买入信号',
    desc: '双低把“价格+溢价”压到一个可比较的尺度，但仍要看正股和规模。',
    points: [
      '双低低 ≠ 一定涨，只是相对便宜',
      '剩余规模太小容易被流动性放大波动',
      '双低升破阈值要降级，不要硬拿',
    ],
  },
  {
    title: '正股动量决定进攻债的弹性',
    desc: '进攻债本质是“正股强 + 溢价不高”。正股走弱时，溢价会迅速吃掉安全边际。',
    points: [
      '正股在 MA20 上方，弹性债更可信',
      '正股跌破 MA20，先降级观察',
      '强赎线附近要看是否有强赎公告风险',
    ],
  },
  {
    title: '防守债看债底保护',
    desc: '防守不是“低价就行”，而是价格离纯债价值不要太远。',
    points: [
      '纯债溢价低，下跌时有债底接住',
      '价格过低但临近到期，时间价值反而衰减',
      '纯债溢价快速抬升，说明债底逻辑失效',
    ],
  },
  {
    title: '强赎/到期是隐形地雷',
    desc: '转债最大的非线性风险来自条款，而不是价格波动。',
    points: [
      '正股站上强赎线，要盯公告',
      '临近到期，时间价值衰减且流动性变差',
      '剩余规模过小，容易被资金操纵或退市',
    ],
  },
]

function open() {
  show.value = true
  if (!focus.value.length) load()
}

async function load() {
  loading.value = true
  try {
    const r = await axios.get(API + '/api/cb/coach')
    asof.value = r.data.asof || ''
    active.value = r.data.active ?? null
    medianPrice.value = r.data.median_price ?? null
    medianPremiumPct.value = r.data.median_premium_pct ?? null
    counts.value = r.data.strategy_counts || {}
    suggestion.value = r.data.suggestion || ''
    focus.value = r.data.focus || []
    risk.value = r.data.risk || []
  } catch (e) {
    suggestion.value = '辅导数据加载失败，请稍后重试。'
  } finally {
    loading.value = false
  }
}

function openBond(x) {
  if (x?.bond_code) nav.showBondDetail(x.bond_code)
}

function fmtNum(v) {
  return v == null ? '—' : Number(v).toFixed(2)
}
function fmtPct(v) {
  return v == null ? '—' : v.toFixed(1) + '%'
}
function premClass(v) {
  if (v == null) return ''
  const p = v * 100
  if (p <= 20) return 'up'
  if (p >= 80) return 'down'
  return ''
}
function premText(v) {
  return v == null ? '—' : (v * 100).toFixed(1) + '%'
}
</script>

<style>
.cb-coach-edge {
  position: fixed; right: 0; top: 128px; z-index: 900;
  cursor: pointer;
}
.cb-coach-edge .cc-tab {
  display: flex; align-items: center; gap: 5px;
  padding: 8px 9px 8px 8px;
  border: 1px solid var(--c-border);
  border-left: 3px solid var(--c-accent);
  border-right: 0; border-radius: 8px 0 0 8px;
  background: var(--c-bg-header); color: var(--c-text-dim);
  font-size: var(--text-meta); box-shadow: -2px 0 8px var(--c-shadow-color);
}
.cb-coach-edge:hover .cc-tab { color: var(--c-text); background: var(--c-card-bg-hover); }
.cc-tab-count {
  min-width: 16px; height: 16px; padding: 0 3px; border-radius: 999px;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--c-accent); color: #fff; font-size: 10px;
}
.cc-head { display: flex; align-items: center; gap: 8px; }
.cc-head-icon {
  width: 30px; height: 30px; border-radius: 8px; display: inline-flex;
  align-items: center; justify-content: center;
  background: var(--c-accent); color: #fff;
}
.cc-head-title { font-weight: 600; color: var(--c-text); }
.cc-head-sub { font-size: var(--text-meta); color: var(--c-text-faint); }
.cc-body { display: flex; flex-direction: column; gap: 14px; }
.cc-hero {
  padding: 10px 12px; border-radius: var(--radius);
  background: var(--c-card-bg-hover); border: 1px solid var(--c-border);
}
.cc-hero-line { display: flex; justify-content: space-between; gap: 8px; color: var(--c-text-dim); font-size: var(--text-meta); }
.cc-hero-line b { color: var(--c-text); }
.cc-hero-note { margin-top: 6px; font-size: var(--text-meta); line-height: 1.6; color: var(--c-text); }
.cc-stats { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px 12px; font-size: var(--text-meta); color: var(--c-text-dim); }
.cc-stats b { color: var(--c-text); font-variant-numeric: tabular-nums; }
.cc-section-title { font-weight: 600; color: var(--c-text); margin-bottom: 6px; }
.cc-section-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.cc-note { font-size: var(--text-meta); color: var(--c-text-faint); }
.cc-guide { display: flex; flex-direction: column; gap: 6px; }
.cc-guide-item { border: 1px solid var(--c-border-light); border-radius: 6px; padding: 6px 8px; background: var(--c-card-bg); }
.cc-guide-item summary { cursor: pointer; font-size: var(--text-meta); color: var(--c-text); font-weight: 500; }
.cc-guide-item p { margin: 6px 0 4px; font-size: var(--text-meta); color: var(--c-text-dim); line-height: 1.6; }
.cc-guide-item ul { margin: 0 0 2px 16px; padding: 0; }
.cc-guide-item li { font-size: var(--text-meta); color: var(--c-text-dim); line-height: 1.6; }
.cc-rows { display: flex; flex-direction: column; gap: 6px; max-height: 340px; overflow-y: auto; }
.cc-row {
  display: block; width: 100%; text-align: left; cursor: pointer;
  padding: 6px 8px; border-radius: 6px; border: 1px solid var(--c-border-light);
  background: var(--c-card-bg); color: inherit;
}
.cc-row:hover { background: var(--c-card-bg-hover); border-color: var(--c-accent); }
.cc-row.cc-risk { border-color: var(--c-border); background: var(--c-card-bg); }
.cc-row.cc-risk:hover { border-color: var(--c-warn); }
.cc-row-main { display: flex; align-items: center; gap: 6px; font-size: var(--text-meta); }
.cc-name { color: var(--c-text); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.cc-code { color: var(--c-text-faint); }
.cc-price { color: var(--c-text); font-variant-numeric: tabular-nums; }
.cc-prem.up { color: var(--c-up); }
.cc-prem.down { color: var(--c-down); }
.cc-score { color: var(--c-accent); }
.cc-risk-tags { color: var(--c-warn); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cc-row-triggers { margin-top: 3px; font-size: var(--text-meta); color: var(--c-text-faint); line-height: 1.5; }
.cc-disclaimer { font-size: var(--text-meta); color: var(--c-text-faint); line-height: 1.6; }
</style>
