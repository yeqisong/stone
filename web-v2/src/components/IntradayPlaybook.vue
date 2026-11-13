<template>
  <!-- 左边缘入口：细线常驻，hover/键盘聚焦滑出小图标；点击打开执行卡 -->
  <div class="pb-edge" role="button" tabindex="0" aria-label="打开盘中执行卡"
    title="盘中执行卡（鼠标移到左边缘）"
    @click="open" @keydown.enter.prevent="open" @keydown.space.prevent="open">
    <span class="pb-rail"></span>
    <div class="pb-tab">
      <span class="pb-tab-icon"><AppIcon name="target" :size="14" /></span>
      <span class="pb-tab-label">盘中执行卡<span v-if="doneCount" class="pb-tab-count">{{ doneCount }}/5</span></span>
    </div>
  </div>

  <n-drawer v-model:show="show" placement="left" :width="isNarrow ? '94%' : 430">
    <n-drawer-content closable>
      <template #header>
        <div class="pb-head">
          <span class="pb-head-icon"><AppIcon name="target" :size="16" /></span>
          <div>
            <div class="pb-head-title">盘中执行卡</div>
            <div class="pb-head-sub">{{ name || code || '当前个股' }} · {{ day }}</div>
          </div>
        </div>
      </template>

      <div class="pb-body">
        <div class="pb-hero">
          <div class="pb-hero-line">
            <span>提醒 → 解读 → 结构 → 风控 → 记录</span>
            <b>{{ doneCount }}/{{ STEPS.length }}</b>
          </div>
          <div class="pb-progress"><i :style="{ width: pct + '%' }"></i></div>
          <div class="pb-hero-note">
            今日已走完 <b>{{ dailyCount }}</b> 次 · 流程不是预测，是防止你从提醒直接跳到下单
          </div>
        </div>

        <div v-for="(s, i) in STEPS" :key="s.title" class="pb-step"
          :class="{ done: done[i], locked: !canCheck(i) }">
          <div class="pb-step-idx">
            <AppIcon v-if="done[i]" name="check" :size="12" />
            <template v-else>{{ i + 1 }}</template>
          </div>
          <div class="pb-step-main">
            <div class="pb-step-title">
              {{ s.title }}
              <span class="pb-step-tag">{{ s.tag }}</span>
            </div>
            <div class="pb-step-desc">{{ s.desc }}</div>
            <ul class="pb-step-points">
              <li v-for="p in s.points" :key="p">{{ p }}</li>
            </ul>
            <div v-if="i === STEPS.length - 1" class="pb-note-wrap">
              <textarea v-model="note" class="pb-note" rows="3" maxlength="300"
                placeholder="本次结论 / 放弃理由（自动保存，收盘复盘用）"></textarea>
            </div>
            <div class="pb-step-actions">
              <n-button size="tiny" secondary @click="runAction(s.action)">
                {{ s.actionLabel }} →
              </n-button>
              <button type="button" class="pb-check-btn" :class="{ done: done[i] }"
                :disabled="!canCheck(i)" :aria-pressed="done[i] ? 'true' : 'false'"
                @click="toggle(i)">
                <span class="pb-check-mark">
                  <AppIcon v-if="done[i]" name="check" :size="11" />
                </span>
                {{ done[i] ? '已完成' : '完成这一步' }}
              </button>
            </div>
          </div>
        </div>

        <div v-if="allDone" class="pb-finish">
          这一遍走完了。下一次提醒出现时，从第 1 步重新来。
        </div>
      </div>

      <template #footer>
        <div class="pb-foot">
          <n-button size="tiny" quaternary @click="reset">重置本次</n-button>
          <n-button size="tiny" @click="show = false">收起</n-button>
        </div>
      </template>
    </n-drawer-content>
  </n-drawer>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { NButton, NDrawer, NDrawerContent } from 'naive-ui'
import AppIcon from './AppIcon.vue'
import { useViewport } from '../utils/viewport'
import { bjToday } from '../utils/date'

