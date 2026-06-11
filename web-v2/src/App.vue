<template>
  <n-config-provider :theme="darkTheme">
    <n-dialog-provider>
    <n-message-provider>
      <LoginView v-if="showLogin" />
      <div v-else style="height:100vh;display:flex;flex-direction:column;background:#101014;overflow:hidden">
        <div style="padding:8px 24px;border-bottom:1px solid rgba(255,255,255,.09);display:flex;align-items:center;justify-content:space-between;background:#1a1a1e">
          <div>
            <span style="font-size:18px;font-weight:700;color:#fff">K道</span>
            <span style="font-size:11px;color:rgba(255,255,255,.38);margin-left:12px" id="header-date"></span>
          </div>
          <div style="display:flex;align-items:center;gap:12px">
            <span style="font-size:12px;color:rgba(255,255,255,.55)">👤 {{ auth.username || 'admin' }}</span>
            <n-button size="tiny" text @click="auth.logout">退出</n-button>
          </div>
        </div>
        <div style="padding:6px 20px;border-bottom:1px solid rgba(255,255,255,.09);display:flex;gap:4px">
          <n-button :type="nav.tab==='p'?'primary':'default'" size="small" @click="nav.switchTab('p')">💼 持仓</n-button>
          <n-button :type="nav.tab==='m'?'primary':'default'" size="small" @click="nav.switchTab('m')">📊 选股</n-button>
          <n-button :type="nav.tab==='s'?'primary':'default'" size="small" @click="nav.switchTab('s')">🔴 信号</n-button>
          <n-button :type="nav.tab==='l'?'primary':'default'" size="small" @click="nav.switchTab('l')">📋 个股</n-button>
          <n-button :type="nav.tab==='x'?'primary':'default'" size="small" @click="nav.switchTab('x')">📊 状态</n-button>
          <n-button :type="nav.tab==='o'?'primary':'default'" size="small" @click="nav.switchTab('o')">⚙️ 设置</n-button>
        </div>
        <div style="flex:1;overflow-y:auto;padding:6px 20px" class="main-content">
          <div v-if="nav.tab==='p'"><PortfolioView @show-detail="nav.showDetail" /></div>
          <div v-if="nav.tab==='m'"><TreemapView @show-detail="nav.showDetail" /></div>
          <div v-if="nav.tab==='s'"><SignalsView @show-detail="nav.showDetail" /></div>
          <div v-if="nav.tab==='l'"><StocksView @show-detail="nav.showDetail" /></div>
          <div v-if="nav.tab==='d'"><DetailView :code="nav.dcode" @back="nav.backFromDetail" /></div>
          <div v-if="nav.tab==='x'"><StatusView /></div>
          <div v-if="nav.tab==='o'"><SettingsView /></div>
        </div>
      </div>
    </n-message-provider>
    </n-dialog-provider>
  </n-config-provider>
</template>

<script setup>
import { computed } from 'vue'
import { darkTheme } from 'naive-ui'
import { NConfigProvider, NMessageProvider, NDialogProvider, NButton } from 'naive-ui'
import axios from 'axios'
import { useAuthStore } from './stores/auth'
import { useNavStore } from './stores/nav'
import LoginView from './components/LoginView.vue'
import PortfolioView from './components/PortfolioView.vue'
import SignalsView from './components/SignalsView.vue'
import TreemapView from './components/TreemapView.vue'
import StocksView from './components/StocksView.vue'
import DetailView from './components/DetailView.vue'
import StatusView from './components/StatusView.vue'
import SettingsView from './components/SettingsView.vue'

const auth = useAuthStore()
const nav = useNavStore()
const showLogin = computed(() => !auth.token)

watch(showLogin, (v) => {
  if (v) document.title = '登录 - K道'
})

const titleMap = {
  p: '持仓 - K道',
  m: '选股 - 市值 - K道',
  s: '买点信号 - K道',
  l: '个股列表 - K道',
  d: '个股详情 - K道',
  x: '数据状态 - K道',
  o: '系统设置 - K道',
}

// tab 变化时更新页面标题
import { watch } from 'vue'
watch(() => nav.tab, (t) => {
  if (t === 'd') {
    document.title = (nav.dcode ? nav.dcode + ' - ' : '') + '个股详情 - K道'
  } else {
    document.title = titleMap[t] || 'K道'
  }
}, { immediate: true })

if (!showLogin.value) {
  setTimeout(async () => {
    try {
      const r = await axios.get(window.location.origin + '/api/data_status')
      if (r.data.overview && r.data.overview.latest_date) {
        const el = document.getElementById('header-date')
        if (el) el.textContent = r.data.overview.latest_date
      }
    } catch(e) {}
  }, 200)
}
</script>
