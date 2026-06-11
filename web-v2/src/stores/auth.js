import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import axios from 'axios'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const username = ref(localStorage.getItem('username') || '')
  const isLoggedIn = computed(() => !!token.value)

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
