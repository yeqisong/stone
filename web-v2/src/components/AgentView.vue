<template>
<div class="agent-wrap">
  <!-- 左：会话列表（窄屏收起，改由头部下拉选择） -->
  <aside v-if="!isNarrow" class="agent-side">
    <div class="agent-side-head">
      <n-button size="small" type="primary" block @click="newSession"><AppIcon name="plus" :size="12" /> 新会话</n-button>
      <n-input v-if="sessions.length > 6" v-model:value="sessQ" size="small" placeholder="筛选会话"
               style="margin-top:6px" clearable />
    </div>
    <div class="agent-sess-list">
      <div v-for="s in filteredSessions" :key="s.id" class="agent-sess" :class="{ active: s.id === sid }"
           @click="selectSession(s.id)">
        <template v-if="editingId === s.id">
          <n-input v-model:value="editingTitle" size="tiny" @keyup.enter="commitRename" @blur="commitRename" @click.stop />
        </template>
        <template v-else>
          <div class="agent-sess-body">
            <span class="agent-sess-title" :title="s.title">{{ s.title }}</span>
            <span class="agent-sess-time">{{ relTime(s.updated_at) }}</span>
          </div>
          <span class="agent-sess-tools" @click.stop>
            <AppIcon name="edit" :size="11" class="agent-ico" title="重命名" @click="startRename(s)" />
            <AppIcon name="trash" :size="11" class="agent-ico agent-ico-danger" title="删除" @click="removeSession(s)" />
          </span>
        </template>
      </div>
      <div v-if="!filteredSessions.length" class="agent-side-empty">
        {{ sessions.length ? '没有匹配的会话' : '还没有会话，点上方新建' }}
      </div>
    </div>
    <div class="agent-side-foot">
      <span v-if="info && !info.configured" style="color:#ef4444">未配置大模型 Key</span>
      <span v-else>{{ info?.model || '-' }} · {{ info?.tools?.length || 0 }} 工具 · {{ skills.length }} 技能</span>
    </div>
  </aside>

  <!-- 右：对话 -->
  <section class="agent-main">
    <header class="agent-head">
      <n-select v-if="isNarrow" size="small" :options="sessOptions" :value="sid" style="width:150px"
                placeholder="选择会话" @update:value="selectSession" />
      <div class="agent-head-title" :title="currentTitle">{{ currentTitle }}</div>
      <div class="agent-head-meta">
        <n-tag v-if="info?.model" size="small" :bordered="false" type="info">{{ info.model }}</n-tag>
        <n-button size="tiny" quaternary @click="openSkills"><AppIcon name="compass" :size="12" /> 技能 {{ skills.length }}</n-button>
        <n-button v-if="isNarrow" size="tiny" quaternary @click="newSession"><AppIcon name="plus" :size="12" /></n-button>
      </div>
    </header>

    <div ref="scrollEl" class="agent-thread" @scroll="onScroll">
      <!-- 空态：讲清能力边界，而不只是一句提示 -->
      <div v-if="!blocks.length" class="agent-empty">
        <div class="agent-empty-title">把问题交给它——数据在本地库，分析在沙箱，流程能直接建</div>
        <div class="agent-cards">
          <div v-for="c in capabilities" :key="c.t" class="agent-card">
            <div class="agent-card-t"><AppIcon :name="c.icon" :size="12" /> {{ c.t }}</div>
            <div class="agent-card-d">{{ c.d }}</div>
          </div>
        </div>
        <div class="agent-empty-sub">试试这些：</div>
        <div class="agent-chips">
          <button v-for="c in quickAsks" :key="c" class="agent-chip" @click="fillPrompt(c)">{{ c }}</button>
        </div>
      </div>

      <div v-for="(b, i) in blocks" :key="i" class="agent-block" :class="b.role">
        <template v-if="b.role === 'user'">
          <div class="agent-bubble user">{{ b.text }}</div>
        </template>
        <template v-else>
          <div v-if="b.text" class="agent-ans">
            <div class="agent-bubble assistant" v-html="renderMarkdown(b.text)"></div>
            <button class="agent-copy" title="复制回答" @click="copyText(b.text)"><AppIcon name="save" :size="11" /></button>
          </div>
          <div v-for="t in b.tools" :key="t.id" class="agent-tool" :class="{ fail: t.result && !t.result.ok }">
            <div class="agent-tool-head" @click="toggleTool(t.id)">
              <AppIcon :name="t.result ? (t.result.ok ? 'check' : 'alert') : 'refresh'" :size="11"
                       :style="{ color: t.result ? (t.result.ok ? '#10b981' : '#ef4444') : 'var(--c-text-dim)' }" />
              <span class="agent-tool-name">{{ toolLabel(t.name) }}</span>
              <span class="agent-tool-sum">{{ argsSummary(t) }}</span>
              <span class="agent-tool-meta">{{ t.result ? `${t.result.elapsed_ms}ms` : '运行中…' }}</span>
              <span class="agent-tool-fold">{{ expanded[t.id] ? '收起' : '详情' }}</span>
            </div>
            <pre v-if="expanded[t.id]" class="agent-tool-body">{{ t.result ? t.result.preview : JSON.stringify(t.args, null, 2) }}</pre>
          </div>
        </template>
      </div>

      <div v-if="running" class="agent-running">
        <span class="agent-dot"></span>{{ statusText }}
      </div>
    </div>

    <button v-if="showJump" class="agent-jump" @click="scrollDown(true)">↓ 回到底部</button>

    <footer class="agent-composer">
      <n-input v-model:value="draft" type="textarea" size="small" :autosize="{ minRows: 2, maxRows: 6 }"
               placeholder="描述你的问题或任务…（Enter 发送，Shift+Enter 换行）" :disabled="running"
               @keydown="onKey" />
      <div class="agent-composer-bar">
        <span class="agent-hint">
          {{ running ? statusText : '数据查库 · 沙箱可跑 Python/Node · 能建 DAG 流程' }}
        </span>
        <n-button v-if="running" size="small" type="error" secondary @click="stopRun">
          <AppIcon name="close" :size="12" /> 停止
        </n-button>
        <n-button v-else size="small" type="primary" :disabled="!draft.trim()" @click="send">
          <AppIcon name="send" :size="12" /> 发送
        </n-button>
      </div>
    </footer>
  </section>

  <!-- 技能面板 -->
  <n-modal v-model:show="showSkills" preset="card" title="技能（可复用的分析方法）" style="width:780px;max-width:94vw">
    <div class="agent-sk-wrap">
      <div class="agent-sk-list">
        <n-button size="tiny" type="primary" block style="margin-bottom:6px" @click="startNewSkill">
          <AppIcon name="plus" :size="12" /> 新建技能
        </n-button>
        <div v-for="s in skills" :key="s.name" class="agent-sess" :class="{ active: skEditing.name === s.name }"
             @click="openSkill(s)">
          <div class="agent-sess-body">
            <span class="agent-sess-title" :title="s.description">{{ s.display_name }}</span>
            <span class="agent-sess-time">{{ s.builtin ? '内置' : '用户' }}</span>
          </div>
        </div>
      </div>
      <div class="agent-sk-edit">
        <n-space align="center" :wrap="false" size="small">
          <n-input v-model:value="skEditing.name" size="small" placeholder="英文名（如 my-screen）" style="width:190px"
                   :disabled="skEditing.builtin" />
          <n-input v-model:value="skEditing.title" size="small" placeholder="中文标题（可选）" style="width:150px" />
          <n-tag v-if="skEditing.builtin" size="small" type="warning" :bordered="false">内置只读</n-tag>
        </n-space>
        <n-input v-model:value="skEditing.description" size="small" placeholder="何时使用（一句话，模型靠它判断命中）"
                 :disabled="skEditing.builtin" />
        <n-input v-model:value="skEditing.content" type="textarea" size="small"
                 :autosize="{ minRows: 12, maxRows: 20 }" placeholder="Markdown：步骤 / SQL 模板 / 输出格式 / 注意事项"
                 :disabled="skEditing.builtin" class="agent-sk-code" />
        <div class="agent-sk-actions">
          <n-button v-if="!skEditing.builtin" size="small" type="primary" :loading="skSaving" @click="saveSkill">保存</n-button>
          <n-button v-if="!skEditing.builtin && isUserSkill" size="small" type="error" quaternary @click="removeSkill">删除</n-button>
          <n-button size="small" quaternary @click="useSkill">在对话中使用</n-button>
          <span class="agent-hint">正文按需读取，不进常驻上下文</span>
        </div>
      </div>
    </div>
  </n-modal>
