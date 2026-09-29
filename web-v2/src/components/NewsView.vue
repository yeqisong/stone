<template>
<div class="page-fill">
  <!-- 筛选条：分类 / 重要度 / 关注池 / 关键词（全部持久化到 URL，返回不丢） -->
  <n-space align="center" style="margin-bottom:8px;flex-shrink:0" wrap>
    <n-button-group size="tiny">
      <n-button v-for="c in CATS" :key="c.k" size="tiny" :type="cat===c.k?'primary':'default'" @click="switchCat(c.k)">{{c.t}}</n-button>
    </n-button-group>
    <n-select v-model:value="imp" size="tiny" style="width:112px" :options="IMP_OPTS" />
    <n-button size="tiny" :type="onlyFocus?'primary':'default'" @click="toggleFocus">
      <AppIcon name="target" :size="13" /> 只看关注池
    </n-button>
    <n-input v-model:value="kw" size="tiny" style="width:180px" placeholder="标题/摘要关键词..." clearable @keyup.enter="doSearch" />
    <n-button type="primary" size="tiny" :loading="loading" @click="doSearch">搜索</n-button>
    <span style="font-size:11px;color:var(--c-text-faint)">共 {{total}} 条</span>
  </n-space>

  <div class="fill-scroll" style="flex:1;min-height:0;overflow-y:auto">
    <n-spin :show="loading">
      <div v-if="!rows.length && !loading" style="padding:32px 0;text-align:center;color:var(--c-text-faint);font-size:12px">
        没有符合条件的资讯
      </div>
      <div style="display:flex;flex-direction:column;gap:6px;padding-bottom:8px">
        <div v-for="n in rows" :key="n.id" class="nv-item" @click="open(n)">
          <div class="nv-meta">
            <span>{{ (n.published_at||'').slice(5,16) }}</span>
            <span class="nv-cat" :style="{color:CAT_COLOR[n.category]||'var(--c-text-dim)'}">{{ CAT_NAME[n.category]||n.category }}</span>
            <span>{{ SRC_NAME[n.source]||n.source }}</span>
            <!-- 重要度：3 级只在真正重大时出现，故用色块而非数字（质量/状态域，红=高） -->
            <span v-if="n.importance===3" class="nv-imp nv-imp3" title="重要度 3：全市场级或个股重大">重</span>
            <span v-else-if="n.importance===2" class="nv-imp nv-imp2" title="重要度 2：行业级或有实质信息">中</span>
            <span v-if="n.summary===null" class="nv-pending" title="尚未 LLM 总结（每 20 分钟一轮增量总结）">待总结</span>
            <span v-if="sentiOf(n.sentiment)" :style="{color:sentiOf(n.sentiment).c,fontWeight:600}">{{ sentiOf(n.sentiment).t }}</span>
            <span style="margin-left:auto;flex-shrink:0;display:inline-flex;gap:5px;align-items:center">
              <span v-for="s in n.stocks.slice(0,3)" :key="s.code" class="nv-stock" @click.stop="emit('show-detail', s.code)">{{ s.name }}</span>
            </span>
          </div>
          <div class="nv-title" :title="n.title">{{ n.title }}</div>
          <!-- 摘要与标题重叠时不重复显示（行情统计类素材标题即摘要，模型输出常与标题同义） -->
          <div v-if="n.summary && !sameAsTitle(n)" class="nv-summary">{{ n.summary }}</div>
          <div v-if="n.topics.length" class="nv-topics">
            <span v-for="t in n.topics.slice(0,4)" :key="t" class="nv-topic">{{ t }}</span>
          </div>
        </div>
      </div>
    </n-spin>
  </div>
  <ListPagination :total="total" :page="page" :page-size="PAGE_SIZE" @change="goPage" />
</div>
</template>
<script setup>
import AppIcon from './AppIcon.vue'
import ListPagination from './ListPagination.vue'
import { ref, onMounted, onUnmounted } from 'vue'
import { useNavStore } from '../stores/nav'
import { NButton, NButtonGroup, NInput, NSelect, NSpace, NSpin, useMessage } from 'naive-ui'
import axios from 'axios'
import { UP_COLOR, DOWN_COLOR } from '../utils/ui'

const emit = defineEmits(['show-detail'])
const message = useMessage()
const API = window.location.origin
const PAGE_SIZE = 30
const CATS = [
  { k: '', t: '全部' }, { k: 'flash', t: '快讯' },
  { k: 'stock_news', t: '个股' }, { k: 'report', t: '研报' }, { k: 'notice', t: '公告' },
]
const IMP_OPTS = [
  { label: '全部重要度', value: 0 }, { label: '≥ 中', value: 2 }, { label: '仅重大', value: 3 },
]
const CAT_NAME = { flash: '快讯', stock_news: '个股新闻', report: '研报', notice: '公告' }
// 分类色属「质量/状态域」的标识色，不表达涨跌方向，故不受红绿铁律约束
const CAT_COLOR = { flash: 'var(--c-info)', stock_news: 'var(--c-text-dim)', report: '#a78bfa', notice: '#f59e0b' }
const SRC_NAME = { cls: '财联社', em724: '东财7×24', wscn: '华尔街见闻', em_stock: '东财', em_report: '东财研报', cninfo: '巨潮' }
const SENTI = { 1: { t: '利好', c: UP_COLOR }, 0: { t: '中性', c: 'var(--c-text-faint)' }, '-1': { t: '利空', c: DOWN_COLOR } }
const sentiOf = s => (s === null || s === undefined) ? null : SENTI[s] || SENTI[String(s)]

