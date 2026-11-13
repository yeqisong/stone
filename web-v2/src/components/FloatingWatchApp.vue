<template>
  <div ref="rootEl" class="fw-root">
    <div ref="canvasEl" class="fw-canvas" @dblclick.self="arrange">
      <div v-for="it in items" :key="it.code" class="fw-card"
        :class="{ dragging: dragging === it.code }"
        :style="{ left: it._x + 'px', top: it._y + 'px' }"
        @click="onCardClick(it)">
        <div class="fw-head" @pointerdown.prevent="startDrag($event, it)">
          <span class="fw-name">{{ it.name }}</span>
          <span class="fw-code">{{ it.code }}</span>
        </div>
        <div class="fw-price" :class="tone(it)">{{ fmtPrice(it.price) }}</div>
        <div class="fw-chg" :class="tone(it)">
          {{ fmtChange(it.change) }} <span>{{ fmtPct(it.change_pct) }}</span>
        </div>
        <svg class="fw-spark" viewBox="0 0 176 36" preserveAspectRatio="none">
          <polyline :points="sparkPoints(it)" fill="none" :stroke="sparkColor(it)"
            stroke-width="1.4" vector-effect="non-scaling-stroke" />
        </svg>
      </div>
      <div v-if="!items.length && !loading" class="fw-empty">{{ error || '暂无持仓' }}</div>
      <div v-if="loading && !items.length" class="fw-empty">加载中…</div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import axios from 'axios'

const props = defineProps({
  openDetail: { type: Function, default: null },
})

const API = window.location.origin
const rootEl = ref(null)
const canvasEl = ref(null)
const items = ref([])
const data = reactive({ asof: '' })
const loading = ref(false)
const error = ref('')
const dragging = ref('')
let lastFitCount = -1
const POS_KEY = 'floating_watch_positions_v1'
const CARD_W = 190
const CARD_H = 116
const GAP = 10
let timer = null
let dragState = null
let dragWin = null
let boundWin = null
let didDrag = false

// 组件挂到 PiP document 后，事件/窗口监听必须用 PiP 的 window，而不是主窗口
function hostWindow(){
  return rootEl.value?.ownerDocument?.defaultView || window
}

function loadPositions(){
  try { return JSON.parse(localStorage.getItem(POS_KEY) || '{}') || {} } catch (e) { return {} }
}
function savePositions(){
  try {
    const out = {}
    for(const it of items.value) out[it.code] = { x: Math.round(it._x), y: Math.round(it._y) }
    localStorage.setItem(POS_KEY, JSON.stringify(out))
  } catch (e) {}
}
function layout(list, keepExisting = false, colsHint = null){
  const saved = loadPositions()
  const prev = new Map(items.value.map(x => [x.code, { x: x._x, y: x._y }]))
  const el = canvasEl.value
  const width = el ? el.clientWidth : 600
  const cols = colsHint || Math.max(1, Math.floor((width - GAP) / (CARD_W + GAP)))
  list.forEach((it, i) => {
    const p = (keepExisting ? prev.get(it.code) : null) || saved[it.code]
    it._x = p ? p.x : GAP + (i % cols) * (CARD_W + GAP)
    it._y = p ? p.y : GAP + Math.floor(i / cols) * (CARD_H + GAP)
  })
}

// 按卡片实际占用计算 PiP 最小窗口：无标题栏、无额外留白
function fitWindow(){
  const n = items.value.length
  if(!n) return
  const saved = loadPositions()
  const cols = Math.max(1, Math.ceil(Math.sqrt(n)))
  const rows = Math.ceil(n / cols)
  const maxX = Math.max(...items.value.map(it => it._x + CARD_W))
  const maxY = Math.max(...items.value.map(it => it._y + CARD_H))
  const hasSaved = items.value.some(it => saved[it.code])
  const width = Math.ceil((hasSaved ? maxX : cols * CARD_W + (cols - 1) * GAP) + GAP)
  const height = Math.ceil((hasSaved ? maxY : rows * CARD_H + (rows - 1) * GAP) + GAP)
  try { hostWindow().resizeTo(Math.max(210, width), Math.max(126, height)) } catch (e) {}
}
function clamp(item){
  const el = canvasEl.value
  const maxX = Math.max(0, (el?.clientWidth || 600) - CARD_W)
  const maxY = Math.max(0, (el?.clientHeight || 400) - CARD_H)
  item._x = Math.max(0, Math.min(maxX, item._x))
  item._y = Math.max(0, Math.min(maxY, item._y))
}
function arrange(){
  try { localStorage.removeItem(POS_KEY) } catch (e) {}
  layout(items.value, false, Math.max(1, Math.ceil(Math.sqrt(items.value.length || 1))))
  savePositions()
  fitWindow()
}