</div>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import { ref, computed, onMounted, nextTick } from 'vue'
import { NButton, NInput, NSelect, NTag, NModal, useMessage, useDialog } from 'naive-ui'
import axios from 'axios'
import { useAuthStore } from '../stores/auth'
import { useViewport } from '../utils/viewport'
import { renderMarkdown } from '../utils/markdown'

const { isNarrow } = useViewport()
const API = window.location.origin
const auth = useAuthStore()
const message = useMessage()
const dialog = useDialog()

const sessions = ref([])
const sid = ref(null)
const blocks = ref([])
const draft = ref('')
const running = ref(false)
const statusText = ref('思考中…')
const info = ref(null)
const scrollEl = ref(null)
const expanded = ref({})
const sessQ = ref('')
const showJump = ref(false)
let abortCtl = null
let nearBottom = true

const editingId = ref(null), editingTitle = ref('')
const currentTitle = computed(() => sessions.value.find(s => s.id === sid.value)?.title || '新会话')
const filteredSessions = computed(() => {
  const q = sessQ.value.trim().toLowerCase()
  return q ? sessions.value.filter(s => (s.title || '').toLowerCase().includes(q)) : sessions.value
})
const sessOptions = computed(() => sessions.value.map(s => ({ label: s.title, value: s.id })))

