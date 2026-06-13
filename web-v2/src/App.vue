<template>
  <n-config-provider :theme="theme.naiveTheme">
    <n-dialog-provider>
    <n-message-provider>
      <LoginView v-if="showLogin" />
      <div v-else :style="{height:'100vh',display:'flex',flexDirection:'column',overflow:'hidden',
        '--c-bg':theme.colors.bg, '--c-bg-header':theme.colors.bgHeader,
        '--c-text':theme.colors.text, '--c-text-dim':theme.colors.textDim,
        '--c-text-dimmer':theme.colors.textDimmer, '--c-text-faint':theme.colors.textFaint,
        '--c-border':theme.colors.border, '--c-border-light':theme.colors.borderLight,
        '--c-card-bg':theme.colors.cardBg, '--c-card-bg-hover':theme.colors.cardBgHover,
        '--c-input-bg':theme.colors.inputBg,
        background:'var(--c-bg)'}">
        <div :style="{padding:'8px 24px',borderBottom:'1px solid '+theme.colors.border,display:'flex',alignItems:'center',justifyContent:'space-between',background:theme.colors.bgHeader}">
          <div>
            <span :style="{fontSize:'18px',fontWeight:700,color:theme.colors.text}">K道</span>
            <span v-if="dag.hasRunning" :style="{fontSize:'11px',color:'#f59e0b',marginLeft:'10px',animation:'pulse 1.5s infinite'}">⟳ 数据更新中</span>
            <span :style="{fontSize:'11px',color:theme.colors.textDimmer,marginLeft:'12px'}" id="header-date"></span>
          </div>
          <div style="display:flex;align-items:center;gap:12px">
            <n-dropdown trigger="click" :options="userOptions" @select="handleUserMenu">
              <span :style="{fontSize:'12px',color:theme.colors.textDim,cursor:'pointer'}">👤 {{ auth.username || 'admin' }}</span>
            </n-dropdown>
            <span :style="{fontSize:'9px',color:wsConnected?'#10b981':'#ef4444',display:'flex',alignItems:'center',gap:'2px',lineHeight:'1'}">
              <span :style="{display:'inline-block',width:'6px',height:'6px',borderRadius:'50%',background:wsConnected?'#10b981':'#ef4444'}"></span>
              {{wsConnected?'已连接':'连接失败'}}
            </span>
            <n-button v-if="!wsConnected" size="tiny" text :style="{fontSize:'10px',color:theme.colors.textDim}" @click="connectWebSocket">重连</n-button>
            <n-button size="tiny" text @click="theme.toggle" :style="{fontSize:'16px'}" :title="theme.modeHint">{{ theme.modeLabel }}</n-button>
          </div>
        </div>
        <div :style="{padding:'6px 20px',borderBottom:'1px solid '+theme.colors.border,display:'flex',gap:'4px'}">
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
import { computed, ref, onMounted, watch } from 'vue'
import { NConfigProvider, NMessageProvider, NDialogProvider, NButton, NDropdown } from 'naive-ui'
import axios from 'axios'
import { useAuthStore } from './stores/auth'
import { useNavStore } from './stores/nav'
import { useThemeStore } from './stores/theme'
import { useDagStore } from './stores/dag'
import { connectWebSocket, wsState } from './utils/ws'

const theme = useThemeStore()
const dag = useDagStore()

const wsConnected = ref(false)
onMounted(() => {
  connectWebSocket()
  dag.initWs()
  theme.startAutoTimer()
  setInterval(() => { wsConnected.value = wsState.connected }, 1000)
})
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

const userOptions = [
  { label: '退出登录', key: 'logout' }
]
function handleUserMenu(key) {
  if (key === 'logout') auth.logout()
}
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
