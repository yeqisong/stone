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
  const agentSeed = ref(null)   // 跨页面带入问答页的预填问题 { text, at }
  const tradeReviewTab = ref('review')  // 交易复盘二级页的初始 tab
  const bondCode = ref('')               // 可转债详情码（/bond/:code）
  const midx = ref('000001.SH')          // 大盘页选中指数（ts_code 带后缀，/k?idx=）

  // URL hash 路由
  function parseHash() {
    const hash = location.hash.slice(1) || '/'
    const path = hash.includes('?') ? hash.split('?')[0] : hash
    const q = (k) => new URLSearchParams(hash.split('?')[1] || '').get(k) || ''

    if (hash.startsWith('/bond/')) { bondCode.value = hash.split('/')[2] || ''; tab.value = 'c'; return }
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
    if (path === '/k') { midx.value = q('idx') || '000001.SH'; tab.value = 'k'; return }
    if (hash.startsWith('/stock-fund/')) { stockFundType.value = hash.split('/')[2] || 'stock'; tab.value = 'u'; return }
    if (path === '/trade-review') { tradeReviewTab.value = q('tab') || 'review'; tab.value = 't'; return }
    if (path === '/admin/users') { tab.value = 'y'; return }
    if (path === '/admin/roles') { tab.value = 'z'; return }
    if (path === '/admin/audit') { tab.value = 'h'; return }
    if (path === '/admin/settings') { tab.value = 'o'; return }
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
    const map = {'':'p','/':'p','/market':'m','/signals':'s','/stocks':'l','/status':'x','/models':'a','/functions':'f','/features':'e','/dag-flows':'g','/portfolio':'p','/dbx':'w','/news':'n','/agent':'i','/bonds':'b','/k':'k','/admin/users':'y','/admin/roles':'z','/admin/audit':'h','/admin/settings':'o'}
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
      case 'b': {
        // 可转债列表：保留查询参数（kw/tab/sf/sd，BondView 维护）
        const cur = location.hash.slice(1)
        const qm = cur.startsWith('/bonds') && cur.includes('?') ? cur.slice(cur.indexOf('?')) : ''
        target = '/bonds' + qm
        break
      }
      case 'c': target = '/bond/' + (bondCode.value || ''); break
      case 'k': {
        // 大盘页：默认指数不带参数，其余带 ?idx=（选中态在 URL 里可分享）
        target = '/k' + (midx.value && midx.value !== '000001.SH' ? '?idx=' + midx.value : '')
        break
      }
      case 'l': {
        // 保留列表页 URL 上已有的查询参数（cat/kw/page/sf/sd，StocksView 维护）——
        // history.back() 回到列表时 popstate 已把地址还原成带参 URL，此时 syncHash
        // 若推裸地址会在 StocksView 挂载读参之前把 URL 覆盖掉，页码/排序全部丢失
        const cur = location.hash.slice(1)
        const qm = cur.startsWith('/stocks') && cur.includes('?') ? cur.slice(cur.indexOf('?')) : ''
        target = '/stocks' + qm
        break
      }
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
      case 't': target = '/trade-review' + (tradeReviewTab.value && tradeReviewTab.value !== 'review' ? '?tab=' + tradeReviewTab.value : ''); break
      case 'w': target = '/dbx'; break
      case 'y': target = '/admin/users'; break
      case 'z': target = '/admin/roles'; break
      case 'h': target = '/admin/audit'; break
      case 'o': target = '/admin/settings'; break
      case 'i': target = '/agent'; break
      case 'n': {
        // 同 'l'：保留列表页查询参数（cat/kw/imp/sf/page，NewsView 维护），
        // 否则 history.back() 回列表时页码/筛选全部丢失
        const cur = location.hash.slice(1)
        const qm = cur.startsWith('/news') && cur.includes('?') ? cur.slice(cur.indexOf('?')) : ''
        target = '/news' + qm
        break
      }
      default: target = '/'
    }
    if (location.hash.slice(1) !== target) history.pushState(null, '', '#'+target)
  }

  function switchTab(t) {
    tab.value = t
  }

  /** 从任意页面带着问题跳到问答页（详情页「问 AI」等入口）。 */
  function askAgent(text) {
    agentSeed.value = { text: String(text || ''), at: Date.now() }
    tab.value = 'i'
  }

  function takeAgentSeed() {
    const s = agentSeed.value
    agentSeed.value = null
    return s
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

  /** 打开交易复盘二级页；reviewTab 可取 review/trades/stocks/rules。 */
  function openTradeReview(reviewTab = 'review') {
    prevTab.value = tab.value
    tradeReviewTab.value = reviewTab || 'review'
    tab.value = 't'
  }

  function backFromTradeReview() {
    // 从持仓页进来走浏览器历史，保留原页面状态；直链进入则回持仓
    if (prevTab.value) { history.back(); return }
    tab.value = 'p'
  }

  function showDetail(code) {
    prevTab.value = tab.value
    dcode.value = code
    tab.value = 'd'
  }

  function backFromDetail() {
    // 有来路（showDetail 必经 pushState）走浏览器历史：列表页的页码/排序在 URL
    // 参数里，back 原样恢复；直链进入详情（无来路）才落到默认列表
    if (prevTab.value) { history.back(); return }
    tab.value = 'l'
  }

  function showFeatureDetail(id) {
    prevTab.value = tab.value
    fid.value = id
    tab.value = 'v'
  }

  function backFromFeatureDetail() {
    tab.value = prevTab.value || 'e'
  }

  /** 打开可转债详情（个股详情/转债列表均可入）。 */
  function showBondDetail(code) {
    prevTab.value = tab.value
    bondCode.value = code
    tab.value = 'c'
  }

  function backFromBondDetail() {
    if (prevTab.value) { history.back(); return }
    tab.value = 'b'
  }

  // tab 变化时同步 URL + 离开 g 页清空 flowId
  watch(tab, (t) => { if (t !== 'g' && t !== 'q' && t !== 'r') flowId.value = null; syncHash() })
  watch(dcode, () => { if (tab.value === 'd') syncHash() })
  watch(fid, () => { if (tab.value === 'v') syncHash() })
  watch(bondCode, () => { if (tab.value === 'c') syncHash() })
  watch(midx, () => { if (tab.value === 'k') syncHash() })

  watch(flowId, () => { if (tab.value === 'g' || tab.value === 'q' || tab.value === 'r') syncHash() })

  // 页面加载时解析 URL
  parseHash()
  window.addEventListener('popstate', parseHash)

  return { tab, dcode, fid, flowId, prevTab, pendingEditFeatureId, stockFundType, marketDate, modelEntity, modelVersion, modelTab, statusMonth, bfLogPage, agentSeed, tradeReviewTab, bondCode, midx, switchTab, askAgent, takeAgentSeed, showDetail, backFromDetail, showFeatureDetail, backFromFeatureDetail, showBondDetail, backFromBondDetail, showFlowEditor, backFromFlowEditor, openTradeReview, backFromTradeReview, parseHash, syncHash }
})
