import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { useMarketStore } from './market'

export const useNavStore = defineStore('nav', () => {
  const tab = ref('p')
  const dcode = ref('')
  const fid = ref(null)   // feature detail id
  const prevTab = ref('')

  // URL hash 路由
  function parseHash() {
    const hash = location.hash.slice(1) || '/'
    if (hash.startsWith('/detail/')) {
      const code = hash.split('/')[2]
      if (code) { dcode.value = code; tab.value = 'd'; return }
    }
    if (hash.startsWith('/feature/')) {
      const id = parseInt(hash.split('/')[2])
      if (id) { fid.value = id; tab.value = 'v'; return }
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
    // 去掉 query 参数进行路径匹配
    const path = hash.includes('?') ? hash.split('?')[0] : hash
    const map = {'':'p','/':'p','/market':'m','/signals':'s','/stocks':'l','/status':'x','/models':'a','/settings':'o','/functions':'f','/features':'e','/dag-flows':'g'}
    tab.value = map[path] || 'p'
  }

  function syncHash() {
    const map = {p:'/',m:'/market',s:'/signals',l:'/stocks',x:'/status',a:'/models',o:'/settings',f:'/functions',e:'/features',g:'/dag-flows',v:'/feature/'+fid.value,d:'/detail/'+dcode.value}
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

  function showFeatureDetail(id) {
    prevTab.value = tab.value
    fid.value = id
    tab.value = 'v'
  }

  function backFromFeatureDetail() {
    tab.value = prevTab.value || 'e'
  }

  // tab 变化时同步 URL
  watch(tab, syncHash)
  watch(dcode, () => { if (tab.value === 'd') syncHash() })
  watch(fid, () => { if (tab.value === 'v') syncHash() })

  // 页面加载时解析 URL
  parseHash()
  window.addEventListener('popstate', parseHash)

  return { tab, dcode, fid, prevTab, switchTab, showDetail, backFromDetail, showFeatureDetail, backFromFeatureDetail, parseHash }
})
