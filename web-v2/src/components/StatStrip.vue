<template>
  <div class="stat-strip">
    <div v-for="it in items" :key="it.label" class="stat-item">
      <div class="stat-label">{{ it.label }}</div>
      <div class="stat-value" :style="{ color: it.color || 'var(--c-text)' }">{{ texts[it.label] ?? it.value }}</div>
    </div>
  </div>
</template>

<script setup>
import { reactive, watch, onUnmounted } from 'vue'

const props = defineProps({
  items: { type: Array, required: true, default: () => [] }, // [{label, value, color?}]
})

// ── 数字滚动动效 ──
// 仅对「纯数值」项做缓动计数（支持 ¥ 前缀 / 正负号 / 千分位 / 小数 / 万·亿后缀 / % / 尾缀涨跌幅），
// 日期、名称、比值（3/10）、带括注（62% (12/20)）等文本项原样直显。
// 收尾帧直接渲染原始字符串，保证最终显示与传入值逐字符一致。
const NUM_RE = /^(¥)?([+-])?([\d,]+(?:\.\d+)?)([万亿])?(%)?(?:([+-]\d+(?:\.\d+)?)%)?$/
const texts = reactive({})   // label → 当前显示文本
const rafs = new Map()       // label → rafId
const timers = new Map()     // label → stagger 启动延时
const reduced = typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches
const DUR = 900              // 单项滚动时长 ms
const STAGGER = 70           // 逐项错峰 ms（封顶 350，避免尾部等待过久）

function parseNum(v) {
  if (typeof v === 'number') return Number.isFinite(v) ? { prefix: '', num: v, plus: false, suffix: '', dec: 0, group: false } : null
  if (typeof v !== 'string') return null
  const m = NUM_RE.exec(v.trim())
  if (!m) return null
  return {
    prefix: m[1] || '',
    num: parseFloat(m[3].replace(/,/g, '')) * (m[2] === '-' ? -1 : 1),
    plus: m[2] === '+',   // 显式正号（wanSigned 等）需在滚动帧中保留
    suffix: (m[4] || '') + (m[5] || '') + (m[6] ? m[6] + '%' : ''),
    dec: (m[3].split('.')[1] || '').length,
    group: m[3].includes(','),
  }
}

function fmtCur(v, p) {
  let s = p.dec > 0 ? Math.abs(v).toFixed(p.dec) : String(Math.round(Math.abs(v)))
  if (p.group) {
    const i = s.indexOf('.')
    const grouped = Number(i < 0 ? s : s.slice(0, i)).toLocaleString()
    s = i < 0 ? grouped : grouped + s.slice(i)
  }
  return p.prefix + (v < 0 ? '-' : p.plus ? '+' : '') + s + p.suffix
}

function animate(it, idx) {
  const p = parseNum(it.value)
  if (!p) { delete texts[it.label]; return }
  const from = parseNum(texts[it.label])?.num ?? 0
  if (reduced || p.num === from) { texts[it.label] = it.value; return }
  cancelAnimationFrame(rafs.get(it.label))
  clearTimeout(timers.get(it.label))
  texts[it.label] = fmtCur(from, p)
  const delay = Math.min(idx * STAGGER, 350)
  const t0 = performance.now() + delay
  timers.set(it.label, setTimeout(() => {
    const step = now => {
      const t = Math.min((now - t0) / DUR, 1)
      const e = 1 - Math.pow(1 - t, 3)   // easeOutCubic
      texts[it.label] = t < 1 ? fmtCur(from + (p.num - from) * e, p) : it.value
      if (t < 1) rafs.set(it.label, requestAnimationFrame(step))
    }
    rafs.set(it.label, requestAnimationFrame(step))
  }, delay))
}

watch(() => props.items, list => (list || []).forEach(animate), { immediate: true })
onUnmounted(() => { rafs.forEach(id => cancelAnimationFrame(id)); timers.forEach(id => clearTimeout(id)) })
</script>

<style scoped>
.stat-strip { display: flex; gap: 18px; justify-content: center; padding: 8px 0 12px; flex-wrap: wrap; }
.stat-item { text-align: center; min-width: 62px; }
.stat-label { font-size: 11px; color: var(--c-text-dim); white-space: nowrap; }
.stat-value { font-size: 20px; font-weight: 700; line-height: 1.25; }
@media (max-width: 768px) {
  .stat-strip { gap: 10px; padding: 6px 0 10px; }
  .stat-item { min-width: 56px; }
  .stat-label { font-size: 10px; }
  .stat-value { font-size: 18px; }
}
</style>
