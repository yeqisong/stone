<template>
<div class="tg-card" :class="'tg-' + (st?.level || 'ok')">
  <div class="tg-head">
    <b><AppIcon name="shield" :size="14" />  实时仓位护栏</b>
    <span v-if="st" class="tg-asof">{{ st.asof }} · 20s 刷新</span>
    <span v-if="st" class="tg-source">{{ sourceText }}</span>
    <span v-if="st && !isToday(st.slip_end)" class="tg-warn-chip">今日交割单未导入</span>
    <span v-if="collapsed && st" class="tg-mini">
      仓位 {{ pct(st.position_ratio) }} · 现金 {{ pct(st.cash_ratio) }} · 今日开仓 {{ st.daily_buy_count }}/{{ st.max_new_buys_per_day }} · 还能买 {{ money(st.max_buy_amount) }}
    </span>
    <n-button size="tiny" quaternary class="tg-toggle" @click="toggleCollapsed">
      {{ collapsed ? '展开明细' : '收起明细' }}<span class="tg-caret">{{ collapsed ? '▾' : '▴' }}</span>
    </n-button>
  </div>

  <div v-if="loading && !st" class="tg-loading"><n-spin size="small" /></div>

  <template v-else-if="st">
    <div v-if="!collapsed && !isToday(st.slip_end) && !st.today_actions.length" class="tg-stale">
      今天有操作？先点「记录今日操作」；否则下面的仓位/现金还是 {{ st.slip_end || '上次' }} 的。
    </div>
    <div v-if="!collapsed" class="tg-metrics">
      <div class="tg-m">
        <span>仓位</span>
        <b :style="{ color: ratioColor }">{{ pct(st.position_ratio) }}</b>
        <i>目标 ≤{{ pct(st.target_position_ratio, 0) }} · 硬上限 {{ pct(st.max_position_ratio, 0) }}</i>
      </div>
      <div class="tg-m">
        <span>现金</span>
        <b>{{ money(st.cash) }}</b>
        <i>{{ pct(st.cash_ratio) }} · 底线 ≥{{ pct(st.cash_floor, 0) }}</i>
      </div>
      <div class="tg-m">
        <span>今日开仓</span>
        <b>{{ st.daily_buy_count }}/{{ st.max_new_buys_per_day }}</b>
        <i>还可 {{ st.remaining_new_buys }} 笔</i>
      </div>
      <div class="tg-m">
        <span>今日还能买</span>
        <b>{{ money(st.max_buy_amount) }}</b>
        <i>日额度 {{ money(st.daily_buy_budget) }} · 受仓位/现金限制</i>
      </div>
      <div class="tg-m">
        <span>最大单票</span>
        <b :style="{ color: (st.top_weight || 0) > (st.rules?.max_single_weight || 0.2) ? 'var(--c-error)' : 'var(--c-text)' }">{{ pct(st.top_weight) }}</b>
        <i>{{ st.top_position?.stock_name || '—' }} · 占账户 · 上限 {{ pct(st.rules?.max_single_weight, 0) }}</i>
      </div>
      <div class="tg-m">
        <span>账户总资产</span>
        <b>{{ money(st.equity) }}</b>
        <i>持仓 {{ money(st.market_value) }}</i>
      </div>
    </div>

    <template v-if="!collapsed">
      <div class="tg-bar">
        <div class="tg-fill" :style="{ width: posPct + '%', background: ratioColor }"></div>
        <span class="tg-mark" :style="{ left: targetPct + '%' }" :title="'目标 ' + pct(st.target_position_ratio,0)"></span>
        <span class="tg-mark tg-hard" :style="{ left: hardPct + '%' }" :title="'硬上限 ' + pct(st.max_position_ratio,0)"></span>
      </div>
      <div class="tg-legend">
        <span>0%</span>
        <span class="tg-legend-target">目标 {{ pct(st.target_position_ratio, 0) }}</span>
        <span class="tg-legend-hard">硬上限 {{ pct(st.max_position_ratio, 0) }}</span>
        <span>100%</span>
      </div>
    </template>

    <div class="tg-advice" :class="[st.level, { 'tg-advice-mini': collapsed }]">{{ st.advice }}</div>

    <!-- 今日教练：基于交易画像 + 当前仓位 + 持仓板块资金流 -->
    <div v-if="st.coach" class="tg-coach">
      <div class="tg-coach-head">
        <b>今日教练</b>
        <span class="tg-coach-next">{{ st.coach.next }}</span>
        <span class="tg-coach-toggle" @click="coachOpen = !coachOpen">{{ coachOpen ? '收起计划' : '看计划' }}</span>
      </div>
      <div v-if="coachOpen && st.coach.profile?.length" class="tg-coach-tags">
        <span v-for="p in st.coach.profile" :key="p.key" class="tg-coach-tag" :title="p.evidence">{{ p.label }}</span>
      </div>
      <div v-if="st.coach.plan" class="tg-plan-progress">
        计划卖出 <b>{{ money(st.coach.plan.executed) }}</b> / {{ money(st.coach.plan.need) }}
        <span v-if="st.coach.plan.done" class="tg-plan-done">已完成</span>
        <span v-else>剩余 {{ money(st.coach.plan.remaining) }}</span>
      </div>
      <div v-if="coachOpen" class="tg-coach-detail">
        <div v-for="a in st.coach.active" :key="a.title" class="tg-coach-risk" :class="'lv-' + a.level">
          <b>{{ a.title }}</b><span>{{ a.action }}</span>
          <i>{{ a.rule }}</i>
        </div>
        <div v-if="st.coach.tranches?.length" class="tg-coach-tranches">
          <div v-for="(t, i) in st.coach.tranches" :key="i"
            class="tg-coach-tranche" :class="'tg-t-' + (t.status || 'pending')">
            <span class="tg-t-when">{{ t.when }}</span>
            <b>{{ money(t.amount) }}</b>
            <span class="tg-t-status">{{ statusLabel(t.status) }}</span>
            <span class="tg-t-hint">{{ t.hint }}</span>
          </div>
        </div>
      </div>
    </div>

    <div v-if="journalHint && !collapsed" class="tg-journal-hint" @click="openJournal">
      {{ journalHint }}
    </div>

    <div class="tg-actions">
      <n-button v-perm="'portfolio.trades.intraday'" size="small" type="primary" @click="openCheck('buy')"><AppIcon name="shield" :size="12" />  交易前检查</n-button>
      <n-button v-perm="'portfolio.trades.intraday'" size="small" @click="openCheck('buy')">记录今日操作</n-button>
      <n-button v-perm="'portfolio.trades.intraday'" size="small" quaternary @click="showReduction = true">减仓清单</n-button>
      <n-button v-perm="'portfolio.trades.journal.edit'" size="small" quaternary @click="openJournal">今日计划/复盘</n-button>
      <n-button v-perm="'portfolio.trades.intraday'" size="small" quaternary @click="openCash">修正现金</n-button>
      <n-button v-perm="'portfolio.trades.import'" size="small" quaternary @click="emit('import')">导入交割单</n-button>
    </div>

    <div v-if="!collapsed" class="tg-today">
      <div class="tg-today-head">
        今日记录
        <span>{{ st.today_actions.length ? `${st.daily_buy_count} 买 / ${st.daily_sell_count} 卖` : '今天还没有记录' }}</span>
      </div>
      <div v-if="!st.today_actions.length" class="tg-none">
        今天有操作就记一笔，护栏会立刻更新；导入交割单后以交割单为准。
      </div>
      <div v-for="a in st.today_actions" :key="a.id || (a.trade_time + a.stock_code + a.side)"
        class="tg-action">
        <span class="tg-time">{{ (a.trade_time || '').slice(0, 5) || '—' }}</span>
        <span class="tg-side" :class="a.side === 'buy' ? 'buy' : 'sell'">{{ a.side === 'buy' ? '买' : '卖' }}</span>
        <b>{{ a.stock_name }}</b>
        <span class="tg-code">{{ a.stock_code }}</span>
        <span class="tg-qty">{{ a.quantity }} 股 @ ¥{{ a.price }}</span>
        <span v-if="a.reason" class="tg-reason" :title="a.reason">{{ a.reason }}</span>
        <span v-if="a.emotion" class="tg-emotion" :class="emotionClass(a.emotion)">{{ a.emotion }}</span>
        <span v-if="a.hard_block" class="tg-hard-chip" :title="a.override_reason || ''">硬拦截</span>
        <n-button v-if="a.source === 'manual'" size="tiny" quaternary type="error" @click="remove(a.id)">删除</n-button>
      </div>
    </div>

    <div v-if="!collapsed" class="tg-note">
      临时口径：{{ st.slip_end || '无' }} 交割单 + 今日手动记录；{{ st.quote_ok ? '实时价' : '收盘价（快照不可用）' }}。
      手动记录只影响护栏，不改写持仓表；导入今日交割单后自动校准。
    </div>
  </template>

  <div v-else class="tg-error">
    {{ error || '护栏状态加载失败' }}
    <n-button size="tiny" @click="load">重试</n-button>
  </div>

  <TradeCheck v-model:show="showCheck" :default-side="checkSide" @recorded="onRecorded" />
  <ReductionPlan v-model:show="showReduction" />

  <n-modal v-model:show="showJournal">
    <n-card style="width:min(600px,94vw)" title="今日计划 / 收盘复盘" :bordered="false">
      <div class="tg-journal-form">
        <label>
          <span>盘前计划（最多几笔 / 仓位上限 / 只做什么 / 不做什么）</span>
          <n-input v-model:value="journalForm.pre_plan" type="textarea" :rows="3"
            maxlength="1000" show-count placeholder="例：今天只减不加，优先卖阳光500/沪电100，不开新仓" />
        </label>
        <label>
          <span>收盘复盘（哪里执行了 / 哪里违背计划 / 当时情绪）</span>
          <n-input v-model:value="journalForm.post_review" type="textarea" :rows="3"
            maxlength="1000" show-count placeholder="例：14:00减了阳光300；收盘前想追涨但没动手" />
        </label>
      </div>
      <template #footer>
        <n-space justify="flex-end">
          <n-button size="small" @click="showJournal=false">取消</n-button>
          <n-button size="small" type="primary" :loading="savingJournal" @click="saveJournal">保存</n-button>
        </n-space>
      </template>
    </n-card>
  </n-modal>

  <n-modal v-model:show="showCash">
    <n-card style="width:min(400px,92vw)" title="修正当前可用资金" :bordered="false">
      <p class="tg-cash-hint">
        填券商 App 里现在的「可用资金」。出入金、未记录交易导致现金对不上时用；记录新交易会自动扣/加。
      </p>
      <n-input-number v-model:value="cashValue" :min="0" :step="100" :precision="2" style="width:100%" />
      <template #footer>
        <n-space justify="flex-end">
          <n-button size="small" @click="showCash=false">取消</n-button>
          <n-button size="small" type="primary" :loading="savingCash" @click="saveCash">保存</n-button>
        </n-space>
      </template>
    </n-card>
  </n-modal>
