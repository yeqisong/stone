<template>
<div class="agent-wrap">
  <!-- 左：会话列表 -->
  <aside class="agent-side">
    <div class="agent-side-head">
      <n-button size="tiny" type="primary" block @click="newSession"><AppIcon name="plus" :size="12" /> 新会话</n-button>
    </div>
    <div class="agent-sess-list">
      <div v-for="s in sessions" :key="s.id" class="agent-sess" :class="{ active: s.id === sid }" @click="selectSession(s.id)">
        <template v-if="editingId === s.id">
          <n-input v-model:value="editingTitle" size="tiny" @keyup.enter="commitRename" @blur="commitRename" @click.stop />
        </template>
        <template v-else>
          <span class="agent-sess-title" :title="s.title">{{ s.title }}</span>
          <span class="agent-sess-tools" @click.stop>
            <AppIcon name="edit" :size="11" class="agent-ico" title="重命名" @click="startRename(s)" />
            <AppIcon name="trash" :size="11" class="agent-ico agent-ico-danger" title="删除" @click="removeSession(s)" />
          </span>
        </template>
      </div>
      <div v-if="!sessions.length" style="font-size:11px;color:var(--c-text-faint);padding:10px 8px">还没有会话</div>
    </div>
    <div class="agent-side-foot">
      <div v-if="info && !info.configured" style="font-size:10px;color:#ef4444">未配置大模型 Key（去状态页）</div>
      <div v-else style="font-size:10px;color:var(--c-text-faint)">{{ info?.model || '-' }} · {{ info?.tools?.length || 0 }} 个工具</div>
    </div>
  </aside>

  <!-- 右：对话 -->
  <section class="agent-main">
    <header class="agent-head">
      <div style="font-size:13px;font-weight:600;color:var(--c-text)">{{ currentTitle }}</div>
      <div style="display:flex;align-items:center;gap:8px;margin-left:auto">
        <span style="font-size:10px;color:var(--c-text-faint)">数据在本地库 · 可跑沙箱 · 可建 DAG 流程</span>
        <n-button size="tiny" quaternary @click="openSkills"><AppIcon name="compass" :size="12" /> 技能 {{ skills.length || '' }}</n-button>
      </div>
    </header>

    <div ref="scrollEl" class="agent-thread">
      <div v-if="!blocks.length" class="agent-empty">
        <div style="font-size:13px;color:var(--c-text-dim);margin-bottom:10px">问点什么——行情、特征、信号、资讯、组合、流程都能查</div>
        <div class="agent-chips">
          <button v-for="c in quickAsks" :key="c" class="agent-chip" @click="fillPrompt(c)">{{ c }}</button>
        </div>
      </div>

      <div v-for="(b, i) in blocks" :key="i" class="agent-block" :class="b.role">
        <template v-if="b.role === 'user'">
          <div class="agent-bubble user">{{ b.text }}</div>
        </template>
        <template v-else>
          <div v-if="b.text" class="agent-bubble assistant" v-html="renderMd(b.text)"></div>
          <div v-for="t in b.tools" :key="t.id" class="agent-tool" :class="{ fail: t.result && !t.result.ok }">
            <div class="agent-tool-head" @click="toggleTool(t.id)">
              <AppIcon :name="t.result ? (t.result.ok ? 'check' : 'alert') : 'refresh'" :size="11"
                       :style="{ color: t.result ? (t.result.ok ? 'var(--c-success, #10b981)' : '#ef4444') : 'var(--c-text-dim)' }" />
              <span class="agent-tool-name">{{ t.name }}</span>
              <span class="agent-tool-sum">{{ argsSummary(t) }}</span>
              <span class="agent-tool-meta">{{ t.result ? `${t.result.elapsed_ms}ms` : '运行中…' }}</span>
            </div>
            <pre v-if="expanded[t.id]" class="agent-tool-body">{{ t.result ? t.result.preview : JSON.stringify(t.args, null, 2) }}</pre>
          </div>
        </template>
      </div>

      <div v-if="running" class="agent-running">
        <span class="agent-dot"></span>{{ statusText }}
        <n-button size="tiny" quaternary type="error" style="margin-left:8px" @click="stopRun">停止</n-button>
      </div>
    </div>

    <footer class="agent-composer">
      <n-input v-model:value="draft" type="textarea" size="small" :autosize="{ minRows: 2, maxRows: 6 }"
               placeholder="Enter 发送，Shift+Enter 换行" :disabled="running" @keydown="onKey" />
      <n-button size="small" type="primary" :loading="running" :disabled="!draft.trim() || running" @click="send">发送</n-button>
    </footer>
  </section>

  <!-- 技能面板 -->
  <n-modal v-model:show="showSkills" preset="card" title="技能（可复用的分析方法）" style="width:760px;max-width:94vw">
    <div style="display:flex;gap:12px;min-height:360px">
      <div style="width:220px;flex-shrink:0;border-right:1px solid var(--c-border-light);padding-right:8px">
        <n-button size="tiny" type="primary" block style="margin-bottom:6px" @click="startNewSkill">
          <AppIcon name="plus" :size="12" /> 新建技能
        </n-button>
        <div v-for="s in skills" :key="s.name" class="agent-sess" :class="{ active: skEditing.name === s.name }" @click="openSkill(s)">
          <span class="agent-sess-title" :title="s.name">{{ s.display_name }}</span>
          <span style="font-size:9px;color:var(--c-text-faint);flex-shrink:0">{{ s.builtin ? '内置' : '用户' }}</span>
        </div>
      </div>
      <div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:6px">
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
                 :disabled="skEditing.builtin" style="font-family:monospace" />
        <div style="display:flex;gap:8px;align-items:center">
          <n-button v-if="!skEditing.builtin" size="small" type="primary" :loading="skSaving" @click="saveSkill">保存</n-button>
          <n-button v-if="!skEditing.builtin && skEditing.name && skills.some(x => x.name === skEditing.name && !x.builtin)"
                    size="small" type="error" quaternary @click="removeSkill">删除</n-button>
          <n-button size="small" quaternary @click="useSkill">在对话中使用</n-button>
          <span style="font-size:10px;color:var(--c-text-faint)">技能正文会在模型判断命中后按需读取，不占常驻上下文</span>
        </div>
      </div>
    </div>
  </n-modal>
