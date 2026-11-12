<template>
<div class="agent-wrap">
  <!-- 左：会话列表（窄屏收起，改由头部下拉选择） -->
  <aside v-if="!isNarrow" class="agent-side">
    <div class="agent-side-head">
      <n-button size="small" type="primary" block @click="newSession"><AppIcon name="plus" :size="12" /> 新会话</n-button>
      <n-input v-if="sessions.length > 5 || sessQ" v-model:value="sessQ" size="small" placeholder="搜索标题或对话内容"
               style="margin-top:6px" clearable>
        <template #prefix><AppIcon name="search" :size="11" /></template>
      </n-input>
    </div>
    <div class="agent-sess-list">
      <div v-for="s in sessions" :key="s.id" class="agent-sess"
           :class="{ active: s.id === sid, archived: s.status === 'archived' }"
           @click="selectSession(s.id)">
        <template v-if="editingId === s.id">
          <n-input v-model:value="editingTitle" size="tiny" @keyup.enter="commitRename" @blur="commitRename" @click.stop />
        </template>
        <template v-else>
          <span v-if="runningSid === s.id" class="agent-live-dot" title="运行中"></span>
          <div class="agent-sess-body">
            <span class="agent-sess-title" :title="s.title">{{ s.title }}</span>
            <span class="agent-sess-time">{{ relTime(s.updated_at) }}<template v-if="s.status === 'archived'"> · 已归档</template></span>
          </div>
          <span class="agent-sess-tools" @click.stop>
            <AppIcon name="target" :size="11" class="agent-ico" :class="{ 'agent-ico-on': s.pinned }"
                     :title="s.pinned ? '取消置顶' : '置顶'" @click="togglePin(s)" />
            <AppIcon name="edit" :size="11" class="agent-ico" title="重命名" @click="startRename(s)" />
            <AppIcon name="inbox" :size="11" class="agent-ico" :title="s.status === 'archived' ? '取消归档' : '归档'"
                     @click="toggleArchive(s)" />
            <AppIcon name="trash" :size="11" class="agent-ico agent-ico-danger" title="删除" @click="removeSession(s)" />
          </span>
        </template>
      </div>
      <div v-if="!sessions.length" class="agent-side-empty">
        {{ sessQ ? '没有匹配的会话' : '还没有会话，点上方新建' }}
      </div>
    </div>
    <div class="agent-side-foot">
      <div v-if="info && !info.configured" style="color:var(--c-up)">未配置大模型 Key</div>
      <div v-else>{{ info?.model || '-' }}，{{ info?.tools?.length || 0 }} 个工具，{{ skills.length }} 项技能</div>
      <button class="agent-link" @click="toggleArchived">{{ showArchived ? '隐藏已归档' : '显示已归档' }}</button>
    </div>
  </aside>

  <!-- 右：对话 -->
  <section class="agent-main">
    <header class="agent-head">
      <n-select v-if="isNarrow" size="small" :options="sessOptions" :value="sid" style="width:150px"
                placeholder="选择会话" @update:value="selectSession" />
      <div class="agent-head-title" :title="currentTitle">{{ currentTitle }}</div>
      <span v-if="runningSid === sid" class="agent-head-run">
        <span class="agent-dot"></span>{{ stopping ? '正在停止…' : `${statusText} ${elapsed}s` }}
      </span>
      <div class="agent-head-meta">
        <n-select v-if="modelOptions.length" size="tiny" :options="modelOptions" :value="sessModel"
                  style="width:150px" @update:value="changeModel" />
        <n-button size="tiny" quaternary @click="openSkills"><AppIcon name="compass" :size="12" /> 技能 {{ skills.length }}</n-button>
        <n-button size="tiny" quaternary title="查看差评反馈" @click="openFeedbackList">
          <AppIcon name="alert" :size="12" /> 反馈
        </n-button>
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

      <div v-for="(b, i) in blocks" :key="b.id || ('b' + i)" class="agent-block" :class="b.role">
        <!-- 用户消息 -->
        <template v-if="b.role === 'user'">
          <div v-if="editingUser && editingUser.id === b.id" class="agent-user-edit">
            <n-input v-model:value="editingUser.text" type="textarea" size="small"
                     :autosize="{ minRows: 2, maxRows: 8 }" @keydown.esc="editingUser = null" />
            <div class="agent-user-edit-bar">
              <n-button size="tiny" type="primary" @click="saveEditUser">保存并重发</n-button>
              <n-button size="tiny" quaternary @click="editingUser = null">取消</n-button>
            </div>
          </div>
          <template v-else>
            <div class="agent-bubble user">{{ b.text }}</div>
            <div v-if="b.attachments && b.attachments.length" class="agent-user-atts">
              <span v-for="a in b.attachments" :key="a" class="agent-attach-chip"><AppIcon name="folder" :size="10" /> {{ a }}</span>
            </div>
            <div v-if="b.id && !isRunning" class="agent-msg-actions">
              <button class="agent-mini" title="编辑后重发" @click="startEditUser(b)"><AppIcon name="edit" :size="10" /> 编辑</button>
              <button class="agent-mini" title="重新发送这条问题" @click="resendUser(b)"><AppIcon name="refresh" :size="10" /> 重发</button>
            </div>
          </template>
        </template>

        <!-- 提示（软步数预算等） -->
        <div v-else-if="b.role === 'notice'" class="agent-notice">{{ b.text }}</div>

        <!-- 助手消息 -->
        <template v-else>
          <details v-if="b.reasoning" class="agent-reason" :open="!!b.streaming && !b.text">
            <summary>思考过程<span v-if="b.streaming" class="agent-reason-live">（进行中）</span></summary>
            <pre>{{ b.reasoning }}</pre>
          </details>

          <div v-if="b.text || b.streaming" class="agent-ans">
            <div class="agent-bubble assistant" v-html="renderMarkdown(b.text || '', { linkifyCodes: true })"
                 @click="onAnswerClick"></div>
            <span v-if="b.streaming" class="agent-caret"></span>
            <button class="agent-copy" title="复制回答" @click="copyText(b.text)"><AppIcon name="save" :size="11" /></button>
          </div>

          <div v-for="t in b.tools" :key="t.id" class="agent-tool" :class="{ fail: t.result && !t.result.ok }">
            <div class="agent-tool-head" @click="toggleTool(t.id)">
              <AppIcon :name="t.result ? (t.result.ok ? 'check' : 'alert') : 'refresh'" :size="11"
                       :style="{ color: t.result ? (t.result.ok ? 'var(--c-down)' : 'var(--c-up)') : 'var(--c-text-dim)' }" />
              <span class="agent-tool-name">{{ toolLabel(t.name) }}</span>
              <span class="agent-tool-sum">{{ argsSummary(t) }}</span>
              <span v-if="t.result && t.result.truncated" class="agent-badge">已截断</span>
              <span class="agent-tool-meta">{{ t.result ? `${t.result.elapsed_ms}ms` : '运行中…' }}</span>
              <span class="agent-tool-fold">{{ expanded[t.id] ? '收起' : '详情' }}</span>
            </div>
            <template v-if="expanded[t.id]">
              <div v-if="t.name === 'query_sql' && sqlTable(t)" class="agent-tool-body"
                   v-html="renderMarkdown(sqlTable(t))"></div>
              <pre v-else class="agent-tool-body">{{ t.result ? t.result.preview : JSON.stringify(t.args, null, 2) }}</pre>
              <div v-if="isSqlTool(t) && t.result && t.result.ok" class="agent-tool-actions">
                <button class="agent-mini" @click.stop="downloadCsv(t)"><AppIcon name="download" :size="10" /> 下载 CSV</button>
                <span v-if="t.result.truncated" class="agent-tool-note">
                  结果过多已截断（{{ t.result.total_rows || '?' }} 行中返回 {{ t.result.row_count || '?' }} 行）
                </span>
              </div>
            </template>
            <div v-if="t.images && t.images.length" class="agent-imgs">
              <a v-for="im in t.images" :key="im.url" :href="mediaUrl(im.url)" target="_blank" rel="noopener" :title="im.name">
                <img :src="mediaUrl(im.url)" :alt="im.name" loading="lazy" />
              </a>
            </div>
          </div>

          <div v-if="b.id && !b.streaming" class="agent-ans-foot">
            <span class="agent-ans-meta">{{ answerMeta(b) }}</span>
            <span class="agent-ans-actions">
              <button class="agent-mini" title="复制" @click="copyText(b.text)"><AppIcon name="save" :size="10" /></button>
              <button class="agent-mini" title="重新生成" @click="regenerate(b)"><AppIcon name="refresh" :size="10" /> 重答</button>
              <button class="agent-mini" title="从这条回答分支新会话" @click="branchFrom(b)"><AppIcon name="shuffle" :size="10" /> 分支</button>
              <button class="agent-mini" :class="{ 'agent-mini-on': b.feedback === 1 }" title="有用" @click="rate(b, 1)"><AppIcon name="check" :size="10" /></button>
              <button class="agent-mini" :class="{ 'agent-mini-bad': b.feedback === -1 }" title="没帮助" @click="rate(b, -1)"><AppIcon name="alert" :size="10" /></button>
            </span>
          </div>
        </template>
      </div>

      <div v-if="runningSid === sid && !liveBlock" class="agent-running">
        <span class="agent-dot"></span>{{ stopping ? '正在停止…' : statusText }}
        <span class="agent-running-t">已等待 {{ elapsed }}s</span>
      </div>
    </div>

    <button v-if="showJump" class="agent-jump" @click="scrollDown(true)">↓ 回到底部</button>

    <footer class="agent-composer">
      <div v-if="attachments.length" class="agent-attach-row">
        <span v-for="a in attachments" :key="a.name" class="agent-attach-chip">
          <AppIcon name="folder" :size="10" /> {{ a.name }}
          <button class="agent-attach-x" title="移除" @click="removeAttachment(a)">×</button>
        </span>
      </div>
      <n-input v-model:value="draft" type="textarea" size="small" :autosize="{ minRows: 2, maxRows: 6 }"
               placeholder="描述你的问题或任务…（Enter 发送，Shift+Enter 换行；可 Ctrl+V 粘贴截图）" :disabled="isRunning"
               :input-props="{ onPaste }" @keydown="onKey" />
      <div class="agent-composer-bar">
        <input ref="fileEl" type="file" multiple style="display:none" @change="onFiles" />
        <n-button size="small" quaternary :disabled="isRunning || uploading" title="上传附件（CSV/Excel/图片等）"
                  @click="fileEl && fileEl.click()">
          <AppIcon name="upload" :size="13" /> {{ uploading ? '上传中…' : '附件' }}
        </n-button>
        <span class="agent-hint">
          {{ isRunning ? (stopping ? '正在停止…' : `${statusText} · ${elapsed}s`) : '数据查库，沙箱可跑 Python/Node，能建 DAG 流程' }}
        </span>
        <n-button v-if="isRunning" size="small" type="error" secondary :loading="stopping" @click="stopRun">
          <AppIcon name="close" :size="12" /> {{ stopping ? '停止中' : '停止' }}
        </n-button>
        <n-button v-else size="small" type="primary" :disabled="!draft.trim() || uploading" @click="send()">
          <AppIcon name="send" :size="12" /> 发送
        </n-button>
      </div>
    </footer>
  </section>

  <!-- 技能面板 -->
  <n-modal v-model:show="showSkills" preset="card" title="技能（可复用的分析方法）" style="width:780px;max-width:94vw">
    <div class="agent-sk-wrap">
      <div class="agent-sk-list">
        <n-button class="cta" size="tiny" type="primary" block style="margin-bottom:4px" @click="startNewSkill">
          <AppIcon name="plus" :size="12" /> 新建技能
        </n-button>
        <n-button size="tiny" block style="margin-bottom:6px" @click="startImport">
          <AppIcon name="download" :size="12" /> GitHub 导入
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
                   :disabled="skEditing.builtin" @update:value="markSkillDirty" />
          <n-input v-model:value="skEditing.title" size="small" placeholder="中文标题（可选）" style="width:150px"
                   @update:value="markSkillDirty" />
          <n-tag v-if="skEditing.builtin" size="small" type="warning" :bordered="false">内置只读</n-tag>
          <n-tag v-else-if="skDirty" size="small" type="warning" :bordered="false">未保存</n-tag>
        </n-space>
        <n-input v-model:value="skEditing.description" size="small" placeholder="何时使用（一句话，模型靠它判断命中）"
                 :disabled="skEditing.builtin" @update:value="markSkillDirty" />
        <n-input v-model:value="skEditing.content" type="textarea" size="small"
                 :autosize="{ minRows: 12, maxRows: 20 }" placeholder="Markdown：步骤 / SQL 模板 / 输出格式 / 注意事项"
                 :disabled="skEditing.builtin" class="agent-sk-code" @update:value="markSkillDirty" />
        <div class="agent-sk-actions">
          <n-button v-if="!skEditing.builtin" size="small" type="primary" :loading="skSaving" @click="saveSkill">保存</n-button>
          <n-button v-if="!skEditing.builtin && isUserSkill" size="small" type="error" quaternary @click="removeSkill">删除</n-button>
          <n-button size="small" quaternary @click="useSkill">在对话中使用</n-button>
          <span class="agent-hint">正文按需读取，不进常驻上下文</span>
        </div>
      </div>
    </div>
  </n-modal>

  <!-- GitHub 技能导入：预览 → 人工审核 → 入库 -->
  <n-modal v-model:show="showImport" preset="card" title="从 GitHub 导入技能" style="width:560px;max-width:94vw">
    <n-space vertical size="small">
      <n-input v-model:value="importUrl" size="small" placeholder="https://github.com/<owner>/<repo>"
               :disabled="importPreview !== null" @keyup.enter="doPreview" />
      <template v-if="importPreview">
        <div class="agent-card" style="background:var(--c-bg)">
          <div class="agent-card-t">{{ importPreview.title }} <span style="font-weight:400;color:var(--c-text-faint)">/ {{ importPreview.repo }}</span></div>
          <div class="agent-card-d">{{ importPreview.description || '（无描述）' }}</div>
          <div class="agent-card-d" style="margin-top:4px">
            {{ importPreview.body_chars }} 字正文，{{ importPreview.file_count }} 个文件，{{ importPreview.size_mb }} MB
          </div>
        </div>
        <pre class="agent-tool-body" style="max-height:200px">{{ importPreview.body_preview }}…</pre>
        <div style="display:flex;gap:6px;align-items:center">
          <span style="font-size: var(--text-meta);color:var(--c-text-dim);flex-shrink:0">安装名</span>
          <n-input v-model:value="importSlug" size="small" style="width:200px" placeholder="英文技能名" />
        </div>
        <div style="font-size: var(--text-meta);color:var(--c-warn)">第三方内容会进入模型上下文，请确认来源可信后再导入</div>
      </template>
      <div v-if="importErr" style="font-size: var(--text-meta);color:var(--c-up)">{{ importErr }}</div>
    </n-space>
    <template #footer>
      <n-space justify="flex-end">
        <n-button size="small" @click="showImport=false; importPreview=null">{{ importPreview ? '取消' : '关闭' }}</n-button>
        <n-button v-if="!importPreview" size="small" type="primary" :loading="importing" @click="doPreview">拉取预览</n-button>
        <n-button v-else size="small" type="primary" :loading="importing" @click="doConfirm">确认导入</n-button>
      </n-space>
    </template>
  </n-modal>

  <!-- 差评补充说明 -->
  <n-modal v-model:show="showFb" preset="card" title="这条回答哪里不好？" style="width:460px;max-width:94vw">
    <n-input v-model:value="fbComment" type="textarea" size="small" :autosize="{ minRows: 3, maxRows: 6 }"
             placeholder="例如：口径不对 / 数据过时 / 漏了条件。会记入反馈列表，便于以后沉淀成技能。" />
    <template #footer>
      <n-space justify="flex-end">
        <n-button size="small" @click="showFb = false">取消</n-button>
        <n-button size="small" type="primary" @click="submitFb">提交反馈</n-button>
      </n-space>
    </template>
  </n-modal>

  <!-- 差评列表 -->
  <n-modal v-model:show="showFbList" preset="card" title="差评反馈（可据此沉淀技能）" style="width:720px;max-width:94vw">
    <div v-if="!fbList.length" class="agent-side-empty">还没有差评反馈</div>
    <div v-for="f in fbList" :key="f.id" class="agent-fb-item">
      <div class="agent-fb-head">
        <span class="agent-fb-q">{{ f.question || '（未记录问题）' }}</span>
        <span class="agent-fb-t">{{ f.created_at }}</span>
      </div>
      <div v-if="f.comment" class="agent-fb-c">{{ f.comment }}</div>
      <button class="agent-link" @click="openFbSession(f)">打开会话「{{ f.session_title || f.session_id }}」</button>
    </div>
  </n-modal>