const props = defineProps({
  code: { type: String, default: '' },
  name: { type: String, default: '' },
})
const emit = defineEmits(['action'])

const { isNarrow } = useViewport()
const show = ref(false)
const day = ref(bjToday())
const done = ref([false, false, false, false, false])
const note = ref('')
const dailyCount = ref(0)

const STEPS = [
  {
    title: '先看提醒，不追价',
    tag: '信号',
    desc: '只处理高信号：止损/止盈、涨跌停、急涨急跌，以及 VWAP / 开盘区间 / 昨高昨低突破。先确认触发价与现价，不在提醒弹出的瞬间追。',
    points: [
      '看 kind 和触发价：是止损/跌停，还是突破？',
      '看现价离触发位多远：已经追高/杀跌就不做',
    ],
    action: 'alerts',
    actionLabel: '定位盘中提醒',
  },
  {
    title: '读事件 AI：确认还是背离',
    tag: '解读',
    desc: 'AI 只做盘面解读，不预测涨跌。先读 summary 第一句：是量价/结构确认，还是背离；再把 observations 当检查清单，把 risks 当反向证据。',
    points: [
      'summary 是“确认”还是“背离”？',
      'observations 里哪条能被行情数据复核？',
      'risks 里有没有直接否定这次突破的证据？',
    ],
    action: 'ai',
    actionLabel: '打开 AI 解读',
  },
  {
    title: '核对三条线：VWAP / 开盘区间 / 昨高昨低',
    tag: '结构',
    desc: '突破要有位置和量能配合。至少两条确认，再考虑下一步；只有价格、没有量能和相对强弱，按假突破处理。',
    points: [
      '现价在 VWAP 哪一侧？偏离多少？',
      'OR 上/下沿、昨高/昨低是否被有效突破？',
      '量比 / RVOL 是否放大？相对强弱是否同向？',
    ],
    action: 'levels',
    actionLabel: '打开价位层',
  },
  {
    title: '做决策检查，不通过就不做',
    tag: '风控',
    desc: '先算最大亏损，再想要不要做。止损位、T+1、仓位上限任何一项答不上来，就放弃这次机会。',
    points: [
      '这笔最大亏损是多少？',
      '止损价在哪里？什么信号证明我错了？',
      'T+1 下明天能不能承受？仓位是否超上限？',
    ],
    action: 'risk',
    actionLabel: '看成本 / 止损',
  },
  {
    title: '执行或放弃，都写下理由',
    tag: '闭环',
    desc: '做了：记触发事件、入场、止损、目标。没做：记放弃原因。收盘复盘时回看，才能把流程变成动作。',
    points: [
      '做了：触发事件 / 入场 / 止损 / 目标',
      '没做：假突破 / 量能不配合 / 仓位已满',
    ],
    action: 'ask',
    actionLabel: '问 AI 复盘',
  },
]

const key = computed(() => `detail_playbook_v1:${day.value}:${props.code || 'default'}`)
const countKey = computed(() => `detail_playbook_count_v1:${day.value}`)
const doneCount = computed(() => done.value.filter(Boolean).length)
const pct = computed(() => Math.round(doneCount.value / STEPS.length * 100))
const allDone = computed(() => doneCount.value === STEPS.length)

function load() {
  try {
    const raw = JSON.parse(localStorage.getItem(key.value) || '{}') || {}
    const d = Array.isArray(raw.done) ? raw.done : []
    done.value = STEPS.map((_, i) => Boolean(d[i]))
    note.value = typeof raw.note === 'string' ? raw.note : ''
  } catch (e) {
    done.value = STEPS.map(() => false)
    note.value = ''
  }
}

function save() {
  try {
    localStorage.setItem(key.value, JSON.stringify({ done: done.value, note: note.value }))
  } catch (e) { /* localStorage 满/隐私模式：不影响流程使用 */ }
}

