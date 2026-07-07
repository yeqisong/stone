// Monaco Editor worker setup — 必须在 monaco-editor import 之前加载
import './utils/monaco-worker.js'

// Axios 全局拦截器：所有请求自动带 Authorization header（必须在所有组件之前注册）
import axios from 'axios'
axios.interceptors.request.use(config => {
  const t = localStorage.getItem('token')
  if (t) {
    config.headers.Authorization = 'Bearer ' + t
  }
  return config
})

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'

const app = createApp(App)
app.use(createPinia())
app.mount('#app')