</div>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { NButton, NInput, NSelect, NTag, NModal, NSpace, useMessage, useDialog } from 'naive-ui'
import axios from 'axios'
import { useAuthStore } from '../stores/auth'
import { useNavStore } from '../stores/nav'
import { useViewport } from '../utils/viewport'
import { renderMarkdown, sqlResultToMarkdown } from '../utils/markdown'

const emit = defineEmits(['show-detail'])
const { isNarrow } = useViewport()
const API = window.location.origin
const auth = useAuthStore()
const navStore = useNavStore()
const message = useMessage()
const dialog = useDialog()

const sessions = ref([])
const sid = ref(null)
const blocks = ref([])
const draft = ref('')
const runningSid = ref(null)
const runSid = ref(null)
const runModel = ref('')
const stopping = ref(false)
const statusText = ref('思考中…')
const elapsed = ref(0)
const info = ref(null)
const scrollEl = ref(null)
const fileEl = ref(null)
const expanded = ref({})
const sessQ = ref('')
const showArchived = ref(false)
const showJump = ref(false)
const attachments = ref([])
const uploading = ref(false)
const liveBlock = ref(null)
const editingUser = ref(null)

let abortCtl = null
let nearBottom = true
let runStartedAt = 0
let runTimer = null
let pollTimer = null
let stopFallback = null
let qTimer = null
let deltaBuf = { text: '', reasoning: '' }
let flushTimer = null