</div>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import TradeCheck from './TradeCheck.vue'
import ReductionPlan from './ReductionPlan.vue'
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { NButton, NSpin, NModal, NCard, NInput, NInputNumber, NSpace, useMessage } from 'naive-ui'
import axios from 'axios'

const emit = defineEmits(['import', 'recorded'])
const API = window.location.origin
const message = useMessage()

const st = ref(null)
const loading = ref(false), error = ref('')
// 默认只看教练/建议 + 操作；仓位明细、进度条、今日记录收进“展开明细”
const _storedDetails = localStorage.getItem('trade_guard_details_collapsed')
const collapsed = ref(_storedDetails === null ? true : _storedDetails === '1')
const coachOpen = ref(false)
const showCheck = ref(false), checkSide = ref('buy')
const showReduction = ref(false)
const showJournal = ref(false), savingJournal = ref(false)
const journalForm = reactive({ pre_plan: '', post_review: '' })
const showCash = ref(false), cashValue = ref(0), savingCash = ref(false)
const journalHint = computed(() => {
  const j = st.value?.journal
  if (!j) return ''
  const hm = String(st.value?.asof || '').slice(0, 5)
  if (!j.pre_plan && hm && hm < '14:00') {
    return '今天还没写计划：先写一句最多几笔/仓位上限/只做什么'
  }
  if (!j.post_review && hm && hm >= '14:50') {
    return '收盘前写一句复盘：今天哪里执行/哪里违背计划'
  }
  return ''
})
const emotionClass = e => ['报复', '追涨', '恐惧'].includes(e)
  ? 'em-bad' : e === '计划内' ? 'em-ok' : 'em-warn'