</div>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import { ref, computed, onMounted, nextTick } from 'vue'
import { NButton, NInput, useMessage, useDialog } from 'naive-ui'
import axios from 'axios'
import { useAuthStore } from '../stores/auth'

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
let abortCtl = null

const editingId = ref(null), editingTitle = ref('')
const currentTitle = computed(() => sessions.value.find(s => s.id === sid.value)?.title || '新会话')

const quickAsks = [
  '今天哪些个股资金净流入最多？',
  '持仓和纸面组合现在的盈亏情况',
  '最近三天有哪些重要资讯？',
  '帮我建一个每天盘后看信号的流程',
]

const H = () => auth.token ? { Authorization: 'Bearer ' + auth.token } : {}

function renderMd(t) {
  // 极简安全渲染：先转义，再还原粗体/行内代码
  const esc = String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return esc
    .replace(/```([\s\S]*?)```/g, (_, c) => `<pre class="agent-code">${c.trim()}</pre>`)
    .replace(/`([^`\n]+)`/g, '<code class="agent-inline">$1</code>')
    .replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>')
    .replace(/\n/g, '<br/>')
}
function argsSummary(t) {
  if (!t.args) return ''
  const keys = Object.keys(t.args)
  if (!keys.length) return ''
  const v = t.args[keys[0]]
  const s = typeof v === 'string' ? v : JSON.stringify(v)
  return s.replace(/\s+/g, ' ').slice(0, 60)
}
function toggleTool(id) { expanded.value = { ...expanded.value, [id]: !expanded.value[id] } }
function fillPrompt(c) { draft.value = c }

function onKey(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send() }
}
function scrollDown() {
  nextTick(() => { const el = scrollEl.value; if (el) el.scrollTop = el.scrollHeight })
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
  } catch (e) { message.error('创建会话失败') }
}
function selectSession(id) {
  if (running.value) { message.warning('当前会话还在运行，先停止或等待完成'); return }
  sid.value = id
  loadMessages(id)
}
async function loadMessages(id) {
  blocks.value = []
  try {
    const r = await axios.get(`${API}/api/agent/sessions/${id}/messages`, { headers: H() })
    blocks.value = toBlocks(r.data.items || [])
    scrollDown()
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
      if (t) {
        let preview = m.content || ''
        t.result = { ok: m.meta?.ok !== false, elapsed_ms: m.meta?.elapsed_ms || 0, preview }
      }
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
  scrollDown()

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
    scrollDown()
  }
}

function lastAssistant() {
  for (let i = blocks.value.length - 1; i >= 0; i--) {
    if (blocks.value[i].role === 'assistant') return blocks.value[i]
  }
  return null
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
    statusText.value = `调用 ${ev.name}…`
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
    statusText.value = '已停止'
  } else if (t === 'error') {
    running.value = false
    blocks.value.push({ role: 'assistant', text: '⚠️ ' + (ev.error || '未知错误'), tools: [] })
    scrollDown()
  }
}

// ── 技能面板 ──
const showSkills = ref(false)
const skills = ref([])
const skSaving = ref(false)
const skEditing = ref({ name: '', title: '', description: '', content: '', builtin: false })

async function loadSkills() {
  try { skills.value = (await axios.get(API + '/api/agent/skills', { headers: H() })).data.items || [] } catch (e) {}
}
async function openSkills() { showSkills.value = true; await loadSkills(); if (skills.value.length) openSkill(skills.value[0]) }
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
.agent-side { width: 208px; flex-shrink: 0; display: flex; flex-direction: column;
  background: var(--c-card-bg); border: 1px solid var(--c-card-bg-hover); border-radius: 8px; padding: 8px; }
.agent-side-head { flex-shrink: 0; margin-bottom: 6px; }
.agent-sess-list { flex: 1; overflow-y: auto; min-height: 0; }
.agent-sess { display: flex; align-items: center; gap: 4px; padding: 6px 8px; border-radius: 6px;
  cursor: pointer; font-size: 12px; color: var(--c-text); }
.agent-sess:hover { background: var(--c-card-bg-hover); }
.agent-sess.active { background: rgba(32,128,240,0.12); color: var(--c-text); }
.agent-sess-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.agent-sess-tools { display: none; gap: 4px; }
.agent-sess:hover .agent-sess-tools { display: flex; }
.agent-ico { color: var(--c-text-faint); cursor: pointer; }
.agent-ico:hover { color: var(--c-info); }
.agent-ico-danger:hover { color: #ef4444; }
.agent-side-foot { flex-shrink: 0; padding-top: 6px; border-top: 1px solid var(--c-border-light); }

.agent-main { flex: 1; min-width: 0; display: flex; flex-direction: column;
  background: var(--c-card-bg); border: 1px solid var(--c-card-bg-hover); border-radius: 8px; }
.agent-head { display: flex; align-items: center; gap: 8px; padding: 8px 12px;
  border-bottom: 1px solid var(--c-border-light); flex-shrink: 0; }
.agent-thread { flex: 1; overflow-y: auto; padding: 12px 14px; min-height: 0; }
.agent-empty { padding: 24px 4px; }
.agent-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.agent-chip { font-size: 11px; padding: 5px 10px; border-radius: 12px; cursor: pointer;
  background: var(--c-card-bg-hover); border: 1px solid var(--c-border); color: var(--c-text-dim); }
.agent-chip:hover { color: var(--c-text); border-color: var(--c-info); }

.agent-block { margin-bottom: 10px; display: flex; flex-direction: column; }
.agent-block.user { align-items: flex-end; }
.agent-bubble { font-size: 12.5px; line-height: 1.7; padding: 8px 12px; border-radius: 8px;
  max-width: 86%; word-break: break-word; }
.agent-bubble.user { background: rgba(32,128,240,0.14); color: var(--c-text); white-space: pre-wrap; }
.agent-bubble.assistant { background: var(--c-bg); color: var(--c-text); border: 1px solid var(--c-border-light); }
.agent-bubble.assistant :deep(.agent-code) { display: block; margin: 6px 0; padding: 8px; font-size: 11px;
  background: var(--c-card-bg); border-radius: 6px; overflow-x: auto; white-space: pre; }
.agent-bubble.assistant :deep(.agent-inline) { font-size: 11.5px; padding: 1px 4px;
  background: var(--c-card-bg); border-radius: 4px; }

.agent-tool { margin-top: 6px; border: 1px solid var(--c-border-light); border-radius: 6px;
  background: var(--c-card-bg); overflow: hidden; max-width: 86%; }
.agent-tool.fail { border-color: rgba(239,68,68,0.35); }
.agent-tool-head { display: flex; align-items: center; gap: 6px; padding: 5px 8px; cursor: pointer; }
.agent-tool-name { font-size: 11px; font-weight: 600; color: var(--c-text); }
.agent-tool-sum { font-size: 10px; color: var(--c-text-faint); flex: 1; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; font-family: monospace; }
.agent-tool-meta { font-size: 9.5px; color: var(--c-text-faint); flex-shrink: 0; }
.agent-tool-body { margin: 0; padding: 8px; font-size: 10.5px; line-height: 1.5;
  max-height: 260px; overflow: auto; white-space: pre-wrap; word-break: break-all;
  border-top: 1px solid var(--c-border-light); color: var(--c-text-dim); }

.agent-running { display: flex; align-items: center; font-size: 11px; color: var(--c-text-dim); padding: 4px 2px; }
.agent-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--c-info); margin-right: 6px;
  animation: agent-pulse 1s infinite; }
@keyframes agent-pulse { 0%,100% { opacity: 0.3 } 50% { opacity: 1 } }

.agent-composer { display: flex; gap: 8px; align-items: flex-end; padding: 8px 12px;
  border-top: 1px solid var(--c-border-light); flex-shrink: 0; }
@media (max-width: 900px) {
  .agent-side { width: 150px; }
  .agent-bubble, .agent-tool { max-width: 96%; }
}
</style>