async function load(){
  if(loading.value) return
  loading.value = true
  try{
    const r = await axios.get(API + '/api/portfolio/watch-window')
    const list = (r.data?.items || []).map(x => ({ ...x, _x: 0, _y: 0 }))
    data.asof = r.data?.asof || ''
    error.value = r.data?.error || ''
    const first = items.value.length === 0
    layout(list, !first, first ? Math.max(1, Math.ceil(Math.sqrt(list.length))) : null)
    for(const it of list) clamp(it)
    items.value = list
    if(list.length !== lastFitCount){
      lastFitCount = list.length
      fitWindow()
    }
  }catch(e){
    error.value = e.response?.data?.detail || '盯盘数据加载失败'
  }finally{
    loading.value = false
  }
}

function fmtPrice(v){ return v == null ? '—' : Number(v).toFixed(2) }
function fmtChange(v){
  if(v == null) return '—'
  return (v >= 0 ? '+' : '') + Number(v).toFixed(2)
}
function fmtPct(v){
  if(v == null) return ''
  return (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%'
}
function tone(it){ return it.change_pct == null ? 'flat' : (it.change_pct >= 0 ? 'up' : 'down') }
function sparkColor(it){ return it.change_pct == null ? 'var(--c-text-faint)' : (it.change_pct >= 0 ? 'var(--c-up)' : 'var(--c-down)') }
function sparkPoints(it){
  const vals = (it.spark || []).filter(v => v != null)
  if(vals.length < 2) return ''
  const min = Math.min(...vals), max = Math.max(...vals)
  const span = max - min || 1
  const n = vals.length
  return vals.map((v, i) => {
    const x = (i / (n - 1)) * 176
    const y = 34 - ((v - min) / span) * 32
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}

function startDrag(e, it){
  const el = canvasEl.value
  if(!el) return
  const rect = el.getBoundingClientRect()
  dragState = { it, dx: e.clientX - rect.left - it._x, dy: e.clientY - rect.top - it._y, startX: e.clientX, startY: e.clientY }
  didDrag = false
  dragging.value = it.code
  dragWin = hostWindow()
  dragWin.addEventListener('pointermove', onDragMove)
  dragWin.addEventListener('pointerup', endDrag, { once: true })
}
function onDragMove(e){
  if(!dragState) return
  if(Math.abs(e.clientX - dragState.startX) + Math.abs(e.clientY - dragState.startY) > 4) didDrag = true
  const el = canvasEl.value
  if(!el) return
  const rect = el.getBoundingClientRect()
  dragState.it._x = e.clientX - rect.left - dragState.dx
  dragState.it._y = e.clientY - rect.top - dragState.dy
  clamp(dragState.it)
}
function endDrag(){
  dragWin?.removeEventListener('pointermove', onDragMove)
  dragWin = null
  if(dragState) savePositions()
  dragState = null
  dragging.value = ''
  // 拖拽结束浏览器仍可能补一个 click：先吞掉它，短延迟后恢复点击
  if(didDrag) setTimeout(() => { didDrag = false }, 120)
}
function onCardClick(it){
  if(didDrag){ didDrag = false; return }
  try{
    if(props.openDetail){
      const ok = props.openDetail(it.code)
      if(ok !== false) return
    }
  }catch(e){}
  hostWindow().open(API + '/#/detail/' + it.code, '_blank')
}

function onResize(){
  for(const it of items.value) clamp(it)
}
onMounted(() => {
  load()
  timer = setInterval(load, 5000)
  boundWin = hostWindow()
  boundWin.addEventListener('resize', onResize)
})
onUnmounted(() => {
  if(timer) clearInterval(timer)
  dragWin?.removeEventListener('pointermove', onDragMove)
  boundWin?.removeEventListener('resize', onResize)
})
</script>

<style scoped>
.fw-root {
  position: fixed; inset: 0; display: flex; flex-direction: column;
  background: transparent; color: var(--c-text); font: 12px/1.4 system-ui, sans-serif;
}
.fw-canvas { position: relative; flex: 1; overflow: hidden; background: transparent; }
.fw-card {
  position: absolute; width: 190px; height: 116px; box-sizing: border-box;
  padding: 7px 9px; border: 1px solid var(--c-border); border-radius: 7px;
  background: var(--c-card-bg); cursor: pointer; user-select: none;
  box-shadow: 0 2px 8px var(--c-shadow-color);
}
.fw-card.dragging { cursor: grabbing; z-index: 20; box-shadow: 0 8px 22px var(--c-shadow-color); }
.fw-head { display: flex; align-items: baseline; gap: 6px; cursor: grab; }
.fw-name { font-size: 12px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fw-code { margin-left: auto; color: var(--c-text-faint); font-size: 10px; }
.fw-price { font-size: 22px; font-weight: 700; line-height: 1.15; margin-top: 3px; font-variant-numeric: tabular-nums; }
.fw-chg { font-size: 11px; font-variant-numeric: tabular-nums; }
.fw-chg span { margin-left: 4px; }
.fw-price.up, .fw-chg.up { color: var(--c-up); }
.fw-price.down, .fw-chg.down { color: var(--c-down); }
.fw-price.flat, .fw-chg.flat { color: var(--c-text); }
.fw-spark { position: absolute; left: 8px; right: 8px; bottom: 4px; width: calc(100% - 16px); height: 30px; }
.fw-empty {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  color: var(--c-text-faint); font-size: 12px;
}
</style>
