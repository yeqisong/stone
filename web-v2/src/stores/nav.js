import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { useMarketStore } from './market'

export const useNavStore = defineStore('nav', () => {
  const tab = ref('p')
  const dcode = ref('')
  const prevTab = ref('')

  // URL hash 路由
  function parseHash() {
    const hash = location.hash.slice(1) || '/'
    if (hash.startsWith('/detail/')) {
      const code = hash.split('/')[2]
      if (code) { dcode.value = code; tab.value = 'd'; return }
    }
    if (hash.startsWith('/market/')) {
      tab.value = 'm'
      const parts = hash.split('/')
      if (parts[2]) {
        const mkt = useMarketStore()
        mkt.setDate(parts[2])
      }
      return
    }
    const map = {'':'p','/':'p','/market':'m','/signals':'s','/stocks':'l','/status':'x','/models':'a','/settings':'o'}
    tab.value = map[hash] || 'p'
  }

  function syncHash() {
    const map = {p:'/',m:'/market',s:'/signals',l:'/stocks',x:'/status',a:'/models',o:'/settings',d:'/detail/'+dcode.value}
    const target = map[tab.value] || '/'
    if (location.hash.slice(1) !== target) history.pushState(null, '', '#'+target)
  }

  function switchTab(t) {
    tab.value = t
  }

  function showDetail(code) {
    prevTab.value = tab.value
    dcode.value = code
    tab.value = 'd'
  }

  function backFromDetail() {
    tab.value = prevTab.value || 'l'
  }

  // tab 变化时同步 URL
  watch(tab, syncHash)
  watch(dcode, () => { if (tab.value === 'd') syncHash() })

  // 页面加载时解析 URL
  parseHash()
  window.addEventListener('popstate', parseHash)

  return { tab, dcode, prevTab, switchTab, showDetail, backFromDetail, parseHash }
})