let timer = null

const money = v => v == null ? '—' : (v < 0 ? '-¥' : '¥') + Number(Math.abs(v)).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
const pct = (v, d = 1) => v == null ? '—' : (Number(v) * 100).toFixed(d) + '%'
const statusLabel = s => ({ done: '已完成', partial: '部分完成', missed: '已错过', pending: '待执行' }[s] || '待执行')
const posPct = computed(() => Math.min(100, Math.max(0, (st.value?.position_ratio || 0) * 100)))
const targetPct = computed(() => Math.min(100, Math.max(0, (st.value?.target_position_ratio || 0.7) * 100)))
const hardPct = computed(() => Math.min(100, Math.max(0, (st.value?.max_position_ratio || 0.9) * 100)))
const ratioColor = computed(() => {
  const r = st.value?.position_ratio ?? 0
  if (r >= (st.value?.max_position_ratio ?? 0.9)) return 'var(--c-error)'
  if (r >= (st.value?.target_position_ratio ?? 0.7)) return 'var(--c-warning)'
  return 'var(--c-down)'
})
const sourceText = computed(() => {
  if (!st.value) return ''
  const end = st.value.slip_end
  if (!end) return '仅今日手动记录'
  if (isToday(end)) return '今日交割单'
  return `${end} 交割单 + 今日手动`
})
function isToday(d){
  if (!d) return false
  const n = new Date()
  const s = `${n.getFullYear()}-${String(n.getMonth()+1).padStart(2,'0')}-${String(n.getDate()).padStart(2,'0')}`
  return d === s
}

