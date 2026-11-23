// Monaco Editor worker setup — 必须在 monaco-editor import 之前加载
import './utils/monaco-worker.js'
import './styles/tokens.css'

// Axios 全局拦截器
import axios from 'axios'

// 请求拦截：自动带 token
axios.interceptors.request.use(config => {
  const t = localStorage.getItem('token')
  if (t) config.headers.Authorization = 'Bearer ' + t
  const st = localStorage.getItem('support_token')
  if (st) config.headers['X-Support-Session'] = st
  return config
})

// 响应拦截：401 → 清 token + reload → 显示 LoginView
// 排除 /api/login：密码错误返回 401 时不应整页刷新（让登录框显示错误提示）
let _401Reloading = false
axios.interceptors.response.use(
  r => r,
  error => {
    const isLogin = error.config?.url?.includes('/api/login')
    if (error.response?.status === 401 && !_401Reloading && !isLogin) {
      _401Reloading = true
      // 支持会话是叠加在主账号 token 上的只读上下文：它失效时只清 support_token，
      // 不能把管理员/用户本人登录态一起清掉（否则每次进支持模式都像被登出）。
      if (localStorage.getItem('support_token')) {
        localStorage.removeItem('support_token')
      } else {
        localStorage.removeItem('token')
        localStorage.removeItem('username')
        localStorage.removeItem('auth_user')
        localStorage.removeItem('auth_roles')
        localStorage.removeItem('auth_permissions')
      }
      window.location.reload()
    }
    return Promise.reject(error)
  }
)

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { useAuthStore } from './stores/auth'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)

// v-perm="'model.train'"：无权限时隐藏元素；权限变化（登录/角色切换）时自动恢复
const auth = useAuthStore(pinia)
function applyPerm(el, binding) {
  el.style.display = auth.can(binding.value) ? '' : 'none'
}
app.directive('perm', {
  mounted(el, binding) { applyPerm(el, binding) },
  updated(el, binding) { applyPerm(el, binding) },
})

app.mount('#app')