const capabilities = [
  { t: '数据查询', d: '行情/特征/信号/资讯/雪球/持仓，直接查本地库', icon: 's' },
  { t: '沙箱计算', d: 'Python/Node 跑统计与画图，依赖装进会话私有目录', icon: 'zap' },
  { t: '流程编排', d: '建 DAG 草稿、配 cron、试跑，把分析变成自动化', icon: 'g' },
  { t: '技能复用', d: '沉淀分析方法，下次一句话按同一口径执行', icon: 'compass' },
]
const quickAsks = [
  '今天哪些个股资金净流入最多？',
  '持仓和纸面组合现在的盈亏情况',
  '最近三天有哪些重要资讯？',
  '帮我建一个每天盘后看信号的流程',
]

const TOOL_LABEL = {
  query_sql: 'SQL 查询', list_tables: '表清单', describe_table: '表结构',
  stock_overview: '个股全景', search_news: '资讯检索',
  run_python: 'Python', run_node: 'Node', pip_install: 'pip 安装',
  npm_install: 'npm 安装', list_workspace: '工作区',
  list_dag_flows: '流程列表', create_dag_flow: '建流程', run_dag_flow: '触发流程',
  list_skills: '技能清单', read_skill: '读技能', save_skill: '存技能',
}

const H = () => auth.token ? { Authorization: 'Bearer ' + auth.token } : {}

function toolLabel(n) { return TOOL_LABEL[n] || n }
function relTime(s) {
  if (!s) return ''
  const d = new Date(String(s).replace(' ', 'T'))
  if (isNaN(d)) return ''
  const diff = (Date.now() - d.getTime()) / 1000
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  if (diff < 172800) return '昨天'
  return `${d.getMonth() + 1}/${d.getDate()}`
}

function argsSummary(t) {
  if (!t.args) return ''
  const keys = Object.keys(t.args)
  if (!keys.length) return ''
  const v = t.args[keys[0]]
  const s = typeof v === 'string' ? v : JSON.stringify(v)
  return s.replace(/\s+/g, ' ').slice(0, 64)
}
function toggleTool(id) { expanded.value = { ...expanded.value, [id]: !expanded.value[id] } }
function fillPrompt(c) { draft.value = c }
async function copyText(t) {
  try { await navigator.clipboard.writeText(t); message.success('已复制') }
  catch (e) { message.error('复制失败') }
}

function onKey(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send() }
}
function onScroll() {
  const el = scrollEl.value
  if (!el) return
  nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  showJump.value = !nearBottom && el.scrollHeight > el.clientHeight + 200
}
function scrollDown(force = false) {
  nextTick(() => {
    const el = scrollEl.value
    if (!el) return
    if (!force && !nearBottom) return
    el.scrollTop = el.scrollHeight
    nearBottom = true
    showJump.value = false
  })
}

