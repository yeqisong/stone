<template>
<div class="login-page">
  <div class="login-card">
    <div class="login-logo"><LogoFull :height="26" /></div>
    <div class="login-sub">Stock Signal Monitor</div>

    <label class="login-label" for="login-user">用户名</label>
    <input id="login-user" v-model="username" class="login-input" placeholder="admin"
           autocomplete="username" @keyup.enter="login" />

    <label class="login-label" for="login-pass">密码</label>
    <input id="login-pass" v-model="password" type="password" class="login-input" placeholder="••••••"
           autocomplete="current-password" @keyup.enter="login" />

    <button class="login-btn" @click="login" :disabled="loading">{{ loading ? '登录中…' : '登 录' }}</button>
    <div class="login-error" role="alert">{{ error }}</div>
  </div>
</div>
</template>

<style scoped>
.login-page {
  height: 100vh;
  height: 100dvh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: var(--paper);
}
.login-card {
  width: 380px;
  max-width: 100%;
  padding: 36px 32px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 10px;
  box-shadow: var(--shadow);
}
.login-logo { display: flex; justify-content: center; color: var(--ink); }
.login-sub { margin: 6px 0 26px; text-align: center; font-size: 12px; color: var(--muted); letter-spacing: .02em; }
.login-label { display: block; margin: 0 0 6px; font-size: 12px; color: var(--muted); }
.login-input {
  width: 100%;
  height: 40px;
  margin-bottom: 16px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: 4px;
  background: var(--surface-2);
  color: var(--ink);
  font: 14px var(--font-sans);
  outline: none;
  transition: border-color .15s, box-shadow .15s;
}
.login-input::placeholder { color: var(--faint); }
.login-input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-ring); }
.login-btn {
  width: 100%;
  height: 40px;
  margin-top: 4px;
  border: 0;
  border-radius: 4px;
  background: var(--accent);
  color: var(--accent-ink);
  font: 500 14px var(--font-sans);
  cursor: pointer;
  transition: background-color .15s;
}
.login-btn:hover { background: var(--accent-hover); }
.login-btn:disabled { opacity: .6; cursor: not-allowed; }
.login-error { min-height: 20px; margin-top: 10px; text-align: center; font-size: 12px; color: var(--accent); }
</style>

<script setup>
import { ref } from 'vue'
import axios from 'axios'
import { useAuthStore } from '../stores/auth'
import LogoFull from './LogoFull.vue'

const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')
const auth = useAuthStore()

async function login() {
  if (!username.value || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  error.value = ''
  loading.value = true
  try {
    const r = await axios.post('/api/login', {
      username: username.value,
      password: password.value
    })
    auth.setToken(r.data.token)
    auth.setUser(r.data.username)
    // 登录后跳回原页面
    const params = new URLSearchParams(location.hash.includes('?') ? location.hash.split('?')[1] : '')
    const returnUrl = params.get('return')
    if (returnUrl) {
      location.hash = decodeURIComponent(returnUrl)
    }
  } catch(e) {
    error.value = e.response?.data?.detail || '登录失败（请检查网络或后端服务）'
  }
  loading.value = false
}
</script>
