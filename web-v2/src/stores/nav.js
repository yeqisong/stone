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
  const marketDate = ref('')
  const stockFundType = ref('stock')     // 个股/指数/ETF 列表筛选
  const modelEntity = ref('stock')
  const modelVersion = ref('')
  const modelTab = ref('')
  const statusMonth = ref('')
  const bfLogPage = ref(1)

  // URL hash 路由
  function parseHash() {
    const hash = location.hash.slice(1) || '/'
    const path = hash.includes('?') ? hash.split('?')[0] : hash
    const q = (k) => new URLSearchParams(hash.split('?')[1] || '').get(k) || ''

    if (hash.startsWith('/detail/')) { dcode.value = hash.split('/')[2] || ''; tab.value = 'd'; return }
    if (hash.startsWith('/feature/')) { fid.value = parseInt(hash.split('/')[2]) || null; tab.value = 'v'; return }
    if (hash.startsWith('/dag-flows/')) {
      const id = hash.split('/')[2]
      if (id === 'new') { flowId.value = 'new'; tab.value = 'g'; return }
      const num = parseInt(id)
      if (num > 0) { flowId.value = num; tab.value = 'g'; return }
    }
    if (hash.startsWith('/dag-logs/')) { flowId.value = parseInt(hash.split('/')[2]) || null; tab.value = 'q'; return }
    if (hash.startsWith('/dag-flow-view/')) { flowId.value = parseInt(hash.split('/')[2]) || null; tab.value = 'r'; return }
    if (hash.startsWith('/market/')) { marketDate.value = hash.split('/')[2] || ''; tab.value = 'm'; return }
    if (hash.startsWith('/stock-fund/')) { stockFundType.value = hash.split('/')[2] || 'stock'; tab.value = 'u'; return }
    if (path.startsWith('/models')) {
      const parts = path.split('/')
      modelEntity.value = parts[2] || 'stock'
      modelVersion.value = parts[3] || ''
      modelTab.value = q('tab')
      tab.value = 'a'
      return
    }
    if (path === '/status') {
      statusMonth.value = q('month')
      bfLogPage.value = parseInt(q('bf_page')) || 1
      tab.value = 'x'
      return
    }
    const map = {'':'p','/':'p','/market':'m','/signals':'s','/stocks':'l','/status':'x','/models':'a','/functions':'f','/features':'e','/dag-flows':'g','/portfolio':'p','/dbx':'w'}
    tab.value = map[path] || 'p'
    if (path === '/dag-flows') flowId.value = null
  }

  function syncHash() {
    let target = '/'
    switch (tab.value) {
      case 'p': target = '/'; break
      case 'm': {
        // 保留树图页的 metric/color 查询参数（TreemapView 维护，切 tab 回来不丢）
        const cur = location.hash.slice(1)
        const qm = cur.startsWith('/market/') && cur.includes('?') ? cur.slice(cur.indexOf('?')) : ''
        target = '/market/' + (marketDate.value || useMarketStore().selDate) + qm
        break
      }
      case 's': target = '/signals'; break
      case 'l': target = '/stocks'; break
      case 'x': {
        target = '/status'
        const sq = []
        if (statusMonth.value) sq.push('month=' + statusMonth.value)
        if (bfLogPage.value > 1) sq.push('bf_page=' + bfLogPage.value)
        if (sq.length) target += '?' + sq.join('&')
        break
      }
      case 'a': {
        target = '/models/' + modelEntity.value
        if (modelVersion.value) target += '/' + modelVersion.value
        if (modelTab.value) target += '?tab=' + modelTab.value
        break
      }
      case 'f': target = '/functions'; break
      case 'e': target = '/features'; break
      case 'g': target = flowId.value !== null ? '/dag-flows/' + flowId.value : '/dag-flows'; break
      case 'v': target = '/feature/' + fid.value; break
      case 'd': target = '/detail/' + dcode.value; break
      case 'q': target = '/dag-logs/' + (flowId.value || ''); break
      case 'r': target = '/dag-flow-view/' + (flowId.value || ''); break
      case 'u': target = '/stock-fund/' + stockFundType.value; break
      case 'w': target = '/dbx'; break
      default: target = '/'
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

  return { tab, dcode, fid, flowId, prevTab, pendingEditFeatureId, stockFundType, marketDate, modelEntity, modelVersion, modelTab, statusMonth, bfLogPage, switchTab, showDetail, backFromDetail, showFeatureDetail, backFromFeatureDetail, showFlowEditor, backFromFlowEditor, parseHash, syncHash }
})