async function loadInfo() {
  try { info.value = (await axios.get(API + '/api/agent/info', { headers: H() })).data } catch (e) {}
}
async function loadSessions() {
  try {
    const r = await axios.get(API + '/api/agent/sessions', { headers: H() })
    sessions.value = r.data.items || []
  } catch (e) { message.error('会话加载失败') }
}
async function newSession() {
  try {
    const r = await axios.post(API + '/api/agent/sessions', { title: '新会话' }, { headers: H() })
    sessions.value.unshift({ id: r.data.id, title: r.data.title, updated_at: '', message_count: 0 })
    sid.value = r.data.id
    blocks.value = []
    draft.value = ''
  } catch (e) { message.error('创建会话失败') }
}
function selectSession(id) {
  if (!id) return
  if (running.value) { message.warning('当前会话还在运行，先停止或等待完成'); return }
  sid.value = id
  loadMessages(id)
}
async function loadMessages(id) {
  blocks.value = []
  try {
    const r = await axios.get(`${API}/api/agent/sessions/${id}/messages`, { headers: H() })
    blocks.value = toBlocks(r.data.items || [])
    nearBottom = true
    scrollDown(true)
  } catch (e) { message.error('消息加载失败') }
}
function toBlocks(msgs) {
  const out = []
  const byCall = {}
  for (const m of msgs) {
    if (m.role === 'user') out.push({ role: 'user', text: m.content })
    else if (m.role === 'assistant') {
      const b = { role: 'assistant', text: m.content || '', tools: [] }
      for (const tc of (m.tool_calls || [])) {
        let args = {}
        try { args = JSON.parse(tc.function?.arguments || '{}') } catch (e) {}
        const t = { id: tc.id, name: tc.function?.name || '', args, result: null }
        b.tools.push(t); byCall[tc.id] = t
      }
      if (b.text || b.tools.length) out.push(b)
    } else if (m.role === 'tool') {
      const t = byCall[m.tool_call_id]
      if (t) t.result = { ok: m.meta?.ok !== false, elapsed_ms: m.meta?.elapsed_ms || 0, preview: m.content || '' }
    }
  }
  return out
}
function startRename(s) { editingId.value = s.id; editingTitle.value = s.title }
async function commitRename() {
  const id = editingId.value
  if (!id) return
  editingId.value = null
  const title = (editingTitle.value || '').trim() || '新会话'
  try {
    await axios.patch(`${API}/api/agent/sessions/${id}`, { title }, { headers: H() })
    const s = sessions.value.find(x => x.id === id); if (s) s.title = title
  } catch (e) { message.error('重命名失败') }
}
function removeSession(s) {
  dialog.warning({
    title: '删除会话', content: `删除「${s.title}」？消息与沙箱文件将一并清除。`,
    positiveText: '删除', negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await axios.delete(`${API}/api/agent/sessions/${s.id}`, { headers: H() })
        sessions.value = sessions.value.filter(x => x.id !== s.id)
        if (sid.value === s.id) { sid.value = null; blocks.value = [] }
        message.success('已删除')
      } catch (e) { message.error('删除失败') }
    },
  })
}

async function stopRun() {
  if (abortCtl) { try { abortCtl.abort() } catch (e) {} }
  try { await axios.post(`${API}/api/agent/sessions/${sid.value}/stop`, {}, { headers: H() }) } catch (e) {}
  running.value = false
  statusText.value = '已停止'
}