function loadDailyCount() {
  try {
    dailyCount.value = Number(localStorage.getItem(countKey.value) || 0) || 0
  } catch (e) {
    dailyCount.value = 0
  }
}

function open() {
  day.value = bjToday()
  loadDailyCount()
  load()
  show.value = true
}

function canCheck(i) {
  return i === 0 || done.value[i - 1] || done.value[i]
}

function toggle(i) {
  if (!canCheck(i)) return
  const wasAll = allDone.value
  const next = done.value.slice()
  next[i] = !next[i]
  if (!next[i]) {
    for (let j = i + 1; j < next.length; j++) next[j] = false
  }
  done.value = next
  if (!wasAll && allDone.value) {
    dailyCount.value += 1
    try { localStorage.setItem(countKey.value, String(dailyCount.value)) } catch (e) {}
  }
  save()
}

function reset() {
  done.value = STEPS.map(() => false)
  note.value = ''
  save()
}

function runAction(action) {
  show.value = false
  emit('action', action)
}

watch(key, load, { immediate: true })
watch(done, save, { deep: true })
watch(note, save)
</script>

<style scoped>
.pb-edge {
  position: fixed; left: 0; top: 50%; transform: translateY(-50%);
  width: 18px; height: 190px; z-index: 80; cursor: pointer;
  outline: none;
}
.pb-rail {
  position: absolute; left: 0; top: 50%; transform: translateY(-50%);
  width: 3px; height: 86px; border-radius: 0 3px 3px 0;
  background: linear-gradient(180deg, transparent, var(--c-accent), transparent);
  opacity: .55; transition: height .18s, opacity .18s;
}
.pb-edge:hover .pb-rail, .pb-edge:focus-visible .pb-rail {
  height: 128px; opacity: 1;
}
.pb-tab {
  position: absolute; left: 6px; top: 50%;
  transform: translate(-14px, -50%); opacity: 0; pointer-events: none;
  display: flex; align-items: center; gap: 7px; white-space: nowrap;
  padding: 7px 11px 7px 8px;
  background: var(--c-card-bg); border: 1px solid var(--c-border); border-left: none;
  border-radius: 0 8px 8px 0; box-shadow: 0 6px 18px var(--c-shadow-color);
  font-size: 11px; font-weight: 600; color: var(--c-text);
  transition: transform .18s ease, opacity .18s ease;
}
.pb-edge:hover .pb-tab, .pb-edge:focus-visible .pb-tab {
  transform: translate(0, -50%); opacity: 1; pointer-events: auto;
}
.pb-tab-icon {
  position: relative; display: flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; border-radius: 50%;
  color: var(--c-accent); background: var(--c-accent-weak);
}
.pb-tab-count { margin-left: 5px; color: var(--c-accent); font-weight: 700; }
.pb-tab-icon::after {
  content: ''; position: absolute; inset: -3px; border-radius: 50%;
  border: 1px solid var(--c-accent); opacity: 0;
  animation: pb-pulse 2.2s ease-out infinite;
}
@keyframes pb-pulse {
  0% { transform: scale(.6); opacity: .65; }
  70% { transform: scale(1.7); opacity: 0; }
  100% { transform: scale(1.7); opacity: 0; }
}

.pb-head { display: flex; align-items: center; gap: 9px; }
.pb-head-icon {
  width: 30px; height: 30px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  color: var(--c-accent); background: var(--c-accent-weak);
}
.pb-head-title { font-size: 15px; font-weight: 700; color: var(--c-text); }
.pb-head-sub { font-size: var(--text-meta); color: var(--c-text-dim); margin-top: 2px; }