let lastNotifyKey = ''
function maybeNotify(s){
  try{
    if (!s || !document.hidden) return
    if (localStorage.getItem('notif_enabled') !== '1') return
    if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return
    if (s.level !== 'critical') return
    const key = `${s.trade_date}|${s.advice}`
    if (key === lastNotifyKey) return
    lastNotifyKey = key
    new Notification('仓位护栏：今天先别开新仓', { body: s.advice, tag: 'trade-guard' })
  }catch(e){ /* 通知不可用不阻断 */ }
}

async function load(){
  loading.value = true; error.value = ''
  try{
    const r = await axios.get(API + '/api/portfolio/trades/intraday/state', { timeout: 30000 })
    st.value = r.data.state
    if (!showJournal.value) {
      journalForm.pre_plan = st.value?.journal?.pre_plan || ''
      journalForm.post_review = st.value?.journal?.post_review || ''
    }
    maybeNotify(st.value)
  }catch(e){
    error.value = e.response?.data?.detail || e.message || '加载失败'
  }finally{ loading.value = false }
}

function toggleCollapsed(){
  collapsed.value = !collapsed.value
  localStorage.setItem('trade_guard_details_collapsed', collapsed.value ? '1' : '0')
}
function openCheck(side){ checkSide.value = side; showCheck.value = true }
function onRecorded(s){
  st.value = s
  emit('recorded', s)
  setTimeout(load, 800)
}
function openJournal(){
  journalForm.pre_plan = st.value?.journal?.pre_plan || ''
  journalForm.post_review = st.value?.journal?.post_review || ''
  showJournal.value = true
}
async function saveJournal(){
  savingJournal.value = true
  try{
    const r = await axios.put(API + '/api/portfolio/trades/journal', {
      pre_plan: journalForm.pre_plan,
      post_review: journalForm.post_review,
    })
    if (st.value) st.value.journal = r.data
    showJournal.value = false
    message.success('计划/复盘已保存')
  }catch(e){ message.error(e.response?.data?.detail || '保存失败') }
  finally{ savingJournal.value = false }
}
function openCash(){
  cashValue.value = st.value?.cash ?? 0
  showCash.value = true
}
async function saveCash(){
  savingCash.value = true
  try{
    const r = await axios.put(API + '/api/portfolio/trades/intraday/cash', { cash: Number(cashValue.value || 0) })
    st.value = r.data.state
    emit('recorded', r.data.state)
    showCash.value = false
    message.success('现金已修正')
  }catch(e){ message.error(e.response?.data?.detail || '保存失败') }
  finally{ savingCash.value = false }
}
async function remove(id){
  if (!id) return
  try{
    const r = await axios.delete(API + '/api/portfolio/trades/intraday/' + id)
    st.value = r.data.state
    emit('recorded', r.data.state)
    message.success('已删除')
  }catch(e){ message.error(e.response?.data?.detail || '删除失败') }
}

