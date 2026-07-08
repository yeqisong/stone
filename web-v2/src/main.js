// Monaco Editor worker setup — 必须在 monaco-editor import 之前加载
import './utils/monaco-worker.js'

// Axios 全局拦截器
import axios from 'axios'

// 请求拦截：自动带 token
axios.interceptors.request.use(config => {
  const t = localStorage.getItem('token')
  if (t) config.headers.Authorization = 'Bearer ' + t
  return config
})

// 响应拦截：401 → 跳转登录页（带 returnUrl）
axios.interceptors.response.use(
  r => r,
  error => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token')
      const returnUrl = encodeURIComponent(location.hash || '/')
      location.hash = '#/login?return=' + returnUrl
    }
    return Promise.reject(error)
  }
)

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'

const app = createApp(App)
app.use(createPinia())
app.mount('#app')
