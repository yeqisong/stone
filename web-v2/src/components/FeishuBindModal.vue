<template>
  <div v-if="show" class="fb-mask" @click.self="close">
    <div class="fb-card" role="dialog" aria-modal="true" aria-label="绑定飞书">
      <div class="fb-title">绑定飞书</div>
      <div class="fb-desc">
        生成一次性绑定码后，在飞书中向机器人发送
        <code>绑定 {{ code || '123456' }}</code>。
        绑定码 10 分钟内有效，仅可使用一次。
      </div>

      <div class="fb-code" :class="{ empty: !code }">{{ code || '------' }}</div>

      <div class="fb-actions">
        <button class="fb-btn primary" :disabled="loading" @click="generate">
          {{ code ? '重新生成' : (loading ? '生成中…' : '生成绑定码') }}
        </button>
        <button v-if="code" class="fb-btn" @click="copy">{{ copied ? '已复制' : '复制' }}</button>
        <button v-if="bound" class="fb-btn danger" :disabled="loading" @click="unbind">解绑</button>
        <button class="fb-btn" @click="close">关闭</button>
      </div>

      <div v-if="error" class="fb-error">{{ error }}</div>
      <div v-if="bound" class="fb-note">当前账号已绑定飞书；重新生成绑定码会覆盖旧绑定关系。</div>
      <div v-else class="fb-note">未绑定前，飞书机器人不会响应该账号的任何消息。</div>
    </div>
  </div>
</template>

<style scoped>
.fb-mask { position: fixed; inset: 0; z-index: 3000; display: flex; align-items: center; justify-content: center; padding: 20px; background: rgba(0,0,0,.38); }
.fb-card { width: 440px; max-width: 100%; padding: 24px; border: 1px solid var(--c-border, #ddd); border-radius: 8px; background: var(--c-card-bg, #fff); color: var(--c-text, #222); box-shadow: 0 18px 50px rgba(0,0,0,.2); }
.fb-title { font: 600 17px var(--font-sans, sans-serif); }
.fb-desc { margin: 10px 0 16px; font-size: 13px; line-height: 1.7; color: var(--c-text-dim, #666); }
.fb-desc code { padding: 1px 5px; border-radius: 3px; background: var(--c-input-bg, #f4f4f4); color: var(--c-text, #222); }
.fb-code { margin: 4px 0 18px; padding: 18px 12px; border: 1px dashed var(--c-border, #bbb); border-radius: 6px; text-align: center; font: 600 34px/1 ui-monospace, SFMono-Regular, Consolas, monospace; letter-spacing: 8px; color: var(--c-accent, #8f1a2b); }
.fb-code.empty { color: var(--c-text-faint, #aaa); letter-spacing: 4px; }
.fb-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.fb-btn { height: 34px; padding: 0 14px; border: 1px solid var(--c-border, #ccc); border-radius: 4px; background: var(--c-card-bg, #fff); color: var(--c-text, #222); font: 13px var(--font-sans, sans-serif); cursor: pointer; }
.fb-btn:hover { border-color: var(--c-accent, #8f1a2b); }
.fb-btn.primary { border-color: var(--c-accent, #8f1a2b); background: var(--c-accent, #8f1a2b); color: #fff; }
.fb-btn.danger { color: var(--c-error, #b3261e); }
.fb-btn:disabled { opacity: .6; cursor: not-allowed; }
.fb-error { margin-top: 12px; color: var(--c-error, #b3261e); font-size: 12px; }
.fb-note { margin-top: 14px; color: var(--c-text-dimmer, #888); font-size: 12px; line-height: 1.6; }
</style>

<script setup>
import { ref, watch } from 'vue'
import axios from 'axios'

const props = defineProps({
  show: { type: Boolean, default: false },
  bound: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'changed'])

const code = ref('')
const expiresIn = ref(0)
const loading = ref(false)
const error = ref('')
const copied = ref(false)

watch(() => props.show, (v) => {
  if (v) {
    code.value = ''
    expiresIn.value = 0
    error.value = ''
    copied.value = false
  }
})

async function generate() {
  loading.value = true
  error.value = ''
  copied.value = false
  try {
    const r = await axios.post('/api/me/feishu/bind-code')
    code.value = r.data?.code || ''
    expiresIn.value = Number(r.data?.expires_in || 0)
  } catch (e) {
    error.value = e.response?.data?.detail || '生成失败'
  } finally {
    loading.value = false
  }
}

async function copy() {
  try {
    await navigator.clipboard.writeText(code.value)
    copied.value = true
    setTimeout(() => { copied.value = false }, 1200)
  } catch (_) {
    error.value = '复制失败，请手动选择复制'
  }
}

async function unbind() {
  if (!window.confirm('确定解除飞书绑定？解绑后该飞书账号将无法使用机器人。')) return
  loading.value = true
  error.value = ''
  try {
    await axios.delete('/api/me/feishu/bind')
    code.value = ''
    emit('changed')
  } catch (e) {
    error.value = e.response?.data?.detail || '解绑失败'
  } finally {
    loading.value = false
  }
}

function close() {
  emit('close')
}
</script>