onMounted(() => { load(); timer = setInterval(load, 20000) })
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.tg-card {
  margin-bottom: 10px; padding: 10px 12px; flex-shrink: 0;
  border: 1px solid var(--c-border); border-left: 3px solid var(--c-down);
  border-radius: var(--radius-sm); background: var(--c-card-bg);
}
.tg-card.tg-warn { border-left-color: var(--c-warning); }
.tg-card.tg-critical { border-left-color: var(--c-error); }
.tg-head { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; margin-bottom:8px; }
.tg-head b { font-size:13px; }
.tg-asof { font-size: var(--text-meta); color: var(--c-text-faint); }
.tg-source { font-size: var(--text-meta); color: var(--c-text-dim); }
.tg-warn-chip { font-size:10px; padding:1px 6px; border-radius:3px; color: var(--c-warning);
  border:1px solid color-mix(in srgb, var(--c-warning) 40%, transparent); }
.tg-toggle { margin-left:auto; align-self:center; font-size: var(--text-meta); color: var(--c-text-dim); }
.tg-mini { font-size: var(--text-meta); color: var(--c-text-dim); }
.tg-caret { font-size:9px; margin-left:2px; }
.tg-loading { padding:16px; text-align:center; }
.tg-stale { margin-bottom:8px; padding:6px 9px; border-radius: var(--radius-sm); font-size: var(--text-meta);
  line-height:1.6; color: var(--c-warning);
  background: color-mix(in srgb, var(--c-warning) 8%, transparent);
  border:1px solid color-mix(in srgb, var(--c-warning) 30%, transparent); }
.tg-metrics { display:grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap:8px; }
.tg-m { background: var(--c-card-bg-hover); border-radius: var(--radius-sm); padding:7px 9px; min-width:0; }
.tg-m span { display:block; font-size: var(--text-meta); color: var(--c-text-dim); }
.tg-m b { display:block; margin-top:2px; font-size:16px; font-variant-numeric: tabular-nums; line-height:1.25; }
.tg-m i { display:block; margin-top:2px; font-size: var(--text-meta); color: var(--c-text-faint); font-style:normal; }
.tg-bar { position:relative; height:8px; margin-top:12px; border-radius:4px; background: var(--c-border); overflow:visible; }
.tg-fill { height:100%; border-radius:4px; transition: width .3s; }
.tg-mark { position:absolute; top:-3px; z-index:2; width:2px; height:14px; background:#fff;
  box-shadow:0 0 0 1px rgba(0,0,0,.28); opacity:.95; }
.tg-mark.tg-hard { background: var(--c-error); box-shadow:0 0 0 1px rgba(0,0,0,.28); }
.tg-legend { display:flex; justify-content:space-between; margin-top:3px; font-size:10px; color: var(--c-text-faint); }
.tg-legend-target { color: var(--c-text-dim); }
.tg-legend-hard { color: var(--c-error); }
.tg-advice { margin-top:9px; padding:7px 9px; border-radius: var(--radius-sm); font-size: var(--text-meta); line-height:1.65;
  background: var(--c-card-bg-hover); border-left:2px solid var(--c-down); }