async function send() {
  const content = draft.value.trim()
  if (!content || running.value) return
  if (!sid.value) await newSession()
  draft.value = ''
  blocks.value.push({ role: 'user', text: content })
  running.value = true
  statusText.value = '思考中…'
  nearBottom = true
  scrollDown(true)

  abortCtl = new AbortController()
  try {
    const resp = await fetch(`${API}/api/agent/sessions/${sid.value}/chat`, {
      method: 'POST', headers: { ...H(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }), signal: abortCtl.signal,
    })
    if (!resp.ok || !resp.body) {
      const t = await resp.text()
      throw new Error(t.slice(0, 200))
    }
    const reader = resp.body.getReader()
    const dec = new TextDecoder()
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      const parts = buf.split('\n\n')
      buf = parts.pop()
      for (const p of parts) {
        const line = p.trim()
        if (!line.startsWith('data: ')) continue
        let ev = {}
        try { ev = JSON.parse(line.slice(6)) } catch (e) { continue }
        handleEvent(ev)
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') message.error('对话失败: ' + (e.message || e))
  } finally {
    running.value = false
    abortCtl = null
    loadSessions()
  }
}

function handleEvent(ev) {
  const t = ev.type
  if (t === 'assistant') {
    blocks.value.push({ role: 'assistant', text: ev.content || '', tools: [] })
    scrollDown()
  } else if (t === 'tool_call') {
    let b = blocks.value[blocks.value.length - 1]
    if (!b || b.role !== 'assistant') { b = { role: 'assistant', text: '', tools: [] }; blocks.value.push(b) }
    b.tools.push({ id: ev.id, name: ev.name, args: ev.args || {}, result: null })
    statusText.value = `${toolLabel(ev.name)}…`
    scrollDown()
  } else if (t === 'tool_result') {
    for (const b of blocks.value) {
      const tt = (b.tools || []).find(x => x.id === ev.id)
      if (tt) { tt.result = { ok: ev.ok, elapsed_ms: ev.elapsed_ms, preview: ev.preview }; break }
    }
    statusText.value = '整理结果…'
    scrollDown()
  } else if (t === 'done') {
    running.value = false
  } else if (t === 'stopped') {
    running.value = false
  } else if (t === 'error') {
    running.value = false
    blocks.value.push({ role: 'assistant', text: '⚠️ ' + (ev.error || '未知错误'), tools: [] })
    scrollDown()
  }
}

// ── 技能 ──
const showSkills = ref(false)
const skills = ref([])
const skSaving = ref(false)
const skEditing = ref({ name: '', title: '', description: '', content: '', builtin: false })
const isUserSkill = computed(() => skills.value.some(x => x.name === skEditing.value.name && !x.builtin))

async function loadSkills() {
  try { skills.value = (await axios.get(API + '/api/agent/skills', { headers: H() })).data.items || [] } catch (e) {}
}
async function openSkills() {
  showSkills.value = true
  await loadSkills()
  if (skills.value.length) openSkill(skills.value[0])
}
async function openSkill(s) {
  try {
    const r = await axios.get(`${API}/api/agent/skills/${s.name}`, { headers: H() })
    skEditing.value = { name: s.name, title: s.display_name, description: s.description, content: r.data.content, builtin: !!s.builtin }
  } catch (e) { message.error('技能读取失败') }
}
function startNewSkill() {
  skEditing.value = { name: '', title: '', description: '', content: '', builtin: false }
}
async function saveSkill() {
  const e = skEditing.value
  if (!e.name.trim() || !e.description.trim() || !e.content.trim()) { message.warning('名称、描述、正文都要填'); return }
  skSaving.value = true
  try {
    await axios.post(API + '/api/agent/skills', {
      name: e.name.trim(), title: e.title, description: e.description, content: e.content }, { headers: H() })
    message.success('技能已保存（下一条消息起生效）')
    await loadSkills()
  } catch (err) { message.error(err.response?.data?.detail || '保存失败') }
  skSaving.value = false
}
async function removeSkill() {
  const e = skEditing.value
  dialog.warning({
    title: '删除技能', content: `删除技能「${e.name}」？`,
    positiveText: '删除', negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await axios.delete(`${API}/api/agent/skills/${e.name}`, { headers: H() })
        message.success('已删除'); showSkills.value = false; await loadSkills()
      } catch (err) { message.error(err.response?.data?.detail || '删除失败') }
    },
  })
}
function useSkill() {
  draft.value = `请使用技能 ${skEditing.value.name} 帮我分析：`
  showSkills.value = false
}

onMounted(async () => {
  await Promise.all([loadInfo(), loadSessions(), loadSkills()])
  if (sessions.value.length) { sid.value = sessions.value[0].id; await loadMessages(sid.value) }
})
</script>

<style scoped>
.agent-wrap { display: flex; flex: 1; min-height: 0; gap: 10px; }

/* ── 侧栏 ── */
.agent-side { width: 216px; flex-shrink: 0; display: flex; flex-direction: column;
  background: var(--c-card-bg); border: 1px solid var(--c-card-bg-hover); border-radius: 8px; padding: 8px; }
.agent-side-head { flex-shrink: 0; }
.agent-sess-list { flex: 1; overflow-y: auto; min-height: 0; margin-top: 6px; }
.agent-sess { display: flex; align-items: center; gap: 4px; padding: 6px 8px; border-radius: 6px;
  cursor: pointer; font-size: 12px; color: var(--c-text); border-left: 2px solid transparent; }
