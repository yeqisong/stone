<template>
  <div class="support-page">
    <div class="support-card">
      <div class="support-head">
        <div>
          <div class="support-title">支持模式</div>
          <div class="support-sub">管理员创建支持会话后，把一次性令牌交给支持人员；此处进入后只读查看目标用户数据。</div>
        </div>
        <span class="support-badge" :class="{ on: auth.supportMode }">
          {{ auth.supportMode ? '支持中' : '未进入' }}
        </span>
      </div>

      <template v-if="auth.supportMode">
        <div class="support-target">
          <div class="support-target-label">当前目标用户</div>
          <div class="support-target-name">
            {{ auth.supportTargetName || ('#' + auth.supportTargetId) }}
            <span>ID {{ auth.supportTargetId }}</span>
          </div>
          <div class="support-note">
            只读模式：不能写入、不能查看个人配置/Agent 会话/系统设置；所有访问已记入审计。
          </div>
        </div>
        <div class="support-actions">
          <n-button size="small" type="primary" @click="enterTarget">查看目标用户页面</n-button>
          <n-button size="small" @click="exitSupport">退出支持模式</n-button>
        </div>
      </template>

      <template v-else>
        <div class="support-form">
          <label class="support-label" for="support-token">已有支持令牌</label>
          <n-input id="support-token" v-model:value="token" type="password"
                   placeholder="粘贴管理员生成的 support_token" show-password-on="click"
                   @keyup.enter="applyToken" />
          <div class="support-actions">
            <n-button size="small" type="primary" :loading="loading" @click="applyToken">进入支持模式</n-button>
          </div>
        </div>

        <div class="support-divider">或创建一个新的支持会话</div>
        <div class="support-create">
          <label class="support-label">目标用户 ID</label>
          <n-input v-model:value="targetId" placeholder="如 3；可在用户管理页查看" />
          <label class="support-label">支持原因（至少 10 字，写入审计）</label>
          <n-input v-model:value="reason" placeholder="如：排查用户反馈的持仓显示异常" />
          <label class="support-label">有效期</label>
          <n-input-number v-model:value="ttl" :min="1" :max="60" style="width:120px">
            <template #suffix>分钟</template>
          </n-input-number>
          <div class="support-actions">
            <n-button size="small" type="primary" :loading="creating" @click="createSession">创建并进入</n-button>
          </div>
          <div v-if="error" class="support-error">{{ error }}</div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.support-page { padding: 16px; display: flex; justify-content: center; }
.support-card { width: 680px; max-width: 100%; padding: 18px 20px; border: 1px solid var(--c-border); border-radius: 8px; background: var(--c-card-bg); }
.support-head { display: flex; align-items: flex-start; gap: 12px; }
.support-title { color: var(--c-text); font-size: 16px; font-weight: 600; }
.support-sub { margin-top: 4px; color: var(--c-text-dim); font-size: 12px; line-height: 1.6; }
.support-badge { margin-left: auto; flex: none; padding: 1px 8px; border: 1px solid var(--c-border); border-radius: 10px; color: var(--c-text-faint); font-size: 11px; }
.support-badge.on { border-color: var(--c-accent); color: var(--c-accent); background: var(--c-accent-weak); }
.support-form { margin-top: 18px; }
.support-divider { display: flex; align-items: center; gap: 10px; margin: 18px 0 12px; color: var(--c-text-faint); font-size: 11px; }
.support-divider::before, .support-divider::after { content: ""; flex: 1; height: 1px; background: var(--c-border-light); }
.support-create { display: grid; grid-template-columns: 110px minmax(0, 1fr); align-items: center; gap: 8px 10px; }
.support-create .support-label { margin: 0; }
.support-create .support-actions { grid-column: 2; }
.support-create .support-error { grid-column: 2; margin-top: 0; }
.support-label { display: block; margin-bottom: 6px; color: var(--c-text-dim); font-size: 12px; }
.support-actions { display: flex; gap: 8px; margin-top: 12px; }
.support-error { margin-top: 8px; color: var(--c-error); font-size: 12px; }
.support-target { margin-top: 18px; padding: 12px 14px; border: 1px solid var(--c-border-light); border-radius: 6px; background: var(--c-bg); }
.support-target-label { color: var(--c-text-faint); font-size: 11px; }
.support-target-name { margin-top: 4px; color: var(--c-text); font-size: 16px; font-weight: 600; }
.support-target-name span { margin-left: 6px; color: var(--c-text-faint); font-size: 11px; font-weight: 400; }
.support-note { margin-top: 8px; color: var(--c-text-dim); font-size: 11.5px; line-height: 1.6; }
</style>

<script setup>
import { ref } from 'vue'
import { NButton, NInput, NInputNumber, useMessage } from 'naive-ui'
import axios from 'axios'
import { useAuthStore } from '../stores/auth'
import { useNavStore } from '../stores/nav'

const auth = useAuthStore()
const nav = useNavStore()
const message = useMessage()
const token = ref('')
const loading = ref(false)
const creating = ref(false)
const error = ref('')
const targetId = ref('')
const reason = ref('')
const ttl = ref(30)

const TABS = [
  ['p', 'portfolio.position.view'], ['k', 'research.market.view'],
  ['s', 'research.signal.view'], ['l', 'research.stock.view'],
  ['b', 'research.cb.view'], ['n', 'research.news.view'],
  ['i', 'agent.use'], ['x', 'system.status.view'],
  ['a', 'model.view'], ['f', 'function.view'],
  ['e', 'feature.view'], ['g', 'dag.view'],
]
function enterTarget() {
  const hit = TABS.find(([, perm]) => auth.can(perm))
  nav.switchTab(hit ? hit[0] : 'p')
}

async function applyToken() {
  error.value = ''
  const t = (token.value || '').trim()
  if (!t) { error.value = '请填写支持令牌'; return }
  loading.value = true
  localStorage.setItem('support_token', t)
  try {
    const me = await auth.refreshMe()
    if (!me.support_mode) {
      localStorage.removeItem('support_token')
      error.value = '令牌无效、已过期，或不是分配给当前账号的支持会话'
      await auth.refreshMe()
      return
    }
    message.success(`已进入支持模式：${me.support_target_name || ('#' + me.support_target_id)}`)
    token.value = ''
    enterTarget()
  } catch (e) {
    localStorage.removeItem('support_token')
    error.value = e.response?.data?.detail || '进入支持模式失败'
    await auth.refreshMe().catch(() => {})
  } finally {
    loading.value = false
  }
}

async function createSession() {
  error.value = ''
  if (!targetId.value || Number(targetId.value) <= 0) { error.value = '请填写目标用户 ID'; return }
  if ((reason.value || '').trim().length < 10) { error.value = '支持原因至少 10 个字'; return }
  creating.value = true
  try {
    const r = await axios.post('/api/admin/support/sessions', {
      target_user_id: Number(targetId.value),
      reason: reason.value.trim(),
      ttl_minutes: Number(ttl.value || 30),
    })
    token.value = r.data.support_token
    await applyToken()
    targetId.value = ''
    reason.value = ''
  } catch (e) {
    error.value = e.response?.data?.detail || '创建支持会话失败'
  } finally {
    creating.value = false
  }
}

async function exitSupport() {
  localStorage.removeItem('support_token')
  await auth.refreshMe().catch(() => {})
  nav.switchTab('j')
  message.success('已退出支持模式')
}
</script>
