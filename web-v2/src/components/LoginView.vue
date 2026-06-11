<template>
<div style="height:100vh;display:flex;align-items:center;justify-content:center;background:#101014">
  <div style="background:#1e1e22;border:1px solid rgba(255,255,255,.09);border-radius:16px;padding:40px;width:380px;max-width:90vw">
    <h1 style="font-size:24px;text-align:center;margin-bottom:4px;color:#fff">K道</h1>
    <div style="font-size:11px;color:rgba(255,255,255,.38);text-align:center;margin-bottom:28px">Stock Signal Monitor</div>
    <div style="font-size:11px;color:rgba(255,255,255,.55);margin-bottom:4px">用户名</div>
    <input v-model="username" placeholder="admin" class="input-dark" @keyup.enter="login" />
    <div style="font-size:11px;color:rgba(255,255,255,.55);margin-top:14px;margin-bottom:4px">密码</div>
    <input v-model="password" type="password" placeholder="••••••" class="input-dark" @keyup.enter="login" />
    <button style="width:100%;background:#2080f0;color:#fff;border:none;padding:12px;border-radius:8px;cursor:pointer;font-size:14px;margin-top:20px" @click="login" :disabled="loading">{{loading?'登录中…':'登 录'}}</button>
    <div style="color:#d03050;font-size:12px;text-align:center;margin-top:8px;min-height:18px;padding:4px 0;word-break:break-all">{{error}}</div>
  </div>
</div>
</template>
<style scoped>
.input-dark{width:100%;background:#101014;border:1px solid rgba(255,255,255,.04);color:#fff;padding:10px 14px;border-radius:8px;font-size:14px;outline:none;box-sizing:border-box}
.input-dark:-webkit-autofill{background:#101014!important;-webkit-box-shadow:0 0 0 30px #101014 inset!important;-webkit-text-fill-color:#fff!important;caret-color:#fff;border:1px solid #000!important}
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
  } catch(e) {
    error.value = e.response?.data?.detail || '登录失败（请检查网络或后端服务）'
  }
  loading.value = false
}
</script>
