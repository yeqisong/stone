<template>
<div style="height:100vh;display:flex;align-items:center;justify-content:center;background:var(--c-bg)">
  <div style="background:#1e1e22;border:1px solid var(--c-border);border-radius:16px;padding:40px;width:380px;max-width:90vw">
    <h1 style="font-size:24px;text-align:center;margin-bottom:4px;color:var(--c-text)">K道</h1>
    <div style="font-size:11px;color:var(--c-text-dimmer);text-align:center;margin-bottom:28px">Stock Signal Monitor</div>
    <div style="font-size:11px;color:var(--c-text-dim);margin-bottom:4px">用户名</div>
    <input v-model="username" placeholder="admin" class="input-dark" @keyup.enter="login" />
    <div style="font-size:11px;color:var(--c-text-dim);margin-top:14px;margin-bottom:4px">密码</div>
    <input v-model="password" type="password" placeholder="••••••" class="input-dark" @keyup.enter="login" />
    <button style="width:100%;background:#2080f0;color:var(--c-text);border:none;padding:12px;border-radius:8px;cursor:pointer;font-size:14px;margin-top:20px" @click="login" :disabled="loading">{{loading?'登录中…':'登 录'}}</button>
    <div style="color:#d03050;font-size:12px;text-align:center;margin-top:8px;min-height:18px;padding:4px 0;word-break:break-all">{{error}}</div>
  </div>
</div>
</template>
<style scoped>
.input-dark{width:100%;background:var(--c-bg);border:1px solid var(--c-card-bg-hover);color:var(--c-text);padding:10px 14px;border-radius:8px;font-size:14px;outline:none;box-sizing:border-box}
.input-dark:-webkit-autofill{background:var(--c-bg)!important;-webkit-box-shadow:0 0 0 30px #101014 inset!important;-webkit-text-fill-color:#fff!important;caret-color:#fff;border:1px solid #000!important}
.input-dark:focus{border-color:#2080f0}
</style>

<script setup>
import { ref } from 'vue'
import axios from 'axios'
import { useAuthStore } from '../stores/auth'

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