.agent-sess:hover { background: var(--c-card-bg-hover); }
.agent-sess.active { background: rgba(32,128,240,0.10); border-left-color: #2080f0; }
.agent-sess-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.agent-sess-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.agent-sess-time { font-size: 9.5px; color: var(--c-text-faint); }
.agent-sess-tools { display: none; gap: 5px; flex-shrink: 0; }
.agent-sess:hover .agent-sess-tools { display: flex; }
.agent-ico { color: var(--c-text-faint); cursor: pointer; }
.agent-ico:hover { color: #2080f0; }
.agent-ico-danger:hover { color: #ef4444; }
.agent-side-empty { font-size: 11px; color: var(--c-text-faint); padding: 12px 8px; line-height: 1.6; }
.agent-side-foot { flex-shrink: 0; padding-top: 6px; margin-top: 4px; font-size: 10px;
  color: var(--c-text-faint); border-top: 1px solid var(--c-border-light); }

/* ── 主区 ── */
.agent-main { position: relative; flex: 1; min-width: 0; display: flex; flex-direction: column;
  background: var(--c-card-bg); border: 1px solid var(--c-card-bg-hover); border-radius: 8px; overflow: hidden; }
.agent-head { display: flex; align-items: center; gap: 8px; padding: 7px 12px;
  border-bottom: 1px solid var(--c-border-light); flex-shrink: 0; }
.agent-head-title { font-size: 13px; font-weight: 600; color: var(--c-text);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.agent-head-meta { margin-left: auto; display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.agent-thread { flex: 1; overflow-y: auto; padding: 14px 16px 8px; min-height: 0; }

.agent-empty { padding: 18px 4px 8px; }
.agent-empty-title { font-size: 13px; color: var(--c-text); margin-bottom: 14px; }
.agent-cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-bottom: 16px; }
.agent-card { padding: 9px 11px; background: var(--c-bg); border: 1px solid var(--c-border-light); border-radius: 7px; }
.agent-card-t { font-size: 11.5px; font-weight: 600; color: var(--c-text); display: flex; align-items: center; gap: 5px; }
.agent-card-d { font-size: 10.5px; color: var(--c-text-dim); margin-top: 3px; line-height: 1.5; }
.agent-empty-sub { font-size: 11px; color: var(--c-text-dim); margin-bottom: 7px; }
.agent-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.agent-chip { font-size: 11px; padding: 5px 11px; border-radius: 12px; cursor: pointer;
  background: var(--c-bg); border: 1px solid var(--c-border); color: var(--c-text-dim); transition: all .15s; }
.agent-chip:hover { color: var(--c-text); border-color: #2080f0; }

.agent-block { margin-bottom: 12px; display: flex; flex-direction: column; }
.agent-block.user { align-items: flex-end; }
.agent-bubble { font-size: 12.5px; line-height: 1.72; padding: 8px 12px; border-radius: 8px;
  word-break: break-word; }
.agent-bubble.user { background: rgba(32,128,240,0.13); color: var(--c-text); white-space: pre-wrap;
  max-width: 82%; border-bottom-right-radius: 3px; }
.agent-ans { position: relative; max-width: 92%; }
.agent-bubble.assistant { background: var(--c-bg); color: var(--c-text);
  border: 1px solid var(--c-border-light); border-bottom-left-radius: 3px; }
.agent-copy { position: absolute; top: 6px; right: 6px; opacity: 0; border: 0; cursor: pointer;
  background: var(--c-card-bg-hover); color: var(--c-text-faint); border-radius: 4px; padding: 2px 4px; }
.agent-ans:hover .agent-copy { opacity: 1; }
.agent-copy:hover { color: var(--c-text); }

/* Markdown 排版 */
.agent-bubble.assistant :deep(.agent-p) { margin: 0 0 6px; }
.agent-bubble.assistant :deep(.agent-p:last-child) { margin-bottom: 0; }
.agent-bubble.assistant :deep(.agent-h) { font-weight: 600; color: var(--c-text); margin: 10px 0 5px; }
.agent-bubble.assistant :deep(.agent-h1) { font-size: 14px; }
.agent-bubble.assistant :deep(.agent-h2) { font-size: 13px; }
.agent-bubble.assistant :deep(.agent-h3),
.agent-bubble.assistant :deep(.agent-h4) { font-size: 12.5px; }
.agent-bubble.assistant :deep(.agent-list) { margin: 4px 0 6px; padding-left: 18px; }
.agent-bubble.assistant :deep(.agent-list li) { margin: 2px 0; }
.agent-bubble.assistant :deep(.agent-code) { display: block; margin: 6px 0; padding: 8px 10px;
  font-size: 11px; background: var(--c-card-bg); border-radius: 6px; overflow-x: auto; white-space: pre; }
.agent-bubble.assistant :deep(.agent-inline) { font-size: 11.5px; padding: 1px 4px;
  background: var(--c-card-bg); border-radius: 4px; }
.agent-bubble.assistant :deep(.agent-table-wrap) { overflow-x: auto; margin: 7px 0; }
.agent-bubble.assistant :deep(.agent-table) { border-collapse: collapse; font-size: 11.5px; width: 100%; }
.agent-bubble.assistant :deep(.agent-table th) { text-align: left; padding: 4px 8px; font-weight: 600;
  color: var(--c-text); background: var(--c-card-bg-hover); border: 1px solid var(--c-border-light); white-space: nowrap; }
.agent-bubble.assistant :deep(.agent-table td) { padding: 4px 8px; color: var(--c-text-dim);
  border: 1px solid var(--c-border-light); }
.agent-bubble.assistant :deep(.agent-table tr:nth-child(even) td) { background: rgba(128,128,128,0.03); }

/* 工具卡片 */
.agent-tool { margin-top: 6px; border: 1px solid var(--c-border-light); border-radius: 6px;
  background: var(--c-bg); overflow: hidden; max-width: 88%; }
.agent-tool.fail { border-color: rgba(239,68,68,0.35); }
.agent-tool-head { display: flex; align-items: center; gap: 6px; padding: 5px 9px; cursor: pointer; }
.agent-tool-head:hover { background: var(--c-card-bg-hover); }
.agent-tool-name { font-size: 11px; font-weight: 600; color: var(--c-text); flex-shrink: 0; }
.agent-tool-sum { font-size: 10px; color: var(--c-text-faint); flex: 1; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; font-family: ui-monospace, monospace; }
.agent-tool-meta { font-size: 9.5px; color: var(--c-text-faint); flex-shrink: 0; }
.agent-tool-fold { font-size: 9.5px; color: var(--c-text-dimmer); flex-shrink: 0; }
.agent-tool-body { margin: 0; padding: 8px 10px; font-size: 10.5px; line-height: 1.55;
  max-height: 280px; overflow: auto; white-space: pre-wrap; word-break: break-all;
  border-top: 1px solid var(--c-border-light); color: var(--c-text-dim); background: var(--c-card-bg); }

.agent-running { display: flex; align-items: center; font-size: 11px; color: var(--c-text-dim); padding: 4px 2px 10px; }
.agent-dot { width: 6px; height: 6px; border-radius: 50%; background: #2080f0; margin-right: 6px;
  animation: agent-pulse 1s infinite; }
@keyframes agent-pulse { 0%,100% { opacity: 0.3 } 50% { opacity: 1 } }

.agent-jump { position: absolute; right: 22px; bottom: 88px; z-index: 5; font-size: 11px;
  padding: 5px 10px; border-radius: 14px; cursor: pointer; color: var(--c-text-dim);
  background: var(--c-card-bg-hover); border: 1px solid var(--c-border); box-shadow: 0 2px 8px rgba(0,0,0,0.18); }
.agent-jump:hover { color: var(--c-text); }

.agent-composer { flex-shrink: 0; padding: 8px 12px 10px; border-top: 1px solid var(--c-border-light); }
.agent-composer-bar { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
.agent-hint { font-size: 10px; color: var(--c-text-faint); flex: 1; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }

/* 技能面板 */
.agent-sk-wrap { display: flex; gap: 12px; min-height: 380px; }
.agent-sk-list { width: 224px; flex-shrink: 0; border-right: 1px solid var(--c-border-light); padding-right: 8px; }
.agent-sk-edit { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.agent-sk-code :deep(textarea) { font-family: ui-monospace, monospace; font-size: 11.5px; }
.agent-sk-actions { display: flex; align-items: center; gap: 8px; }

@media (max-width: 900px) {
  .agent-main { border-radius: 6px; }
  .agent-thread { padding: 10px 10px 6px; }
  .agent-bubble.user, .agent-ans, .agent-tool { max-width: 100%; }
  .agent-cards { grid-template-columns: 1fr; }
  .agent-sk-wrap { flex-direction: column; }
  .agent-sk-list { width: 100%; border-right: 0; border-bottom: 1px solid var(--c-border-light); padding-bottom: 8px; }
}
</style>