.pb-body { display: flex; flex-direction: column; gap: 10px; padding-bottom: 4px; }
.pb-hero {
  padding: 11px 12px; border-radius: var(--radius);
  border: 1px solid var(--c-border); background: var(--c-accent-weak);
}
.pb-hero-line {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  font-size: 12px; font-weight: 600; color: var(--c-text);
}
.pb-hero-line b { color: var(--c-accent); font-variant-numeric: tabular-nums; }
.pb-progress {
  height: 4px; border-radius: 2px; margin: 8px 0 7px;
  background: var(--c-border); overflow: hidden;
}
.pb-progress i {
  display: block; height: 100%; border-radius: 2px;
  background: var(--c-accent); transition: width .25s ease;
}
.pb-hero-note { font-size: var(--text-meta); line-height: 1.55; color: var(--c-text-dim); }
.pb-hero-note b { color: var(--c-accent); }

.pb-step {
  display: flex; gap: 10px; padding: 11px 12px 12px;
  border: 1px solid var(--c-border); border-radius: var(--radius);
  background: var(--c-card-bg); transition: opacity .15s, border-color .15s;
}
.pb-step.done { border-color: color-mix(in srgb, var(--c-accent) 45%, var(--c-border)); }
.pb-step.locked { opacity: .62; }
.pb-step-idx {
  flex: none; width: 22px; height: 22px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700; color: var(--c-text-dim);
  border: 1px solid var(--c-border); background: var(--c-bg);
}
.pb-step.done .pb-step-idx {
  color: #fff; border-color: var(--c-accent); background: var(--c-accent);
}
.pb-step-main { min-width: 0; flex: 1; }
.pb-step-title {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  font-size: 13px; font-weight: 650; color: var(--c-text);
}
.pb-step-tag {
  font-size: 10px; font-weight: 600; padding: 1px 5px; border-radius: 3px;
  color: var(--c-accent); background: var(--c-accent-weak);
}
.pb-step-desc {
  margin-top: 5px; font-size: var(--text-meta); line-height: 1.65; color: var(--c-text-dim);
}
.pb-step-points {
  margin: 7px 0 0; padding-left: 16px;
  font-size: var(--text-meta); line-height: 1.7; color: var(--c-text);
}
.pb-step-points li::marker { color: var(--c-accent); }
.pb-step-actions {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  margin-top: 9px; flex-wrap: wrap;
}
.pb-check-btn {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 4px 8px; border-radius: var(--radius-sm);
  border: 1px solid var(--c-border); background: transparent;
  font-size: var(--text-meta); color: var(--c-text-dim); cursor: pointer;
}
.pb-check-btn:hover:not(:disabled) { color: var(--c-text); border-color: var(--c-accent); }
.pb-check-btn:disabled { cursor: not-allowed; opacity: .55; }
.pb-check-btn.done {
  color: var(--c-accent); border-color: color-mix(in srgb, var(--c-accent) 55%, var(--c-border));
  background: var(--c-accent-weak);
}
.pb-check-mark {
  width: 12px; height: 12px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid currentColor;
}
.pb-check-btn.done .pb-check-mark { border-color: var(--c-accent); background: var(--c-accent); color: #fff; }

.pb-note-wrap { margin-top: 8px; }
.pb-note {
  width: 100%; box-sizing: border-box; resize: vertical;
  padding: 7px 8px; border-radius: var(--radius-sm);
  border: 1px solid var(--c-border); background: var(--c-bg);
  color: var(--c-text); font-size: var(--text-meta); line-height: 1.6;
  font-family: inherit; outline: none;
}
.pb-note:focus { border-color: var(--c-accent); }
.pb-finish {
  padding: 9px 11px; border-radius: var(--radius);
  border: 1px dashed var(--c-accent); color: var(--c-accent);
  background: var(--c-accent-weak); font-size: var(--text-meta); text-align: center;
}
.pb-foot { display: flex; align-items: center; justify-content: space-between; }

@media (max-width: 900px) {
  .pb-edge { width: 26px; }
  .pb-tab-label { display: none; }
  .pb-tab { padding: 7px; }
}
@media (prefers-reduced-motion: reduce) {
  .pb-tab-icon::after { animation: none; }
  .pb-tab, .pb-rail, .pb-progress i { transition: none; }
}
</style>
