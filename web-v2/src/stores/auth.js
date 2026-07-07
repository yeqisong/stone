import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import axios from 'axios'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const username = ref(localStorage.getItem('username') || '')
  const isLoggedIn = computed(() => !!token.value)

  // 初始化时从 localStorage 恢复 axios Authorization header（刷新页面后必须）
  if (token.value) {
    axios.defaults.headers.common['Authorization'] = 'Bearer ' + token.value
  }

  // 请求拦截器：每次请求自动从 localStorage 读取最新 token
  axios.interceptors.request.use(config => {
    const t = localStorage.getItem('token')
    if (t) {
      config.headers.Authorization = 'Bearer ' + t
    }
    return config
  })

  function setToken(t) {
    token.value = t
    localStorage.setItem('token', t)
    axios.defaults.headers.common['Authorization'] = 'Bearer ' + t
  }

  function setUser(name) {
    username.value = name
    localStorage.setItem('username', name)
  }

  function logout() {
    token.value = ''
    username.value = ''
    localStorage.removeItem('token')
    localStorage.removeItem('username')
    delete axios.defaults.headers.common['Authorization']
  }

  return { token, username, isLoggedIn, setToken, setUser, logout }
})