const editingId = ref(null), editingTitle = ref('')
const currentSession = computed(() => sessions.value.find(s => s.id === sid.value) || null)
const currentTitle = computed(() => currentSession.value?.title || '新会话')
const isRunning = computed(() => runningSid.value === sid.value)
const sessOptions = computed(() => sessions.value.map(s => ({ label: s.title, value: s.id })))
const modelOptions = computed(() => {
  const list = info.value?.models || []
  const opts = list.map(m => ({ label: m, value: m }))
  if (info.value?.model) opts.unshift({ label: `默认（${info.value.model}）`, value: '' })
  return opts
})
const sessModel = computed(() => currentSession.value?.model || '')

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
  get_dag_run_status: '流程状态', model_performance: '模型表现',
  portfolio_summary: '组合概览', recent_signals: '最近信号',
  list_skills: '技能清单', read_skill: '读技能', save_skill: '存技能',
}

const H = () => auth.token ? { Authorization: 'Bearer ' + auth.token } : {}

// 回答里的 6 位代码可点击跳个股详情（模板对 v-html 内容做事件委托）
function onAnswerClick(e) {
  const el = e.target.closest('.agent-stock-link')
  if (el && el.dataset.code) emit('show-detail', el.dataset.code)
}
// 图片 URL：优先用后端签名；旧链接回退到登录 Token（<img> 不能带 Authorization 头）
function mediaUrl(u) {
  if (!u) return u
  if (u.includes('sig=')) return u
  return u + (u.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(auth.token || '')
}

// SQL 工具结果 → Markdown 表格（解析失败回退原始 JSON）
function sqlTable(t) {
  return t.result ? sqlResultToMarkdown(t.result.preview) : null
}
function isSqlTool(t) { return t.name === 'query_sql' }

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
function fmtMs(ms) {
  if (!ms && ms !== 0) return ''
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}
function fmtUsage(u) {
  if (!u) return ''
  const n = u.total_tokens || u.estimated_total_tokens || 0
  if (!n) return ''
  return (u.estimated ? '≈' : '') + (n >= 1000 ? (n / 1000).toFixed(1) + 'k' : n) + ' tok'
}
function answerMeta(b) {
  return [b.model, b.step ? `${b.step} 步` : '', fmtUsage(b.usage), fmtMs(b.elapsed_ms)]
    .filter(Boolean).join(' · ')
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
  if (!t) return
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

// ── 会话 ──
async function loadInfo() {
  try { info.value = (await axios.get(API + '/api/agent/info', { headers: H() })).data } catch (e) {}
}
async function loadSessions(q = sessQ.value) {
  try {
    const r = await axios.get(API + '/api/agent/sessions', {
      headers: H(), params: { q: q || '', include_archived: showArchived.value },
    })
    sessions.value = r.data.items || []
  } catch (e) { message.error('会话加载失败') }
}
watch(sessQ, (q) => {
  clearTimeout(qTimer)
  qTimer = setTimeout(() => loadSessions(q), 300)
})

async function newSession() {
  try {
    const r = await axios.post(API + '/api/agent/sessions', { title: '新会话' }, { headers: H() })
    sessions.value.unshift({ id: r.data.id, title: r.data.title, updated_at: '', message_count: 0, status: 'active' })
    sid.value = r.data.id
    blocks.value = []
    attachments.value = []
    editingUser.value = null
    draft.value = localStorage.getItem(draftKey(r.data.id)) || ''
  } catch (e) { message.error('创建会话失败') }
}
function selectSession(id) {
  if (!id || id === sid.value) return
  sid.value = id
  editingUser.value = null
  attachments.value = []
  draft.value = localStorage.getItem(draftKey(id)) || ''
  loadMessages(id)
  if (runningSid.value === id && !runSid.value) startPoll(id)
  else if (runningSid.value !== id) stopPoll()
}
function draftKey(id) { return 'agent_draft_' + id }
watch(draft, (v) => {
  if (!sid.value) return
  if (v) localStorage.setItem(draftKey(sid.value), v)
  else localStorage.removeItem(draftKey(sid.value))
})

async function loadMessages(id) {
  try {
    const r = await axios.get(`${API}/api/agent/sessions/${id}/messages`, { headers: H() })
    if (id !== sid.value) return
    blocks.value = toBlocks(r.data.items || [])
    nearBottom = true
    scrollDown(true)
  } catch (e) { message.error('消息加载失败') }
}

function toBlocks(msgs) {
  const out = []
  const byCall = {}
  for (const m of msgs) {
    if (m.role === 'user') {
      out.push({ role: 'user', text: m.content, id: m.id, attachments: m.meta?.attachments || [] })
    } else if (m.role === 'assistant') {
      const b = {
        role: 'assistant', text: m.content || '', tools: [], id: m.id,
        reasoning: m.meta?.reasoning || '', usage: m.meta?.usage || null,
        model: m.meta?.model || '', elapsed_ms: m.meta?.elapsed_ms || 0,
        step: m.meta?.step || 0, streaming: false,
      }
      for (const tc of (m.tool_calls || [])) {
        let args = {}
        try { args = JSON.parse(tc.function?.arguments || '{}') } catch (e) {}
        const t = { id: tc.id, name: tc.function?.name || '', args, result: null, images: [] }
        b.tools.push(t); byCall[tc.id] = t
      }
      if (b.text || b.reasoning || b.tools.length) out.push(b)
    } else if (m.role === 'tool') {
      const t = byCall[m.tool_call_id]
      if (t) {
        t.result = {
          ok: m.meta?.ok !== false, elapsed_ms: m.meta?.elapsed_ms || 0,
          preview: m.content || '', images: m.meta?.images || [],
          truncated: !!m.meta?.truncated,
        }
        t.images = m.meta?.images || []
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
async function togglePin(s) {
  try {
    await axios.patch(`${API}/api/agent/sessions/${s.id}`, { pinned: !s.pinned }, { headers: H() })
    await loadSessions()
  } catch (e) { message.error('置顶失败') }
}
async function toggleArchive(s) {
  try {
    await axios.patch(`${API}/api/agent/sessions/${s.id}`,
      { status: s.status === 'archived' ? 'active' : 'archived' }, { headers: H() })
    if (s.id === sid.value && s.status !== 'archived') { sid.value = null; blocks.value = [] }
    await loadSessions()
  } catch (e) { message.error('归档失败') }
}
async function toggleArchived() {
  showArchived.value = !showArchived.value
  await loadSessions()
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
async function changeModel(m) {
  if (!sid.value) return
  try {
    await axios.patch(`${API}/api/agent/sessions/${sid.value}`, { model: m || '' }, { headers: H() })
    const s = currentSession.value
    if (s) s.model = m || null
    message.success(m ? `本会话使用 ${m}` : '已恢复默认模型')
  } catch (e) { message.error('模型切换失败') }
}

// ── 运行状态 / 轮询 ──
function startRunTimer() {
  clearInterval(runTimer)
  runStartedAt = Date.now()
  elapsed.value = 0
  runTimer = setInterval(() => { elapsed.value = Math.floor((Date.now() - runStartedAt) / 1000) }, 1000)
}
function finishRun() {
  flushDeltas()
  clearInterval(runTimer); runTimer = null
  clearTimeout(stopFallback); stopFallback = null
  runningSid.value = null
  runSid.value = null
  stopping.value = false
  if (liveBlock.value) liveBlock.value.streaming = false
  liveBlock.value = null
  loadSessions()
}
function startPoll(id) {
  stopPoll()
  pollTimer = setInterval(async () => {
    if (sid.value !== id) return stopPoll()
    try {
      const st = (await axios.get(`${API}/api/agent/sessions/${id}/status`, { headers: H() })).data
      await loadMessages(id)
      if (!st.running) {
        runningSid.value = null
        runSid.value = null
        stopPoll()
        loadSessions()
      }
    } catch (e) { /* 网络抖动继续轮询 */ }
  }, 2000)
}
function stopPoll() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}
async function checkStatus(id) {
  if (!id) return
  try {
    const st = (await axios.get(`${API}/api/agent/sessions/${id}/status`, { headers: H() })).data
    if (st.running) {
      runningSid.value = id
      startRunTimer()
      startPoll(id)
    }
  } catch (e) {}
}

// ── 发送 / SSE ──
async function stopRun() {
  if (!sid.value || stopping.value) return
  stopping.value = true
  statusText.value = '正在停止…'
  try { await axios.post(`${API}/api/agent/sessions/${sid.value}/stop`, {}, { headers: H() }) } catch (e) {}
  // 兜底：服务端 8s 内没发 stopped 就断开前端流，避免按钮永远转
  clearTimeout(stopFallback)
  stopFallback = setTimeout(() => {
    if (abortCtl) { try { abortCtl.abort() } catch (e) {} }
    finishRun()
    message.warning('已断开前端连接；服务端可能仍在收尾，稍后刷新可见结果')
  }, 8000)
}

async function send(text = null, opts = {}) {
  const content = (typeof text === 'string' ? text : draft.value).trim()
  if (!content || isRunning.value) return
  if (!sid.value) await newSession()
  if (!sid.value) return
  const sendSid = sid.value
  if (runningSid.value && runningSid.value !== sendSid) {
    message.warning('另一个会话还在运行，可等待或先去停止')
    return
  }
  const atts = opts.attachments || attachments.value.map(a => a.name)
  if (typeof text !== 'string') { draft.value = ''; localStorage.removeItem(draftKey(sendSid)) }
  blocks.value.push({ role: 'user', text: content, id: null, attachments: atts.slice() })
  attachments.value = []
  runSid.value = sendSid
  runningSid.value = sendSid
  stopping.value = false
  statusText.value = '思考中…'
  liveBlock.value = null
  deltaBuf = { text: '', reasoning: '' }
  startRunTimer()
  nearBottom = true
  scrollDown(true)

  abortCtl = new AbortController()
  try {
    const resp = await fetch(`${API}/api/agent/sessions/${sendSid}/chat`, {
      method: 'POST', headers: { ...H(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, attachments: atts }), signal: abortCtl.signal,
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
        handleEvent(ev, sendSid)
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      if (sid.value === sendSid) blocks.value.push({ role: 'assistant', text: '错误：' + (e.message || e), tools: [], id: null })
      message.error('对话失败: ' + (e.message || e))
    }
  } finally {
    if (runningSid.value === sendSid) finishRun()
    abortCtl = null
  }
}

function ensureLive(step) {
  if (liveBlock.value && liveBlock.value._step === step) return liveBlock.value
  flushDeltas()
  if (liveBlock.value) liveBlock.value.streaming = false
  const b = {
    role: 'assistant', text: '', reasoning: '', tools: [], streaming: true,
    id: null, usage: null, model: runModel.value, elapsed_ms: 0, step, _step: step,
  }
  blocks.value.push(b)
  liveBlock.value = b
  return b
}
function queueDelta(kind, text, step) {
  ensureLive(step)
  deltaBuf[kind] += text
  if (!flushTimer) flushTimer = setTimeout(flushDeltas, 40)
}
function flushDeltas() {
  if (flushTimer) { clearTimeout(flushTimer); flushTimer = null }
  const b = liveBlock.value
  if (!b) { deltaBuf = { text: '', reasoning: '' }; return }
  if (deltaBuf.text) b.text += deltaBuf.text
  if (deltaBuf.reasoning) b.reasoning += deltaBuf.reasoning
  deltaBuf = { text: '', reasoning: '' }
  if (b.text || b.reasoning) scrollDown()
}

function handleEvent(ev, runSessionId) {
  const t = ev.type
  const visible = sid.value === runSessionId
  if (t === 'start') {
    runModel.value = ev.model || ''
    if (visible) {
      const u = [...blocks.value].reverse().find(x => x.role === 'user' && !x.id)
      if (u) u.id = ev.user_message_id
    }
    return
  }
  if (t === 'done' || t === 'stopped' || t === 'error') {
    if (visible) {
      if (t === 'error') blocks.value.push({ role: 'assistant', text: '错误：' + (ev.error || '未知错误'), tools: [], id: null })
      if (t === 'stopped') statusText.value = '已停止'
    }
    finishRun()
    return
  }
  if (!visible) return
  if (t === 'reasoning_delta') {
    queueDelta('reasoning', ev.text || '', ev.step || 0)
  } else if (t === 'assistant_delta') {
    queueDelta('text', ev.text || '', ev.step || 0)
  } else if (t === 'assistant') {
    flushDeltas()
    const b = liveBlock.value
    if (b && b._step === (ev.step || 0)) {
      b.text = ev.content || b.text
      b.reasoning = ev.reasoning || b.reasoning
      b.id = ev.message_id || b.id
      b.usage = ev.usage || b.usage
      b.model = ev.model || b.model
      b.elapsed_ms = ev.elapsed_ms || 0
      b.step = ev.step || b.step
      b.streaming = false
      liveBlock.value = null
    } else if (ev.content || ev.reasoning) {
      if (liveBlock.value) liveBlock.value.streaming = false
      liveBlock.value = null
      blocks.value.push({
        role: 'assistant', text: ev.content || '', reasoning: ev.reasoning || '', tools: [],
        id: ev.message_id || null, usage: ev.usage || null, model: ev.model || runModel.value,
        elapsed_ms: ev.elapsed_ms || 0, step: ev.step || 0, streaming: false,
      })
    } else {
      ensureLive(ev.step || 0).id = ev.message_id || null
    }
    scrollDown()
  } else if (t === 'tool_call') {
    flushDeltas()
    let b = blocks.value[blocks.value.length - 1]
    if (!b || b.role !== 'assistant') { b = { role: 'assistant', text: '', tools: [], id: null }; blocks.value.push(b) }
    b.tools.push({ id: ev.id, name: ev.name, args: ev.args || {}, result: null, images: [] })
    statusText.value = `${toolLabel(ev.name)}…`
    scrollDown()
  } else if (t === 'tool_result') {
    for (const b of blocks.value) {
      const tt = (b.tools || []).find(x => x.id === ev.id)
      if (tt) {
        tt.result = { ok: ev.ok, elapsed_ms: ev.elapsed_ms, preview: ev.preview,
                      images: ev.images || [], truncated: !!ev.truncated }
        tt.images = ev.images || []
        break
      }
    }
    statusText.value = '整理结果…'
    scrollDown()
  } else if (t === 'notice') {
    blocks.value.push({ role: 'notice', text: ev.text || '' })
    scrollDown()
  }
}

// ── 消息编辑 / 重答 / 分支 ──
function startEditUser(b) { editingUser.value = { id: b.id, text: b.text, attachments: b.attachments || [] } }
async function saveEditUser() {
  const e = editingUser.value
  if (!e) return
  editingUser.value = null
  const b = blocks.value.find(x => x.id === e.id)
  if (b) await rewindAndSend(b, e.text, e.attachments || [])
}
async function resendUser(b) { await rewindAndSend(b, b.text, b.attachments || []) }
async function rewindAndSend(userBlock, text, atts) {
  if (!text || !text.trim()) return
  if (runningSid.value) { message.warning('当前有会话在运行，先停止或等待完成'); return }
  if (!userBlock.id) { message.warning('这条消息尚未持久化，无法重发'); return }
  try {
    await axios.post(`${API}/api/agent/sessions/${sid.value}/rewind`,
      { message_id: userBlock.id }, { headers: H() })
  } catch (e) { message.error('回滚失败'); return }
  const idx = blocks.value.indexOf(userBlock)
  if (idx >= 0) blocks.value.splice(idx)
  await send(text, { attachments: atts })
}
async function regenerate(b) {
  const idx = blocks.value.indexOf(b)
  for (let i = idx - 1; i >= 0; i--) {
    if (blocks.value[i].role === 'user') { await rewindAndSend(blocks.value[i], blocks.value[i].text, blocks.value[i].attachments || []); return }
  }
  message.warning('找不到对应的问题')
}
async function branchFrom(b) {
  if (!b.id) return message.warning('这条回答尚未持久化，无法分支')
  try {
    const r = (await axios.post(`${API}/api/agent/sessions/${sid.value}/branch`,
      { message_id: b.id }, { headers: H() })).data
    await loadSessions()
    sid.value = r.id
    blocks.value = []
    await loadMessages(r.id)
    message.success('已创建分支会话')
  } catch (e) { message.error(e.response?.data?.detail || '分支失败') }
}

// ── 附件 ──
function pastedName(f, i) {
  if (f.name && f.name.trim() && !/^image\.(png|jpe?g|webp|gif)$/i.test(f.name.trim())) return f.name
  const sub = (f.type || '').split('/')[1] || 'png'
  const ext = sub === 'jpeg' ? 'jpg' : sub.replace(/[^a-z0-9]/gi, '') || 'png'
  return `paste-${Date.now()}-${i + 1}.${ext}`
}

async function uploadFiles(files) {
  files = (files || []).filter(Boolean)
  if (!files.length) return
  if (!sid.value) await newSession()
  if (!sid.value) return
  uploading.value = true
  for (let i = 0; i < files.length; i++) {
    const f = files[i]
    const name = pastedName(f, i)
    if (f.size > 20 * 1024 * 1024) { message.warning(`${name} 超过 20MB，已跳过`); continue }
    try {
      const r = (await axios.post(
        `${API}/api/agent/sessions/${sid.value}/upload`,
        f, { headers: { ...H(), 'Content-Type': 'application/octet-stream' },
             params: { name } })).data
      attachments.value.push({ name: r.name, size: r.size, path: r.path })
    } catch (err) { message.error(`${name} 上传失败：` + (err.response?.data?.detail || err.message)) }
  }
  uploading.value = false
}

async function onFiles(e) {
  const files = [...(e.target.files || [])]
  e.target.value = ''
  await uploadFiles(files)
}

// 截图粘贴：剪贴板里的图片直接转成附件；纯文本粘贴保持默认行为
function onPaste(e) {
  const dt = e.clipboardData
  if (!dt) return
  const files = []
  for (const item of (dt.items || [])) {
    if (item.kind === 'file' && (item.type || '').startsWith('image/')) {
      const f = item.getAsFile()
      if (f) files.push(f)
    }
  }
  if (!files.length || isRunning.value) return
  e.preventDefault()
  uploadFiles(files)
}
function removeAttachment(a) { attachments.value = attachments.value.filter(x => x !== a) }

// ── SQL CSV ──
function downloadCsv(t) {
  let j
  try { j = JSON.parse(t.result?.preview || '') } catch (e) { message.error('结果不是可解析的表格'); return }
  if (!Array.isArray(j.columns) || !Array.isArray(j.rows)) { message.error('结果不含表格结构'); return }
  const esc = (v) => {
    const s = String(v ?? '')
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const csv = '\ufeff' + [j.columns, ...j.rows].map(r => r.map(esc).join(',')).join('\n')
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url; a.download = `query_${Date.now()}.csv`; a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// ── 反馈 ──
const showFb = ref(false)
const fbComment = ref('')
const fbTarget = ref(null)
const showFbList = ref(false)
const fbList = ref([])
async function rate(b, rating) {
  if (!b.id) { message.warning('这条回答尚未持久化，无法评价'); return }
  if (rating < 0) { fbTarget.value = b; fbComment.value = ''; showFb.value = true; return }
  await postFeedback(b, 1, '')
}
async function postFeedback(b, rating, comment) {
  try {
    await axios.post(`${API}/api/agent/sessions/${sid.value}/feedback`,
      { message_id: b.id, rating, comment }, { headers: H() })
    b.feedback = rating
    message.success(rating > 0 ? '已记录：这条回答有用' : '已记录反馈')
  } catch (e) { message.error('反馈提交失败') }
}
async function submitFb() {
  if (fbTarget.value) await postFeedback(fbTarget.value, -1, fbComment.value)
  showFb.value = false
}
async function openFeedbackList() {
  try {
    fbList.value = (await axios.get(API + '/api/agent/feedback',
      { headers: H(), params: { rating: -1, limit: 100 } })).data.items || []
    showFbList.value = true
  } catch (e) { message.error('反馈列表加载失败') }
}
async function openFbSession(f) {
  showFbList.value = false
  await loadSessions()
  sid.value = f.session_id
  await loadMessages(f.session_id)
}

// ── 技能 ──
const showSkills = ref(false)
const skills = ref([])
const skSaving = ref(false)
const skEditing = ref({ name: '', title: '', description: '', content: '', builtin: false })
const skOriginal = ref('')
const skDirty = ref(false)
const isUserSkill = computed(() => skills.value.some(x => x.name === skEditing.value.name && !x.builtin))

function markSkillDirty() {
  const cur = JSON.stringify([skEditing.value.name, skEditing.value.title,
                              skEditing.value.description, skEditing.value.content])
  skDirty.value = cur !== skOriginal.value
}
function snapSkill() {
  skOriginal.value = JSON.stringify([skEditing.value.name, skEditing.value.title,
                                     skEditing.value.description, skEditing.value.content])
  skDirty.value = false
}
async function loadSkills() {
  try { skills.value = (await axios.get(API + '/api/agent/skills', { headers: H() })).data.items || [] } catch (e) {}
}
async function openSkills() {
  showSkills.value = true
  await loadSkills()
  if (skills.value.length) openSkill(skills.value[0])
}
async function openSkill(s) {
  if (skDirty.value) {
    const ok = await new Promise(resolve => dialog.warning({
      title: '未保存的技能修改', content: '切换技能会丢失当前修改，继续？',
      positiveText: '放弃修改', negativeText: '取消',
      onPositiveClick: () => resolve(true), onNegativeClick: () => resolve(false), onClose: () => resolve(false),
    }))
    if (!ok) return
  }
  try {
    const r = await axios.get(`${API}/api/agent/skills/${s.name}`, { headers: H() })
    skEditing.value = { name: s.name, title: s.display_name, description: s.description, content: r.data.content, builtin: !!s.builtin }
    snapSkill()
  } catch (e) { message.error('技能读取失败') }
}
function startNewSkill() {
  if (skDirty.value && !confirmDiscard()) return
  skEditing.value = { name: '', title: '', description: '', content: '', builtin: false }
  snapSkill()
}
function confirmDiscard() {
  return window.confirm('当前技能有未保存修改，确定放弃？')
}
async function saveSkill() {
  const e = skEditing.value
  if (!e.name.trim() || !e.description.trim() || !e.content.trim()) { message.warning('名称、描述、正文都要填'); return }
  skSaving.value = true
  try {
    await axios.post(API + '/api/agent/skills', {
      name: e.name.trim(), title: e.title, description: e.description, content: e.content }, { headers: H() })
    message.success('技能已保存（下一条消息起生效）')
    snapSkill()
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
// ── GitHub 导入 ──
const showImport = ref(false)
const importUrl = ref('')
const importSlug = ref('')
const importPreview = ref(null)
const importing = ref(false)
const importErr = ref('')
function startImport() {
  showImport.value = true; importPreview.value = null; importErr.value = ''
  importUrl.value = ''; importSlug.value = ''
}
async function doPreview() {
  if (!importUrl.value.trim()) { message.warning('请粘贴仓库 URL'); return }
  importing.value = true; importErr.value = ''
  try {
    const r = await axios.post(API + '/api/agent/skills/import/preview',
      { repo_url: importUrl.value.trim() }, { headers: H() })
    importPreview.value = r.data
    importSlug.value = r.data.default_slug || ''
  } catch (e) { importErr.value = e.response?.data?.detail || e.message }
  importing.value = false
}
async function doConfirm() {
  importing.value = true; importErr.value = ''
  try {
    const r = await axios.post(API + '/api/agent/skills/import/confirm',
      { repo_url: importUrl.value.trim(), slug: importSlug.value.trim() }, { headers: H() })
    message.success(`技能「${r.data.name}」已导入`)
    showImport.value = false
    await loadSkills()
  } catch (e) { importErr.value = e.response?.data?.detail || e.message }
  importing.value = false
}
function useSkill() {
  draft.value = `请使用技能 ${skEditing.value.name} 帮我分析：`
  showSkills.value = false
}

onMounted(async () => {
  const seed = navStore.takeAgentSeed()
  await Promise.all([loadInfo(), loadSessions(), loadSkills()])
  if (sessions.value.length) {
    sid.value = sessions.value[0].id
    draft.value = localStorage.getItem(draftKey(sid.value)) || ''
    await loadMessages(sid.value)
    await checkStatus(sid.value)
  }
  if (seed?.text) {
    draft.value = seed.text
    await nextTick()
    const el = scrollEl.value
    if (el) el.scrollTop = el.scrollHeight
  }
})
onUnmounted(() => {
  stopPoll()
  clearInterval(runTimer)
  clearTimeout(stopFallback)
  clearTimeout(qTimer)
  if (flushTimer) clearTimeout(flushTimer)
  if (abortCtl) { try { abortCtl.abort() } catch (e) {} }
})
</script>

<style scoped>
.agent-wrap { display: flex; flex: 1; min-height: 0; gap: 10px; }

/* ── 侧栏 ── */
.agent-side { width: 216px; flex-shrink: 0; display: flex; flex-direction: column;
  background: var(--c-card-bg); border: 1px solid var(--c-card-bg-hover); border-radius: 6px; padding: 8px; }
.agent-side-head { flex-shrink: 0; }
.agent-sess-list { flex: 1; overflow-y: auto; min-height: 0; margin-top: 6px; }
.agent-sess { display: flex; align-items: center; gap: 4px; padding: 6px 8px; border-radius: 6px;
  cursor: pointer; font-size: 12px; color: var(--c-text); border-left: 2px solid transparent; }
.agent-sess:hover { background: var(--c-card-bg-hover); }
.agent-sess.active { background: color-mix(in srgb, var(--c-up) 10%, transparent); border-left-color: var(--c-up); }
.agent-sess.archived { opacity: .65; }
.agent-sess-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.agent-sess-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.agent-sess-time { font-size: var(--text-meta); color: var(--c-text-faint); }
.agent-sess-tools { display: none; gap: 4px; flex-shrink: 0; }
.agent-sess:hover .agent-sess-tools { display: flex; }
.agent-ico { color: var(--c-text-faint); cursor: pointer; }
.agent-ico:hover { color: var(--c-up); }
.agent-ico-on { color: var(--c-up); }
.agent-ico-danger:hover { color: var(--c-up); }
.agent-live-dot { width: 5px; height: 5px; border-radius: 50%; background: var(--c-up);
  flex-shrink: 0; animation: agent-pulse 1s infinite; }
.agent-side-empty { font-size: var(--text-meta); color: var(--c-text-faint); padding: 12px 8px; line-height: 1.6; }
.agent-side-foot { flex-shrink: 0; padding-top: 6px; margin-top: 4px; font-size: var(--text-meta);
  color: var(--c-text-faint); border-top: 1px solid var(--c-border-light); }
.agent-link { border: 0; background: none; color: var(--c-text-dim); cursor: pointer;
  font-size: var(--text-meta); padding: 2px 0; text-align: left; }
.agent-link:hover { color: var(--c-up); text-decoration: underline; }

/* ── 主区 ── */
.agent-main { position: relative; flex: 1; min-width: 0; display: flex; flex-direction: column;
  background: var(--c-card-bg); border: 1px solid var(--c-card-bg-hover); border-radius: 6px; overflow: hidden; }
.agent-head { display: flex; align-items: center; gap: 8px; padding: 8px 12px;
  border-bottom: 1px solid var(--c-border-light); flex-shrink: 0; }
.agent-head-title { font-size: 13px; font-weight: 600; color: var(--c-text);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.agent-head-run { display: flex; align-items: center; font-size: var(--text-meta);
  color: var(--c-text-dim); white-space: nowrap; }
.agent-head-meta { margin-left: auto; display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
@media (max-width: 768px) {
  .agent-head { padding: 6px 10px; gap: 6px; }
  .agent-head-title { display: none; }
  .agent-head-run { display: none; }
  .agent-head-meta { gap: 2px; }
}
.agent-thread { flex: 1; overflow-y: auto; padding: 12px 16px 8px; min-height: 0; }

.agent-empty { padding: 16px 4px 8px; }
.agent-empty-title { font-size: 13px; color: var(--c-text); margin-bottom: 12px; }
.agent-cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-bottom: 16px; }
.agent-card { padding: 8px 12px; background: var(--c-bg); border: 1px solid var(--c-border-light); border-radius: var(--radius-sm); }
.agent-card-t { font-size: var(--text-meta); font-weight: 600; color: var(--c-text); display: flex; align-items: center; gap: 4px; }
.agent-card-d { font-size: var(--text-meta); color: var(--c-text-dim); margin-top: 3px; line-height: 1.5; }
.agent-empty-sub { font-size: var(--text-meta); color: var(--c-text-dim); margin-bottom: 8px; }
.agent-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.agent-chip { font-size: var(--text-meta); padding: 4px 12px; border-radius: var(--radius); cursor: pointer;
  background: var(--c-bg); border: 1px solid var(--c-border); color: var(--c-text-dim); transition: color .15s, border-color .15s, background-color .15s; }
.agent-chip:hover { color: var(--c-text); border-color: var(--c-up); }

@media (prefers-reduced-motion: reduce) {
  .agent-chip, .agent-tool-head { transition: none !important; }
  .agent-live-dot, .agent-dot, .agent-caret { animation: none !important; }
}

.agent-block { margin-bottom: 12px; display: flex; flex-direction: column; }
.agent-block.user { align-items: flex-end; }
.agent-bubble { font-size: var(--text-body); line-height: 1.75; padding: 8px 12px; border-radius: var(--radius);
  word-break: break-word; }
.agent-bubble.user { background: color-mix(in srgb, var(--c-up) 13%, transparent); color: var(--c-text); white-space: pre-wrap;
  max-width: 82%; border-bottom-right-radius: 3px; }
.agent-ans { position: relative; max-width: 86%; }
.agent-bubble.assistant { background: var(--c-bg); color: var(--c-text);
  border: 1px solid var(--c-border-light); border-bottom-left-radius: 3px; }
.agent-caret { position: absolute; right: 9px; bottom: 9px; width: 6px; height: 12px;
  background: var(--c-up); animation: agent-pulse 1s infinite; }
.agent-copy { position: absolute; top: 6px; right: 6px; opacity: 0; border: 0; cursor: pointer;
  background: var(--c-card-bg-hover); color: var(--c-text-faint); border-radius: 4px; padding: 2px 4px; }
.agent-ans:hover .agent-copy { opacity: 1; }
.agent-copy:hover { color: var(--c-text); }

.agent-msg-actions { display: flex; gap: 6px; margin-top: 3px; opacity: 0; transition: opacity .15s; }
.agent-block.user:hover .agent-msg-actions { opacity: 1; }
.agent-user-edit { width: 82%; display: flex; flex-direction: column; gap: 4px; }
.agent-user-edit-bar { display: flex; gap: 6px; justify-content: flex-end; }
.agent-user-atts { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; max-width: 82%; justify-content: flex-end; }
.agent-attach-chip { display: inline-flex; align-items: center; gap: 4px; font-size: var(--text-meta);
  padding: 2px 7px; border: 1px solid var(--c-border-light); border-radius: var(--radius-sm);
  color: var(--c-text-dim); background: var(--c-bg); max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.agent-attach-x { border: 0; background: none; color: var(--c-text-faint); cursor: pointer; font-size: 12px; padding: 0 2px; }
.agent-attach-x:hover { color: var(--c-up); }
.agent-attach-row { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 5px; }
.agent-notice { align-self: center; font-size: var(--text-meta); color: var(--c-text-faint);
  padding: 2px 10px; border: 1px dashed var(--c-border-light); border-radius: var(--radius-sm); }

/* 推理过程 */
.agent-reason { max-width: 86%; margin-bottom: 4px; border-left: 2px solid var(--c-border-light); padding-left: 8px; }
.agent-reason summary { cursor: pointer; font-size: var(--text-meta); color: var(--c-text-faint); }
.agent-reason summary:hover { color: var(--c-text-dim); }
.agent-reason pre { margin: 4px 0 0; white-space: pre-wrap; word-break: break-word;
  font-size: var(--text-meta); line-height: 1.6; color: var(--c-text-dim); max-height: 240px; overflow: auto; }
.agent-reason-live { color: var(--c-up); }

/* 回答底部元信息/动作 */
.agent-ans-foot { display: flex; align-items: center; gap: 8px; max-width: 86%; margin-top: 4px; }
.agent-ans-meta { font-size: var(--text-meta); color: var(--c-text-faint); flex: 1;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.agent-ans-actions { display: flex; gap: 2px; opacity: .45; transition: opacity .15s; }
.agent-ans-foot:hover .agent-ans-actions { opacity: 1; }
.agent-mini { display: inline-flex; align-items: center; gap: 3px; border: 1px solid transparent;
  background: none; color: var(--c-text-faint); cursor: pointer; font-size: var(--text-meta);
  padding: 1px 4px; border-radius: 4px; }
.agent-mini:hover { color: var(--c-text); background: var(--c-card-bg-hover); }
.agent-mini-on { color: var(--c-down); }
.agent-mini-bad { color: var(--c-up); }

/* Markdown 排版 */
.agent-bubble.assistant :deep(.agent-p) { margin: 0 0 6px; }
.agent-bubble.assistant :deep(.agent-p:last-child) { margin-bottom: 0; }
.agent-bubble.assistant :deep(.agent-h) { font-weight: 600; color: var(--c-text); margin: 10px 0 4px; }
.agent-bubble.assistant :deep(.agent-h1) { font-size: 14px; }
.agent-bubble.assistant :deep(.agent-h2) { font-size: 13px; }
.agent-bubble.assistant :deep(.agent-h3),
.agent-bubble.assistant :deep(.agent-h4) { font-size: 12.5px; }
.agent-bubble.assistant :deep(.agent-list) { margin: 4px 0 6px; padding-left: 16px; }
.agent-bubble.assistant :deep(.agent-list li) { margin: 2px 0; }
.agent-bubble.assistant :deep(.agent-quote) {
  margin: 6px 0; padding: 6px 10px; border-left: 2px solid var(--c-border);
  color: var(--c-text-dim); font-size: var(--text-meta);
}
.agent-bubble.assistant :deep(.agent-code) { display: block; margin: 6px 0; padding: 8px 10px;
  font-size: var(--text-meta); background: var(--c-card-bg); border-radius: 6px; overflow-x: auto; white-space: pre; }
.agent-bubble.assistant :deep(.agent-list li::marker) { color: var(--c-text-faint); }
.agent-bubble.assistant :deep(.agent-inline) { font-size: var(--text-meta); padding: 1px 4px;
  background: var(--c-card-bg); border-radius: 4px; }
.agent-bubble.assistant :deep(.agent-table-wrap) { overflow-x: auto; margin: 8px 0; }
.agent-bubble.assistant :deep(.agent-table) { border-collapse: collapse; font-size: var(--text-body); width: 100%; }
.agent-bubble.assistant :deep(.agent-table th) { text-align: left; padding: 4px 8px; font-weight: 600;
  color: var(--c-text); background: var(--c-card-bg-hover); border: 1px solid var(--c-border-light); white-space: nowrap; }
.agent-bubble.assistant :deep(.agent-table td) { padding: 4px 8px; color: var(--c-text-dim);
  border: 1px solid var(--c-border-light); }
.agent-bubble.assistant :deep(.agent-table tr:nth-child(even) td) { background: color-mix(in srgb, var(--c-text-faint) 3%, transparent); }

/* 回答中的股票代码（可点击跳详情） */
.agent-bubble.assistant :deep(.agent-stock-link) { color: var(--c-up); cursor: pointer;
  border-bottom: 1px dashed color-mix(in srgb, var(--c-up) 50%, transparent); }
.agent-bubble.assistant :deep(.agent-stock-link:hover) { color: var(--c-series-4); }

/* 工具生成的图表缩略图 */
.agent-imgs { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; max-width: 88%; }
.agent-imgs a { display: block; border: 1px solid var(--c-border-light); border-radius: 6px; overflow: hidden; }
.agent-imgs img { display: block; max-width: 240px; max-height: 170px; object-fit: contain; background: var(--c-card-bg); }

/* 工具卡片 */
.agent-tool { margin-top: 6px; border: 1px solid var(--c-border-light); border-radius: 6px;
  background: var(--c-bg); overflow: hidden; max-width: 88%; }
.agent-tool.fail { border-color: color-mix(in srgb, var(--c-up) 35%, transparent); }
.agent-tool-head { display: flex; align-items: center; gap: 6px; padding: 4px 8px; cursor: pointer; }
.agent-tool-head:hover { background: var(--c-card-bg-hover); }
.agent-tool-name { font-size: var(--text-meta); font-weight: 600; color: var(--c-text); flex-shrink: 0; }
.agent-tool-sum { font-size: var(--text-meta); color: var(--c-text-faint); flex: 1; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; font-family: ui-monospace, monospace; }
.agent-tool-meta { font-size: var(--text-meta); color: var(--c-text-faint); flex-shrink: 0; }
.agent-tool-fold { font-size: var(--text-meta); color: var(--c-text-dimmer); flex-shrink: 0; }
.agent-badge { font-size: 10px; color: var(--c-warn); border: 1px solid color-mix(in srgb, var(--c-warn) 40%, transparent);
  border-radius: 3px; padding: 0 3px; flex-shrink: 0; }
.agent-tool-body { margin: 0; padding: 8px 10px; font-size: var(--text-meta); line-height: 1.55;
  max-height: 300px; overflow: auto; white-space: pre-wrap; word-break: break-all;
  border-top: 1px solid var(--c-border-light); color: var(--c-text-dim); background: var(--c-card-bg); }
.agent-tool-body :deep(.agent-table-wrap) { overflow-x: auto; margin: 0; }
.agent-tool-body :deep(.agent-table) { border-collapse: collapse; font-size: var(--text-meta); width: 100%; }
.agent-tool-body :deep(.agent-table th) { text-align: left; padding: 3px 7px; font-weight: 600;
  color: var(--c-text); background: var(--c-card-bg-hover); border: 1px solid var(--c-border-light); white-space: nowrap; }
.agent-tool-body :deep(.agent-table td) { padding: 3px 7px; border: 1px solid var(--c-border-light); }
.agent-tool-body :deep(.agent-p) { margin: 0 0 4px; }
.agent-tool-actions { display: flex; align-items: center; gap: 8px; padding: 3px 8px;
  border-top: 1px solid var(--c-border-light); background: var(--c-card-bg); }
.agent-tool-note { font-size: var(--text-meta); color: var(--c-warn); }

.agent-running { display: flex; align-items: center; font-size: var(--text-meta); color: var(--c-text-dim); padding: 4px 2px 10px; }
.agent-running-t { margin-left: 8px; color: var(--c-text-faint); }
.agent-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--c-up); margin-right: 6px;
  animation: agent-pulse 1s infinite; }
@keyframes agent-pulse { 0%,100% { opacity: 0.3 } 50% { opacity: 1 } }

.agent-jump { position: absolute; right: 22px; bottom: 88px; z-index: 5; font-size: var(--text-meta);
  padding: 4px 10px; border-radius: var(--radius-sm); cursor: pointer; color: var(--c-text-dim);
  background: var(--c-card-bg-hover); border: 1px solid var(--c-border); box-shadow: 0 2px 8px var(--c-shadow-color); }
.agent-jump:hover { color: var(--c-text); }

.agent-composer { flex-shrink: 0; padding: 8px 12px 10px; border-top: 1px solid var(--c-border-light); }
.agent-composer-bar { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
.agent-hint { font-size: var(--text-meta); color: var(--c-text-faint); flex: 1; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }

/* 技能面板 */
.agent-sk-wrap { display: flex; gap: 12px; min-height: 380px; }
.agent-sk-list { width: 224px; flex-shrink: 0; border-right: 1px solid var(--c-border-light); padding-right: 8px;
  max-height: 520px; overflow-y: auto; }
.agent-sk-edit { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.agent-sk-code :deep(textarea) { font-family: ui-monospace, monospace; font-size: var(--text-meta); }
.agent-sk-actions { display: flex; align-items: center; gap: 8px; }
.agent-fb-item { padding: 8px 0; border-bottom: 1px solid var(--c-border-light); }
.agent-fb-head { display: flex; gap: 10px; align-items: baseline; }
.agent-fb-q { font-size: var(--text-body); color: var(--c-text); flex: 1; }
.agent-fb-t { font-size: var(--text-meta); color: var(--c-text-faint); flex-shrink: 0; }
.agent-fb-c { font-size: var(--text-meta); color: var(--c-text-dim); margin: 3px 0; }

@media (max-width: 900px) {
  .agent-thread { padding: 10px 10px 6px; }
  .agent-bubble.user, .agent-ans, .agent-tool, .agent-reason, .agent-ans-foot { max-width: 100%; }
  .agent-user-edit { width: 100%; }
  .agent-cards { grid-template-columns: 1fr; }
  .agent-sk-wrap { flex-direction: column; }
  .agent-sk-list { width: 100%; border-right: 0; border-bottom: 1px solid var(--c-border-light); padding-bottom: 8px; }
}
</style>
