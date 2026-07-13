import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { useMarketStore } from './market'

export const useNavStore = defineStore('nav', () => {
  const tab = ref('p')
  const dcode = ref('')
  const fid = ref(null)   // feature detail id
  const flowId = ref(null) // DAG flow editor id (null=list, 'new'=新建, number=编辑)
  const prevTab = ref('')
  const pendingEditFeatureId = ref(null)  // 从详情页点编辑时传回列表

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
    if (hash.startsWith('/dag-flows/')) {
      const id = hash.split('/')[2]
      if (id === 'new') { flowId.value = 'new'; tab.value = 'g'; return }
      const num = parseInt(id)
      if (num > 0) { flowId.value = num; tab.value = 'g'; return }
    }
    if (hash.startsWith('/dag-logs/')) {
      const parts = hash.split('/')
      flowId.value = parseInt(parts[2]) || null
      tab.value = 'q'
      return
    }
    if (hash.startsWith('/dag-flow-view/')) {
      flowId.value = parseInt(hash.split('/')[2]) || null
      tab.value = 'r'
      return
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
    const map = {'':'p','/':'p','/market':'m','/signals':'s','/stocks':'l','/status':'x','/models':'a','':'','/functions':'f','/features':'e','/dag-flows':'g'}
    tab.value = map[path] || 'p'
    if (path === '/dag-flows') flowId.value = null  // 普通列表页清空编辑id
  }

  function syncHash() {
    const map = {p:'/',m:'/market',s:'/signals',l:'/stocks',x:'/status',a:'/models',f:'/functions',e:'/features',g:'/dag-flows',v:'/feature/'+fid.value,d:'/detail/'+dcode.value}
    let target = map[tab.value] || '/'
    if (tab.value === 'g' && flowId.value !== null) {
      target = '/dag-flows/' + flowId.value
    }
    if ((tab.value === 'q' || tab.value === 'r') && flowId.value !== null) {
      const prefix = tab.value === 'q' ? '/dag-logs/' : '/dag-flow-view/'
      target = prefix + flowId.value
    }
    if (location.hash.slice(1) !== target) history.pushState(null, '', '#'+target)
  }

  function switchTab(t) {
    tab.value = t
  }

  function showFlowEditor(id) {
    prevTab.value = tab.value
    flowId.value = id
    tab.value = 'g'
  }

  function backFromFlowEditor() {
    flowId.value = null
    tab.value = prevTab.value || 'g'
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

  // tab 变化时同步 URL + 离开 g 页清空 flowId
  watch(tab, (t) => { if (t !== 'g' && t !== 'q' && t !== 'r') flowId.value = null; syncHash() })
  watch(dcode, () => { if (tab.value === 'd') syncHash() })
  watch(fid, () => { if (tab.value === 'v') syncHash() })

  watch(flowId, () => { if (tab.value === 'g' || tab.value === 'q' || tab.value === 'r') syncHash() })

  // 页面加载时解析 URL
  parseHash()
  window.addEventListener('popstate', parseHash)

  return { tab, dcode, fid, flowId, prevTab, pendingEditFeatureId, switchTab, showDetail, backFromDetail, showFeatureDetail, backFromFeatureDetail, showFlowEditor, backFromFlowEditor, parseHash }
})
