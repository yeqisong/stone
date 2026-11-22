<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-logo"><LogoFull :height="26" /></div>
      <div class="login-sub">首次登录 / 密码重置后必须修改密码</div>

      <label class="login-label" for="cp-old">当前密码</label>
      <input id="cp-old" v-model="oldPassword" type="password" class="login-input"
             autocomplete="current-password" @keyup.enter="submit" />

      <label class="login-label" for="cp-new">新密码（至少 8 位）</label>
      <input id="cp-new" v-model="newPassword" type="password" class="login-input"
             autocomplete="new-password" @keyup.enter="submit" />

      <label class="login-label" for="cp-confirm">确认新密码</label>
      <input id="cp-confirm" v-model="confirmPassword" type="password" class="login-input"
             autocomplete="new-password" @keyup.enter="submit" />

      <button class="login-btn" :disabled="loading" @click="submit">
        {{ loading ? '提交中…' : '修改密码并继续' }}
      </button>
      <div class="login-error" role="alert">{{ error }}</div>
    </div>
  </div>
</template>

<style scoped>
.login-page { min-height: 100vh; min-height: 100dvh; display: flex; align-items: center; justify-content: center; padding: 24px; background: var(--paper); }
.login-card { width: 400px; max-width: 100%; padding: 36px 32px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); }
.login-logo { display: flex; justify-content: center; color: var(--ink); }
.login-sub { margin: 6px 0 26px; text-align: center; font-size: 12px; color: var(--muted); }
.login-label { display: block; margin: 0 0 6px; font-size: 12px; color: var(--muted); }
.login-input { width: 100%; height: 40px; margin-bottom: 16px; padding: 0 12px; border: 1px solid var(--line); border-radius: 4px; background: var(--surface-2); color: var(--ink); font: 14px var(--font-sans); outline: none; }
.login-input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-ring); }
.login-btn { width: 100%; height: 40px; margin-top: 4px; border: 0; border-radius: 4px; background: var(--accent); color: var(--accent-ink); font: 500 14px var(--font-sans); cursor: pointer; }
.login-btn:disabled { opacity: .6; cursor: not-allowed; }
.login-error { min-height: 20px; margin-top: 10px; text-align: center; font-size: 12px; color: var(--accent); }
</style>

<script setup>
import { ref } from 'vue'
import axios from 'axios'
import { useAuthStore } from '../stores/auth'
import LogoFull from './LogoFull.vue'

const emit = defineEmits(['done'])
const auth = useAuthStore()
const oldPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const loading = ref(false)
const error = ref('')

async function submit() {
  error.value = ''
  if (!oldPassword.value || !newPassword.value) { error.value = '请填写新旧密码'; return }
  if (newPassword.value.length < 8) { error.value = '新密码至少 8 位'; return }
  if (newPassword.value !== confirmPassword.value) { error.value = '两次输入的新密码不一致'; return }
  loading.value = true
  try {
    const r = await axios.post('/api/me/password', {
      old_password: oldPassword.value,
      new_password: newPassword.value,
    })
    auth.setAuth(r.data)
    emit('done')
  } catch (e) {
    error.value = e.response?.data?.detail || '修改失败'
  } finally {
    loading.value = false
  }
}
</script>