.tg-advice.tg-warn { border-left-color: var(--c-warning); color: var(--c-warning); }
.tg-advice.tg-critical { border-left-color: var(--c-error); color: var(--c-error); }
.tg-advice-mini { margin-top:5px; padding:4px 8px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.tg-coach {
  margin-top: 9px; padding: 8px 9px; border: 1px solid var(--c-border);
  border-left: 3px solid var(--c-accent); border-radius: var(--radius-sm);
  background: var(--c-card-bg);
}
.tg-coach-head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.tg-coach-head b { font-size: 12px; flex-shrink: 0; }
.tg-coach-next { flex: 1; min-width: 0; font-size: var(--text-meta); color: var(--c-text); line-height: 1.6; }
.tg-coach-toggle { font-size: var(--text-meta); color: var(--c-text-faint); cursor: pointer; user-select: none; flex-shrink: 0; }
.tg-coach-toggle:hover { color: var(--c-text); }
.tg-coach-tags { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 5px; }
.tg-coach-tag {
  font-size: 10px; padding: 1px 6px; border-radius: 3px; cursor: help;
  color: var(--c-warning); border: 1px solid color-mix(in srgb, var(--c-warning) 35%, transparent);
}
.tg-coach-detail { margin-top: 7px; padding-top: 6px; border-top: 1px dashed var(--c-border); }
.tg-coach-risk { font-size: var(--text-meta); line-height: 1.6; margin-bottom: 4px; }
.tg-coach-risk b { font-weight: 600; }
.tg-coach-risk span { color: var(--c-text); margin-left: 4px; }
.tg-coach-risk i { display: block; color: var(--c-text-faint); font-style: normal; }
.tg-coach-risk.lv-critical b { color: var(--c-error); }
.tg-coach-risk.lv-warn b { color: var(--c-warning); }
.tg-coach-tranches { margin-top: 5px; display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; }
@media (max-width: 900px) { .tg-coach-tranches { grid-template-columns: 1fr; } }
.tg-coach-tranche { display: flex; gap: 8px; align-items: baseline; font-size: var(--text-meta); line-height: 1.7; }
.tg-t-when { width: 78px; color: var(--c-text-dim); flex-shrink: 0; }
.tg-coach-tranche b { width: 68px; text-align: right; flex-shrink: 0; }
.tg-t-status { width: 52px; flex-shrink: 0; font-size: 10px; color: var(--c-text-faint); }
.tg-coach-tranche.tg-t-done .tg-t-status { color: var(--c-info); }
.tg-coach-tranche.tg-t-partial .tg-t-status { color: var(--c-warning); }
.tg-coach-tranche.tg-t-missed .tg-t-status { color: var(--c-error); }
.tg-t-hint { color: var(--c-text-faint); }
.tg-plan-progress { margin-top: 5px; font-size: var(--text-meta); color: var(--c-text-dim); }
.tg-plan-progress b { color: var(--c-text); }
.tg-plan-done { margin-left: 6px; color: var(--c-info); font-weight: 600; }
.tg-actions { display:flex; gap:6px; flex-wrap:wrap; margin-top:9px; }
@media (max-width: 640px) {
  /* 操作按钮单行横滑，避免 6 个按钮换 3 行把持仓挤出首屏 */
  .tg-actions { flex-wrap:nowrap; overflow-x:auto; padding-bottom:2px; }
  .tg-actions > * { flex-shrink:0; }
}
.tg-today { margin-top:10px; border-top:1px dashed var(--c-border); padding-top:7px; }
.tg-today-head { display:flex; align-items:baseline; gap:8px; font-size:12px; font-weight:600; margin-bottom:4px; }
.tg-today-head span { font-size: var(--text-meta); color: var(--c-text-faint); font-weight:400; }
.tg-none { font-size: var(--text-meta); color: var(--c-text-faint); line-height:1.6; }
.tg-action { display:flex; align-items:center; gap:7px; flex-wrap:wrap; font-size: var(--text-meta); color: var(--c-text-dim); line-height:1.9; }
.tg-action b { color: var(--c-text); font-weight:500; }
.tg-hard-chip { padding:0 4px; border-radius:3px; font-size:10px; color:#fff; background: var(--c-error); }
.tg-emotion { padding:0 4px; border-radius:3px; font-size:10px; color: var(--c-text-dim); background: var(--c-card-bg-hover); }
.tg-emotion.em-bad { color:#fff; background: var(--c-error); }
.tg-emotion.em-warn { color:#fff; background: var(--c-warning); }
.tg-emotion.em-ok { color: var(--c-info); background: color-mix(in srgb, var(--c-info) 12%, transparent); }
.tg-journal-hint { margin-top:7px; padding:5px 8px; border-radius: var(--radius-sm); cursor:pointer;
  font-size: var(--text-meta); color: var(--c-warning);
  background: color-mix(in srgb, var(--c-warning) 8%, transparent); }
.tg-journal-hint:hover { background: color-mix(in srgb, var(--c-warning) 14%, transparent); }
.tg-journal-form { display:flex; flex-direction:column; gap:12px; }
.tg-journal-form label { display:flex; flex-direction:column; gap:5px; }
.tg-journal-form label > span { font-size: var(--text-meta); color: var(--c-text-dim); }
.tg-time { width:36px; color: var(--c-text-faint); font-variant-numeric: tabular-nums; }
.tg-side { display:inline-block; width:18px; text-align:center; border-radius:3px; font-size:10px; color:#fff; }
.tg-side.buy { background: var(--c-up); }
.tg-side.sell { background: var(--c-down); }
.tg-code { color: var(--c-text-faint); }
.tg-reason { max-width:260px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color: var(--c-text-faint); }
.tg-note { margin-top:8px; font-size:10px; color: var(--c-text-faint); line-height:1.6; }
.tg-error { padding:12px; font-size:13px; color: var(--c-error); }
.tg-cash-hint { margin:0 0 10px; font-size: var(--text-meta); color: var(--c-text-dim); line-height:1.65; }
@media (max-width: 640px) {
  .tg-metrics { grid-template-columns: 1fr 1fr; }
  .tg-m b { font-size:14px; }
  .tg-reason { display:none; }
}
</style>