// 默认「≥ 中」：重要度分层就是为了把行情统计类噪音（imp=1，实测约占 2/3）挡在默认视图外
const cat = ref(''), imp = ref(2), kw = ref(''), onlyFocus = ref(false), page = ref(1)
const rows = ref([]), total = ref(0), loading = ref(false)

// URL 状态持久化（#/news?cat=&imp=&kw=&sf=&page=）：与个股列表同约定
function parseHash() {
  try {
    const q = new URLSearchParams(location.hash.split('?')[1] || '')
    const c = q.get('cat'); if (c !== null) cat.value = c || ''
    const i = parseInt(q.get('imp')); if (!isNaN(i)) imp.value = i
    const k = q.get('kw'); if (k) kw.value = k
    onlyFocus.value = q.get('sf') === '1'
    const p = parseInt(q.get('page')); if (p > 0) page.value = p
  } catch (e) {}
}
function syncHash() {
  const q = new URLSearchParams()
  q.set('cat', cat.value); q.set('imp', imp.value)
  if (kw.value) q.set('kw', kw.value)
  q.set('sf', onlyFocus.value ? '1' : '0'); q.set('page', page.value)
  history.replaceState(null, '', location.pathname + '#/news?' + q.toString())
}

// 摘要 ≈ 标题时不重复渲染（行情统计类素材标题即摘要，两行同义视觉上是噪音）
function sameAsTitle(n) {
  const a = (n.title || '').replace(/[\s，。：；、]/g, ''), b = (n.summary || '').replace(/[\s，。：；、]/g, '')
  if (!a || !b) return false
  return a === b || a.startsWith(b) || b.startsWith(a)
}

async function load() {
  loading.value = true
  syncHash()
  try {
    const r = await axios.get(API + '/api/news', { params: {
      category: cat.value, source: '', code: '', min_importance: imp.value,
      only_focus: onlyFocus.value, since_hours: 0, keyword: kw.value,
      sort: 'time', page: page.value, page_size: PAGE_SIZE,
    }})
    rows.value = r.data.data || []
    total.value = r.data.total || 0
  } catch (e) {
    message.error('资讯加载失败，请重试')
  } finally {
    loading.value = false
  }
}
function open(n) { if (n.url) window.open(n.url, '_blank', 'noopener') }
function switchCat(c) { cat.value = c; page.value = 1; load() }
function toggleFocus() { onlyFocus.value = !onlyFocus.value; page.value = 1; load() }
function doSearch() { page.value = 1; load() }
function goPage(p) { page.value = p; load() }

const nav = useNavStore()
function onPopstate() {
  // 组件可能已卸载（v-if 切换）：非本页时不响应，避免用本页 URL 覆盖地址栏
  if (nav.tab !== 'n') return
  parseHash()
  load()
}
onMounted(() => { parseHash(); load(); window.addEventListener('popstate', onPopstate) })
onUnmounted(() => window.removeEventListener('popstate', onPopstate))
</script>
<style scoped>
.nv-item {
  padding: 7px 9px; background: var(--c-card-bg); border-radius: 6px;
  border: 1px solid var(--c-border); cursor: pointer;
}
.nv-item:hover { border-color: var(--c-card-bg-hover); }
.nv-meta {
  display: flex; align-items: center; gap: 7px; font-size: 10px;
  color: var(--c-text-faint); margin-bottom: 3px;
}
.nv-cat { font-weight: 600; }
.nv-imp { border-radius: 8px; padding: 0 5px; font-weight: 600; }
.nv-imp3 { background: rgba(239,68,68,0.16); color: #ef4444; }
.nv-imp2 { background: rgba(148,163,184,0.18); color: var(--c-text-dim); }
.nv-pending { background: rgba(148,163,184,0.13); border-radius: 8px; padding: 0 5px; }
.nv-stock {
  background: rgba(148,163,184,0.16); border-radius: 8px; padding: 0 5px;
  color: var(--c-info); cursor: pointer; white-space: nowrap;
}
.nv-title { font-size: 12px; line-height: 1.5; color: var(--c-text); font-weight: 500; word-break: break-word; }
.nv-summary { font-size: 11px; line-height: 1.55; color: var(--c-text-dim); margin-top: 2px; word-break: break-word; }
.nv-topics { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 4px; }
.nv-topic { font-size: 10px; color: var(--c-text-faint); background: rgba(148,163,184,0.12); border-radius: 3px; padding: 0 4px; }
</style>
