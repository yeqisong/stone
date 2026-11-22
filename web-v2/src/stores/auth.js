import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import axios from 'axios'
import { disconnectWebSocket } from '../utils/ws'

function readJSON(key, fallback) {
  try {
    const v = localStorage.getItem(key)
    return v ? JSON.parse(v) : fallback
  } catch (_) {
    return fallback
  }
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const user = ref(readJSON('auth_user', null))
  const roles = ref(readJSON('auth_roles', []))
  const permissions = ref(readJSON('auth_permissions', []))
  const permVersion = ref(Number(localStorage.getItem('auth_perm_version') || 0))
  const mustChangePassword = ref(localStorage.getItem('auth_must_change') === '1')
  const supportMode = ref(false)
  const supportTargetId = ref(null)
  const supportTargetName = ref('')
  const permissionSet = computed(() => new Set(permissions.value || []))
  const username = computed(() => user.value?.username || localStorage.getItem('username') || '')
  const isLoggedIn = computed(() => !!token.value)
  const isAdmin = computed(() => (roles.value || []).includes('admin'))

  function _persist() {
    localStorage.setItem('username', username.value)
    localStorage.setItem('auth_user', JSON.stringify(user.value || {}))
    localStorage.setItem('auth_roles', JSON.stringify(roles.value || []))
    localStorage.setItem('auth_permissions', JSON.stringify(permissions.value || []))
    localStorage.setItem('auth_perm_version', String(permVersion.value || 0))
    localStorage.setItem('auth_must_change', mustChangePassword.value ? '1' : '0')
  }

  function setToken(t) {
    token.value = t
    localStorage.setItem('token', t)
    axios.defaults.headers.common['Authorization'] = 'Bearer ' + t
  }

  function setUser(name) {
    user.value = { ...(user.value || {}), username: name }
    localStorage.setItem('username', name)
    _persist()
  }

  function _apply(d) {
    if (d.user) user.value = d.user
    roles.value = d.roles || []
    permissions.value = d.permissions || []
    permVersion.value = Number(d.perm_version || 0)
    mustChangePassword.value = !!d.must_change_password
    supportMode.value = !!d.support_mode
    supportTargetId.value = d.support_target_id || null
    supportTargetName.value = d.support_target_name || ''
    _persist()
  }

  function setAuth(data) {
    setToken(data.token)
    _apply(data)
  }

  async function refreshMe() {
    const r = await axios.get('/api/me')
    _apply(r.data || {})
    return r.data || {}
  }

  function can(code) {
    if (!code) return true
    return isAdmin.value || permissionSet.value.has(code)
  }

  function canAny(codes) {
    if (!codes || !codes.length) return true
    return codes.some(c => can(c))
  }

  function logout() {
    token.value = ''
    user.value = null
    roles.value = []
    permissions.value = []
    permVersion.value = 0
    mustChangePassword.value = false
    supportMode.value = false
    supportTargetId.value = null
    supportTargetName.value = ''
    localStorage.removeItem('support_token')
    localStorage.removeItem('token')
    localStorage.removeItem('username')
    localStorage.removeItem('auth_user')
    localStorage.removeItem('auth_roles')
    localStorage.removeItem('auth_permissions')
    localStorage.removeItem('auth_perm_version')
    localStorage.removeItem('auth_must_change')
    delete axios.defaults.headers.common['Authorization']
    try { disconnectWebSocket() } catch (_) {}
  }

  return {
    token, user, roles, permissions, permVersion, mustChangePassword,
    supportMode, supportTargetId, supportTargetName,
    username, isLoggedIn, isAdmin, permissionSet,
    setToken, setUser, setAuth, refreshMe, can, canAny, logout,
  }
})
