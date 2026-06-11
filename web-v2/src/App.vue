<template>
  <n-config-provider :theme="darkTheme">
    <n-dialog-provider>
    <n-message-provider>
      <div style="height:100vh;display:flex;flex-direction:column;background:#101014;overflow:hidden">
        <!-- Header -->
        <div style="padding:8px 24px;border-bottom:1px solid rgba(255,255,255,.09);display:flex;align-items:center;justify-content:space-between;background:#1a1a1e">
          <div>
            <span style="font-size:18px;font-weight:700;color:#fff">悟道</span>
            <span style="font-size:11px;color:rgba(255,255,255,.38);margin-left:12px" id="header-date"></span>
          </div>
          <div style="display:flex;align-items:center;gap:12px">
            <span style="font-size:12px;color:rgba(255,255,255,.55)">👤 admin</span>
            <n-button size="tiny" text @click="doLogout">退出</n-button>
          </div>
        </div>
        
        <!-- Tabs -->
        <div style="padding:6px 20px;border-bottom:1px solid rgba(255,255,255,.09);display:flex;gap:4px">
          <n-button :type="tab==='p'?'primary':'default'" size="small" @click="tab='p'">💼 持仓</n-button>
          <n-button :type="tab==='m'?'primary':'default'" size="small" @click="switchTab('m')">📊 选股</n-button>
          <n-button :type="tab==='s'?'primary':'default'" size="small" @click="switchTab('s')">🔴 信号</n-button>
          <n-button :type="tab==='l'?'primary':'default'" size="small" @click="switchTab('l')">📋 个股</n-button>
          <n-button :type="tab==='x'?'primary':'default'" size="small" @click="switchTab('x')">📊 状态</n-button>
          <n-button :type="tab==='o'?'primary':'default'" size="small" @click="switchTab('o')">⚙️ 设置</n-button>
        </div>

        <!-- Content -->
        <div style="flex:1;overflow-y:auto;padding:6px 20px" class="main-content">
          <!-- Portfolio -->
          <div v-if="tab==='p'">
            <PortfolioView @show-detail="showDetail" />
          </div>
          <!-- Treemap Selection -->
          <div v-if="tab==='m'">
            <TreemapView @show-detail="showDetail" />
          </div>
          <!-- Signals -->
          <div v-if="tab==='s'">
            <SignalsView @show-detail="showDetail" />
          </div>
          <!-- Stocks -->
          <div v-if="tab==='l'">
            <StocksView @show-detail="showDetail" />
          </div>
          <!-- Detail -->
          <div v-if="tab==='d'">
            <DetailView :code="dcode" @back="backFromDetail" />
          </div>
          <!-- Data Status -->
          <div v-if="tab==='x'">
            <StatusView />
          </div>
          <!-- Settings -->
          <div v-if="tab==='o'">
            <SettingsView />
          </div>
        </div>
      </div>
    </n-message-provider>
    </n-dialog-provider>
  </n-config-provider>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { darkTheme } from 'naive-ui'
import { NConfigProvider, NMessageProvider, NDialogProvider, NButton } from 'naive-ui'
import axios from 'axios'
import PortfolioView from './components/PortfolioView.vue'
import SignalsView from './components/SignalsView.vue'
import TreemapView from './components/TreemapView.vue'
import StocksView from './components/StocksView.vue'
import DetailView from './components/DetailView.vue'
import StatusView from './components/StatusView.vue'
import SettingsView from './components/SettingsView.vue'

const API = window.location.origin
const tab = ref('p')
const dcode = ref('')
const prevTab = ref('')  // 进入详情前的页面


// URL hash 路由同步
function parseHash() {
  const hash = location.hash.slice(1) || '/'
  if (hash.startsWith('/detail/')) {
    const code = hash.split('/')[2]
    if (code) { dcode.value = code; tab.value = 'd'; return }
  }
  if (hash.startsWith('/market/')) {
    tab.value = 'm'
    const parts = hash.split('/')
    if (parts[2]) window._treemapDate = parts[2]
    return
  }
  const map = {'':'p','/':'p','/market':'m','/signals':'s','/stocks':'l','/status':'x','/settings':'o'}
  tab.value = map[hash] || 'p'
}
function syncHash() {
  const map = {p:'/',m:'/market',s:'/signals',l:'/stocks',x:'/status',o:'/settings',d:'/detail/'+dcode.value}
  const target = map[tab.value] || '/'
  if (location.hash.slice(1) !== target) history.pushState(null, '', '#'+target)
}
watch(tab, syncHash)
watch(dcode, () => { if (tab.value === 'd') syncHash() })
window.addEventListener('popstate', parseHash)

function switchTab(t) {
  tab.value = t
  if (t === 'm') window._treemapDate = null  // 重置选股日期
}
function showDetail(code) {
  prevTab.value = tab.value  // 记住当前页面
  dcode.value = code
  tab.value = 'd'
}
function backFromDetail() {
  tab.value = prevTab.value || 'l'  // 回到之前页面，默认个股
}
function doLogout() { localStorage.clear(); window.location.href='/login.html' }

onMounted(async () => {
  parseHash()  // 读取 URL hash 恢复页面状态
  // 设置 auth header
  const token = localStorage.getItem('token')
  if (token) {
    axios.defaults.headers.common['Authorization'] = 'Bearer ' + token
  } else if (window.location.pathname !== '/login.html') {
    window.location.href = '/login.html'
    return
  }
  // 加载概览数据（用 DOM 操作绕过 Vue 响应式）
  setTimeout(async () => {
    try {
      const r = await axios.get(API + '/api/data_status')
      if (r.data.overview && r.data.overview.latest_date) {
        const el = document.getElementById('header-date')
        if (el) el.textContent = r.data.overview.latest_date
      }
    } catch(e) {}
  }, 200)
})
</script>
